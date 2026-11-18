"""Triage quality eval: is the full answer (severity, citations, root cause, fix) right?

    python -m scripts.eval_triage                        # FREE: demo triager + keyword judge
    python -m scripts.eval_triage --estimate-only --triager claude --judge claude
    python -m scripts.eval_triage --triager claude --judge claude --yes   # PAID, after approval
    python -m scripts.eval_triage --triager claude --judge keyword --limit 5 --yes

Every query goes through the real /triage route (redaction, hybrid search, threshold,
citation guard) against the seeded database. Only the triager and the judge change.

Spending guard: any paid component prints a cost estimate first and refuses to run
without --yes. With --estimate-only it stops after the estimate. Retrieval is free.

Writes results/triage_eval_<triager-model>_<date>.json. See docs/EVAL_PLAN.md §3.
"""

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field

from app.config import settings
from app.models import SEVERITY_SCORE, Severity

# USD per million tokens (input, output), list prices from the Claude API docs.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts ≤ 100K tokens
    "claude-fable-5-2": (8.0, 40.0),
}
# Output-token assumptions for the estimate (structured JSON at effort low), with margin.
EST_TRIAGE_OUT = 1000
EST_JUDGE_OUT = 400
EST_MARGIN = 1.3


# ---------- data ----------

@dataclass
class Case:
    name: str
    log: str
    family: str | None            # None = no real match exists
    severity: Severity | None
    facts: list[dict] = field(default_factory=list)


def load_cases(queries_path: str, truth_path: str) -> list[Case]:
    queries = json.load(open(queries_path))
    truth = json.load(open(truth_path))["families"]
    cases = []
    for q in queries:
        fam = q["expected_family"]
        if fam is not None and fam not in truth:
            raise ValueError(f"no ground truth for family {fam!r} ({q['name']})")
        t = truth.get(fam, {})
        cases.append(Case(
            name=q["name"], log=q["log"], family=fam,
            severity=Severity(t["severity"]) if fam else None,
            facts=t.get("key_facts", []),
        ))
    return cases


# ---------- judges ----------

class Verdict(BaseModel):
    fact_scores: list[int]
    actionable: bool
    rationale: str = ""
    usage: dict[str, int] | None = None


class Judge(Protocol):
    name: str
    model: str | None

    def judge(self, case: Case, resp: dict) -> Verdict: ...


_ACTION_WORDS = ("set ", "add ", "increase", "raise", "lower", "pin ", "run ", "grant", "rotate",
                 "renew", "replace", "remove", "wrap", "enable", "disable", "configure", "upgrade",
                 "downgrade", "install", "commit", "create", "delete", "restart", "change", "use ")


class KeywordJudge:
    """Free proxy judge: a fact counts (2) if any of its keywords appears in the root cause.

    Coarse (0 or 2 only, no paraphrase understanding), but it costs nothing and is
    deterministic, so it's good for the demo baseline and for spotting regressions.
    """

    name = "keyword"
    model = None

    def judge(self, case: Case, resp: dict) -> Verdict:
        text = resp["root_cause_summary"].lower()
        scores = [2 if any(k in text for k in f["keywords"]) else 0 for f in case.facts]
        fix = resp["suggested_fix"].lower()
        actionable = "`" in fix or "=" in fix or any(w in fix for w in _ACTION_WORDS)
        return Verdict(fact_scores=scores, actionable=actionable, rationale="keyword match")


JUDGE_SYSTEM = """\
You grade answers from an incident-triage assistant against an answer key.

For each numbered expected fact, score the ROOT CAUSE text:
  0 = missing or contradicted
  1 = vague or only partly stated
  2 = clearly stated (paraphrase is fine)
Return exactly one score per expected fact, in order.

actionable = true only if the SUGGESTED FIX names a concrete change: a config key or value,
a code change, or a command. Advice like "investigate" or "check the logs" is not actionable.

rationale: one or two sentences. Judge only what is written; don't reward extra detail."""


class JudgeOutput(BaseModel):
    fact_scores: list[Literal[0, 1, 2]] = Field(..., description="One score per expected fact, in order")
    actionable: bool
    rationale: str


def build_judge_message(case: Case, resp: dict) -> str:
    facts = "\n".join(f"{i + 1}. {f['fact']}" for i, f in enumerate(case.facts))
    return (
        f"<expected_facts>\n{facts}\n</expected_facts>\n\n"
        f"<root_cause>\n{resp['root_cause_summary']}\n</root_cause>\n\n"
        f"<suggested_fix>\n{resp['suggested_fix']}\n</suggested_fix>"
    )


