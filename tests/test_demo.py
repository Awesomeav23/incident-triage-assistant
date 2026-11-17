"""Demo mode: free, no API calls, clearly labelled."""

import pytest

from app.llm import ClaudeTriager, DemoTriager, make_triager
from app.models import Severity
from tests.conftest import make_incident


def test_demo_reuses_closest_incident():
    top = make_incident(7, similarity=0.88)
    r = DemoTriager().triage("some log", [top, make_incident(8, 0.80)])
    assert r.suggested_fix == top.fix and r.severity == top.severity
    assert r.matched_incident_ids == [7] and r.confidence == 0.88
    assert "88% similar" in r.root_cause_summary and "demo mode" in r.severity_reason
    # the past incident's service is history, not where the new log came from
    assert any("fix here (last time it was fixed in session-service)" in s for s in r.next_steps)


def test_demo_without_matches_says_so():
    r = DemoTriager().triage("some log", [])
    assert r.matched_incident_ids == [] and r.confidence == 0.1
    assert r.severity == Severity.medium and "LLM_MODE=claude" in r.suggested_fix


@pytest.mark.parametrize("mode, expected", [
    ("demo", DemoTriager), ("", DemoTriager), ("Claud", DemoTriager),  # typo stays free
    ("openai", DemoTriager), ("claude", ClaudeTriager), (" CLAUDE ", ClaudeTriager),
])
def test_only_explicit_claude_mode_makes_paid_calls(mode, expected):
    assert type(make_triager(mode)) is expected


def test_default_mode_is_demo(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    from app.config import Settings
    assert Settings().llm_mode == "demo"


def test_triage_in_demo_mode_reports_demo_model(fakes):
    from app.llm import get_triager
    from app.main import app
    client, *_ = fakes
    app.dependency_overrides[get_triager] = lambda: DemoTriager()
    body = client.post("/triage", json={"log": "redis.exceptions.ConnectionError: refused"}).json()
    assert body["model"] == "demo" and body["matched_incident_ids"] == [1]


def test_health_reports_mode(fakes):
    client, *_ = fakes
    assert client.get("/health").json()["llm_mode"] in ("demo", "claude")


def test_classes_exist():
    assert DemoTriager.model == "demo" and ClaudeTriager
