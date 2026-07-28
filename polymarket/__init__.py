"""
Polymarket Client Library

Future-proof, thread-safe client for Polymarket trading.
Supports multiple wallets across multiple strategies.

Adapted from Polymarket's official clients (MIT License):
- https://github.com/Polymarket/py-clob-client
- https://github.com/Polymarket/clob-client
- https://github.com/Polymarket/neg-risk-ctf-adapter
"""

from .client import PolymarketClient

from .exceptions import (
    APIError,
    AuthenticationError,
    BalanceTrackingError,
    CircuitBreakerError,
    FOKNotFilledError,
    InsufficientAllowanceError,
    InsufficientBalanceError,
    InvalidOrderError,
    MarketDataError,
    MarketNotFoundError,
    MarketNotReadyError,
    OrderBookError,
    OrderDelayedError,
    OrderExpiredError,
    OrderNotFoundError,
    OrderRejectedError,
    PolymarketError,
    PriceUnavailableError,
    RateLimitError,
    TickSizeError,
    TimeoutError,
    TradingError,
    ValidationError,
    WebSocketConnectionError,
    WebSocketDisconnectedError,
    WebSocketError,
)
from .market_manager import MarketManager, MarketManagerConfig, MarketStats
from .models import (
    Balance,
    ClosedPosition,
    FeeInfo,
    FeeSchedule,
    LeaderboardTrader,
    Market,
    MarketOrderRequest,
    Order,
    OrderBook,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Position,
    PricePoint,
    Side,
    SignatureType,
    WalletConfig,
)
from .wallet_identity import (
    ResolvedWalletIdentity,
    ResolvedWalletRouting,
    abbreviate_address,
    resolve_wallet_config,
    resolve_wallet_identity_from_env,
    resolve_wallet_routing_from_env,
)

# Fee calculation utilities
from .utils.fees import (
    calculate_net_cost,
    calculate_order_fee,
    calculate_profit_after_fees,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    get_effective_spread,
)

# Order validation utilities
from .utils.validation import (
    check_order_profitability,
    validate_balance,
    validate_fee_rate,
    validate_neg_risk_market,
    validate_order,
    validate_order_amounts,
    validate_price_bounds,
    validate_size,
    validate_token_complementarity,
)

__version__ = "3.7.0"

_CTF_EXPORTS = frozenset(
    {
        "CTF_ADDRESS",
        "NEG_RISK_ADAPTER",
        "NEG_RISK_EXCHANGE",
        "ConversionCalculator",
        "NegRiskAdapter",
        "is_safe_to_trade",
    }
)


def __getattr__(name: str):
    """Load optional on-chain CTF/Web3 support only when it is requested."""
    if name not in _CTF_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import ctf

    value = getattr(ctf, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _CTF_EXPORTS)


__all__ = [
    # Main client
    "PolymarketClient",
    # Market manager
    "MarketManager",
    "MarketManagerConfig",
    "MarketStats",
    # Types
    "Side",
    "OrderType",
    "OrderStatus",
    "SignatureType",
    "OrderRequest",
    "MarketOrderRequest",
    "ClosedPosition",
    "OrderResponse",
    "Order",
    "Position",
    "Balance",
    "FeeInfo",
    "FeeSchedule",
    "Market",
    "OrderBook",
    "PricePoint",
    "WalletConfig",
    "ResolvedWalletIdentity",
    "ResolvedWalletRouting",
    "abbreviate_address",
    "resolve_wallet_config",
    "resolve_wallet_identity_from_env",
    "resolve_wallet_routing_from_env",
    "LeaderboardTrader",
    # Exceptions
    "PolymarketError",
    "APIError",
    "AuthenticationError",
    "ValidationError",
    "RateLimitError",
    "TimeoutError",
    "CircuitBreakerError",
    "TradingError",
    "BalanceTrackingError",
    "InsufficientAllowanceError",
    "InsufficientBalanceError",
    "FOKNotFilledError",
    "OrderDelayedError",
    "OrderExpiredError",
    "OrderNotFoundError",
    "OrderRejectedError",
    "MarketNotReadyError",
    "InvalidOrderError",
    "TickSizeError",
    "MarketDataError",
    "MarketNotFoundError",
    "OrderBookError",
    "PriceUnavailableError",
    "WebSocketError",
    "WebSocketConnectionError",
    "WebSocketDisconnectedError",
    # CTF - Neg-Risk adapter
    "NegRiskAdapter",
    "ConversionCalculator",
    "is_safe_to_trade",
    "NEG_RISK_ADAPTER",
    "NEG_RISK_EXCHANGE",
    "CTF_ADDRESS",
    # Fee utilities
    "calculate_order_fee",
    "calculate_net_cost",
    "compare_fees_buy_vs_sell",
    "estimate_breakeven_exit",
    "calculate_profit_after_fees",
    "get_effective_spread",
    # Validation utilities
    "validate_order",
    "validate_price_bounds",
    "validate_size",
    "validate_fee_rate",
    "validate_token_complementarity",
    "validate_neg_risk_market",
    "validate_balance",
    "validate_order_amounts",
    "check_order_profitability",
]
