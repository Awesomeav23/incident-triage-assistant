"""One real Claude call. Skipped unless ANTHROPIC_API_KEY is set.

    ANTHROPIC_API_KEY=... pytest -m live -s
"""

import os

import pytest

from app.llm import ClaudeTriager
from app.models import LLMTriage
from tests.conftest import make_incident

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


def test_real_triage_returns_valid_structured_output():
    log = ("redis.exceptions.ConnectionError: Error 111 connecting to 10.0.7.12:6379. "
           "Connection refused.\n  File \"/app/limiter/bucket.py\", line 52, in take")
    past = make_incident(1, similarity=0.89)
    result = ClaudeTriager().triage(log, [past])
    assert isinstance(result, LLMTriage)
    assert 1 in result.matched_incident_ids
    assert result.severity.value in ("critical", "high")
    assert 0 <= result.confidence <= 1 and 1 <= len(result.next_steps) <= 5
    print("\n", result.model_dump_json(indent=2))
