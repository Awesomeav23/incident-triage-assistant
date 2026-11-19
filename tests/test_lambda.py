"""Phase 7: S3 → Lambda → /triage → S3, and CloudWatch metrics, on Moto's in-memory AWS.

No network, no real AWS, no API: the triage call is stubbed.
"""

import json

import boto3
import pytest
from moto import mock_aws

from lambda_fn import handler as h
from scripts.simulate_s3_ingest import ALARM, ensure_resources

BUCKET = "test-bucket"
LOG = "redis.exceptions.ConnectionError: Error 111 connecting to 10.0.7.12:6379. Connection refused."
RESULT = {"severity": "critical", "root_cause_summary": "pinned IP", "duplicate_of": None, "run_id": 1}


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield s3, cw


def upload(s3, key, body=LOG):
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode() if isinstance(body, str) else body)
    return h.handler(h.s3_event(BUCKET, key))


def read(s3, key):
    return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())


def stub_api(monkeypatch, *responses):
    """Each call pops the next response; an Exception instance is raised instead."""
    calls = []
    queue = list(responses)

    def fake(url, body, timeout):
        calls.append((url, body))
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(h, "post_json", fake)
    return calls


# ---------- pure helpers ----------

@pytest.mark.parametrize("key,out", [
    ("incoming/job-42.log", "triaged/job-42.json"),
    ("incoming/ci/main/job 7.txt", "triaged/ci/main/job 7.json"),
    ("incoming/ci/no-extension", "triaged/ci/no-extension.json"),
    ("incoming/v1.2/run", "triaged/v1.2/run.json"),
])
def test_output_key(key, out):
    assert h.output_key(key, h.OK_PREFIX) == out


def test_prepare_log_keeps_tail_and_survives_bad_bytes():
    text, cut = h.prepare_log(b"head\xff\xfe " + b"x" * 60_000 + b" FATAL: the real error")
    assert cut and len(text) == h.MAX_LOG_CHARS and text.endswith("FATAL: the real error")
    text, cut = h.prepare_log(b"ok \xff log line")
    assert not cut and "�" in text


# ---------- handler on Moto ----------

def test_upload_is_triaged_and_written(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, RESULT)
    monkeypatch.setenv("TRIAGE_API_URL", "http://api:8030/")
    out = upload(s3, "incoming/ci/job-1.log")
    assert out["processed"] == 1 and out["results"][0]["status"] == "triaged"
    assert calls == [("http://api:8030/triage", {"log": LOG})]
    doc = read(s3, "triaged/ci/job-1.json")
    assert doc["source"] == f"s3://{BUCKET}/incoming/ci/job-1.log"
    assert doc["result"] == RESULT and doc["truncated"] is False


def test_url_encoded_key_from_event(aws, monkeypatch):
    s3, _ = aws
    stub_api(monkeypatch, RESULT)
    out = upload(s3, "incoming/nightly build/job+1.log")
    assert out["results"][0]["status"] == "triaged"
    assert read(s3, "triaged/nightly build/job+1.json")["result"] == RESULT


def test_api_rejection_goes_to_failed_without_retry(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, h.TriageCallError("triage API returned 422: log too long", 422))
    out = upload(s3, "incoming/job-2.log")
    assert out["results"][0]["status"] == "failed" and len(calls) == 1
    doc = read(s3, "failed/job-2.json")
    assert doc["status"] == 422 and "422" in doc["error"]
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="triaged/")


def test_gateway_error_is_retried_once(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, h.TriageCallError("503", 503), RESULT)
    assert upload(s3, "incoming/job-3.log")["results"][0]["status"] == "triaged"
    assert len(calls) == 2


def test_unreachable_api_fails_after_retries(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, h.TriageCallError("triage API unreachable: refused"))
    assert upload(s3, "incoming/job-4.log")["results"][0]["status"] == "failed"
    assert len(calls) == h.ATTEMPTS
    assert read(s3, "failed/job-4.json")["status"] is None


def test_empty_log_fails_without_calling_api(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, RESULT)
    assert upload(s3, "incoming/empty.log", "   \n")["results"][0]["status"] == "failed"
    assert calls == []


