"""Pydantic schemas for API input/output and the LLM's structured response."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import settings


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


class IncidentSummary(BaseModel):
    id: int
    title: str
    service: str
    error_type: str
    severity: Severity
    family: str | None = None
    synthetic: bool
    tags: list[str]


class IncidentDetail(IncidentSummary):
    log_text: str
    root_cause: str
    fix: str


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    total: int


class Sample(BaseModel):
    name: str
    log: str


# ---------- /triage ----------


class TriageRequest(BaseModel):
    log: str = Field(..., min_length=10, max_length=50_000)
    service: str | None = Field(None, description="Accepted for future use; currently ignored")
    top_k: int = Field(default_factory=lambda: settings.top_k, ge=1, le=10,
                       description="Defaults to the TOP_K env var (3)")


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
    suggested_fix: str = Field(
        ..., description="Concrete fix; one step per line (newline-separated), code in backticks"
    )
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
    # Phase 6: run history + duplicate reuse
    run_id: int | None = Field(None, description="Stored triage run; send feedback for this id")
    duplicate_of: int | None = Field(
        None, description="Set when this is a reused result for a log seen within the dedup window"
    )
    duplicate_count: int = Field(0, description="Times this log was seen again after the first triage")


# ---------- /feedback, /stats (Phase 6) ----------


class FeedbackIn(BaseModel):
    triage_run_id: int
    correct: bool
    root_cause: str | None = Field(None, max_length=5000, description="Engineer's correction")
    fix: str | None = Field(None, max_length=5000)
    severity: Severity | None = None
    index: bool = Field(False, description="Add this log + (corrected) diagnosis as a new incident")
    title: str | None = Field(None, min_length=3, max_length=200)
    service: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def correction_needed_to_index(self):
        if self.index and not self.correct and not (
            self.root_cause and self.root_cause.strip() and self.fix and self.fix.strip()
        ):
            raise ValueError("to index a corrected triage, give the real root_cause and fix")
        return self


class FeedbackOut(BaseModel):
    id: int
    triage_run_id: int
    correct: bool
    indexed_as: int | None = None


class RunSummary(BaseModel):
    id: int
    created_at: str
    model: str
    severity: Severity
    summary: str
    duplicate_count: int
    feedback: Literal["correct", "incorrect"] | None = None


class Stats(BaseModel):
    incidents_indexed: int
    triage_requests: int = Field(..., description="All /triage calls, including duplicates")
    triage_runs: int = Field(..., description="Calls that ran search + the model")
    duplicates_suppressed: int
    feedback_count: int
    feedback_correct: int
    feedback_accuracy: float | None
    indexed_from_feedback: int
    severity_counts: dict[str, int]
    recent_runs: list[RunSummary]
