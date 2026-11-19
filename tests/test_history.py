"""Phase 6: run history, duplicate reuse, feedback, stats.

API tests use the in-memory fakes; the PgRunStore tests run the real SQL against the
triage_test database and are skipped when it isn't reachable.
"""

import pytest

from app.config import settings
from app.history import PgRunStore, fingerprint, guess_error_type, guess_title
from app.models import FeedbackIn, IncidentIn, Severity
from app.rag import IncidentStore
from tests.test_rag import TEST_DSN, db

LOG = ("2026-11-18T10:00:01Z ERROR session-api request_id=3f1c9a2e-8b7d-4c1e-9a0b-1234567890ab\n"
       "redis.exceptions.ConnectionError: Error 111 connecting to 10.0.7.12:6379. Connection refused.")
SAME_FAILURE_LATER = ("2026-11-18T10:41:59Z ERROR session-api request_id=00000000-1111-2222-3333-444444444444\n"
                      "redis.exceptions.ConnectionError: Error 111 connecting to 10.0.9.200:6379. Connection refused.")
OTHER = "java.lang.NullPointerException: Cannot invoke \"Address.getCountry()\" because \"addr\" is null"


# ---------- fingerprint + helpers ----------

def test_fingerprint_ignores_timestamps_ids_and_ips():
    assert fingerprint(LOG) == fingerprint(SAME_FAILURE_LATER)
    assert fingerprint(LOG) == fingerprint("  \n" + LOG.replace("\n", "\n\n  ") + "\n")


def test_fingerprint_differs_for_different_failures():
    assert fingerprint(LOG) != fingerprint(OTHER)
    assert fingerprint(LOG) != fingerprint(LOG.replace("111", "113"))


def test_fingerprint_uses_the_whole_long_log():
    """Embedding keeps only head + tail of long logs; the fingerprint must not."""
    body = "\n".join(f"  at frame{i} (worker.js:{i})" for i in range(400))
    a = f"Error: boom\n{body}\nmiddle-A\n{body}\nend"
    b = f"Error: boom\n{body}\nmiddle-B\n{body}\nend"
    assert fingerprint(a) != fingerprint(b)


def test_guess_error_type_takes_innermost():
    log = "Traceback ...\nFooError: outer\nThe above exception was the direct cause\nredis.exceptions.ConnectionError: x"
    assert guess_error_type(log) == "redis.exceptions.ConnectionError"
    assert guess_error_type("exit code 1") == "unknown"


def test_guess_title_skips_timestamp_and_level():
    assert guess_title(LOG).startswith("session-api request_id=<uuid>")
    assert guess_title("Traceback (most recent call last):\n  File x\nValueError: bad") == "File x"


# ---------- /triage dedup (fakes) ----------

def test_triage_records_a_run(fakes, fake_runs):
    client, *_ = fakes
    body = client.post("/triage", json={"log": LOG}).json()
    assert body["run_id"] == 1 and body["duplicate_of"] is None and body["duplicate_count"] == 0
    assert fake_runs.runs[1]["fp"] == fingerprint(LOG)
    assert "run_id" not in fake_runs.runs[1]["result"]


def test_same_failure_reuses_result_without_search_or_model(fakes, fake_runs):
    client, store, embedder, triager = fakes
    first = client.post("/triage", json={"log": LOG}).json()
    calls = (len(embedder.calls), len(triager.calls))
    second = client.post("/triage", json={"log": SAME_FAILURE_LATER}).json()
    third = client.post("/triage", json={"log": LOG}).json()
    assert (len(embedder.calls), len(triager.calls)) == calls  # no embed, no model call
    assert second["duplicate_of"] == first["run_id"] == second["run_id"]
    assert (second["duplicate_count"], third["duplicate_count"]) == (1, 2)
    assert second["root_cause_summary"] == first["root_cause_summary"]
    assert second["similar_incidents"] == first["similar_incidents"]
    assert second["latency_ms"]["llm"] == 0


def test_different_failure_is_not_a_duplicate(fakes):
    client, _, _, triager = fakes
    client.post("/triage", json={"log": LOG})
    body = client.post("/triage", json={"log": OTHER}).json()
    assert body["duplicate_of"] is None and len(triager.calls) == 2


def test_outside_window_runs_again(fakes, fake_runs):
    client, _, _, triager = fakes
    client.post("/triage", json={"log": LOG})
    fake_runs.window_open = False
    body = client.post("/triage", json={"log": LOG}).json()
    assert body["duplicate_of"] is None and body["run_id"] == 2 and len(triager.calls) == 2


