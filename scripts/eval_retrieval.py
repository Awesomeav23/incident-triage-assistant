"""Retrieval eval: does search rank the right past incident first / in the top 3?

    python -m scripts.eval_retrieval [--queries data/eval_queries.json] [--k 10]

Writes results/retrieval_<date>.json. See docs/EVAL_PLAN.md §2.
"""

import argparse
import json
import statistics
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import settings
from app.rag import IncidentStore, get_embedder


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 2)


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queries", default="data/eval_queries.json")
    p.add_argument("--k", type=int, default=10, help="depth searched for MRR")
    args = p.parse_args()

    queries = json.load(open(args.queries))
    store = IncidentStore()
    embedder = get_embedder()
    embedder.embed("warm-up")
    store.search(embedder.embed("warm-up"), 1)

    per_query, embed_ms, search_ms = [], [], []
    for q in queries:
        t0 = time.perf_counter()
        vec = embedder.embed(q["log"])
        t1 = time.perf_counter()
        results = store.search(vec, args.k)
        t2 = time.perf_counter()
        embed_ms.append((t1 - t0) * 1000)
        search_ms.append((t2 - t1) * 1000)

        titles = [r.title for r in results]
        expected = q["expected_title"]
        rank = titles.index(expected) + 1 if expected in titles else None
        per_query.append({
            "name": q["name"],
            "expected": expected,
            "rank": rank,
            "top1": titles[0] if titles else None,
            "top_similarity": round(results[0].similarity, 3) if results else None,
        })

    matchable = [r for r in per_query if r["expected"] is not None]
    no_match = [r for r in per_query if r["expected"] is None]
    n = len(matchable)
    metrics = {
        "hit@1": round(sum(r["rank"] == 1 for r in matchable) / n, 3),
        "hit@3": round(sum(r["rank"] is not None and r["rank"] <= 3 for r in matchable) / n, 3),
        "mrr": round(sum(1 / r["rank"] for r in matchable if r["rank"]) / n, 3),
        "mean_top_sim_matchable": round(statistics.mean(r["top_similarity"] for r in matchable), 3),
        "mean_top_sim_no_match": (
            round(statistics.mean(r["top_similarity"] for r in no_match), 3) if no_match else None
        ),
        "embed_ms_p50": pct(embed_ms, 50), "embed_ms_p95": pct(embed_ms, 95),
        "search_ms_p50": pct(search_ms, 50), "search_ms_p95": pct(search_ms, 95),
    }

    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "config": {"mode": "vector", "embed_model": settings.embed_model, "k": args.k},
        "dataset": {"indexed": store.count(), "queries": len(queries), "matchable": n},
        "metrics": metrics,
        "per_query": per_query,
    }
    Path("results").mkdir(exist_ok=True)
    path = Path("results") / f"retrieval_{date.today()}.json"
    path.write_text(json.dumps(out, indent=2))

    print(f"\n{'query':<42} {'rank':>4} {'sim':>6}")
    for r in per_query:
        rank = "-" if r["rank"] is None else r["rank"]
        flag = "" if r["expected"] is None or r["rank"] == 1 else "  <-- top1: " + str(r["top1"])
        print(f"{r['name'][:42]:<42} {rank:>4} {r['top_similarity']:>6}{flag}")
    print(f"\nindexed={out['dataset']['indexed']}  queries={len(queries)} (matchable {n})")
    for k, v in metrics.items():
        print(f"  {k:<24} {v}")
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
