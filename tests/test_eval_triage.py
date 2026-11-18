"""Triage eval: answer key, scoring, judge, cost guard. No DB, no model, no API key."""

import json

import pytest

from app.models import Severity
from scripts.eval_triage import (
    Case,
    KeywordJudge,
    PRICES,
    Verdict,
    aggregate,
    build_judge_message,
    check_spend,
    estimate_cost,
    load_cases,
    run_cases,
    score_case,
)

TRUTH = json.load(open("data/triage_truth.json"))["families"]


def resp(cited=(1,), severity="critical", confidence=0.9, shown=(("redis-failover-refused", 1),),
         root="Failover promoted a new primary but the client pinned the old node IP.",
         fix="Point REDIS_HOST at the primary `endpoint`."):
    return {
        "root_cause_summary": root, "severity": severity, "suggested_fix": fix,
        "matched_incident_ids": list(cited), "confidence": confidence,
        "similar_incidents": [{"id": i, "family": f} for f, i in shown],
        "latency_ms": {"embed": 5, "search": 10, "llm": 100, "total": 115},
    }


REDIS = Case(name="redis", log="...", family="redis-failover-refused", severity=Severity.critical,
             facts=TRUTH["redis-failover-refused"]["key_facts"])
NOVEL = Case(name="novel", log="...", family=None, severity=None)


# ---------- answer key ----------

def test_every_matchable_eval_query_has_ground_truth():
    cases = load_cases("data/eval_set.json", "data/triage_truth.json")
    assert len(cases) == 53
    assert sum(c.family is None for c in cases) == 6
    assert all(c.facts and c.severity for c in cases if c.family)


def test_truth_severity_matches_the_indexed_base_incident():
    base = {i["family"]: i["severity"] for i in json.load(open("data/incidents_expanded.json"))
            if not i["synthetic"]}
    assert set(TRUTH) == set(base)
    assert all(TRUTH[f]["severity"] == base[f] for f in base)


def test_facts_are_well_formed():
    for fam, t in TRUTH.items():
        assert 2 <= len(t["key_facts"]) <= 4, fam
        for f in t["key_facts"]:
            assert f["fact"] and f["keywords"], fam
            assert all(k == k.lower() for k in f["keywords"]), fam


def test_keyword_judge_credits_each_base_root_cause():
    """Sanity check on the keywords: the real diagnosis of each family scores well."""
    base = {i["family"]: i for i in json.load(open("data/incidents_expanded.json")) if not i["synthetic"]}
    judge = KeywordJudge()
    for fam, inc in base.items():
        case = Case(name=fam, log="", family=fam, severity=Severity(inc["severity"]),
                    facts=TRUTH[fam]["key_facts"])
        v = judge.judge(case, {"root_cause_summary": inc["root_cause"], "suggested_fix": inc["fix"]})
        assert sum(v.fact_scores) >= len(v.fact_scores), (fam, v.fact_scores)  # ≥ half the points


def test_unknown_family_in_queries_is_an_error(tmp_path):
    q = tmp_path / "q.json"
    q.write_text(json.dumps([{"name": "x", "log": "x", "expected_family": "nope"}]))
    with pytest.raises(ValueError, match="nope"):
        load_cases(str(q), "data/triage_truth.json")


# ---------- judge ----------

def test_keyword_judge_scores_present_and_missing_facts():
    v = KeywordJudge().judge(REDIS, resp())
    assert v.fact_scores == [2, 2] and v.actionable
    v = KeywordJudge().judge(REDIS, resp(root="Something went wrong with the cache.", fix="Investigate."))
    assert v.fact_scores == [0, 0] and not v.actionable


def test_judge_message_numbers_facts_and_includes_answer():
    msg = build_judge_message(REDIS, resp())
    assert "1. " in msg and "2. " in msg
    assert "pinned the old node IP" in msg and "REDIS_HOST" in msg


# ---------- scoring ----------

def test_score_correct_answer():
    row = score_case(REDIS, resp(), Verdict(fact_scores=[2, 1], actionable=True))
    assert row["severity_exact"] and row["severity_within_1"]
    assert row["retrieved"] and row["cited_correct"] and row["cited_wrong"] == 0
    assert row["root_cause_score"] == 0.75


