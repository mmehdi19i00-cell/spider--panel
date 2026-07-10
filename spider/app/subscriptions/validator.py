"""Subscription validation — guarantees we never return a broken config.

Wrap the URI validator and provide a batch checker used by the test suite
and by the subscription endpoint's defensive check.
"""
from __future__ import annotations

from app.xray.validator import validate_vless_uri


def assert_valid_subscription(uris: list[str]) -> list[str]:
    """Raise ValueError with collected errors if any URI is invalid."""
    all_errors: list[str] = []
    for u in uris:
        ok, errs = validate_vless_uri(u)
        if not ok:
            all_errors.append(f"{u[:60]}... -> {', '.join(errs)}")
    if all_errors:
        raise ValueError("Invalid subscription URIs:\n" + "\n".join(all_errors))
    return uris


def filter_valid(uris: list[str]) -> list[str]:
    out = []
    for u in uris:
        ok, _ = validate_vless_uri(u)
        if ok:
            out.append(u)
    return out