def test_window_zero_disables_reuse(fakes):
    client, _, _, triager = fakes
    object.__setattr__(settings, "dedup_window_min", 0)  # Settings is frozen
    try:
        client.post("/triage", json={"log": LOG})
        client.post("/triage", json={"log": LOG})
    finally:
        object.__setattr__(settings, "dedup_window_min", 60)
    assert len(triager.calls) == 2


def test_result_marked_wrong_is_not_reused(fakes):
    client, _, _, triager = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    assert client.post("/feedback", json={"triage_run_id": rid, "correct": False}).status_code == 201
    body = client.post("/triage", json={"log": LOG}).json()
    assert body["duplicate_of"] is None and len(triager.calls) == 2


def test_secrets_are_masked_before_fingerprinting(fakes, fake_runs):
    """Logs differing only in a secret's value are duplicates, and no secret is stored."""
    client, *_ = fakes
    first = client.post("/triage", json={"log": LOG + "\npassword=hunter2hunter2"}).json()
    second = client.post("/triage", json={"log": LOG + "\npassword=other-secret-99"}).json()
    assert second["duplicate_of"] == first["run_id"]
    assert first["redacted_secrets"] == second["redacted_secrets"] == 1
    assert "hunter2" not in fake_runs.runs[1]["log_text"]


# ---------- /feedback (fakes) ----------

def test_feedback_unknown_run_is_404(fakes):
    client, *_ = fakes
    assert client.post("/feedback", json={"triage_run_id": 99, "correct": True}).status_code == 404


def test_feedback_correct_without_index(fakes, fake_runs):
    client, store, *_ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    r = client.post("/feedback", json={"triage_run_id": rid, "correct": True})
    assert r.status_code == 201
    assert r.json() == {"id": 1, "triage_run_id": rid, "correct": True, "indexed_as": None}
    assert store.added == []


def test_feedback_twice_is_409(fakes):
    client, *_ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    client.post("/feedback", json={"triage_run_id": rid, "correct": True})
    assert client.post("/feedback", json={"triage_run_id": rid, "correct": False}).status_code == 409


def test_correction_needs_root_cause_and_fix_to_index(fakes):
    client, *_ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    r = client.post("/feedback", json={"triage_run_id": rid, "correct": False, "index": True,
                                       "root_cause": "real cause"})
    assert r.status_code == 422


def test_correction_indexes_new_incident(fakes):
    client, store, embedder, _ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    r = client.post("/feedback", json={
        "triage_run_id": rid, "correct": False, "index": True, "severity": "high",
        "root_cause": "Security group change blocked port 6379.", "fix": "Re-open 6379 to the app SG.",
        "service": "session-api",
    })
    assert r.status_code == 201 and r.json()["indexed_as"] == 101
    inc = store.added[0]
    assert inc.root_cause == "Security group change blocked port 6379."
    assert inc.severity == Severity.high and inc.service == "session-api"
    assert inc.error_type == "redis.exceptions.ConnectionError"
    assert inc.family is None and inc.tags == ["from-feedback"]
    assert inc.log_text == LOG and embedder.calls[-1] == LOG


def test_confirmed_triage_indexes_into_cited_family(fakes):
    client, store, *_ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]  # FakeTriager cites #1
    r = client.post("/feedback", json={"triage_run_id": rid, "correct": True, "index": True})
    assert r.json()["indexed_as"] == 101
    inc = store.added[0]
    assert inc.family == "redis-failover-refused"
    assert inc.root_cause.startswith("Client pinned to a node IP")  # the triage's own answer
    assert inc.severity == Severity.critical


def test_indexed_log_is_redacted(fakes):
    client, store, *_ = fakes
    rid = client.post("/triage", json={"log": LOG + "\nAuthorization: Bearer abcdef1234567890abcdef"}).json()["run_id"]
    client.post("/feedback", json={"triage_run_id": rid, "correct": True, "index": True})
    assert "abcdef1234567890" not in store.added[0].log_text


# ---------- /stats (fakes) ----------

def test_stats_counts(fakes):
    client, *_ = fakes
    rid = client.post("/triage", json={"log": LOG}).json()["run_id"]
    client.post("/triage", json={"log": LOG})
    client.post("/triage", json={"log": OTHER})
    client.post("/feedback", json={"triage_run_id": rid, "correct": True})
    s = client.get("/stats").json()
    assert s["triage_requests"] == 3 and s["triage_runs"] == 2 and s["duplicates_suppressed"] == 1
    assert s["feedback_count"] == 1 and s["feedback_accuracy"] == 1.0
    assert s["incidents_indexed"] == 2
    assert set(s["severity_counts"]) == {"critical", "high", "medium", "low", "info"}


