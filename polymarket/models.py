"""
Type definitions for Polymarket client.

Uses Pydantic for runtime validation and type safety.
DECIMAL PRECISION: All numeric types use Decimal for financial-grade accuracy.
"""

from enum import Enum
from typing import Optional, Any, Union
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pydantic import BaseModel, Field, field_validator, ConfigDict, field_serializer


class Side(str, Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type."""
    GTC = "GTC"  # Good-til-cancelled
    GTD = "GTD"  # Good-til-date
    FOK = "FOK"  # Fill-or-kill
    FAK = "FAK"  # Fill-and-kill


class OrderStatus(str, Enum):
    """
    Order status values from Polymarket CLOB API.

    Official statuses from /data/orders endpoint.
    """
    LIVE = "live"               # Active on exchange
    PENDING = "pending"         # Being processed
    FILLED = "filled"           # Completely filled
    MATCHED = "matched"         # Matched (legacy/alias for filled)
    CANCELLED = "cancelled"     # User cancelled
    EXPIRED = "expired"         # Good-till-date expired
    REJECTED = "rejected"       # Order rejected by exchange

    # Legacy statuses (may appear in older data)
    DELAYED = "delayed"         # Processing delayed
    UNMATCHED = "unmatched"     # Not matched


class SignatureType(int, Enum):
    """Wallet signature type."""
    EOA = 0  # Externally Owned Account (MetaMask, hardware wallet)
    MAGIC = 1  # Magic/Email wallet
    PROXY = 2  # Proxy wallet


# Request Models
class OrderRequest(BaseModel):
    """Order placement request."""
    # NOTE: Removed use_enum_values=True to keep enums as enums (not auto-convert to strings)
    # model_config = ConfigDict(use_enum_values=True)

    token_id: str = Field(..., description="ERC1155 token ID")
    price: Decimal = Field(..., ge=Decimal("0.01"), le=Decimal("0.99"), description="Order price (0.01-0.99)")
    size: Decimal = Field(..., gt=0, description="Order size in USDC value (BUY: spend amount, SELL: sell value)")
    side: Side = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.GTC, description="Order type")
    expiration: Optional[int] = Field(None, description="Unix timestamp for GTD orders")

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: Any) -> Decimal:
        """Convert to Decimal and round to 2 decimals."""
        if isinstance(v, Decimal):
            dec = v
        elif isinstance(v, str):
            dec = Decimal(v)
        elif isinstance(v, (int, float)):
            dec = Decimal(str(v))  # Convert via string to avoid float precision loss
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")

        # Quantize to 2 decimals (tick size)
        return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @field_validator("size", mode="before")
    @classmethod
    def validate_size(cls, v: Any) -> Decimal:
        """Convert to Decimal and round to 2 decimals."""
        if isinstance(v, Decimal):
            dec = v
        elif isinstance(v, str):
            dec = Decimal(v)
        elif isinstance(v, (int, float)):
            dec = Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")

        # Quantize to 2 decimals
        return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class MarketOrderRequest(BaseModel):
    """Market order request."""
    model_config = ConfigDict(use_enum_values=True)

    token_id: str = Field(..., description="ERC1155 token ID")
    amount: Decimal = Field(..., gt=0, description="Amount in USDC")
    side: Side = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.FOK, description="FOK or FAK")

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> Decimal:
        """Convert to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


# Response Models
class OrderResponse(BaseModel):
    """Order placement response."""
    success: bool
    order_id: Optional[str] = None
    status: Optional[OrderStatus] = None
    error_msg: Optional[str] = None
    order_hashes: Optional[list[str]] = None

    model_config = ConfigDict(use_enum_values=True)


class Order(BaseModel):
    """Open order."""
    id: str
    market: str
    asset_id: str
    token_id: str
    price: Decimal
    size: Decimal
    side: Side
    status: OrderStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    expiration: Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("price", "size", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class Position(BaseModel):
    """Trading position with comprehensive PnL tracking."""
    # Identity
    proxy_wallet: str = Field(..., alias="proxyWallet")
    asset: str
    condition_id: str = Field(..., alias="conditionId")

    # Position metrics
    size: Decimal
    avg_price: Decimal = Field(..., alias="avgPrice")
    current_value: Decimal = Field(..., alias="currentValue")
    initial_value: Decimal = Field(..., alias="initialValue")
    cur_price: Decimal = Field(..., alias="curPrice")

    # P&L metrics
    cash_pnl: Decimal = Field(..., alias="cashPnl")
    percent_pnl: Decimal = Field(..., alias="percentPnl")
    realized_pnl: Decimal = Field(default=Decimal("0.0"), alias="realizedPnl")
    percent_realized_pnl: Decimal = Field(default=Decimal("0.0"), alias="percentRealizedPnl")

    # Market details
    title: str
    slug: str
    icon: Optional[str] = None
    outcome: str
    outcome_index: int = Field(..., alias="outcomeIndex")
    opposite_outcome: str = Field(..., alias="oppositeOutcome")
    end_date: Optional[str] = Field(None, alias="endDate")

    # Status flags
    redeemable: bool = False
    mergeable: bool = False
    negative_risk: bool = Field(default=False, alias="negativeRisk")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator(
        "size", "avg_price", "current_value", "initial_value", "cur_price",
        "cash_pnl", "percent_pnl", "realized_pnl", "percent_realized_pnl",
        mode="before"
    )
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        elif v is None:
            return Decimal("0.0")
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class Trade(BaseModel):
    """Trade execution record."""
    # Trade identity
    id: str
    market: str
    condition_id: str = Field(..., alias="conditionId")
    asset: str

    # Trade details
    side: Side
    size: Decimal
    price: Decimal
    fee_rate_bps: int = Field(..., alias="feeRateBps")

    # Timing
    timestamp: int

    # Blockchain
    transaction_hash: Optional[str] = Field(None, alias="transactionHash")

    # Participants
    maker_address: Optional[str] = Field(None, alias="makerAddress")
    maker_pseudonym: Optional[str] = Field(None, alias="makerPseudonym")
    taker_address: Optional[str] = Field(None, alias="takerAddress")
    taker_pseudonym: Optional[str] = Field(None, alias="takerPseudonym")

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("size", "price", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class ActivityType(str, Enum):
    """Onchain activity types."""
    TRADE = "TRADE"
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    REDEEM = "REDEEM"
    REWARD = "REWARD"
    CONVERSION = "CONVERSION"


class Activity(BaseModel):
    """Onchain activity record."""
    # Timing
    timestamp: int

    # Activity type
    type: ActivityType

    # Blockchain
    transaction_hash: str = Field(..., alias="transactionHash")

    # Market context
    market: str
    condition_id: str = Field(..., alias="conditionId")
    asset: str
    title: str
    outcome: str

    # Trade-specific (optional)
    side: Optional[Side] = None

    # Amounts
    size: Decimal
    usd_value: Decimal = Field(..., alias="usdValue")

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("size", "usd_value", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class PortfolioValue(BaseModel):
    """
    Total portfolio value breakdown.

    Returned by /value endpoint with detailed portfolio metrics.
    """
    user: str
    value: Decimal  # Legacy field - total value (same as equity_total)
    bets: Optional[Decimal] = None  # Total bet value
    cash: Optional[Decimal] = None  # Available USDC
    equity_total: Optional[Decimal] = Field(None, alias="equityTotal")  # Total portfolio value

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("value", "bets", "cash", "equity_total", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Optional[Decimal]:
        """Convert numeric fields to Decimal."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class Holder(BaseModel):
    """Market token holder."""
    proxy_wallet: str = Field(..., alias="proxyWallet")
    pseudonym: Optional[str] = None
    amount: Decimal
    outcome: str
    profile_picture: Optional[str] = Field(None, alias="profilePicture")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class LeaderboardTrader(BaseModel):
    """Leaderboard trader entry."""
    rank: str
    user_id: str = Field(..., alias="user_id")
    user_name: str = Field(..., alias="user_name")
    vol: Decimal
    pnl: Decimal
    profile_image: Optional[str] = Field(None, alias="profile_image")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("vol", "pnl", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class Balance(BaseModel):
    """Wallet balance."""
    collateral: Decimal = Field(..., description="USDC balance")
    tokens: dict[str, Decimal] = Field(default_factory=dict, description="Token ID -> balance")

    @field_validator("collateral", mode="before")
    @classmethod
    def validate_collateral(cls, v: Any) -> Decimal:
        """Convert collateral to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")

    @field_validator("tokens", mode="before")
    @classmethod
    def validate_tokens(cls, v: Any) -> dict[str, Decimal]:
        """Convert token balances to Decimal."""
        if not isinstance(v, dict):
            return {}

        result = {}
        for token_id, balance in v.items():
            if isinstance(balance, Decimal):
                result[token_id] = balance
            elif isinstance(balance, str):
                result[token_id] = Decimal(balance)
            elif isinstance(balance, (int, float)):
                result[token_id] = Decimal(str(balance))
            else:
                result[token_id] = Decimal("0.0")
        return result


# Market Data Models
class Market(BaseModel):
    """Market information."""
    id: str
    question: str
    slug: str
    condition_id: str
    category: str
    outcomes: list[str]
    outcome_prices: list[Decimal]
    volume: Decimal
    liquidity: Decimal
    active: bool
    closed: bool
    tokens: Optional[list[str]] = None  # ERC1155 token IDs for each outcome
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    # Additional fields from official Polymarket agents repo
    rewards_min_size: Optional[Decimal] = Field(None, alias="rewardsMinSize", description="Minimum size for rewards")
    rewards_max_spread: Optional[Decimal] = Field(None, alias="rewardsMaxSpread", description="Maximum spread for rewards")
    ticker: Optional[str] = Field(None, description="Short ticker/code for market")
    new: Optional[bool] = Field(None, description="Newly created market flag")
    featured: Optional[bool] = Field(None, description="Featured market flag")
    restricted: Optional[bool] = Field(None, description="Geographic/access restrictions")
    archived: Optional[bool] = Field(None, description="Archived/deprecated market")

    # Neg-risk CTF adapter fields
    neg_risk: Optional[bool] = Field(None, alias="negRisk", description="Negative risk market (mutually exclusive outcomes)")
    enable_neg_risk: Optional[bool] = Field(None, alias="enableNegRisk", description="Neg-risk enabled for this market")
    neg_risk_augmented: Optional[bool] = Field(None, alias="negRiskAugmented", description="Augmented neg-risk (incomplete outcome universe)")

    @field_validator("outcomes", mode="before")
    @classmethod
    def parse_outcomes(cls, v: Any) -> list[str]:
        """Parse outcomes from JSON string if needed."""
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def parse_outcome_prices(cls, v: Any) -> list[Decimal]:
        """Parse outcome prices from JSON string or list, convert to Decimal."""
        if isinstance(v, str):
            import json
            prices = json.loads(v)
        else:
            prices = v

        result = []
        for p in prices:
            if isinstance(p, Decimal):
                result.append(p)
            elif isinstance(p, str):
                result.append(Decimal(p))
            elif isinstance(p, (int, float)):
                result.append(Decimal(str(p)))
            else:
                result.append(Decimal("0.0"))
        return result

    @field_validator("volume", "liquidity", mode="before")
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if v is None:
            return Decimal("0.0")
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            return Decimal("0.0")

    @field_validator("rewards_min_size", "rewards_max_spread", mode="before")
    @classmethod
    def validate_optional_numeric(cls, v: Any) -> Optional[Decimal]:
        """Convert optional numeric fields to Decimal."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            return None

    @field_validator("tokens", mode="before")
    @classmethod
    def parse_tokens(cls, v: Any) -> Optional[list[str]]:
        """Parse tokens from JSON string if needed."""
        if v is None:
            return None
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    model_config = ConfigDict(populate_by_name=True)


class Event(BaseModel):
    """Event information (group of related markets)."""
    id: str
    slug: str
    title: str
    description: Optional[str] = None
    ticker: Optional[str] = Field(None, description="Short ticker/code for event")

    # Status flags
    active: bool
    closed: bool
    archived: bool
    new: Optional[bool] = Field(None, description="Newly created event flag")
    featured: Optional[bool] = Field(None, description="Featured event flag")
    restricted: Optional[bool] = Field(None, description="Geographic/access restrictions")

    # Timing
    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")

    # Markets in this event (FULL market objects, not just IDs!)
    markets: list["Market"] = Field(default_factory=list, description="Full market objects in this event")

    # Negative risk indicator
    neg_risk: Optional[bool] = Field(None, alias="negRisk", description="Negative risk event")

    @field_validator("markets", mode="before")
    @classmethod
    def parse_markets(cls, v: Any) -> list[Any]:
        """Parse markets from comma-separated string if needed."""
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v if v is not None else []

    model_config = ConfigDict(populate_by_name=True)


class OrderBook(BaseModel):
    """Order book for a token."""
    token_id: str
    bids: list[tuple[Decimal, Decimal]] = Field(default_factory=list, description="[(price, size)]")
    asks: list[tuple[Decimal, Decimal]] = Field(default_factory=list, description="[(price, size)]")
    market: Optional[str] = None
    tick_size: Optional[Decimal] = None
    neg_risk: Optional[bool] = None
    timestamp: Union[datetime, int] = Field(default_factory=lambda: datetime.utcnow())

    @field_validator("tick_size", mode="before")
    @classmethod
    def validate_tick_size(cls, v: Any) -> Optional[Decimal]:
        """Convert tick_size to Decimal."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            return None

    @property
    def best_bid(self) -> Optional[Decimal]:
        """Get best bid price."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        """Get best ask price."""
        return self.asks[0][0] if self.asks else None

    @property
    def midpoint(self) -> Optional[Decimal]:
        """Calculate midpoint price."""
        if self.best_bid is not None and self.best_ask is not None:
            mid = (self.best_bid + self.best_ask) / Decimal("2")
            return mid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return None

    @property
    def spread(self) -> Optional[Decimal]:
        """Calculate bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            spread = self.best_ask - self.best_bid
            return spread.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return None


# Configuration Models
class WalletConfig(BaseModel):
    """Wallet configuration."""
    private_key: str = Field(..., description="Wallet private key (hex)")
    address: Optional[str] = Field(None, description="Wallet address (derived if not provided)")
    signature_type: SignatureType = Field(default=SignatureType.EOA)
    funder: Optional[str] = Field(None, description="Funder address for proxy wallets")


class ClientConfig(BaseModel):
    """Client configuration."""
    chain_id: int = Field(default=137, description="Polygon chain ID")
    clob_url: str = Field(default="https://clob.polymarket.com", description="CLOB API URL")
    gamma_url: str = Field(default="https://gamma-api.polymarket.com", description="Gamma API URL")
    request_timeout: float = Field(default=30.0, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Max retry attempts")
    enable_rate_limiting: bool = Field(default=True, description="Enable rate limiting")


# Filter Models
class MarketFilters(BaseModel):
    """Filters for market queries."""
    limit: int = Field(default=100, le=1000)
    offset: int = Field(default=0, ge=0)
    active: Optional[bool] = None
    closed: Optional[bool] = None
    tag_id: Optional[int] = None
    slug: Optional[str] = None


class OrderFilters(BaseModel):
    """Filters for order queries."""
    market: Optional[str] = None
    asset_id: Optional[str] = None
    status: Optional[OrderStatus] = None


# WebSocket Models
class WebSocketMessage(BaseModel):
    """WebSocket message."""
    channel: str
    event: str
    data: dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
