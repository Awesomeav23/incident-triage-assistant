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


@db
def test_list_filters_and_paginates(store):
    items, total = store.list(limit=10)
    assert total == 47 and len(items) == 10
    assert [i.id for i in items] == sorted((i.id for i in items), reverse=True)  # newest first
    page2, _ = store.list(limit=10, offset=10)
    assert not {i.id for i in items} & {i.id for i in page2}
    crit, n_crit = store.list(severity="critical", limit=100)
    assert n_crit == len(crit) > 0 and all(i.severity.value == "critical" for i in crit)
    redis, _ = store.list(q="redis", limit=100)
    assert redis and all("redis" in (i.title + i.service + i.error_type + (i.family or "")).lower() for i in redis)


@db
def test_list_can_hide_synthetic_recurrences(full_store):
    _, everything = full_store.list(limit=1)
    real, n_real = full_store.list(limit=100, include_synthetic=False)
    assert everything == 235 and n_real == 47 and not any(i.synthetic for i in real)


@db
def test_list_search_treats_wildcards_literally(store):
    assert store.list(q="%")[1] == 0  # no incident contains a literal %
    items, n = store.list(q="_", limit=100)  # unescaped, "_" would match all 47
    assert 0 < n < 47
    assert all("_" in (i.title + i.service + i.error_type + (i.family or "")) for i in items)


@db
def test_get_returns_detail_or_none(store):
    first = store.list(limit=1)[0][0]
    detail = store.get(first.id)
    assert detail and detail.log_text and detail.root_cause and detail.fix
    assert store.get(10_000_000) is None


@pytest.fixture(scope="module")
def full_store():
    """All 235 incidents, including the synthetic recurrences."""
    s = IncidentStore(TEST_DSN)
    s.truncate()
    emb = get_embedder()
    for r in json.load(open("data/incidents_expanded.json")):
        s.add(IncidentIn(**r), emb.embed(r["log_text"]))
    yield s
    s.truncate()


@db
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_distinct_families_returns_k_different_causes(full_store, mode):
    log = "redis.exceptions.ConnectionError: Error 111 connecting to 10.0.7.12:6379. Connection refused."
    vec = get_embedder().embed(log)
    plain = full_store.search(vec, 3, mode=mode, query_text=log)
    distinct = full_store.search(vec, 3, mode=mode, query_text=log, distinct_families=True)
    assert len({r.family for r in plain}) < 3  # recurrences crowd the plain top 3
    assert len(distinct) == 3 and len({r.family for r in distinct}) == 3
    assert distinct[0].family == plain[0].family == "redis-failover-refused"
