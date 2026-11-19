"""Triage run history, duplicate reuse and engineer feedback (Phase 6).

A run is stored for every /triage call that actually ran search + the model. A later call
with the same normalised log (same fingerprint) within DEDUP_WINDOW_MIN reuses that run's
result instead of calling the model again, and bumps its duplicate_count (ADR-010).
"""

import hashlib
import re
from typing import Any, Protocol

from psycopg2.extras import Json, RealDictCursor

from app.models import FeedbackIn, RunSummary, Severity
from app.rag import IncidentStore, strip_noise

RUNS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS triage_runs (
    id              SERIAL PRIMARY KEY,
    fingerprint     TEXT NOT NULL,
    log_text        TEXT NOT NULL,
    result          JSONB NOT NULL,
    model           TEXT NOT NULL,
    latency_ms      JSONB NOT NULL,
    duplicate_count INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS triage_runs_fp ON triage_runs (fingerprint, created_at DESC);

CREATE TABLE IF NOT EXISTS feedback (
    id            SERIAL PRIMARY KEY,
    triage_run_id INT NOT NULL UNIQUE REFERENCES triage_runs(id) ON DELETE CASCADE,
    correct       BOOLEAN NOT NULL,
    root_cause    TEXT,
    fix           TEXT,
    severity      TEXT,
    indexed_as    INT REFERENCES incidents(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def fingerprint(log: str) -> str:
    """SHA-256 of the whole log with timestamps, IDs, IPs, addresses and hashes masked.

    Uses the full text (not the embedding's head+tail cut), so two long logs that differ
    only in the middle don't collide.
    """
    return hashlib.sha256("\n".join(strip_noise(log)).encode()).hexdigest()


_ERROR_TYPE = re.compile(
    r"\b([A-Za-z_][\w.$]*(?:Error|Exception|Exceeded|Timeout|Killed|BackOff|Denied|Refused))\b"
)


def guess_error_type(log: str) -> str:
    """Last exception-like name in the log (the innermost cause), else 'unknown'."""
    found = _ERROR_TYPE.findall(log)
    return found[-1][:100] if found else "unknown"


def guess_title(log: str) -> str:
    """First meaningful line, masked and shortened, for incidents created from feedback."""
    for ln in strip_noise(log):
        ln = re.sub(r"^(<ts>\s*)?(ERROR|FATAL|WARN(ING)?|INFO)?:?\s*", "", ln).strip()
        if len(ln) >= 3 and not ln.lower().startswith("traceback"):
            return ln[:120]
    return "Incident from triage feedback"


class RunStore(Protocol):
    def find_recent(self, fp: str, model: str, window_min: int) -> dict | None: ...
    def bump(self, run_id: int) -> int: ...
    def record(self, fp: str, log_text: str, result: dict, model: str, latency: dict) -> int | None: ...
    def get(self, run_id: int) -> dict | None: ...
    def has_feedback(self, run_id: int) -> bool: ...
    def add_feedback(self, fb: FeedbackIn, indexed_as: int | None) -> int: ...
    def stats(self, recent: int = 10) -> dict: ...


class PgRunStore:
    """Runs + feedback in the same Postgres as the incidents (shares its connection pool)."""

    def __init__(self, store: IncidentStore):
        self.store = store
        with store.conn() as c, c.cursor() as cur:
            cur.execute(RUNS_SCHEMA_SQL)

    def find_recent(self, fp: str, model: str, window_min: int) -> dict | None:
        """Newest run of this log by this model inside the window.

        Runs an engineer marked incorrect are never reused: the next call gets a fresh answer.
        """
        if window_min <= 0:
            return None
        with self.store.conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT r.id, r.result, r.duplicate_count FROM triage_runs r
                   LEFT JOIN feedback f ON f.triage_run_id = r.id
                   WHERE r.fingerprint = %s AND r.model = %s
                     AND r.created_at > now() - make_interval(mins => %s)
                     AND f.correct IS NOT FALSE
                   ORDER BY r.created_at DESC LIMIT 1""",
                (fp, model, window_min),
            )
            return cur.fetchone()

    def bump(self, run_id: int) -> int:
        with self.store.conn() as c, c.cursor() as cur:
            cur.execute(
                "UPDATE triage_runs SET duplicate_count = duplicate_count + 1 WHERE id = %s "
                "RETURNING duplicate_count",
                (run_id,),
            )
            return cur.fetchone()[0]

    def record(self, fp: str, log_text: str, result: dict, model: str, latency: dict) -> int:
        with self.store.conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO triage_runs (fingerprint, log_text, result, model, latency_ms)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (fp, log_text, Json(result), model, Json(latency)),
            )
            return cur.fetchone()[0]

    def get(self, run_id: int) -> dict | None:
        with self.store.conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, log_text, result, model FROM triage_runs WHERE id = %s", (run_id,))
            return cur.fetchone()

    def has_feedback(self, run_id: int) -> bool:
        with self.store.conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM feedback WHERE triage_run_id = %s", (run_id,))
            return cur.fetchone() is not None

    def add_feedback(self, fb: FeedbackIn, indexed_as: int | None) -> int:
        with self.store.conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO feedback (triage_run_id, correct, root_cause, fix, severity, indexed_as)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (fb.triage_run_id, fb.correct, fb.root_cause, fb.fix,
                 fb.severity.value if fb.severity else None, indexed_as),
            )
            return cur.fetchone()[0]

    def stats(self, recent: int = 10) -> dict:
        with self.store.conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT count(*) AS runs, coalesce(sum(duplicate_count), 0) AS dups
                   FROM triage_runs"""
            )
            runs = cur.fetchone()
            cur.execute(
                """SELECT count(*) AS n, count(*) FILTER (WHERE correct) AS ok,
                          count(indexed_as) AS indexed FROM feedback"""
            )
            fb = cur.fetchone()
            cur.execute(
                """SELECT result->>'severity' AS severity, count(*) AS n
                   FROM triage_runs GROUP BY 1"""
            )
            sev = {r["severity"]: r["n"] for r in cur.fetchall()}
            cur.execute(
                """SELECT r.id, r.created_at, r.model, r.result->>'severity' AS severity,
                          r.result->>'root_cause_summary' AS summary, r.duplicate_count,
                          CASE WHEN f.correct THEN 'correct' WHEN NOT f.correct THEN 'incorrect' END
                            AS feedback
                   FROM triage_runs r LEFT JOIN feedback f ON f.triage_run_id = r.id
                   ORDER BY r.created_at DESC, r.id DESC LIMIT %s""",
                (recent,),
            )
            rows = cur.fetchall()
        return _stats_dict(runs["runs"], int(runs["dups"]), fb["n"], fb["ok"], fb["indexed"], sev, [
            RunSummary(id=r["id"], created_at=r["created_at"].isoformat(timespec="seconds"),
                       model=r["model"], severity=Severity(r["severity"]),
                       summary=(r["summary"] or "")[:200], duplicate_count=r["duplicate_count"],
                       feedback=r["feedback"])
            for r in rows
        ])


def _stats_dict(runs: int, dups: int, fb_n: int, fb_ok: int, indexed: int,
                sev: dict[str, int], recent: list[RunSummary]) -> dict[str, Any]:
    return {
        "triage_requests": runs + dups,
        "triage_runs": runs,
        "duplicates_suppressed": dups,
        "feedback_count": fb_n,
        "feedback_correct": fb_ok,
        "feedback_accuracy": round(fb_ok / fb_n, 3) if fb_n else None,
        "indexed_from_feedback": indexed,
        "severity_counts": {s.value: sev.get(s.value, 0) for s in Severity},
        "recent_runs": recent,
    }


class NullRunStore:
    """Records nothing and never reuses: for evals, which must call the model every time."""

    def find_recent(self, fp, model, window_min):
        return None

    def bump(self, run_id):
        return 0

    def record(self, fp, log_text, result, model, latency):
        return None

    def get(self, run_id):
        return None

    def has_feedback(self, run_id):
        return False

    def add_feedback(self, fb, indexed_as):
        raise RuntimeError("NullRunStore does not store feedback")

    def stats(self, recent=10):
        return _stats_dict(0, 0, 0, 0, 0, {}, [])
