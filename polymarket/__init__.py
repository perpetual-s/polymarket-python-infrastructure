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

# CTF (Conditional Token Framework) - Neg-Risk adapter
from .ctf import (
    CTF_ADDRESS,
    NEG_RISK_ADAPTER,
    NEG_RISK_EXCHANGE,
    ConversionCalculator,
    NegRiskAdapter,
    is_safe_to_trade,
)
from .exceptions import (
    APIError,
    AuthenticationError,
    CircuitBreakerError,
    InsufficientBalanceError,
    InvalidOrderError,
    MarketNotReadyError,
    OrderRejectedError,
    PolymarketError,
    PriceUnavailableError,
    RateLimitError,
    TimeoutError,
    TradingError,
    ValidationError,
)
from .market_manager import MarketManager, MarketManagerConfig, MarketStats
from .models import (
    Balance,
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

# Fee calculation utilities
# NOTE: Polymarket has NO trading fees (https://docs.polymarket.com/polymarket-learn/trading/fees)
# These functions return 0 fees for API compatibility only
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

__all__ = [
    # Main client
    "PolymarketClient",
    # Market Manager (real-time market data)
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
    "OrderResponse",
    "Order",
    "Position",
    "Balance",
    "Market",
    "OrderBook",
    "PricePoint",
    "WalletConfig",
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
    "InsufficientBalanceError",
    "OrderRejectedError",
    "MarketNotReadyError",
    "InvalidOrderError",
    "PriceUnavailableError",
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
