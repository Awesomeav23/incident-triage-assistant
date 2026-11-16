"""Secret redaction: catches common secret shapes, leaves ordinary log text alone."""

import json

import pytest

from app.redact import MASK, redact


@pytest.mark.parametrize("text, secret", [
    ("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("Authorization: Bearer abc.def-ghi_jklmnop", "abc.def-ghi_jklmnop"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGciOiJIUzI1NiJ9"),
    ("DATABASE_URL=postgres://app:hunter2pass@db:5432/x", "hunter2pass"),
    ("api_key=sk-ant-abc123def456ghi789", "sk-ant-abc123def456ghi789"),
    ("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("slack xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij"),
    ("STRIPE_SECRET_KEY=sk_live_abcdefghijklmnop", "sk_live_abcdefghijklmnop"),
    ('{"password": "s3cret!x"}', "s3cret!x"),
    ("client_secret: 9f8e7d6c5b4a", "9f8e7d6c5b4a"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEpAIBAAKCAQEA"),
])
def test_secret_is_masked(text, secret):
    out, n = redact(text)
    assert secret not in out and MASK in out and n >= 1


def test_each_secret_counted_once():
    out, n = redact("api_key=sk-ant-abc123def456ghi789")
    assert out == f"api_key={MASK}" and n == 1


@pytest.mark.parametrize("text", [
    "The security token included in the request is expired",
    "QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00",
    "Error: Error acquiring the state lock",
    "panic: assignment to entry in nil map",
])
def test_ordinary_text_untouched(text):
    assert redact(text) == (text, 0)


def test_no_false_positives_across_the_whole_dataset():
    """None of the 235 fictional incident logs contain secrets, so nothing should be masked."""
    rows = json.load(open("data/incidents_expanded.json"))
    hits = [(r["title"], redact(r["log_text"])[0]) for r in rows if redact(r["log_text"])[1]]
    assert hits == []
