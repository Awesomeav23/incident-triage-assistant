"""Shared fakes: tests run without a database, an embedding model, or an API key."""

import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.llm import LLMError, get_triager
from app.main import app, get_store
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


@pytest.fixture
def fakes():
    """Override the app's dependencies; yields (client, store, embedder, triager)."""
    store, embedder, triager = FakeStore(), FakeEmbedder(), FakeTriager()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_triager] = lambda: triager
    with TestClient(app) as client:
        yield client, store, embedder, triager
    app.dependency_overrides.clear()
