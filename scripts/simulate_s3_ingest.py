"""Simulate the cloud workflow on the local AWS emulator (Moto): S3 → Lambda → /triage → S3.

    docker compose up -d db moto                       # Postgres + Moto on 4567
    METRICS_ENABLED=true AWS_ENDPOINT_URL=http://127.0.0.1:4567 \\
        uvicorn app.main:app --port 8030 --env-file .env   # API publishing metrics to Moto
    python -m scripts.simulate_s3_ingest               # upload samples, run the handler

Steps: create the bucket and the critical-severity alarm (idempotent), upload sample
logs to incoming/, call the Lambda handler with a real-shaped S3 event per upload
(Moto doesn't run Lambdas from S3 notifications here, ADR-009), then list the results
in triaged/ and failed/ and the CloudWatch metrics. Free: Moto + demo mode.
"""

import argparse
import json
import os
import time

import boto3

from lambda_fn.handler import FAIL_PREFIX, OK_PREFIX, handler, s3_event

BUCKET = "incident-triage-logs"
NAMESPACE = os.getenv("METRICS_NAMESPACE", "IncidentTriage")
ALARM = "critical-severity-triage"


def clients(endpoint: str, region: str):
    kw = {"endpoint_url": endpoint, "region_name": region}
    return boto3.client("s3", **kw), boto3.client("cloudwatch", **kw)


def ensure_resources(s3, cw, bucket: str = BUCKET, namespace: str = NAMESPACE) -> None:
    """Bucket + alarm on SeverityScore >= 5 (critical). Safe to run repeatedly."""
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        s3.create_bucket(Bucket=bucket)
    cw.put_metric_alarm(
        AlarmName=ALARM,
        AlarmDescription="A triage came back critical (SeverityScore 5)",
        Namespace=namespace, MetricName="SeverityScore", Statistic="Maximum",
        Period=60, EvaluationPeriods=1, Threshold=5, ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default=os.getenv("AWS_ENDPOINT_URL", "http://127.0.0.1:4567"))
    p.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    p.add_argument("--api", default=os.getenv("TRIAGE_API_URL", "http://127.0.0.1:8030"))
    p.add_argument("--count", type=int, default=3, help="sample logs to upload (max 7)")
    p.add_argument("--repeat-first", action="store_true",
                   help="upload the first sample twice to show duplicate reuse")
    args = p.parse_args()

    # Moto accepts any credentials; never use real ones against the emulator.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ["AWS_ENDPOINT_URL"] = args.endpoint
    os.environ["TRIAGE_API_URL"] = args.api

    s3, cw = clients(args.endpoint, args.region)
    try:
        ensure_resources(s3, cw)
    except Exception as e:
        raise SystemExit(f"Can't reach the AWS emulator at {args.endpoint} ({e}). "
                         "Start it: docker compose up -d moto")

    samples = json.load(open("data/samples.json"))[: args.count]
    uploads = [(s["name"], s["log"]) for s in samples]
    if args.repeat_first and uploads:
        uploads.append((uploads[0][0] + " (repeat)", uploads[0][1]))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    print(f"bucket s3://{BUCKET}  api {args.api}  emulator {args.endpoint}\n")
    for i, (name, text) in enumerate(uploads, 1):
        key = f"incoming/ci/{stamp}/job-{i}.log"
        s3.put_object(Bucket=BUCKET, Key=key, Body=text.encode())
        out = handler(s3_event(BUCKET, key, len(text)))["results"][0]
        extra = (f"severity={out['severity']}" + (f"  reused from run #{out['duplicate_of']}"
                                                   if out.get("duplicate_of") else "")
                 if out["status"] == "triaged" else out.get("error", ""))
        print(f"  {name[:34]:<34} {out['status']:<8} {out.get('output', '')}  {extra}")

    for prefix in (OK_PREFIX, FAIL_PREFIX):
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{prefix}ci/{stamp}/")
                .get("Contents", [])]
        print(f"\n{prefix:<9} {len(keys)} object(s) from this run")

    metrics = sorted({m["MetricName"] for m in cw.list_metrics(Namespace=NAMESPACE)["Metrics"]})
    print(f"\nCloudWatch metrics in {NAMESPACE}: {', '.join(metrics) or 'none'}")
    if "SeverityScore" not in metrics:
        print("  (API metrics missing: start the API with METRICS_ENABLED=true "
              f"AWS_ENDPOINT_URL={args.endpoint})")
    alarm = cw.describe_alarms(AlarmNames=[ALARM])["MetricAlarms"][0]
    print(f"alarm {ALARM}: SeverityScore >= {alarm['Threshold']:.0f}, state {alarm['StateValue']} "
          "(Moto stores alarms but doesn't evaluate them)")


if __name__ == "__main__":
    main()
