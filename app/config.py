"""Runtime settings, read once from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://triage:triage@localhost:5438/triage"
    )
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    # low effort keeps triage latency down; raise for harder logs
    claude_effort: str = os.getenv("CLAUDE_EFFORT", "low")
    embed_model: str = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    embed_dim: int = int(os.getenv("EMBED_DIM", "384"))
    top_k: int = int(os.getenv("TOP_K", "3"))
    # CloudWatch metrics (pointed at the local Moto emulator by default)
    metrics_enabled: bool = os.getenv("METRICS_ENABLED", "false").lower() == "true"
    aws_endpoint_url: str | None = os.getenv("AWS_ENDPOINT_URL")
    aws_region: str = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    metrics_namespace: str = os.getenv("METRICS_NAMESPACE", "IncidentTriage")


settings = Settings()
