"""Retrieval eval: is the right failure family ranked first / in the top 3?

    python -m scripts.eval_retrieval                 # all modes + threshold tuning
    python -m scripts.eval_retrieval --mode hybrid   # one mode, per-query table

A hit means an incident from the expected *family* (any recurrence) is ranked at that
position. Writes results/retrieval_<date>.json. See docs/EVAL_PLAN.md §2.
"""

import argparse
import json
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import settings
from app.rag import SEARCH_MODES, IncidentStore, get_embedder


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 2)


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def run_mode(store, embedder, queries, mode: str, k: int) -> dict:
    per_query, embed_ms, search_ms = [], [], []
    for q in queries:
        t0 = time.perf_counter()
        vec = embedder.embed(q["log"])
        t1 = time.perf_counter()
        results = store.search(vec, k, mode=mode, query_text=q["log"])
        t2 = time.perf_counter()
        embed_ms.append((t1 - t0) * 1000)
        search_ms.append((t2 - t1) * 1000)

        fams = [r.family for r in results]
        exp = q["expected_family"]
        rank = fams.index(exp) + 1 if exp in fams else None
        per_query.append({
            "name": q["name"], "expected": exp, "rank": rank,
            "top1_family": fams[0] if fams else None,
            # best cosine similarity anywhere in the index = vector top-1
            "max_similarity": round(store.search(vec, 1)[0].similarity, 3),
        })

    matchable = [r for r in per_query if r["expected"]]
    n = len(matchable)
    return {
        "metrics": {
            "hit@1": round(sum(r["rank"] == 1 for r in matchable) / n, 3),
            "hit@3": round(sum(bool(r["rank"]) and r["rank"] <= 3 for r in matchable) / n, 3),
            "mrr": round(sum(1 / r["rank"] for r in matchable if r["rank"]) / n, 3),
            "embed_ms_p50": pct(embed_ms, 50), "embed_ms_p95": pct(embed_ms, 95),
            "search_ms_p50": pct(search_ms, 50), "search_ms_p95": pct(search_ms, 95),
        },
        "per_query": per_query,
    }


def tune_threshold(per_query: list[dict]) -> dict:
    """Highest cutoff (0.01 steps) that still keeps every correct top-1 match.

    Dropping a correct match costs more than passing an irrelevant one: Claude is told to
    ignore past incidents that don't genuinely match, so it is a second line of defence.
    Tuned on the eval set itself, so treat it as a starting point, not a final value.
    """
    correct = [r["max_similarity"] for r in per_query if r["expected"] and r["rank"] == 1]
    no_match = [r["max_similarity"] for r in per_query if not r["expected"]]
    t = int(min(correct) * 100) / 100
    return {
        "threshold": t,
        "rule": "highest cutoff keeping 100% of correct matches",
        "correct_matches_kept": round(sum(s >= t for s in correct) / len(correct), 3),
        "no_match_rejected": round(sum(s < t for s in no_match) / len(no_match), 3),
        "correct_sim_min": min(correct),
        "no_match_sim_max": max(no_match),
        "curve": [
            {"t": c / 100,
             "correct_kept": round(sum(s >= c / 100 for s in correct) / len(correct), 3),
             "no_match_rejected": round(sum(s < c / 100 for s in no_match) / len(no_match), 3)}
            for c in range(65, 81)
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queries", default="data/eval_set.json")
    p.add_argument("--mode", choices=[*SEARCH_MODES, "all"], default="all")
    p.add_argument("--k", type=int, default=10, help="depth searched for MRR")
    args = p.parse_args()

    queries = json.load(open(args.queries))
    store, embedder = IncidentStore(), get_embedder()
    store.search(embedder.embed("warm-up"), 1)  # warm model + pool

    modes = list(SEARCH_MODES) if args.mode == "all" else [args.mode]
    runs = {m: run_mode(store, embedder, queries, m, args.k) for m in modes}
    best_mode = max(modes, key=lambda m: (runs[m]["metrics"]["hit@1"], runs[m]["metrics"]["mrr"]))
    threshold = tune_threshold(runs[best_mode]["per_query"])

    n_match = sum(1 for q in queries if q["expected_family"])
    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "config": {"embed_model": settings.embed_model, "k": args.k},
        "dataset": {"indexed": store.count(), "queries": len(queries),
                    "matchable": n_match, "no_match": len(queries) - n_match},
        "modes": {m: r["metrics"] for m, r in runs.items()},
        "best_mode": best_mode,
        "threshold": threshold,
        "per_query": {m: r["per_query"] for m, r in runs.items()},
    }
    Path("results").mkdir(exist_ok=True)
    path = Path("results") / f"retrieval_{date.today()}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")

    d = out["dataset"]
    print(f"indexed={d['indexed']}  queries={d['queries']} (matchable {d['matchable']}, no-match {d['no_match']})\n")
    cols = ["hit@1", "hit@3", "mrr", "search_ms_p50", "search_ms_p95"]
    print(f"{'mode':<9}" + "".join(f"{c:>15}" for c in cols))
    for m, r in runs.items():
        print(f"{m:<9}" + "".join(f"{r['metrics'][c]:>15}" for c in cols))
    print(f"\nembed p50/p95 ms: {runs[modes[0]]['metrics']['embed_ms_p50']} / {runs[modes[0]]['metrics']['embed_ms_p95']}")

    print(f"\nmisses ({best_mode}, rank != 1):")
    for r in runs[best_mode]["per_query"]:
        if r["expected"] and r["rank"] != 1:
            print(f"  {r['name'][:44]:<44} rank={r['rank']}  top1={r['top1_family']}")
    t = threshold
    print(f"\nno-match threshold: {t['threshold']}  (keeps {t['correct_matches_kept']:.0%} of correct matches, "
          f"rejects {t['no_match_rejected']:.0%} of no-match logs)")
    print(f"  lowest correct-match similarity {t['correct_sim_min']}, highest no-match similarity {t['no_match_sim_max']}")
    print(f"\nbest mode: {best_mode}    saved {path}")


if __name__ == "__main__":
    main()
