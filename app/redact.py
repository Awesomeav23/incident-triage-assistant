"""Best-effort secret redaction for logs before they are stored or sent to the LLM.

CI logs and env dumps routinely contain tokens. These patterns catch the common
shapes; they are not a guarantee (see docs/DECISIONS.md ADR-007).
"""

import re

MASK = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # PEM private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    # credentials inside URLs: scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@/\s]+(@)", re.I), rf"\g<1>{MASK}\g<2>"),
    # Authorization: Bearer <token> / Basic <b64>
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9\-._~+/]{8,}=*", re.I), rf"\g<1> {MASK}"),
    # JSON Web Tokens
    (re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}"), MASK),
    # vendor token formats
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),  # AWS access key id
    (re.compile(r"\bsk-ant-[\w-]{10,}"), MASK),  # Anthropic
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), MASK),  # GitHub
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),  # Slack
    (re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{10,}"), MASK),  # Stripe
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), MASK),  # generic sk- keys
    # key=value / key: value where the key names a secret
    (
        re.compile(
            r"""(\b[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|"""
            r"""private[_-]?key|client[_-]?secret|auth[_-]?token|access[_-]?token|token)\b"""
            r"""["']?\s*[=:]\s*["']?)(?!\[REDACTED\])([^\s"',;&]{4,})""",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
]


def redact(text: str) -> tuple[str, int]:
    """Return (redacted text, number of secrets masked)."""
    total = 0
    for pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total
