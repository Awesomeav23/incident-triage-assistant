"""Pydantic schemas for API input/output and the LLM's structured response."""

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"  # outage / data loss
    high = "high"  # major feature broken
    medium = "medium"  # degraded, workaround exists
    low = "low"  # cosmetic / single user
    info = "info"  # noise, not a real failure


SEVERITY_SCORE = {
    Severity.critical: 5,
    Severity.high: 4,
    Severity.medium: 3,
    Severity.low: 2,
    Severity.info: 1,
}


# ---------- /index-incident ----------


class IncidentIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    service: str = Field(..., min_length=1, max_length=100)
    error_type: str = Field(..., description="e.g. NullPointerException, OOMKilled")
    log_text: str = Field(..., min_length=10, description="Raw stack trace or CI log")
    root_cause: str
    fix: str
    severity: Severity
    tags: list[str] = []
    family: str | None = Field(None, description="Groups recurrences of the same failure")
    synthetic: bool = False


class IndexedIncident(BaseModel):
    id: int
    title: str


# ---------- /triage ----------


class TriageRequest(BaseModel):
    log: str = Field(..., min_length=10, max_length=50_000)
    service: str | None = Field(None, description="Optional hint, narrows nothing yet")
    top_k: int = Field(3, ge=1, le=10)


class SimilarIncident(BaseModel):
    id: int
    title: str
    service: str
    error_type: str
    root_cause: str
    fix: str
    severity: Severity
    family: str | None = None
    similarity: float = Field(..., description="Cosine similarity, 0..1")
    score: float = Field(..., description="Ranking score for the search mode used")


class LLMTriage(BaseModel):
    """Exactly what Claude must return (enforced via structured outputs)."""

    root_cause_summary: str = Field(..., description="2-4 plain-English sentences")
    severity: Severity
    severity_reason: str
    suggested_fix: str = Field(..., description="Concrete fix, code or config if useful")
    next_steps: list[str] = Field(..., description="Ordered debugging steps, max 5")
    matched_incident_ids: list[int] = Field(
        ..., description="IDs of past incidents that actually informed this answer"
    )
    confidence: float = Field(..., ge=0, le=1)


class TriageResponse(LLMTriage):
    severity_score: int = Field(..., ge=1, le=5)
    similar_incidents: list[SimilarIncident]
    no_close_match: bool = Field(
        ..., description="True when no past incident cleared the similarity threshold"
    )
    search_mode: str
    redacted_secrets: int = Field(..., description="Secrets masked before processing")
    latency_ms: dict[str, float] = Field(
        ..., description="embed / search / llm / total timings"
    )
    model: str
