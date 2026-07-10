"""Shared WebSocket log-severity helpers.

Both Polymarket WebSocket surfaces use ``websocket-client`` under the hood. Its
recoverable remote-close events can arrive through either our callbacks or the
library's own logger, so keep the marker-evidence severity contract in one
place.
"""

from __future__ import annotations

import logging
from typing import Any

WEBSOCKET_TRANSIENT_DISCONNECTS = (
    "Connection to remote host was lost",
    "Connection timed out",
    "ping/pong timed out",
    "opcode=8",
    "Connection reset by peer",
    "Handshake status 429 Too Many Requests",
    # Transient DNS / name-resolution failures from a brief local network
    # outage. The socket recovers when connectivity returns; a sustained
    # outage is still caught by API-polling failure and the inactivity
    # watchdog, so downgrading these to WARNING does not hide a dead feed.
    "nodename nor servname provided",  # macOS getaddrinfo [Errno 8]
    "Name or service not known",  # Linux getaddrinfo [Errno -2]
    "Temporary failure in name resolution",  # Linux getaddrinfo [Errno -3]
)

_WEBSOCKET_FILTER_INSTALLED = False


def is_transient_websocket_disconnect(error: Any) -> bool:
    """Return True for recoverable websocket-client disconnect events."""
    message = str(error)
    return any(token in message for token in WEBSOCKET_TRANSIENT_DISCONNECTS)


class _TransientDisconnectSeverityFilter(logging.Filter):
    """Downgrade websocket-client's recoverable disconnect goodbye logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.levelno >= logging.ERROR
            and is_transient_websocket_disconnect(record.getMessage())
        ):
            record.levelno = logging.WARNING
            record.levelname = logging.getLevelName(logging.WARNING)
        return True


def install_websocket_transient_disconnect_filter() -> None:
    """Install a process-wide filter for websocket-client transient disconnects."""
    global _WEBSOCKET_FILTER_INSTALLED
    if _WEBSOCKET_FILTER_INSTALLED:
        return
    logging.getLogger("websocket").addFilter(_TransientDisconnectSeverityFilter())
    _WEBSOCKET_FILTER_INSTALLED = True
