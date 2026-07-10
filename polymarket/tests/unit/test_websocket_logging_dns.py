"""Transient-disconnect classification for DNS / name-resolution blips.

A brief local network outage surfaces as a getaddrinfo failure on the
WebSocket. The socket recovers when connectivity returns, so these must be
treated as transient (WARNING), not marker-blocking ERROR — the inactivity
watchdog and API-polling fallback still catch a sustained outage.
"""

from polymarket.api.websocket_logging import is_transient_websocket_disconnect


def test_macos_dns_resolution_failure_is_transient():
    # macOS getaddrinfo [Errno 8] during a brief local network outage.
    assert is_transient_websocket_disconnect(
        "WebSocket error: [Errno 8] nodename nor servname provided, or not known"
    )


def test_linux_dns_resolution_failures_are_transient():
    assert is_transient_websocket_disconnect("[Errno -2] Name or service not known")
    assert is_transient_websocket_disconnect(
        "[Errno -3] Temporary failure in name resolution"
    )


def test_unrelated_error_is_not_transient():
    assert not is_transient_websocket_disconnect("Invalid authentication token")
