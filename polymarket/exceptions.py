"""
Custom exceptions for Polymarket client.

Provides typed exceptions for better error handling across all strategies.
"""

from typing import Any, Optional

from .redaction import redact_text, redact_value


class PolymarketError(Exception):
    """Base exception for all Polymarket errors."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        safe_message = redact_text(str(message))
        safe_details = redact_value(details or {})
        super().__init__(safe_message)
        self.message = safe_message
        self.details: dict[str, Any] = safe_details


class APIError(PolymarketError):
    """API request failed."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message, {"status_code": status_code, "response": response})
        self.status_code = self.details["status_code"]
        self.response = self.details["response"]


class AuthenticationError(PolymarketError):
    """Authentication failed."""

    pass


class ValidationError(PolymarketError):
    """Input validation failed."""

    pass


class RateLimitError(PolymarketError):
    """Rate limit exceeded."""

    def __init__(
        self, message: str, endpoint: str, retry_after: Optional[float] = None
    ):
        super().__init__(message, {"endpoint": endpoint, "retry_after": retry_after})
        self.endpoint = self.details["endpoint"]
        self.retry_after = self.details["retry_after"]


class TimeoutError(PolymarketError):
    """Request timed out."""

    pass


class CircuitBreakerError(PolymarketError):
    """Circuit breaker is open, requests blocked."""

    pass


# Trading-specific exceptions
class TradingError(PolymarketError):
    """Base exception for trading operations."""

    pass


class InsufficientBalanceError(TradingError):
    """Insufficient balance for order."""

    pass


class BalanceTrackingError(TradingError):
    """Balance tracking error (e.g., over-release)."""

    pass


class OrderRejectedError(TradingError):
    """Order was rejected by exchange."""

    def __init__(
        self,
        message: str,
        order_id: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        super().__init__(message, {"order_id": order_id, "reason": reason})
        self.order_id = self.details["order_id"]
        self.reason = self.details["reason"]


class MarketNotReadyError(TradingError):
    """Market not accepting orders."""

    pass


class InvalidOrderError(TradingError):
    """Order parameters are invalid."""

    pass


class OrderNotFoundError(TradingError):
    """Order ID not found."""

    pass


class TickSizeError(ValidationError):
    """Order price violates minimum tick size."""

    def __init__(
        self,
        message: str,
        price: Optional[float] = None,
        tick_size: Optional[float] = None,
    ):
        super().__init__(message, {"price": price, "tick_size": tick_size})
        self.price = self.details["price"]
        self.tick_size = self.details["tick_size"]


class InsufficientAllowanceError(TradingError):
    """Insufficient token allowance for trading."""

    def __init__(
        self,
        message: str,
        token: Optional[str] = None,
        required: Optional[int] = None,
        current: Optional[int] = None,
    ):
        super().__init__(
            message,
            {"token_id": token, "required": required, "current": current},
        )
        self.token_id = self.details["token_id"]
        # ``token`` is the long-standing public attribute.  Keep it as a
        # compatibility alias while giving the redactor unambiguous typed-ID
        # context in ``details``.
        self.token = self.token_id
        self.required = self.details["required"]
        self.current = self.details["current"]


class OrderDelayedError(TradingError):
    """Order is in delayed state."""

    def __init__(self, message: str, order_id: Optional[str] = None):
        super().__init__(message, {"order_id": order_id})
        self.order_id = self.details["order_id"]


class OrderExpiredError(ValidationError):
    """Order expiration timestamp is invalid."""

    def __init__(self, message: str, expiration: Optional[int] = None):
        super().__init__(message, {"expiration": expiration})
        self.expiration = self.details["expiration"]


class FOKNotFilledError(TradingError):
    """Fill-or-Kill order could not be filled completely."""

    def __init__(
        self,
        message: str,
        token_id: Optional[str] = None,
        requested_size: Optional[float] = None,
    ):
        super().__init__(
            message, {"token_id": token_id, "requested_size": requested_size}
        )
        self.token_id = self.details["token_id"]
        self.requested_size = self.details["requested_size"]


def is_definitive_order_rejection(error: Exception) -> bool:
    """Return whether the exchange proved that this submission did not land."""
    if isinstance(error, OrderRejectedError):
        reason = str(error.reason or "").strip().upper()
        # A duplicate response means the same exchange identity may already
        # exist and therefore still requires exact-order reconciliation.
        return reason != "DUPLICATE"
    return isinstance(
        error,
        (
            AuthenticationError,
            FOKNotFilledError,
            InsufficientAllowanceError,
            InsufficientBalanceError,
            InvalidOrderError,
            MarketNotReadyError,
            OrderExpiredError,
            TickSizeError,
        ),
    )


# Market data exceptions
class MarketDataError(PolymarketError):
    """Market data unavailable or invalid."""

    pass


class PriceUnavailableError(MarketDataError):
    """Price data not available."""

    def __init__(self, message: str, token_id: Optional[str] = None):
        super().__init__(message, {"token_id": token_id})
        self.token_id = self.details["token_id"]


class OrderBookError(MarketDataError):
    """Order book data unavailable or invalid."""

    def __init__(self, message: str, token_id: Optional[str] = None):
        super().__init__(message, {"token_id": token_id})
        self.token_id = self.details["token_id"]


class MarketNotFoundError(MarketDataError):
    """Market not found."""

    def __init__(self, message: str, market_id: Optional[str] = None):
        super().__init__(message, {"market_id": market_id})
        self.market_id = self.details["market_id"]


# WebSocket exceptions
class WebSocketError(PolymarketError):
    """WebSocket connection error."""

    pass


class WebSocketConnectionError(WebSocketError):
    """Failed to connect to WebSocket."""

    pass


class WebSocketDisconnectedError(WebSocketError):
    """WebSocket disconnected unexpectedly."""

    pass
