"""S3 → triage Lambda (Phase 7).

A CI job uploads `incoming/<path>.log` to the triage bucket. For each S3 ObjectCreated
record this handler reads the log, POSTs it to `TRIAGE_API_URL/triage`, and writes:

    triaged/<path>.json   {"source", "triaged_at", "truncated", "result": <TriageResponse>}
    failed/<path>.json    {"source", "failed_at", "error", "status"}   (nothing is lost silently)

Only boto3 (built into the Lambda runtime) and the standard library are used, so the
function deploys as a single file. Locally, scripts/simulate_s3_ingest.py calls it with a
real-shaped S3 event against the Moto emulator (ADR-009).

Env: TRIAGE_API_URL (default http://127.0.0.1:8030), TRIAGE_TIMEOUT_S (60),
AWS_ENDPOINT_URL (Moto locally), METRICS_NAMESPACE (IncidentTriage).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

IN_PREFIX, OK_PREFIX, FAIL_PREFIX = "incoming/", "triaged/", "failed/"
MAX_LOG_CHARS = 50_000  # the API's limit; longer CI logs keep the tail, where errors are
MIN_LOG_CHARS = 10
RETRY_STATUSES = {502, 503, 504}
ATTEMPTS = 2


class TriageCallError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _aws(service: str):
    return boto3.client(service, endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
                        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))


def output_key(key: str, prefix: str) -> str:
    """incoming/ci/job-42.log → <prefix>ci/job-42.json"""
    rest = key[len(IN_PREFIX):]
    stem = rest.rsplit(".", 1)[0] if "." in rest.rsplit("/", 1)[-1] else rest
    return f"{prefix}{stem}.json"


def prepare_log(raw: bytes) -> tuple[str, bool]:
    """Decode (bad bytes replaced) and keep the last MAX_LOG_CHARS if too long."""
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= MAX_LOG_CHARS:
        return text, False
    return text[-MAX_LOG_CHARS:], True


def post_json(url: str, body: dict, timeout: float) -> dict:
    """POST JSON, return the parsed body. Raises TriageCallError with the HTTP status."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", e.reason)
        except Exception:
            detail = e.reason
        raise TriageCallError(f"triage API returned {e.code}: {detail}", e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TriageCallError(f"triage API unreachable: {e}") from e


def call_triage(text: str) -> dict:
    url = os.getenv("TRIAGE_API_URL", "http://127.0.0.1:8030").rstrip("/") + "/triage"
    timeout = float(os.getenv("TRIAGE_TIMEOUT_S", "60"))
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return post_json(url, {"log": text}, timeout)
        except TriageCallError as e:
            # Retry only what might pass next time: unreachable or a 5xx gateway error.
            if attempt == ATTEMPTS or (e.status is not None and e.status not in RETRY_STATUSES):
                raise
            log.warning("triage attempt %d failed (%s), retrying", attempt, e)
            time.sleep(1.0)
    raise AssertionError("unreachable")


def put_metrics(outcome: str, result: dict | None) -> None:
    """Ingest metrics; never fails the invocation (NFR-7)."""
    data = [{"MetricName": "IngestSucceeded" if outcome == "ok" else "IngestFailed",
             "Value": 1, "Unit": "Count"}]
    if result is not None and result.get("duplicate_of") is not None:
        data.append({"MetricName": "IngestDuplicate", "Value": 1, "Unit": "Count"})
    try:
        _aws("cloudwatch").put_metric_data(
            Namespace=os.getenv("METRICS_NAMESPACE", "IncidentTriage"), MetricData=data)
    except Exception:
        log.warning("put_metric_data failed", exc_info=True)


def process_record(s3, record: dict) -> dict:
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])  # S3 events URL-encode keys
    source = f"s3://{bucket}/{key}"
    if not key.startswith(IN_PREFIX):
        # Our own triaged/ and failed/ writes must never re-trigger triage.
        return {"source": source, "status": "skipped"}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        text, truncated = prepare_log(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        if len(text.strip()) < MIN_LOG_CHARS:
            raise TriageCallError("log is empty or too short to triage")
        result = call_triage(text)
    except TriageCallError as e:
        out = output_key(key, FAIL_PREFIX)
        s3.put_object(Bucket=bucket, Key=out, ContentType="application/json",
                      Body=json.dumps({"source": source, "failed_at": now, "error": str(e),
                                       "status": e.status}, indent=2).encode())
        put_metrics("failed", None)
        log.error("triage failed for %s: %s", source, e)
        return {"source": source, "status": "failed", "output": f"s3://{bucket}/{out}", "error": str(e)}

    out = output_key(key, OK_PREFIX)
    s3.put_object(Bucket=bucket, Key=out, ContentType="application/json",
                  Body=json.dumps({"source": source, "triaged_at": now, "truncated": truncated,
                                   "result": result}, indent=2).encode())
    put_metrics("ok", result)
    log.info("triaged %s → %s (%s)", source, out, result.get("severity"))
    return {"source": source, "status": "triaged", "output": f"s3://{bucket}/{out}",
            "severity": result.get("severity"), "duplicate_of": result.get("duplicate_of")}


def handler(event: dict, context=None) -> dict:
    s3 = _aws("s3")
    results = [process_record(s3, r) for r in event.get("Records", [])
               if r.get("eventSource") == "aws:s3" and r.get("eventName", "").startswith("ObjectCreated")]
    return {"processed": len(results), "results": results}


def s3_event(bucket: str, key: str, size: int = 0) -> dict:
    """A real-shaped S3 ObjectCreated:Put event (what S3 sends to Lambda)."""
    from urllib.parse import quote_plus

    return {"Records": [{
        "eventVersion": "2.1", "eventSource": "aws:s3", "awsRegion": "us-east-1",
        "eventTime": datetime.now(timezone.utc).isoformat(), "eventName": "ObjectCreated:Put",
        "s3": {"s3SchemaVersion": "1.0", "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
               "object": {"key": quote_plus(key, safe="/"), "size": size}},
    }]}
