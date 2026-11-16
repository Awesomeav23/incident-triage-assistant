"""Search against a real Postgres + pgvector (the triage_test database) with real embeddings.

Skipped automatically when the database isn't reachable.
"""

import json
import os

import psycopg2
import pytest

from app.models import IncidentIn
from app.rag import IncidentStore, get_embedder, keyword_query, tokens

TEST_DSN = os.getenv("TEST_DATABASE_URL", "postgresql://triage:triage@localhost:5438/triage_test")


def _db_up() -> bool:
    try:
        psycopg2.connect(TEST_DSN, connect_timeout=2).close()
        return True
    except psycopg2.OperationalError:
        return False


db = pytest.mark.skipif(not _db_up(), reason="test database not reachable (docker compose up -d db)")


# ---------- pure functions (no DB) ----------

def test_keyword_query_is_tsquery_safe():
    q = keyword_query("Error: x'); DROP TABLE incidents;-- | & ! :* <ts> 137 EUSAGE")
    assert q is not None
    for tok in q.split(" | "):
        assert tok.isalnum(), tok  # only [a-z0-9] reaches to_tsquery
    assert "eusage" in q and "137" in q and "drop" in q


def test_keyword_query_empty_for_noise_only():
    assert keyword_query("2025-01-01T00:00:00Z :: !!") is None


def test_tokens_dedupe_and_drop_stopwords():
    toks = tokens("Traceback error ERROR redis redis ConnectionError")
    assert toks == ["redis", "connectionerror"]


# ---------- database ----------

@pytest.fixture(scope="module")
def store():
    s = IncidentStore(TEST_DSN)
    s.truncate()
    rows = [r for r in json.load(open("data/incidents_expanded.json")) if not r["synthetic"]]
    emb = get_embedder()
    for r in rows:
        s.add(IncidentIn(**r), emb.embed(r["log_text"]))
    yield s
    s.truncate()


@db
def test_schema_creation_is_idempotent(store):
    IncidentStore(TEST_DSN)  # second init must not fail or wipe data
    assert store.count() == 47


@db
@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid"])
def test_each_mode_finds_its_own_incident(store, mode):
    row = next(r for r in json.load(open("data/sample_logs.json")) if r["family"] == "npm-lockfile-drift")
    vec = get_embedder().embed(row["log_text"])
    top = store.search(vec, 3, mode=mode, query_text=row["log_text"])
    assert top and top[0].family == "npm-lockfile-drift"


@db
def test_keyword_mode_matches_exact_error_code(store):
    log = "src/App.tsx:12:5 - error TS2532: Object is possibly 'undefined'."
    top = store.search(get_embedder().embed(log), 1, mode="keyword", query_text=log)
    assert top[0].family == "ts-strict-null"


@db
def test_similarity_is_cosine_in_every_mode(store):
    log = "redis.exceptions.ConnectionError: Error 111 connecting to 10.0.7.12:6379. Connection refused."
    vec = get_embedder().embed(log)
    for mode in ("vector", "keyword", "hybrid"):
        for r in store.search(vec, 3, mode=mode, query_text=log):
            assert 0.0 <= r.similarity <= 1.0


@db
def test_min_similarity_filters_weak_matches(store):
    log = "error[E0382]: borrow of moved value: `config` --> src/main.rs:41:20"
    vec = get_embedder().embed(log)
    assert store.search(vec, 3, min_similarity=0.99) == []
    assert len(store.search(vec, 3, min_similarity=0.0)) == 3


@db
def test_invalid_mode_rejected(store):
    with pytest.raises(ValueError):
        store.search(get_embedder().embed("x" * 20), 1, mode="fuzzy")
