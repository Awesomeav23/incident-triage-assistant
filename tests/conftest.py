"""Shared fakes: tests run without a database, an embedding model, or an API key."""

import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.history import _stats_dict
from app.llm import LLMError, get_triager
from app.main import app, get_runs, get_store
from app.models import LLMTriage, Severity, SimilarIncident
from app.rag import get_embedder


class FakeEmbedder:
    def __init__(self):
        self.calls: list[str] = []

    def embed(self, text: str) -> np.ndarray:
        self.calls.append(text)
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        v = np.random.default_rng(seed).random(384, dtype=np.float32)
        return v / np.linalg.norm(v)


def make_incident(id: int, similarity: float = 0.9, family: str = "redis-failover-refused") -> SimilarIncident:
    return SimilarIncident(
        id=id, title=f"Past incident {id}", service="session-service",
        error_type="redis.exceptions.ConnectionError", root_cause="pinned node IP",
        fix="use the primary endpoint DNS name", severity=Severity.critical,
        family=family, similarity=similarity, score=similarity,
    )


class FakeStore:
    def __init__(self, results: list[SimilarIncident] | None = None):
        self.results = results if results is not None else [make_incident(1), make_incident(2, 0.8)]
        self.added: list = []
        self.search_kwargs: dict = {}

    def search(self, embedding, top_k, mode="vector", query_text=None, min_similarity=0.0,
               distinct_families=False):
        self.search_kwargs = {"top_k": top_k, "mode": mode, "query_text": query_text,
                              "min_similarity": min_similarity,
                              "distinct_families": distinct_families}
        top = self.results[:top_k]  # same cutoff rule as IncidentStore.search
        if any(r.similarity >= min_similarity for r in top):
            return top
        return [r for r in self.results if r.similarity >= min_similarity][:top_k]

    def add(self, inc, embedding) -> int:
        self.added.append(inc)
        return 100 + len(self.added)

    def count(self) -> int:
        return len(self.results) + len(self.added)


class FakeTriager:
    model = "fake-model"

    def __init__(self, result: LLMTriage | None = None, error: str | None = None):
        self.result = result or LLMTriage(
            root_cause_summary="Client pinned to a node IP; failover moved the primary.",
            severity=Severity.critical, severity_reason="All session reads fail.",
            suggested_fix="Use the ElastiCache primary endpoint.",
            next_steps=["Check failover events", "Update REDIS_HOST"],
            matched_incident_ids=[1], confidence=0.85,
        )
        self.error = error
        self.calls: list[tuple[str, list]] = []

    def triage(self, log, similar):
        self.calls.append((log, similar))
        if self.error:
            raise LLMError(self.error)
        return self.result.model_copy(deep=True)


class FakeRunStore:
    """In-memory run history. `window_open` stands in for "created inside the dedup window"."""

    def __init__(self):
        self.runs: dict[int, dict] = {}
        self.feedback: dict[int, dict] = {}
        self.window_open = True

    def find_recent(self, fp, model, window_min):
        if window_min <= 0 or not self.window_open:
            return None
        for r in sorted(self.runs.values(), key=lambda r: -r["id"]):
            fb = self.feedback.get(r["id"])
            if r["fp"] == fp and r["model"] == model and not (fb and not fb["correct"]):
                return {"id": r["id"], "result": r["result"], "duplicate_count": r["duplicate_count"]}
        return None

    def bump(self, run_id):
        self.runs[run_id]["duplicate_count"] += 1
        return self.runs[run_id]["duplicate_count"]

    def record(self, fp, log_text, result, model, latency):
        rid = len(self.runs) + 1
        self.runs[rid] = {"id": rid, "fp": fp, "log_text": log_text, "result": result,
                          "model": model, "duplicate_count": 0}
        return rid

    def get(self, run_id):
        return self.runs.get(run_id)

    def has_feedback(self, run_id):
        return run_id in self.feedback

    def add_feedback(self, fb, indexed_as):
        self.feedback[fb.triage_run_id] = {"correct": fb.correct, "indexed_as": indexed_as}
        return len(self.feedback)

    def stats(self, recent=10):
        fbs = list(self.feedback.values())
        return _stats_dict(len(self.runs), sum(r["duplicate_count"] for r in self.runs.values()),
                           len(fbs), sum(f["correct"] for f in fbs),
                           sum(f["indexed_as"] is not None for f in fbs), {}, [])


@pytest.fixture
def fake_runs():
    return FakeRunStore()


@pytest.fixture
def fakes(fake_runs):
    """Override the app's dependencies; yields (client, store, embedder, triager).

    The run store is overridden too (request `fake_runs` to inspect it), so no test
    ever touches the real database.
    """
    store, embedder, triager = FakeStore(), FakeEmbedder(), FakeTriager()
    app.dependency_overrides[get_runs] = lambda: fake_runs
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_triager] = lambda: triager
    with TestClient(app) as client:
        yield client, store, embedder, triager
    app.dependency_overrides.clear()