class ClaudeJudge:
    name = "claude"

    def __init__(self, model: str):
        self.model = model
        self._client = None

    def judge(self, case: Case, resp: dict) -> Verdict:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(timeout=60.0, max_retries=2)
        r = self._client.beta.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": build_judge_message(case, resp)}],
            output_config={"effort": "low"},
            output_format=JudgeOutput,
        )
        usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
        out = r.parsed_output
        if out is None:
            return Verdict(fact_scores=[0] * len(case.facts), actionable=False,
                           rationale=f"judge returned nothing (stop_reason={r.stop_reason})", usage=usage)
        scores = list(out.fact_scores)[: len(case.facts)]
        scores += [0] * (len(case.facts) - len(scores))  # wrong count → missing facts score 0
        return Verdict(fact_scores=scores, actionable=out.actionable, rationale=out.rationale, usage=usage)


# ---------- scoring ----------

def score_case(case: Case, resp: dict, verdict: Verdict | None) -> dict:
    """Per-query record. `resp` is a /triage response body (TriageResponse as JSON)."""
    shown = {s["id"]: s.get("family") for s in resp["similar_incidents"]}
    cited = list(resp["matched_incident_ids"])
    cited_fams = [shown.get(i) for i in cited]
    row = {
        "name": case.name,
        "expected_family": case.family,
        "predicted_severity": resp["severity"],
        "confidence": resp["confidence"],
        "cited_families": cited_fams,
        "shown_families": list(shown.values()),
        "top1_family": resp["similar_incidents"][0]["family"] if resp["similar_incidents"] else None,
        "latency_ms": resp["latency_ms"],
    }
    if case.family is None:
        row["honest_no_match"] = not cited and resp["confidence"] < 0.5
        return row

    exp, pred = SEVERITY_SCORE[case.severity], SEVERITY_SCORE[Severity(resp["severity"])]
    row.update({
        "expected_severity": case.severity.value,
        "severity_exact": exp == pred,
        "severity_within_1": abs(exp - pred) <= 1,
        "retrieved": case.family in shown.values(),
        "cited_correct": case.family in cited_fams,
        "cited_wrong": sum(f != case.family for f in cited_fams),
        "n_cited": len(cited),
    })
    if verdict is not None:
        max_pts = 2 * len(verdict.fact_scores) or 1
        row.update({
            "fact_scores": verdict.fact_scores,
            "root_cause_score": round(sum(verdict.fact_scores) / max_pts, 3),
            "actionable": verdict.actionable,
            "judge_rationale": verdict.rationale,
        })
    return row


def _mean(xs) -> float | None:
    xs = list(xs)
    return round(statistics.fmean(xs), 3) if xs else None


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)


def aggregate(rows: list[dict]) -> dict:
    m = [r for r in rows if r["expected_family"]]
    nm = [r for r in rows if not r["expected_family"]]
    retrieved = [r for r in m if r["retrieved"]]
    n_cited = sum(r["n_cited"] for r in m)
    judged = [r for r in m if "root_cause_score" in r]
    # "Correct" for calibration: cited the right family and got the root cause mostly right.
    correct = [r for r in judged if r["cited_correct"] and r["root_cause_score"] >= 0.5]
    wrong = [r for r in judged if r not in correct]
    llm = [r["latency_ms"]["llm"] for r in rows]
    total = [r["latency_ms"]["total"] for r in rows]
    return {
        "severity_exact": _mean(r["severity_exact"] for r in m),
        "severity_within_1": _mean(r["severity_within_1"] for r in m),
        "citation_recall": _mean(r["cited_correct"] for r in m),
        # Retrieval sets the ceiling: Claude can only cite what it was shown.
        "retrieval_ceiling": _mean(r["retrieved"] for r in m),
        "citation_recall_when_shown": _mean(r["cited_correct"] for r in retrieved),
        "citation_precision": round(sum(r["cited_correct"] for r in m) / n_cited, 3) if n_cited else None,
        "no_match_honesty": _mean(r["honest_no_match"] for r in nm),
        "root_cause_score": _mean(r["root_cause_score"] for r in judged),
        "fix_actionable": _mean(r["actionable"] for r in judged),
        "confidence_when_correct": _mean(r["confidence"] for r in correct),
        "confidence_when_wrong": _mean(r["confidence"] for r in wrong),
        "llm_ms_p50": _pct(llm, 50), "llm_ms_p95": _pct(llm, 95),
        "total_ms_p50": _pct(total, 50), "total_ms_p95": _pct(total, 95),
    }


# ---------- cost ----------

