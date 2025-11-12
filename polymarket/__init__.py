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
from .models import (
    Side,
    OrderType,
    OrderStatus,
    SignatureType,
    OrderRequest,
    MarketOrderRequest,
    OrderResponse,
    Order,
    Position,
    Balance,
    Market,
    OrderBook,
    WalletConfig,
    ClientConfig,
    LeaderboardTrader,
)
from .exceptions import (
    PolymarketError,
    APIError,
    AuthenticationError,
    ValidationError,
    RateLimitError,
    TimeoutError,
    CircuitBreakerError,
    TradingError,
    InsufficientBalanceError,
    OrderRejectedError,
    MarketNotReadyError,
    InvalidOrderError,
    PriceUnavailableError,
)

# CTF (Conditional Token Framework) - Neg-Risk adapter
from .ctf import (
    NegRiskAdapter,
    ConversionCalculator,
    is_safe_to_trade,
    NEG_RISK_ADAPTER,
    NEG_RISK_EXCHANGE,
    CTF_ADDRESS,
)

# Fee calculation utilities
# NOTE: Polymarket has NO trading fees (https://docs.polymarket.com/polymarket-learn/trading/fees)
# These functions return 0 fees for API compatibility only
from .utils.fees import (
    calculate_order_fee,
    calculate_net_cost,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    calculate_profit_after_fees,
    get_effective_spread,
)

# Order validation utilities
from .utils.validation import (
    validate_order,
    validate_price_bounds,
    validate_size,
    validate_fee_rate,
    validate_token_complementarity,
    validate_neg_risk_market,
    validate_balance,
    validate_order_amounts,
    check_order_profitability,
)

__version__ = "1.0.3"

__all__ = [
    # Main client
    "PolymarketClient",

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
    "WalletConfig",
    "ClientConfig",
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
