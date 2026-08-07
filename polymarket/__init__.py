"""
Polymarket Client Library

Async client for Polymarket market data, wallet-aware trading, and streams.
Safe for concurrent use from a single event loop; supports multiple wallets
in one client.

Every public model, exception, and helper named in ``API_REFERENCE.md`` is
importable directly from this package.

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
    UnsupportedResolution,
    ValidationError,
    WebSocketConnectionError,
    WebSocketDisconnectedError,
    WebSocketError,
    is_definitive_order_rejection,
)
from .models import (
    Activity,
    ActivityType,
    Balance,
    ClobMakerTrade,
    ClobTrade,
    ClosedPosition,
    DataTradeV1,
    DataTradesCoverageV1,
    DataTradesQueryV1,
    DataTradesResultV1,
    Event,
    FeeInfo,
    FeeSchedule,
    Holder,
    LeaderboardTrader,
    Market,
    MarketFilters,
    MarketOrderRequest,
    MarketTradeEventV1,
    MarketTradeEventsResultV1,
    Order,
    OrderBook,
    OrderFilters,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    PortfolioValue,
    Position,
    PriceHistoryCoverageV1,
    PriceHistoryPointV1,
    PriceHistoryQueryV1,
    PriceHistoryResultV1,
    PricePoint,
    PublicDataStatus,
    PublicRequestEvidenceV1,
    ResolutionPayouts,
    Side,
    SignatureType,
    Trade,
    WalletConfig,
    WebSocketMessage,
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
)

# Order validation utilities
from .utils.validation import (
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
    """Load optional on-chain helpers only when they are requested."""
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
    # Enums
    "ActivityType",
    "OrderStatus",
    "OrderType",
    "PublicDataStatus",
    "Side",
    "SignatureType",
    # Requests and filters
    "MarketFilters",
    "MarketOrderRequest",
    "OrderFilters",
    "OrderRequest",
    "WalletConfig",
    # Orders, trades, and positions
    "ClobMakerTrade",
    "ClobTrade",
    "ClosedPosition",
    "Order",
    "OrderResponse",
    "Position",
    "Trade",
    # Account and portfolio
    "Activity",
    "Balance",
    "FeeInfo",
    "FeeSchedule",
    "Holder",
    "LeaderboardTrader",
    "PortfolioValue",
    # Markets and prices
    "Event",
    "Market",
    "OrderBook",
    "PricePoint",
    "ResolutionPayouts",
    # Typed public-flow results
    "DataTradeV1",
    "DataTradesCoverageV1",
    "DataTradesQueryV1",
    "DataTradesResultV1",
    "MarketTradeEventV1",
    "MarketTradeEventsResultV1",
    "PriceHistoryCoverageV1",
    "PriceHistoryPointV1",
    "PriceHistoryQueryV1",
    "PriceHistoryResultV1",
    "PublicRequestEvidenceV1",
    # Streams
    "WebSocketMessage",
    # Wallet identity
    "ResolvedWalletIdentity",
    "ResolvedWalletRouting",
    "abbreviate_address",
    "resolve_wallet_config",
    "resolve_wallet_identity_from_env",
    "resolve_wallet_routing_from_env",
    # Exceptions - base
    "PolymarketError",
    # Exceptions - request and transport
    "APIError",
    "RateLimitError",
    "TimeoutError",
    # Exceptions - auth
    "AuthenticationError",
    # Exceptions - validation
    "OrderExpiredError",
    "TickSizeError",
    "ValidationError",
    # Exceptions - trading
    "BalanceTrackingError",
    "FOKNotFilledError",
    "InsufficientAllowanceError",
    "InsufficientBalanceError",
    "InvalidOrderError",
    "MarketNotReadyError",
    "OrderDelayedError",
    "OrderNotFoundError",
    "OrderRejectedError",
    "TradingError",
    # Exceptions - market data
    "MarketDataError",
    "MarketNotFoundError",
    "OrderBookError",
    "PriceUnavailableError",
    "UnsupportedResolution",
    # Exceptions - streams
    "WebSocketConnectionError",
    "WebSocketDisconnectedError",
    "WebSocketError",
    # Error classification
    "is_definitive_order_rejection",
    # CTF - Neg-Risk adapter
    "CTF_ADDRESS",
    "ConversionCalculator",
    "NEG_RISK_ADAPTER",
    "NEG_RISK_EXCHANGE",
    "NegRiskAdapter",
    "is_safe_to_trade",
    # Fee utilities
    "calculate_net_cost",
    "calculate_order_fee",
    # Validation utilities
    "validate_balance",
    "validate_fee_rate",
    "validate_neg_risk_market",
    "validate_order",
    "validate_order_amounts",
    "validate_price_bounds",
    "validate_size",
    "validate_token_complementarity",
]