def test_score_wrong_citation_and_severity():
    r = resp(cited=(7,), severity="medium", shown=(("redis-failover-refused", 1), ("docker-no-space", 7)))
    row = score_case(REDIS, r, None)
    assert not row["severity_exact"] and not row["severity_within_1"]  # critical(5) vs medium(3)
    assert row["retrieved"] and not row["cited_correct"] and row["cited_wrong"] == 1
    assert "root_cause_score" not in row


def test_score_no_match_honesty():
    assert score_case(NOVEL, resp(cited=(), confidence=0.3, shown=()), None)["honest_no_match"]
    assert not score_case(NOVEL, resp(cited=(), confidence=0.8, shown=()), None)["honest_no_match"]
    assert not score_case(NOVEL, resp(confidence=0.3), None)["honest_no_match"]  # cited something


def test_aggregate():
    rows = [
        score_case(REDIS, resp(), Verdict(fact_scores=[2, 2], actionable=True)),
        score_case(REDIS, resp(cited=(), severity="high", confidence=0.4),
                   Verdict(fact_scores=[0, 0], actionable=False)),
        score_case(NOVEL, resp(cited=(), confidence=0.2, shown=()), None),
    ]
    m = aggregate(rows)
    assert m["severity_exact"] == 0.5 and m["severity_within_1"] == 1.0
    assert m["citation_recall"] == 0.5 and m["retrieval_ceiling"] == 1.0
    assert m["citation_precision"] == 1.0
    assert m["no_match_honesty"] == 1.0
    assert m["root_cause_score"] == 0.5 and m["fix_actionable"] == 0.5
    assert m["confidence_when_correct"] == 0.9 and m["confidence_when_wrong"] == 0.4


def test_run_cases_with_fake_triage_and_judge():
    calls = []

    def fake_triage(log):
        calls.append(log)
        return resp()

    rows = run_cases([REDIS, NOVEL], fake_triage, KeywordJudge())
    assert len(calls) == 2
    assert rows[0]["root_cause_score"] == 1.0
    assert "root_cause_score" not in rows[1]  # no-match queries aren't judged


# ---------- cost guard ----------

def test_estimate_free_when_nothing_paid():
    assert estimate_cost(["x" * 4000], ["y" * 4000], None, None)["est_usd"] == 0.0


def test_estimate_scales_with_price():
    opus = estimate_cost(["x" * 8000] * 10, ["y" * 4000] * 10, "claude-opus-5-5", "claude-opus-5-5")
    haiku = estimate_cost(["x" * 8000] * 10, ["y" * 4000] * 10, "claude-haiku-5-5", "claude-haiku-5-5")
    assert opus["est_usd"] > haiku["est_usd"] > 0
    assert opus["triage_calls"] == opus["judge_calls"] == 10


def test_estimate_unknown_model_is_none():
    assert estimate_cost(["x"], [], "claude-made-up", None)["est_usd"] is None


def test_prices_cover_the_configured_model():
    from app.config import settings
    assert settings.claude_model in PRICES


def test_paid_run_refused_without_yes(capsys):
    est = {"est_usd": 1.5, "triage_calls": 53, "judge_calls": 47}
    with pytest.raises(SystemExit) as e:
        check_spend(est, paid=True, yes=False, estimate_only=False)
    assert e.value.code == 2
    assert "--yes" in capsys.readouterr().out


def test_estimate_only_stops_before_spending():
    with pytest.raises(SystemExit) as e:
        check_spend({"est_usd": 1.5, "triage_calls": 1, "judge_calls": 1}, True, True, True)
    assert e.value.code == 0


def test_paid_run_needs_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        check_spend({"est_usd": 1.5, "triage_calls": 1, "judge_calls": 1}, True, True, False)


def test_free_run_passes_guard():
    check_spend({"est_usd": 0.0, "triage_calls": 0, "judge_calls": 0}, paid=False, yes=False,
                estimate_only=False)
