"""
Structured JSON logging for production environments.

Enables correlation IDs, structured data, and queryable logs.
"""

import json
import logging
import time
import uuid
import re
from typing import Any, Dict, Optional
from datetime import datetime
from contextvars import ContextVar

# Thread-local correlation ID storage
_correlation_id: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)


class CredentialRedactionFilter(logging.Filter):
    """
    Security filter that redacts credentials from log messages.

    Prevents private keys, API secrets, and other sensitive data from leaking
    into logs, exception messages, or debug output.

    Security Issue Fixed: SEC-001 (Critical)
    - Redacts Ethereum private keys (0x followed by 64 hex chars)
    - Redacts API secrets and passphrases
    - Redacts base64-encoded credentials
    - Prevents credential exposure in exception stack traces

    Usage:
        >>> handler = logging.StreamHandler()
        >>> handler.addFilter(CredentialRedactionFilter())
        >>> logger.addHandler(handler)
    """

    # Patterns for credential detection
    PRIVATE_KEY_PATTERN = re.compile(r'0x[0-9a-fA-F]{64}')
    # BUG FIX: Capture the prefix (key=) to keep it, not the secret value
    API_SECRET_PATTERN = re.compile(
        r'((?:secret|passphrase|password|key)["\']?\s*[:=]\s*["\']?)[a-zA-Z0-9+/=]{20,}["\']?',
        re.IGNORECASE
    )
    BASE64_SECRET_PATTERN = re.compile(r'[A-Za-z0-9+/]{40,}={0,2}')  # Long base64 strings

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact credentials from log record.

        Args:
            record: Log record to filter

        Returns:
            Always True (record is never filtered out, just sanitized)
        """
        # Redact from main message
        if record.msg:
            record.msg = self._redact_credentials(str(record.msg))

        # Redact from args if present
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact_credentials(str(v))
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact_credentials(str(arg))
                    for arg in record.args
                )

        # Redact from exception info
        if record.exc_text:
            record.exc_text = self._redact_credentials(record.exc_text)

        return True  # Always pass record through (just sanitized)

    def _redact_credentials(self, text: str) -> str:
        """
        Redact all credential patterns from text.

        Args:
            text: Text to redact

        Returns:
            Text with credentials redacted
        """
        if not text:
            return text

        # Redact private keys (most critical)
        text = self.PRIVATE_KEY_PATTERN.sub('0x[REDACTED]', text)

        # Redact API secrets/passphrases
        # BUG FIX: Keep the prefix (secret=) but replace the value
        text = self.API_SECRET_PATTERN.sub(r'\1[REDACTED]', text)

        # Redact long base64 strings (likely API keys/secrets)
        # Only redact if 40+ chars to avoid false positives
        def redact_base64(match):
            b64 = match.group(0)
            if len(b64) >= 40:
                return b64[:8] + '...[REDACTED]'
            return b64

        text = self.BASE64_SECRET_PATTERN.sub(redact_base64, text)

        return text


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Outputs logs as JSON for easy parsing by log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Base log structure
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if present
        correlation_id = _correlation_id.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id

        # Add extra fields from record
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info)
            }

        return json.dumps(log_data, default=str)


class StructuredLogger:
    """
    Structured logger wrapper with correlation ID support.

    Provides structured logging methods with automatic correlation tracking.
    """

    def __init__(self, name: str):
        """Initialize structured logger."""
        self.logger = logging.getLogger(name)

    def _log(
        self,
        level: int,
        event: str,
        message: Optional[str] = None,
        **fields
    ) -> None:
        """Log structured event."""
        # Combine event and message
        log_message = f"{event}: {message}" if message else event

        # Create extra fields dict
        extra_fields = {"event": event}
        extra_fields.update(fields)

        # Create LogRecord with extra fields
        extra = {'extra_fields': extra_fields}
        self.logger.log(level, log_message, extra=extra)

    def debug(self, event: str, message: Optional[str] = None, **fields) -> None:
        """Log debug event."""
        self._log(logging.DEBUG, event, message, **fields)

    def info(self, event: str, message: Optional[str] = None, **fields) -> None:
        """
        Log info event.

        Example:
            >>> logger.info(
            ...     "order_placed",
            ...     "Order successfully placed",
            ...     order_id="abc123",
            ...     wallet="strategy1",
            ...     market="trump-vs-biden-2024",
            ...     side="BUY",
            ...     price=0.55,
            ...     size=100.0
            ... )

        Output (JSON):
            {
              "timestamp": "2025-10-25T23:48:23.456Z",
              "level": "INFO",
              "logger": "polymarket",
              "message": "order_placed: Order successfully placed",
              "correlation_id": "req_abc123",
              "event": "order_placed",
              "order_id": "abc123",
              "wallet": "strategy1",
              "market": "trump-vs-biden-2024",
              "side": "BUY",
              "price": 0.55,
              "size": 100.0
            }
        """
        self._log(logging.INFO, event, message, **fields)

    def warning(self, event: str, message: Optional[str] = None, **fields) -> None:
        """Log warning event."""
        self._log(logging.WARNING, event, message, **fields)

    def error(self, event: str, message: Optional[str] = None, **fields) -> None:
        """
        Log error event.

        Example:
            >>> logger.error(
            ...     "order_rejected",
            ...     "Order rejected by exchange",
            ...     order_id="abc123",
            ...     reason="INSUFFICIENT_BALANCE",
            ...     required=100.0,
            ...     available=50.0
            ... )
        """
        self._log(logging.ERROR, event, message, **fields)

    def exception(self, event: str, message: Optional[str] = None, **fields) -> None:
        """Log exception with traceback."""
        self.logger.exception(f"{event}: {message}" if message else event, extra={'extra_fields': fields})


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """
    Set correlation ID for current context.

    Args:
        correlation_id: Correlation ID (generates UUID if None)

    Returns:
        The correlation ID set

    Example:
        >>> # In your API endpoint
        >>> correlation_id = set_correlation_id()
        >>> client.place_order(order)  # All logs will have this correlation_id
    """
    if correlation_id is None:
        correlation_id = f"req_{uuid.uuid4().hex[:12]}"

    _correlation_id.set(correlation_id)
    return correlation_id


def get_correlation_id() -> Optional[str]:
    """Get current correlation ID."""
    return _correlation_id.get()


def clear_correlation_id() -> None:
    """Clear correlation ID from current context."""
    _correlation_id.set(None)


def configure_structured_logging(
    level: str = "INFO",
    enable_json: bool = True,
    enable_credential_redaction: bool = True
) -> None:
    """
    Configure structured logging globally.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        enable_json: Use JSON formatter (True for production)
        enable_credential_redaction: Add credential redaction filter (recommended for security)

    Example:
        >>> # In your strategy backend startup
        >>> configure_structured_logging(
        ...     level="INFO",
        ...     enable_json=True,  # JSON for production
        ...     enable_credential_redaction=True  # Security filter
        ... )
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler()

    # Add credential redaction filter (SECURITY: Prevents credential leakage)
    if enable_credential_redaction:
        handler.addFilter(CredentialRedactionFilter())

    # Set formatter
    if enable_json:
        formatter = StructuredFormatter()
    else:
        # Standard format for development
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


# Convenience function
def get_logger(name: str) -> StructuredLogger:
    """
    Get structured logger instance.

    Example:
        >>> logger = get_logger("polymarket.trading")
        >>> logger.info("order_placed", order_id="abc123", price=0.55)
    """
    return StructuredLogger(name)