def cost_usd(model: str | None, input_tokens: float, output_tokens: float) -> float | None:
    if model is None:
        return 0.0
    if model not in PRICES:
        return None
    pin, pout = PRICES[model]
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def estimate_cost(triage_inputs: list[str], judge_inputs: list[str],
                  triager_model: str | None, judge_model: str | None) -> dict:
    """chars/4 for input tokens, fixed output assumptions, ×EST_MARGIN. None = unknown price."""
    t_in = sum(len(s) for s in triage_inputs) / 4
    j_in = sum(len(s) for s in judge_inputs) / 4
    t = cost_usd(triager_model, t_in, EST_TRIAGE_OUT * len(triage_inputs))
    j = cost_usd(judge_model, j_in, EST_JUDGE_OUT * len(judge_inputs))
    total = None if t is None or j is None else round((t + j) * EST_MARGIN, 2)
    return {"triage_calls": len(triage_inputs) if triager_model else 0,
            "judge_calls": len(judge_inputs) if judge_model else 0,
            "est_input_tokens": int(t_in * bool(triager_model) + j_in * bool(judge_model)),
            "est_usd": total}


def check_spend(est: dict, paid: bool, yes: bool, estimate_only: bool) -> None:
    """Exit before any paid call unless the caller explicitly confirmed with --yes."""
    if not paid:
        return
    usd = "unknown (model not in price table)" if est["est_usd"] is None else f"~${est['est_usd']:.2f}"
    print(f"Estimated cost: {usd}  ({est['triage_calls']} triage + {est['judge_calls']} judge calls)")
    if estimate_only:
        raise SystemExit(0)
    if not yes:
        print("This run makes paid Claude API calls. Re-run with --yes to spend.")
        raise SystemExit(2)
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.")
        raise SystemExit(2)


# ---------- run ----------

def run_cases(cases: list[Case], call_triage: Callable[[str], dict], judge: Judge | None,
              progress: bool = False) -> list[dict]:
    rows = []
    for i, c in enumerate(cases, 1):
        resp = call_triage(c.log)
        verdict = judge.judge(c, resp) if (judge and c.family) else None
        row = score_case(c, resp, verdict)
        row["triage_usage"] = resp.get("_usage")
        row["judge_usage"] = verdict.usage if verdict else None
        rows.append(row)
        if progress:
            mark = "✓" if row.get("cited_correct") or row.get("honest_no_match") else "✗"
            print(f"  [{i:>2}/{len(cases)}] {mark} {c.name[:60]}", file=sys.stderr)
    return rows


def actual_cost(rows: list[dict], triager_model: str | None, judge_model: str | None) -> dict:
    def tot(key, part):
        return sum((r[key] or {}).get(part, 0) for r in rows)
    t = cost_usd(triager_model, tot("triage_usage", "input_tokens"), tot("triage_usage", "output_tokens"))
    j = cost_usd(judge_model, tot("judge_usage", "input_tokens"), tot("judge_usage", "output_tokens"))
    return {
        "triage_input_tokens": tot("triage_usage", "input_tokens"),
        "triage_output_tokens": tot("triage_usage", "output_tokens"),
        "judge_input_tokens": tot("judge_usage", "input_tokens"),
        "judge_output_tokens": tot("judge_usage", "output_tokens"),
        "usd": None if t is None or j is None else round(t + j, 4),
    }


