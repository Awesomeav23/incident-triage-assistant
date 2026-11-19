"""Latency benchmark: per-step p50 / p95 / max for /triage, warm. Free (demo mode).

    python -m scripts.bench            # 30 runs per sample log after 3 warm-ups
    python -m scripts.bench --n 100

Two paths, both through the real /triage route in-process (FastAPI TestClient):
  fresh   - redact, fingerprint, embed, hybrid search, answer (history bypassed)
  repeat  - same log again within the window: fingerprint lookup only (ADR-010)

Demo mode answers in ~0 ms, so "llm" here is not Claude's latency. Claude was measured
separately at 6.7–15 s per call (Phases 3–4) and dominates a real triage; see the README.
Writes results/bench_<date>.json. See docs/EVAL_PLAN.md §4.
"""

import argparse
import json
import statistics
import time
from datetime import UTC, date, datetime
from pathlib import Path


def summarize(values: list[float]) -> dict:
    s = sorted(values)
    pick = lambda p: round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)  # noqa: E731
    return {"p50": pick(50), "p95": pick(95), "max": round(s[-1], 1), "mean": round(statistics.fmean(s), 1)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=30, help="timed requests per sample log")
    p.add_argument("--warmup", type=int, default=3)
    args = p.parse_args()

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.history import NullRunStore, PgRunStore
    from app.llm import DemoTriager, get_triager
    from app.main import app, get_runs, get_store
    from scripts.eval_retrieval import git_sha
    from scripts.replay_noise import ReplayRunStore

    samples = json.load(open("data/samples.json"))
    store = get_store()
    PgRunStore(store)  # tables exist
    with store.conn() as c, c.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM triage_runs")
        start_id = cur.fetchone()[0]

    app.dependency_overrides[get_triager] = lambda: DemoTriager()
    steps = ("embed", "search", "llm", "total")
    fresh = {k: [] for k in steps}
    fresh_wall, repeat_total, repeat_wall = [], [], []
    try:
        with TestClient(app) as client:
            # Fresh path: history bypassed so every request embeds and searches.
            app.dependency_overrides[get_runs] = lambda: NullRunStore()
            for s in samples:
                for i in range(args.warmup + args.n):
                    t0 = time.perf_counter()
                    r = client.post("/triage", json={"log": s["log"]})
                    wall = (time.perf_counter() - t0) * 1000
                    r.raise_for_status()
                    if i >= args.warmup:
                        for k in steps:
                            fresh[k].append(r.json()["latency_ms"][k])
                        fresh_wall.append(wall)

            # Repeat path: first call stores a run, the rest are answered from history.
            runs = ReplayRunStore(store, start_id)
            app.dependency_overrides[get_runs] = lambda: runs
            for s in samples:
                for i in range(1 + args.warmup + args.n):
                    t0 = time.perf_counter()
                    r = client.post("/triage", json={"log": s["log"]})
                    wall = (time.perf_counter() - t0) * 1000
                    r.raise_for_status()
                    if i > args.warmup:
                        assert r.json()["duplicate_of"] is not None
                        repeat_total.append(r.json()["latency_ms"]["total"])
                        repeat_wall.append(wall)
    finally:
        app.dependency_overrides.clear()
        with store.conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM triage_runs WHERE id > %s", (start_id,))

    out = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "config": {"n_per_log": args.n, "warmup": args.warmup, "logs": len(samples),
                   "triager": "demo", "search_mode": settings.search_mode,
                   "embed_model": settings.embed_model, "indexed": store.count()},
        "fresh_ms": {k: summarize(v) for k, v in fresh.items()} | {"client_wall": summarize(fresh_wall)},
        "repeat_ms": {"total": summarize(repeat_total), "client_wall": summarize(repeat_wall)},
        "note": "Demo mode: 'llm' is ~0. Claude measured separately at 6.7-15 s per call (Phases 3-4).",
    }
    Path("results").mkdir(exist_ok=True)
    path = Path("results") / f"bench_{date.today()}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")

    n = len(fresh_wall)
    print(f"{n} fresh + {len(repeat_wall)} repeat requests, {len(samples)} logs, {store.count()} incidents indexed\n")
    print(f"{'ms':<22}{'p50':>8}{'p95':>8}{'max':>8}")
    rows = [(f"fresh {k}", out["fresh_ms"][k]) for k in (*steps, "client_wall")]
    rows += [(f"repeat {k}", v) for k, v in out["repeat_ms"].items()]
    for label, s in rows:
        print(f"{label:<22}{s['p50']:>8}{s['p95']:>8}{s['max']:>8}")
    print(f"\nsaved {path}  (test runs removed from the DB)")


if __name__ == "__main__":
    main()
