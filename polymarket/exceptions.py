"""
Custom exceptions for Polymarket client.

Provides typed exceptions for better error handling across all strategies.
"""

from typing import Optional, Any


class PolymarketError(Exception):
    """Base exception for all Polymarket errors."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class APIError(PolymarketError):
    """API request failed."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 response: Optional[dict] = None):
        super().__init__(message, {"status_code": status_code, "response": response})
        self.status_code = status_code
        self.response = response


class AuthenticationError(PolymarketError):
    """Authentication failed."""
    pass


class ValidationError(PolymarketError):
    """Input validation failed."""
    pass


class RateLimitError(PolymarketError):
    """Rate limit exceeded."""

    def __init__(self, message: str, endpoint: str, retry_after: Optional[float] = None):
        super().__init__(message, {"endpoint": endpoint, "retry_after": retry_after})
        self.endpoint = endpoint
        self.retry_after = retry_after


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

    def __init__(self, message: str, order_id: Optional[str] = None,
                 reason: Optional[str] = None):
        super().__init__(message, {"order_id": order_id, "reason": reason})
        self.order_id = order_id
        self.reason = reason


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

    def __init__(self, message: str, price: Optional[float] = None,
                 tick_size: Optional[float] = None):
        super().__init__(message, {"price": price, "tick_size": tick_size})
        self.price = price
        self.tick_size = tick_size


class InsufficientAllowanceError(TradingError):
    """Insufficient token allowance for trading."""

    def __init__(self, message: str, token: Optional[str] = None,
                 required: Optional[int] = None, current: Optional[int] = None):
        super().__init__(message, {"token": token, "required": required, "current": current})
        self.token = token
        self.required = required
        self.current = current


class OrderDelayedError(TradingError):
    """Order is in delayed state."""

    def __init__(self, message: str, order_id: Optional[str] = None):
        super().__init__(message, {"order_id": order_id})
        self.order_id = order_id


class OrderExpiredError(ValidationError):
    """Order expiration timestamp is invalid."""

    def __init__(self, message: str, expiration: Optional[int] = None):
        super().__init__(message, {"expiration": expiration})
        self.expiration = expiration


class FOKNotFilledError(TradingError):
    """Fill-or-Kill order could not be filled completely."""

    def __init__(self, message: str, token_id: Optional[str] = None,
                 requested_size: Optional[float] = None):
        super().__init__(message, {"token_id": token_id, "requested_size": requested_size})
        self.token_id = token_id
        self.requested_size = requested_size


# Market data exceptions
class MarketDataError(PolymarketError):
    """Market data unavailable or invalid."""
    pass


class PriceUnavailableError(MarketDataError):
    """Price data not available."""

    def __init__(self, message: str, token_id: Optional[str] = None):
        super().__init__(message, {"token_id": token_id})
        self.token_id = token_id


class OrderBookError(MarketDataError):
    """Order book data unavailable or invalid."""

    def __init__(self, message: str, token_id: Optional[str] = None):
        super().__init__(message, {"token_id": token_id})
        self.token_id = token_id


class MarketNotFoundError(MarketDataError):
    """Market not found."""

    def __init__(self, message: str, market_id: Optional[str] = None):
        super().__init__(message, {"market_id": market_id})
        self.market_id = market_id


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
