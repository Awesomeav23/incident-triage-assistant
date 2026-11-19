"""CloudWatch custom metrics. No-op unless METRICS_ENABLED=true.

Locally, AWS_ENDPOINT_URL points boto3 at the Moto emulator from docker-compose.
"""

import logging
from functools import lru_cache

from app.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client(
        "cloudwatch", region_name=settings.aws_region, endpoint_url=settings.aws_endpoint_url
    )


def put_triage_metrics(
    latency_ms: dict[str, float], severity_score: int, matched: int, duplicate: bool = False
) -> None:
    """One data point per /triage call. Duplicates report LLM latency 0 (no model call)."""
    if not settings.metrics_enabled:
        return
    try:
        _client().put_metric_data(
            Namespace=settings.metrics_namespace,
            MetricData=[
                {"MetricName": "TriageLatencyMs", "Value": latency_ms["total"], "Unit": "Milliseconds"},
                {"MetricName": "LLMLatencyMs", "Value": latency_ms["llm"], "Unit": "Milliseconds"},
                {"MetricName": "SeverityScore", "Value": severity_score, "Unit": "None"},
                {"MetricName": "MatchedIncidents", "Value": matched, "Unit": "Count"},
                {"MetricName": "DuplicateSuppressed", "Value": int(duplicate), "Unit": "Count"},
            ],
        )
    except Exception:  # metrics must never break triage
        log.warning("CloudWatch put_metric_data failed", exc_info=True)
