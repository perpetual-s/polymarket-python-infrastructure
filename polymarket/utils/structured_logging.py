"""
Structured JSON logging for production environments.

Enables correlation IDs, structured data, and queryable logs.
"""

import json
import logging
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Optional

from pythonjsonlogger.json import JsonFormatter as _JsonFormatter

from ..redaction import redact_text, redact_value

# Thread-local correlation ID storage
_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}
_INTERNAL_REDACTION_FIELDS = frozenset(
    {
        "redacted_exception_message",
        "redacted_exception_traceback",
        "redacted_exception_type",
    }
)
_ExcInfo = (
    tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
)


def _safe_exception_text(
    exc_info: _ExcInfo,
) -> str:
    """Format an exception without allowing formatter failure to expose it."""
    exception_type = exc_info[0]
    if exception_type is None:
        return "[UNAVAILABLE]"
    try:
        rendered = logging.Formatter().formatException(exc_info)
    except Exception:
        rendered = f"{exception_type.__name__}: [UNAVAILABLE]"
    return redact_text(rendered)


def _record_extras(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_FIELDS
        and key not in _INTERNAL_REDACTION_FIELDS
    }


def _redact_log_record(record: logging.LogRecord) -> None:
    """Sanitize message, arbitrary extras, stack text, and exception output."""
    if isinstance(record.msg, Mapping):
        record.msg = redact_value(record.msg)
        record.args = ()
    elif record.msg is not None:
        try:
            message = record.getMessage()
        except Exception:
            safe_message = redact_value(record.msg)
            message = (
                safe_message if isinstance(safe_message, str) else repr(safe_message)
            )
        record.msg = redact_text(message)
        record.args = ()

    extras = _record_extras(record)
    if extras:
        sanitized_extras = redact_value(extras)
        for key in extras:
            record.__dict__.pop(key, None)
        if isinstance(sanitized_extras, Mapping):
            record.__dict__.update(
                {str(key): value for key, value in sanitized_extras.items()}
            )

    if record.stack_info:
        record.stack_info = redact_text(record.stack_info)

    if record.exc_info:
        raw_exc_info = record.exc_info
        record.exc_text = _safe_exception_text(raw_exc_info)
        exception_type = raw_exc_info[0]
        record.redacted_exception_type = (
            exception_type.__name__ if exception_type is not None else "[UNAVAILABLE]"
        )
        try:
            exception_message = str(raw_exc_info[1])
        except Exception:
            exception_message = "[UNAVAILABLE]"
        record.redacted_exception_message = redact_text(exception_message)
        record.redacted_exception_traceback = record.exc_text
        # A LogRecord is shared by every handler.  Clear the original tuple so
        # a later unfiltered/custom handler cannot recover the raw exception or
        # re-render its traceback after an earlier safe handler ran.
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_text(record.exc_text)
        record.redacted_exception_traceback = record.exc_text


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

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact credentials from log record.

        Args:
            record: Log record to filter

        Returns:
            Always True (record is never filtered out, just sanitized)
        """
        _redact_log_record(record)

        return True  # Always pass record through (just sanitized)

    def _redact_credentials(self, text: str) -> str:
        """
        Redact all credential patterns from text.

        Args:
            text: Text to redact

        Returns:
            Text with credentials redacted
        """
        return redact_text(text)


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Outputs logs as JSON for easy parsing by log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        _redact_log_record(record)

        # Base log structure
        log_data: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if present
        correlation_id = _correlation_id.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id

        # Add both StructuredLogger's wrapper and arbitrary stdlib extras.
        extras = _record_extras(record)
        extra_fields = extras.pop("extra_fields", None)
        if isinstance(extra_fields, Mapping):
            log_data.update({str(key): value for key, value in extra_fields.items()})
        log_data.update(extras)

        # Add exception info if present
        exception_type = getattr(record, "redacted_exception_type", None)
        if exception_type is not None:
            log_data["exception"] = {
                "type": exception_type,
                "message": getattr(
                    record, "redacted_exception_message", "[UNAVAILABLE]"
                ),
                "traceback": getattr(
                    record,
                    "redacted_exception_traceback",
                    record.exc_text or "[UNAVAILABLE]",
                ),
            }

        return json.dumps(
            redact_value(log_data),
            default=lambda value: f"<{type(value).__name__}>",
        )


class RedactingJsonFormatter(_JsonFormatter):
    """python-json-logger formatter with redaction as an output invariant."""

    def format(self, record: logging.LogRecord) -> str:
        # The formatter is safe even if a caller forgets the handler filter.
        _redact_log_record(record)
        return str(super().format(record))

    def formatException(  # noqa: N802 - logging's public API uses camelCase
        self,
        exc_info: _ExcInfo,
    ) -> str:
        rendered = super().formatException(exc_info)
        if isinstance(rendered, list):
            rendered = "\n".join(rendered)
        return redact_text(rendered)

    def process_log_record(self, log_record: dict[str, Any]) -> dict[str, Any]:
        processed = super().process_log_record(log_record)
        sanitized = redact_value(processed)
        if isinstance(sanitized, dict):
            return sanitized
        return {"message": sanitized}


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
        **fields: Any,
    ) -> None:
        """Log structured event."""
        # Combine event and message
        log_message = f"{event}: {message}" if message else event

        # Create extra fields dict
        extra_fields = {"event": event}
        extra_fields.update(fields)

        # Create LogRecord with extra fields
        extra = {"extra_fields": extra_fields}
        self.logger.log(level, log_message, extra=extra)

    def debug(self, event: str, message: Optional[str] = None, **fields: Any) -> None:
        """Log debug event."""
        self._log(logging.DEBUG, event, message, **fields)

    def info(self, event: str, message: Optional[str] = None, **fields: Any) -> None:
        """
        Log info event.

        Example:
            >>> logger.info(
            ...     "order_placed",
            ...     "Order successfully placed",
            ...     order_id="abc123",
            ...     wallet="primary",
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
              "wallet": "primary",
              "market": "trump-vs-biden-2024",
              "side": "BUY",
              "price": 0.55,
              "size": 100.0
            }
        """
        self._log(logging.INFO, event, message, **fields)

    def warning(self, event: str, message: Optional[str] = None, **fields: Any) -> None:
        """Log warning event."""
        self._log(logging.WARNING, event, message, **fields)

    def error(self, event: str, message: Optional[str] = None, **fields: Any) -> None:
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

    def exception(
        self, event: str, message: Optional[str] = None, **fields: Any
    ) -> None:
        """Log exception with traceback."""
        self.logger.exception(
            f"{event}: {message}" if message else event,
            extra={"extra_fields": fields},
        )


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
    enable_credential_redaction: bool = True,
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
    formatter: logging.Formatter
    if enable_json:
        formatter = StructuredFormatter()
    else:
        # Standard format for development
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
