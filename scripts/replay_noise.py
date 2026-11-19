"""Noise-reduction replay: how many model calls does duplicate reuse avoid in a burst?

    python -m scripts.replay_noise                  # 500 logs from 12 failures, demo mode (free)
    python -m scripts.replay_noise --logs 1000 --failures 20 --keep

Builds a burst where each log is one of a few real failures with fresh timestamps,
request IDs, IPs and memory addresses (what a flapping CI job or a crash loop produces),
shuffles it, and sends it through the real /triage route. Always demo mode: no API calls.

Only runs created by this replay count for reuse, and they're deleted afterwards
unless --keep. Writes results/replay_noise_<date>.json. See docs/EVAL_PLAN.md §5.
"""

import argparse
import json
import random
import statistics
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.history import PgRunStore, fingerprint

# Claude-side savings are estimates from earlier measurements, not measured here (demo mode).
LIVE_LLM_MS = 9000  # one Opus 5.5 triage at effort low, Phase 3 live test (6.7–15 s seen)
COST_PER_CALL = 0.03  # USD, ~2,000 input + ~700 output tokens at Opus list price, rounded up


def randomize(log: str, rng: random.Random) -> str:
    """Same failure, new run-specific noise."""
    ts = datetime(2026, 11, 18, tzinfo=UTC) + timedelta(seconds=rng.randrange(86400))
    uuid = "-".join(f"{rng.getrandbits(n * 4):0{n}x}" for n in (8, 4, 4, 4, 12))
    ip = ".".join(str(rng.randrange(1, 255)) for _ in range(4))
    addr = f"0x{rng.getrandbits(48):012x}"
    header = f"{ts.isoformat().replace('+00:00', 'Z')} worker request_id={uuid} host={ip} at {addr}"
    return f"{header}\n{log}"


class ReplayRunStore(PgRunStore):
    """Only reuses runs created after `after_id`, so earlier history can't skew the count."""

    def __init__(self, store, after_id: int):
        super().__init__(store)
        self.after_id = after_id

    def find_recent(self, fp, model, window_min):
        found = super().find_recent(fp, model, window_min)
        return found if found and found["id"] > self.after_id else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--logs", type=int, default=500)
    p.add_argument("--failures", type=int, default=12)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--keep", action="store_true", help="keep the replay's runs in the database")
    args = p.parse_args()

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.llm import DemoTriager, get_triager
    from app.main import app, get_runs, get_store

    if settings.dedup_window_min <= 0:
        raise SystemExit("DEDUP_WINDOW_MIN is 0 (reuse off); nothing to measure.")

    rng = random.Random(args.seed)
    pool = [q for q in json.load(open("data/eval_set.json")) if q["expected_family"]]
    failures = rng.sample(pool, args.failures)
    # Skewed like real noise: a couple of failures produce most of the volume.
    weights = [1 / (i + 1) for i in range(len(failures))]
    burst = [(f["expected_family"], randomize(f["log"], rng))
             for f in rng.choices(failures, weights=weights, k=args.logs)]

    store = get_store()
    with store.conn() as c, c.cursor() as cur:
        PgRunStore(store)  # make sure the tables exist
        cur.execute("SELECT coalesce(max(id), 0) FROM triage_runs")
        start_id = cur.fetchone()[0]
    runs = ReplayRunStore(store, start_id)

    app.dependency_overrides[get_triager] = lambda: DemoTriager()
    app.dependency_overrides[get_runs] = lambda: runs
    model_calls, reused, totals, per_family = 0, 0, [], {}
    t0 = time.perf_counter()
    try:
        with TestClient(app) as client:
            for fam, log in burst:
                r = client.post("/triage", json={"log": log})
                r.raise_for_status()
                body = r.json()
                totals.append(body["latency_ms"]["total"])
                d = per_family.setdefault(fam, {"logs": 0, "model_calls": 0})
                d["logs"] += 1
                if body["duplicate_of"] is None:
                    model_calls += 1
                    d["model_calls"] += 1
                else:
                    reused += 1
    finally:
        app.dependency_overrides.clear()
        if not args.keep:
            with store.conn() as c, c.cursor() as cur:
                cur.execute("DELETE FROM triage_runs WHERE id > %s", (start_id,))
    wall = time.perf_counter() - t0

    unique_fps = len({fingerprint(log) for _, log in burst})
    out = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {"logs": args.logs, "failures": args.failures, "seed": args.seed,
                   "dedup_window_min": settings.dedup_window_min, "triager": "demo"},
        "metrics": {
            "logs": len(burst),
            "distinct_failures": len(failures),
            "unique_fingerprints": unique_fps,
            "model_calls": model_calls,
            "model_calls_avoided": reused,
            "reduction": round(reused / len(burst), 3),
            "wall_s": round(wall, 1),
            "total_ms_p50": round(statistics.median(totals), 1),
            "est_claude_time_saved_min": round(reused * LIVE_LLM_MS / 60000, 1),
            "est_claude_cost_saved_usd": round(reused * COST_PER_CALL, 2),
        },
        "assumptions": {"live_llm_ms": LIVE_LLM_MS, "cost_per_call_usd": COST_PER_CALL,
                        "note": "Claude time/cost saved are estimates; this replay runs in demo mode"},
        "per_family": per_family,
    }
    Path("results").mkdir(exist_ok=True)
    path = Path("results") / f"replay_noise_{date.today()}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")

    m = out["metrics"]
    print(f"logs replayed          {m['logs']}  ({m['distinct_failures']} distinct failures, "
          f"{m['unique_fingerprints']} unique fingerprints)")
    print(f"model calls made       {m['model_calls']}")
    print(f"model calls avoided    {m['model_calls_avoided']}  ({m['reduction']:.1%})")
    print(f"wall time              {m['wall_s']} s  (p50 {m['total_ms_p50']} ms per request)")
    print(f"est. if Claude was on  ~{m['est_claude_time_saved_min']} min of model time and "
          f"~${m['est_claude_cost_saved_usd']} saved  (assumes {LIVE_LLM_MS / 1000:.0f} s, "
          f"${COST_PER_CALL}/call)")
    print(f"\nsaved {path}{'' if args.keep else '  (replay runs deleted from the DB)'}")


if __name__ == "__main__":
    main()