def test_huge_log_sends_tail_and_marks_truncated(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, RESULT)
    upload(s3, "incoming/big.log", "noise\n" * 20_000 + LOG)
    sent = calls[0][1]["log"]
    assert len(sent) == h.MAX_LOG_CHARS and sent.endswith(LOG)
    assert read(s3, "triaged/big.json")["truncated"] is True


def test_own_outputs_never_retrigger(aws, monkeypatch):
    s3, _ = aws
    calls = stub_api(monkeypatch, RESULT)
    s3.put_object(Bucket=BUCKET, Key="triaged/x.json", Body=b"{}")
    out = h.handler(h.s3_event(BUCKET, "triaged/x.json"))
    assert out["results"][0]["status"] == "skipped" and calls == []


def test_non_s3_and_delete_events_ignored(aws, monkeypatch):
    stub_api(monkeypatch, RESULT)
    ev = h.s3_event(BUCKET, "incoming/a.log")
    ev["Records"][0]["eventName"] = "ObjectRemoved:Delete"
    assert h.handler(ev)["processed"] == 0
    assert h.handler({"Records": [{"eventSource": "aws:sqs"}]})["processed"] == 0


# ---------- metrics ----------

def metric_names(cw, ns="IncidentTriage"):
    return {m["MetricName"] for m in cw.list_metrics(Namespace=ns)["Metrics"]}


def test_handler_publishes_ingest_metrics(aws, monkeypatch):
    s3, cw = aws
    stub_api(monkeypatch, {**RESULT, "duplicate_of": 1})
    upload(s3, "incoming/a.log")
    stub_api(monkeypatch, h.TriageCallError("bad", 422))
    upload(s3, "incoming/b.log")
    assert {"IngestSucceeded", "IngestDuplicate", "IngestFailed"} <= metric_names(cw)


def test_metrics_failure_never_fails_the_invocation(aws, monkeypatch):
    s3, _ = aws
    stub_api(monkeypatch, RESULT)

    def broken(service):
        if service == "cloudwatch":
            raise RuntimeError("CloudWatch down")
        return boto3.client(service, region_name="us-east-1")

    monkeypatch.setattr(h, "_aws", broken)
    assert upload(s3, "incoming/c.log")["results"][0]["status"] == "triaged"


def test_api_metrics_go_to_cloudwatch(aws, monkeypatch):
    from app import metrics
    from app.config import settings

    _, cw = aws
    object.__setattr__(settings, "metrics_enabled", True)
    object.__setattr__(settings, "aws_endpoint_url", None)
    metrics._client.cache_clear()
    try:
        metrics.put_triage_metrics({"total": 12.0, "llm": 0.0}, 5, 1, duplicate=True)
    finally:
        object.__setattr__(settings, "metrics_enabled", False)
        metrics._client.cache_clear()
    assert {"TriageLatencyMs", "LLMLatencyMs", "SeverityScore", "MatchedIncidents",
            "DuplicateSuppressed"} <= metric_names(cw)


def test_api_metrics_failure_is_swallowed(monkeypatch):
    from app import metrics
    from app.config import settings

    class Boom:
        def put_metric_data(self, **kw):
            raise RuntimeError("CloudWatch down")

    object.__setattr__(settings, "metrics_enabled", True)
    monkeypatch.setattr(metrics, "_client", lambda: Boom())
    try:
        metrics.put_triage_metrics({"total": 1.0, "llm": 0.0}, 3, 0)  # must not raise
    finally:
        object.__setattr__(settings, "metrics_enabled", False)


# ---------- setup ----------

def test_ensure_resources_is_idempotent(aws):
    s3, cw = aws
    ensure_resources(s3, cw, bucket="new-bucket")
    ensure_resources(s3, cw, bucket="new-bucket")
    assert "new-bucket" in {b["Name"] for b in s3.list_buckets()["Buckets"]}
    (alarm,) = cw.describe_alarms(AlarmNames=[ALARM])["MetricAlarms"]
    assert alarm["MetricName"] == "SeverityScore" and alarm["Threshold"] == 5
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
