"""Tests for secret redaction."""

import pytest
from rea.security.redaction import (
    redact_dict, redact_headers, redact_text, redact_url,
)


def test_redact_authorization_header():
    headers = {"Authorization": "Bearer sk-abc123", "Content-Type": "application/json"}
    result = redact_headers(headers)
    assert result["Authorization"] == "[REDACTED]"
    assert result["Content-Type"] == "application/json"


def test_redact_cookie_header():
    headers = {"Cookie": "session=abc123"}
    assert redact_headers(headers)["Cookie"] == "[REDACTED]"


def test_redact_api_key_in_url():
    url = "https://api.example.com/v1/data?api_key=secret123&page=2"
    result = redact_url(url)
    assert "secret123" not in result
    assert "page=2" in result


def test_redact_bearer_in_text():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
    result = redact_text(text)
    assert "eyJ" not in result
    assert "[REDACTED]" in result


def test_redact_openai_key_in_text():
    text = "Using key sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ"
    result = redact_text(text)
    assert "aBcDeFgHiJkLmNoPqRsTuVwXyZ" not in result


def test_redact_dict_nested():
    data = {
        "config": {"api_key": "supersecret", "timeout": 30},
        "name": "test",
    }
    result = redact_dict(data)
    assert result["config"]["api_key"] == "[REDACTED]"
    assert result["config"]["timeout"] == 30
    assert result["name"] == "test"


def test_redact_dict_list():
    data = [{"token": "abc"}, {"value": "safe"}]
    result = redact_dict(data)
    assert result[0]["token"] == "[REDACTED]"
    assert result[1]["value"] == "safe"


def test_redact_text_clean():
    text = "Hello world, no secrets here."
    assert redact_text(text) == text
