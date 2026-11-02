"""Secret and PII redaction for network events and AI context."""

from __future__ import annotations

import re
from typing import Any

# Header names that carry credentials.
_SENSITIVE_HEADERS = {
    "authorization",
    "x-api-key",
    "x-auth-token",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-csrf-token",
    "x-forwarded-for",
    "x-real-ip",
}

# Query-string parameter names that often carry secrets.
_SENSITIVE_PARAMS = {
    "api_key", "apikey", "api-key", "token", "access_token",
    "refresh_token", "secret", "password", "passwd", "pwd",
    "client_secret", "auth", "key", "session", "sig", "signature",
}

# Regex patterns for inline secrets.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token",  re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I)),
    ("basic_auth",    re.compile(r"Basic\s+[A-Za-z0-9+/]+=*", re.I)),
    ("openai_key",    re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-]{20,}")),
    ("aws_key",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("generic_secret",re.compile(r"(?i)(secret|password|passwd|token)\s*[:=]\s*\S+")),
]

_REDACTED = "[REDACTED]"


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with sensitive values replaced."""
    return {
        k: (_REDACTED if k.lower() in _SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


def redact_url(url: str) -> str:
    """Strip sensitive query parameters from a URL."""
    from urllib.parse import parse_qs, urlencode, urlparse

    parsed = urlparse(url)
    if not parsed.query:
        return url

    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {
        k: ([_REDACTED] if k.lower() in _SENSITIVE_PARAMS else v)
        for k, v in qs.items()
    }
    return parsed._replace(query=urlencode(cleaned, doseq=True)).geturl()


def redact_text(text: str) -> str:
    """Replace inline secrets in arbitrary text."""
    for _name, pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def redact_dict(data: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys from a dict/list structure."""
    if depth > 10:
        return data
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_PARAMS | _SENSITIVE_HEADERS:
                result[k] = _REDACTED
            else:
                result[k] = redact_dict(v, depth + 1)
        return result
    if isinstance(data, list):
        return [redact_dict(item, depth + 1) for item in data]
    if isinstance(data, str):
        return redact_text(data)
    return data


def safe_truncate(text: str, max_chars: int = 500) -> str:
    """Truncate text for inclusion in AI context."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated, {len(text) - max_chars} chars omitted]"