# ---------- PgRunStore against the real test database ----------

RESULT = {"root_cause_summary": "pinned IP", "severity": "critical", "suggested_fix": "use endpoint",
          "matched_incident_ids": [], "similar_incidents": []}


@pytest.fixture
def pg_runs():
    store = IncidentStore(TEST_DSN)
    runs = PgRunStore(store)
    with store.conn() as c, c.cursor() as cur:
        cur.execute("TRUNCATE triage_runs RESTART IDENTITY CASCADE")
    yield runs
    with store.conn() as c, c.cursor() as cur:
        cur.execute("TRUNCATE triage_runs RESTART IDENTITY CASCADE")


@db
def test_pg_record_find_bump(pg_runs):
    fp = fingerprint(LOG)
    rid = pg_runs.record(fp, LOG, RESULT, "demo", {"total": 5})
    found = pg_runs.find_recent(fp, "demo", 60)
    assert found["id"] == rid and found["result"] == RESULT
    assert pg_runs.find_recent(fp, "claude-opus-5-5", 60) is None  # other model
    assert pg_runs.find_recent(fingerprint(OTHER), "demo", 60) is None
    assert pg_runs.find_recent(fp, "demo", 0) is None
    assert pg_runs.bump(rid) == 1 and pg_runs.bump(rid) == 2


@db
def test_pg_window_expiry(pg_runs):
    fp = fingerprint(LOG)
    rid = pg_runs.record(fp, LOG, RESULT, "demo", {})
    with pg_runs.store.conn() as c, c.cursor() as cur:
        cur.execute("UPDATE triage_runs SET created_at = now() - interval '2 hours' WHERE id = %s", (rid,))
    assert pg_runs.find_recent(fp, "demo", 60) is None
    assert pg_runs.find_recent(fp, "demo", 180)["id"] == rid


@db
def test_pg_feedback_and_stats(pg_runs):
    fp = fingerprint(LOG)
    a = pg_runs.record(fp, LOG, RESULT, "demo", {})
    b = pg_runs.record(fingerprint(OTHER), OTHER, {**RESULT, "severity": "high"}, "demo", {})
    pg_runs.bump(a)
    inc_id = pg_runs.store.add(IncidentIn(title="from feedback", service="s", error_type="E",
                                          log_text=LOG, root_cause="r", fix="f",
                                          severity=Severity.critical), _vec())
    assert not pg_runs.has_feedback(a)
    pg_runs.add_feedback(FeedbackIn(triage_run_id=a, correct=True, index=True), inc_id)
    pg_runs.add_feedback(FeedbackIn(triage_run_id=b, correct=False), None)
    assert pg_runs.has_feedback(a)
    assert pg_runs.find_recent(fingerprint(OTHER), "demo", 60) is None  # marked wrong
    s = pg_runs.stats()
    assert (s["triage_requests"], s["triage_runs"], s["duplicates_suppressed"]) == (3, 2, 1)
    assert (s["feedback_count"], s["feedback_correct"], s["feedback_accuracy"]) == (2, 1, 0.5)
    assert s["indexed_from_feedback"] == 1
    assert s["severity_counts"]["critical"] == 1 and s["severity_counts"]["high"] == 1
    assert [r.id for r in s["recent_runs"]] == [b, a]
    assert s["recent_runs"][0].feedback == "incorrect" and s["recent_runs"][1].feedback == "correct"
    with pg_runs.store.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM incidents WHERE id = %s", (inc_id,))


def _vec():
    import numpy as np
    v = np.ones(settings.embed_dim, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_replay_variants_differ_in_text_but_share_a_fingerprint():
    import random

    from scripts.replay_noise import randomize
    rng = random.Random(1)
    a, b = randomize(LOG, rng), randomize(LOG, rng)
    assert a != b
    assert fingerprint(a) == fingerprint(b) != fingerprint(randomize(OTHER, rng))


def test_reused_result_publishes_duplicate_metric(fakes, monkeypatch):
    from app import metrics

    sent = []
    monkeypatch.setattr(metrics, "put_triage_metrics",
                        lambda latency, score, matched, duplicate=False: sent.append((score, matched, duplicate)))
    client, *_ = fakes
    client.post("/triage", json={"log": LOG})
    client.post("/triage", json={"log": LOG})
    assert sent == [(5, 1, False), (5, 1, True)]
