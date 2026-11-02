"""Tests for URL validation and network restriction."""

import pytest
from rea.security.url_validation import (
    URLValidationError, is_same_origin, normalize_url, validate_url,
)


def test_valid_https():
    assert validate_url("https://example.com", resolve_dns=False) == "https://example.com"


def test_valid_http():
    assert validate_url("http://example.com/path?q=1", resolve_dns=False)


def test_rejects_file_scheme():
    with pytest.raises(URLValidationError, match="Scheme"):
        validate_url("file:///etc/passwd")


def test_rejects_javascript_scheme():
    with pytest.raises(URLValidationError):
        validate_url("javascript:alert(1)")


def test_rejects_localhost():
    with pytest.raises(URLValidationError, match="private"):
        validate_url("http://127.0.0.1/admin", resolve_dns=False)


def test_rejects_private_ip_10():
    with pytest.raises(URLValidationError, match="private"):
        validate_url("http://10.0.0.1/", resolve_dns=False)


def test_rejects_private_ip_192():
    with pytest.raises(URLValidationError, match="private"):
        validate_url("http://192.168.1.1/", resolve_dns=False)


def test_rejects_link_local():
    with pytest.raises(URLValidationError, match="private"):
        validate_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)


def test_rejects_metadata_hostname():
    with pytest.raises(URLValidationError, match="cloud metadata"):
        validate_url("http://169.254.169.254/", resolve_dns=False)


def test_allowed_domain_pass():
    result = validate_url("https://sub.example.com/page", allowed_domains=["example.com"], resolve_dns=False)
    assert result


def test_allowed_domain_block():
    with pytest.raises(URLValidationError, match="allowed-domain"):
        validate_url("https://evil.com/", allowed_domains=["example.com"], resolve_dns=False)


def test_is_same_origin_true():
    assert is_same_origin("https://example.com/a", "https://example.com/b")


def test_is_same_origin_false():
    assert not is_same_origin("https://example.com", "https://other.com")


def test_normalize_strips_utm():
    url = "https://example.com/page?utm_source=google&id=5"
    norm = normalize_url(url)
    assert "utm_source" not in norm
    assert "id=5" in norm


def test_normalize_removes_fragment():
    norm = normalize_url("https://example.com/page#section")
    assert "#" not in norm
