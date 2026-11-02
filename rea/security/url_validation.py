"""URL validation and network restriction enforcement."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

# Schemes allowed for website scanning.
ALLOWED_SCHEMES = {"http", "https"}

# Patterns that look like cloud metadata endpoints.
_METADATA_HOSTNAMES = {
    "169.254.169.254",   # AWS / GCP / Azure IMDS
    "metadata.google.internal",
    "100.100.100.200",   # Alibaba Cloud
}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),    # carrier-grade NAT
]


class URLValidationError(ValueError):
    """Raised when a URL is rejected for safety or policy reasons."""


def validate_url(
    url: str,
    allowed_domains: list[str] | None = None,
    resolve_dns: bool = True,
) -> str:
    """Validate a URL for scanning.

    Returns the normalized URL string if safe.
    Raises URLValidationError if the URL is rejected.
    """
    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise URLValidationError(
            f"Scheme '{parsed.scheme}' is not allowed. Use http or https."
        )

    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError("URL has no hostname.")

    if hostname in _METADATA_HOSTNAMES:
        raise URLValidationError(
            f"Access to '{hostname}' is blocked (cloud metadata endpoint)."
        )

    # Domain allowlist check (if configured).
    if allowed_domains:
        if not any(
            hostname == d or hostname.endswith(f".{d}") for d in allowed_domains
        ):
            raise URLValidationError(
                f"'{hostname}' is not in the configured allowed-domain list."
            )

    # IP literal check — strip brackets for IPv6 literals.
    bare = hostname.strip("[]")
    is_ip_literal = False
    try:
        addr = ipaddress.ip_address(bare)
        is_ip_literal = True
        _check_ip(addr, hostname)   # raises URLValidationError if blocked
        return url                  # public IP literal — allow
    except URLValidationError:
        raise                       # propagate the block decision
    except ValueError:
        pass                        # not an IP literal; fall through

    if resolve_dns:
        _check_dns(hostname)

    return url


def validate_redirect(original_url: str, redirect_url: str) -> str:
    """Validate a redirect destination."""
    return validate_url(redirect_url, resolve_dns=True)


def _check_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, label: str) -> None:
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            raise URLValidationError(
                f"Access to private/loopback address '{label}' is blocked."
            )


def _check_dns(hostname: str) -> None:
    """Resolve hostname and block private IP addresses."""
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS failure — let the browser deal with it; not our job to block.
        return

    for _fam, _type, _proto, _cname, sockaddr in results:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
            _check_ip(addr, f"{hostname} → {ip_str}")
        except ValueError:
            pass  # Ignore malformed addresses from getaddrinfo.


def is_same_origin(base_url: str, candidate_url: str) -> bool:
    """Return True if candidate_url shares scheme + host + port with base_url."""
    b = urlparse(base_url)
    c = urlparse(candidate_url)
    return b.scheme == c.scheme and b.hostname == c.hostname and b.port == c.port


def normalize_url(url: str) -> str:
    """Remove common tracking parameters and normalize trailing slashes."""
    tracking_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "source",
    }
    parsed = urlparse(url)
    if parsed.query:
        from urllib.parse import parse_qs, urlencode
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs = {k: v for k, v in qs.items() if k not in tracking_params}
        query = urlencode(qs, doseq=True)
    else:
        query = ""

    path = parsed.path.rstrip("/") or "/"
    normalized = parsed._replace(path=path, query=query, fragment="")
    return normalized.geturl()