def print_table(metrics: dict, cost: dict, n: int) -> None:
    labels = [
        ("severity_exact", "Severity exact", "≥ 0.70"), ("severity_within_1", "Severity ±1", "≥ 0.95"),
        ("citation_recall", "Citation recall", "≥ 0.85"), ("retrieval_ceiling", "  retrieval ceiling", ""),
        ("citation_recall_when_shown", "  recall when shown", ""), ("citation_precision", "Citation precision", ""),
        ("no_match_honesty", "No-match honesty", ""), ("root_cause_score", "Root-cause score", "≥ 0.80"),
        ("fix_actionable", "Fix actionable", ""), ("confidence_when_correct", "Confidence | correct", ""),
        ("confidence_when_wrong", "Confidence | wrong", ""),
        ("llm_ms_p50", "LLM ms p50", ""), ("llm_ms_p95", "LLM ms p95", ""),
        ("total_ms_p50", "Total ms p50", "< 2000"),
    ]
    print(f"\n{'metric':<24}{'value':>10}  target   (n={n})")
    for k, label, target in labels:
        v = metrics[k]
        print(f"{label:<24}{'—' if v is None else v:>10}  {target}")
    usd = "unknown" if cost["usd"] is None else f"${cost['usd']:.4f}"
    print(f"\nactual cost: {usd}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--queries", default="data/eval_set.json")
    p.add_argument("--truth", default="data/triage_truth.json")
    p.add_argument("--triager", choices=["demo", "claude"], default="demo")
    p.add_argument("--judge", choices=["keyword", "claude", "none"], default="keyword")
    p.add_argument("--model", default=settings.claude_model, help="triage model (claude triager)")
    p.add_argument("--effort", default=settings.claude_effort)
    p.add_argument("--judge-model", default=settings.claude_model)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--limit", type=int, help="only the first N queries (cheap smoke run)")
    p.add_argument("--yes", action="store_true", help="confirm paid Claude calls")
    p.add_argument("--estimate-only", action="store_true")
    p.add_argument("--tag", default="", help="suffix for the results file, e.g. 'tuned'")
    args = p.parse_args()

    # Heavy imports here so the scoring functions above import without a DB or model.
    from fastapi.testclient import TestClient

    from app.llm import SYSTEM_PROMPT, ClaudeTriager, DemoTriager, build_user_message, get_triager
    from app.main import app, get_store
    from app.rag import get_embedder
    from app.redact import redact

    cases = load_cases(args.queries, args.truth)[: args.limit]
    triager = ClaudeTriager(model=args.model, effort=args.effort) if args.triager == "claude" else DemoTriager()
    judge: Judge | None = {"keyword": KeywordJudge(), "none": None,
                           "claude": ClaudeJudge(args.judge_model)}[args.judge]
    triager_model = args.model if args.triager == "claude" else None
    judge_model = args.judge_model if args.judge == "claude" else None
    paid = bool(triager_model or judge_model)

    # Free pre-flight: run retrieval exactly as /triage does, to size the prompts.
    store, embedder = get_store(), get_embedder()
    if store.count() == 0:
        sys.exit("The index is empty. Seed it first: python -m scripts.seed")
    if paid:
        t_inputs, j_inputs = [], []
        for c in cases:
            log, _ = redact(c.log)
            sims = store.search(embedder.embed(log), args.top_k, mode=settings.search_mode,
                                query_text=log, min_similarity=settings.min_similarity,
                                distinct_families=True)
            t_inputs.append(SYSTEM_PROMPT + build_user_message(log, sims))
            if c.family:
                # Judge sees the triage answer; assume ~600 + 800 chars for it.
                fake = {"root_cause_summary": "x" * 600, "suggested_fix": "x" * 800}
                j_inputs.append(JUDGE_SYSTEM + build_judge_message(c, fake))
        est = estimate_cost(t_inputs, j_inputs, triager_model, judge_model)
        check_spend(est, paid, args.yes, args.estimate_only)
    elif args.estimate_only:
        print("Estimated cost: $0.00 (demo triager, no paid judge)")
        return

    app.dependency_overrides[get_triager] = lambda: triager
    try:
        with TestClient(app) as client:
            def call_triage(log: str) -> dict:
                r = client.post("/triage", json={"log": log, "top_k": args.top_k})
                r.raise_for_status()
                body = r.json()
                body["_usage"] = getattr(triager, "last_usage", None)
                return body

            rows = run_cases(cases, call_triage, judge, progress=True)
    finally:
        app.dependency_overrides.clear()

    metrics = aggregate(rows)
    cost = actual_cost(rows, triager_model, judge_model)
    from scripts.eval_retrieval import git_sha

    model_tag = triager_model or "demo"
    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "config": {"triager": args.triager, "model": triager_model, "effort": args.effort if triager_model else None,
                   "judge": args.judge, "judge_model": judge_model, "top_k": args.top_k,
                   "search_mode": settings.search_mode, "min_similarity": settings.min_similarity},
        "dataset": {"indexed": store.count(), "queries": len(cases),
                    "matchable": sum(1 for c in cases if c.family),
                    "no_match": sum(1 for c in cases if not c.family)},
        "metrics": metrics,
        "cost": cost,
        "per_query": rows,
    }
    Path("results").mkdir(exist_ok=True)
    suffix = f"_{args.tag}" if args.tag else ""
    path = Path("results") / f"triage_eval_{model_tag}_{args.judge}{suffix}_{date.today()}.json"
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")

    print_table(metrics, cost, len(cases))
    misses = [r for r in rows if r["expected_family"] and not r["cited_correct"]]
    if misses:
        print("\nmissed citations:")
        for r in misses:
            print(f"  {r['name'][:48]:<48} expected={r['expected_family']}  cited={r['cited_families']}")
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
