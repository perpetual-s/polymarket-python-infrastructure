"""
Type definitions for Polymarket client.

Uses Pydantic for runtime validation and type safety.
DECIMAL PRECISION: All numeric types use Decimal for financial-grade accuracy.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

__all__ = [
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
]


class Side(str, Enum):
    """Order side."""

    BUY = "BUY"
    SELL = "SELL"


class PublicDataStatus(str, Enum):
    """Outcome of a versioned public-data request."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"


class PublicRequestEvidenceV1(BaseModel):
    """Exact evidence for one versioned public HTTP facade call.

    ``wall_time_ms`` includes local limiter waits, retry backoff, and transport
    time.  ``rate_limit_error_count`` counts observed ``RateLimitError``
    exceptions only; it deliberately does not claim that a long call was
    throttled.
    """

    wall_time_ms: Decimal = Field(..., ge=0)
    attempt_count: int = Field(..., ge=0)
    retry_count: int = Field(..., ge=0)
    rate_limit_error_count: int = Field(..., ge=0)
    rate_limit_key: str = Field(..., min_length=1)
    retry_enabled: bool
    max_retries: int = Field(..., ge=0)
    local_limiter_enabled: bool
    local_limiter_applied: bool

    @field_validator("wall_time_ms", mode="before")
    @classmethod
    def validate_wall_time(cls, value: Any) -> Decimal:
        try:
            wall_time = value if isinstance(value, Decimal) else Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("wall_time_ms must be a finite decimal") from exc
        if not wall_time.is_finite():
            raise ValueError("wall_time_ms must be a finite decimal")
        return wall_time

    @model_validator(mode="after")
    def validate_counts(self) -> "PublicRequestEvidenceV1":
        if self.retry_count != max(0, self.attempt_count - 1):
            raise ValueError("retry_count must equal attempt_count - 1")
        if self.rate_limit_error_count > self.attempt_count:
            raise ValueError("rate_limit_error_count cannot exceed attempt_count")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count cannot exceed max_retries")
        if self.local_limiter_applied and not self.local_limiter_enabled:
            raise ValueError("a disabled local limiter cannot be applied")
        if self.local_limiter_applied and self.attempt_count == 0:
            raise ValueError("a local limiter cannot be applied without an attempt")
        if not self.retry_enabled and self.max_retries != 0:
            raise ValueError("disabled retries require max_retries=0")
        return self


def _validate_public_result_truth(
    *,
    status: PublicDataStatus,
    error: Optional[str],
    raw_count: int,
    parsed_count: int,
    parse_loss_count: int,
    parsed_items: int,
    parse_complete: bool,
    source_complete: Optional[bool],
) -> None:
    """Reject contradictory metadata in versioned public-data evidence models."""
    if raw_count != parsed_count + parse_loss_count:
        raise ValueError("raw_count must equal parsed_count + parse_loss_count")
    if parsed_count != parsed_items:
        raise ValueError("parsed_count must equal the number of parsed items")

    if status == PublicDataStatus.SUCCESS:
        if error is not None:
            raise ValueError("successful results cannot carry an error")
        if parse_complete != (parse_loss_count == 0):
            raise ValueError("parse_complete must reflect parse_loss_count on success")
    else:
        if not error:
            raise ValueError("non-success results must carry an error")
        if raw_count or parsed_count or parse_loss_count or parsed_items:
            raise ValueError("non-success results cannot claim parsed response rows")
        if parse_complete:
            raise ValueError("non-success results cannot be parse-complete")
        if source_complete is not False:
            raise ValueError("non-success results must be explicitly incomplete")

    if parse_loss_count and source_complete is not False:
        raise ValueError("parse loss must make source completeness false")
    if source_complete is True and (
        status != PublicDataStatus.SUCCESS or not parse_complete
    ):
        raise ValueError("complete source coverage requires parsed success")


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

    LIVE = "live"  # Active on exchange
    PENDING = "pending"  # Being processed
    FILLED = "filled"  # Completely filled
    MATCHED = "matched"  # Matched (legacy/alias for filled)
    CANCELLED = "cancelled"  # User cancelled
    EXPIRED = "expired"  # Good-till-date expired
    REJECTED = "rejected"  # Order rejected by exchange

    # Legacy statuses (may appear in older data)
    DELAYED = "delayed"  # Processing delayed
    UNMATCHED = "unmatched"  # Not matched

    @classmethod
    def normalize(cls, value: Any) -> "OrderStatus":
        """Normalize documented and legacy CLOB order-status spellings."""
        raw = getattr(value, "value", value)
        text = str(raw).strip().lower()
        if text.startswith("order_status_"):
            text = text.removeprefix("order_status_")
        aliases = {
            "canceled": "cancelled",
            "canceled_market_resolved": "cancelled",
            "cancelled_market_resolved": "cancelled",
            "invalid": "rejected",
            "matched": "matched",
            "filled": "filled",
        }
        return cls(aliases.get(text, text))


class SignatureType(int, Enum):
    """Wallet signature type."""

    EOA = 0  # Externally Owned Account (MetaMask, hardware wallet)
    POLY_PROXY = 1  # Polymarket proxy wallet
    GNOSIS_SAFE = 2  # Gnosis Safe wallet
    POLY_1271 = 3  # New deposit wallet

    # Backward-compatible aliases retained for older configuration values.
    MAGIC = 1
    PROXY = 2


# Request Models
class OrderRequest(BaseModel):
    """Order placement request."""

    # NOTE: Removed use_enum_values=True to keep enums as enums (not auto-convert to strings)
    # model_config = ConfigDict(use_enum_values=True)

    token_id: str = Field(..., description="ERC1155 token ID")
    price: Decimal = Field(
        ...,
        gt=Decimal("0"),
        lt=Decimal("1"),
        description="Order price; per-market tick bounds are applied before signing",
    )
    size: Decimal = Field(..., gt=0, description="Number of tokens/contracts to buy or sell")
    side: Side = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.GTC, description="Order type")
    expiration: Optional[int] = Field(None, description="Unix timestamp for GTD orders")

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: Any) -> Decimal:
        """Convert to an exact Decimal; live tick/side decides normalization."""
        if isinstance(v, Decimal):
            dec = v
        elif isinstance(v, str):
            dec = Decimal(v)
        elif isinstance(v, (int, float)):
            dec = Decimal(str(v))  # Convert via string to avoid float precision loss
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")

        if not dec.is_finite():
            raise ValueError("Price must be finite")
        return dec

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
    """
    Market order request.

    CRITICAL: `amount` has DIFFERENT semantics based on side:
    - BUY: amount = USD to spend
    - SELL: amount = tokens (shares) to sell

    This matches official py-clob-client MarketOrderArgs behavior.
    """

    model_config = ConfigDict(use_enum_values=True)

    token_id: str = Field(..., description="ERC1155 token ID")
    amount: Decimal = Field(..., gt=0, description="BUY: USD to spend | SELL: tokens to sell")
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
    # ``False`` means an unsuccessful response may still identify an accepted
    # order (for example duplicate/delayed/unknown batch results). ``True`` is
    # reserved for exchange responses that prove the item did not land.
    definitive_rejection: Optional[bool] = None
    order_hashes: Optional[list[str]] = None
    # V2: successful FAK/FOK matches return trade IDs (poll trades by ID for
    # settlement hashes); prefer these over legacy order_hashes.
    trade_ids: Optional[list[str]] = None

    model_config = ConfigDict(use_enum_values=True)


class Order(BaseModel):
    """Authenticated CLOB order from ``/data/orders`` or ``/data/order/{id}``."""

    id: str
    market: str
    token_id: str = Field(validation_alias=AliasChoices("asset_id", "token_id"))
    price: Decimal
    original_size: Decimal = Field(validation_alias=AliasChoices("original_size", "size"))
    size_matched: Decimal = Decimal("0")
    side: Side
    status: OrderStatus
    created_at: datetime
    expiration: Optional[datetime] = None
    owner: Optional[str] = None
    maker_address: Optional[str] = None
    outcome: Optional[str] = None
    order_type: Optional[str] = None
    associate_trades: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("price", "original_size", "size_matched", mode="before")
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

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: Any) -> OrderStatus:
        return OrderStatus.normalize(value)

    @property
    def asset_id(self) -> str:
        """Documented wire name, retained as a convenience alias."""
        return self.token_id

    @property
    def size(self) -> Decimal:
        """Backward-compatible alias for the original order size."""
        return self.original_size


class ClobMakerTrade(BaseModel):
    """One maker-order contribution in an authenticated CLOB trade."""

    order_id: str
    owner: Optional[str] = None
    maker_address: Optional[str] = None
    matched_amount: Decimal
    price: Decimal
    fee_rate_bps: int = 0
    asset_id: Optional[str] = None
    outcome: Optional[str] = None
    side: Optional[str] = None

    @field_validator("matched_amount", "price", mode="before")
    @classmethod
    def validate_decimal(cls, value: Any) -> Decimal:
        return Decimal(str(value))


class ClobTrade(BaseModel):
    """Authenticated execution record returned by ``GET /data/trades``."""

    id: str
    taker_order_id: str
    market: str
    asset_id: str
    side: str
    size: Decimal
    fee_rate_bps: int = 0
    price: Decimal
    status: str
    match_time: Optional[str] = None
    last_update: Optional[str] = None
    outcome: Optional[str] = None
    owner: Optional[str] = None
    maker_address: Optional[str] = None
    trader_side: Optional[str] = None
    transaction_hash: Optional[str] = None
    maker_orders: list[ClobMakerTrade] = Field(default_factory=list)

    @field_validator("size", "price", mode="before")
    @classmethod
    def validate_decimal(cls, value: Any) -> Decimal:
        return Decimal(str(value))


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
        "size",
        "avg_price",
        "current_value",
        "initial_value",
        "cur_price",
        "cash_pnl",
        "percent_pnl",
        "realized_pnl",
        "percent_realized_pnl",
        mode="before",
    )
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            # Handle empty strings and invalid values
            if not v or v.strip() in ("", "null", "None", "NaN", "nan"):
                return Decimal("0.0")
            try:
                return Decimal(v)
            except InvalidOperation:
                return Decimal("0.0")
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        elif v is None:
            return Decimal("0.0")
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class ClosedPosition(BaseModel):
    """Closed position from the public Data API (`GET /closed-positions`).

    Live-verified shape (2026-07-17): rows carry realized economics
    (avgPrice/totalBought/realizedPnl) and market identity — no open-position
    fields (size/currentValue/cashPnl), hence a distinct model from Position.
    """

    proxy_wallet: str = Field(..., alias="proxyWallet")
    asset: str
    condition_id: str = Field(..., alias="conditionId")

    avg_price: Decimal = Field(..., alias="avgPrice")
    total_bought: Decimal = Field(..., alias="totalBought")
    realized_pnl: Decimal = Field(..., alias="realizedPnl")
    cur_price: Decimal = Field(default=Decimal("0.0"), alias="curPrice")

    title: Optional[str] = None
    slug: Optional[str] = None
    icon: Optional[str] = None
    event_slug: Optional[str] = Field(None, alias="eventSlug")
    outcome: Optional[str] = None
    outcome_index: Optional[int] = Field(None, alias="outcomeIndex")
    opposite_outcome: Optional[str] = Field(None, alias="oppositeOutcome")
    opposite_asset: Optional[str] = Field(None, alias="oppositeAsset")
    end_date: Optional[str] = Field(None, alias="endDate")
    timestamp: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator(
        "avg_price", "total_bought", "realized_pnl", "cur_price", mode="before"
    )
    @classmethod
    def validate_numeric(cls, v: Any) -> Decimal:
        """Convert numeric fields to Decimal."""
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            if not v or v.strip() in ("", "null", "None", "NaN", "nan"):
                return Decimal("0.0")
            try:
                return Decimal(v)
            except InvalidOperation:
                return Decimal("0.0")
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


class DataTradeV1(BaseModel):
    """Documented public Data API ``/trades`` row (contract v1)."""

    proxy_wallet: str = Field(..., alias="proxyWallet", pattern=r"^0x[0-9a-fA-F]{40}$")
    side: Side
    asset: str = Field(..., min_length=1)
    condition_id: str = Field(..., alias="conditionId", min_length=1)
    size: Decimal = Field(..., gt=0)
    price: Decimal = Field(..., ge=0, le=1)
    timestamp: int = Field(..., ge=0)
    # Live global-feed evidence (2026-07-17): neg-risk conversion legs arrive
    # with outcome "" and outcomeIndex 999 — real attributed flow, so the
    # truthful row contract accepts an empty outcome label.
    outcome: str
    outcome_index: int = Field(..., alias="outcomeIndex", ge=0)
    transaction_hash: str = Field(..., alias="transactionHash", min_length=1)

    title: Optional[str] = None
    slug: Optional[str] = None
    icon: Optional[str] = None
    event_slug: Optional[str] = Field(None, alias="eventSlug")
    name: Optional[str] = None
    pseudonym: Optional[str] = None
    bio: Optional[str] = None
    profile_image: Optional[str] = Field(None, alias="profileImage")
    profile_image_optimized: Optional[str] = Field(None, alias="profileImageOptimized")

    # Preserve additional source fields while API-B measures the live contract.
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, extra="allow")

    @field_validator("size", "price", mode="before")
    @classmethod
    def validate_exact_numeric(cls, v: Any) -> Decimal:
        """Keep source decimal values exact and finite."""
        try:
            if isinstance(v, Decimal):
                value = v
            elif isinstance(v, str):
                value = Decimal(v)
            elif isinstance(v, (int, float)):
                value = Decimal(str(v))
            else:
                raise ValueError(f"Cannot convert {type(v)} to Decimal")
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {v!r}") from exc
        if not value.is_finite():
            raise ValueError(f"Decimal value must be finite: {v!r}")
        return value


class DataTradesQueryV1(BaseModel):
    """Exact effective query sent to public Data API ``/trades``."""

    user: Optional[str] = Field(None, pattern=r"^0x[0-9a-fA-F]{40}$")
    market: Optional[str] = Field(None, min_length=1)
    event_id: Optional[str] = Field(None, alias="eventId", min_length=1)
    start: Optional[int] = Field(None, ge=0)
    end: Optional[int] = Field(None, ge=0)
    side: Optional[Side] = None
    taker_only: bool = Field(True, alias="takerOnly")
    filter_type: Optional[Literal["CASH", "TOKENS"]] = Field(None, alias="filterType")
    filter_amount: Optional[float] = Field(
        None, alias="filterAmount", ge=0, allow_inf_nan=False
    )
    limit: int = Field(100, ge=1, le=10_000)
    offset: int = Field(0, ge=0)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @model_validator(mode="after")
    def validate_range(self) -> "DataTradesQueryV1":
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be less than or equal to end")
        return self


class DataTradesCoverageV1(BaseModel):
    """Mechanical completeness evidence for one bounded Data trades page."""

    explicit_time_bounds: bool
    first_page: bool
    timestamps_within_bounds: Optional[bool] = None
    page_full: bool


class DataTradesResultV1(BaseModel):
    """Truthful typed result for public Data API ``/trades``."""

    contract_version: Literal["v1"] = "v1"
    source: Literal["data.trades"] = "data.trades"
    query: DataTradesQueryV1
    status: PublicDataStatus
    error: Optional[str] = None
    http_status: Optional[int] = Field(None, ge=100, le=599)
    request: PublicRequestEvidenceV1
    coverage: DataTradesCoverageV1
    trades: list[DataTradeV1] = Field(default_factory=list)
    raw_count: int = Field(0, ge=0)
    parsed_count: int = Field(0, ge=0)
    parse_loss_count: int = Field(0, ge=0)
    parse_complete: bool = False
    source_complete: Optional[bool] = None

    @model_validator(mode="after")
    def validate_result_truth(self) -> "DataTradesResultV1":
        _validate_public_result_truth(
            status=self.status,
            error=self.error,
            raw_count=self.raw_count,
            parsed_count=self.parsed_count,
            parse_loss_count=self.parse_loss_count,
            parsed_items=len(self.trades),
            parse_complete=self.parse_complete,
            source_complete=self.source_complete,
        )
        if self.status == PublicDataStatus.SUCCESS and self.request.attempt_count == 0:
            raise ValueError("successful results require at least one request attempt")

        expected_bounds = self.query.start is not None and self.query.end is not None
        expected_first_page = self.query.offset == 0
        expected_page_full = self.raw_count >= self.query.limit
        if self.coverage.explicit_time_bounds != expected_bounds:
            raise ValueError("coverage.explicit_time_bounds contradicts the query")
        if self.coverage.first_page != expected_first_page:
            raise ValueError("coverage.first_page contradicts the query offset")
        if self.coverage.page_full != expected_page_full:
            raise ValueError("coverage.page_full contradicts the response count")
        if self.status == PublicDataStatus.SUCCESS and self.raw_count > self.query.limit:
            raise ValueError("successful response rows cannot exceed the query limit")

        if self.status != PublicDataStatus.SUCCESS:
            expected_timestamps_in_bounds = None
        elif expected_bounds:
            expected_timestamps_in_bounds = all(
                self.query.start <= trade.timestamp <= self.query.end
                for trade in self.trades
            )
        else:
            expected_timestamps_in_bounds = None
        if self.coverage.timestamps_within_bounds != expected_timestamps_in_bounds:
            raise ValueError("coverage.timestamps_within_bounds contradicts parsed rows")

        if self.status != PublicDataStatus.SUCCESS or not self.parse_complete:
            expected_complete: Optional[bool] = False
        elif expected_bounds and expected_first_page:
            expected_complete = bool(
                expected_timestamps_in_bounds and not expected_page_full
            )
        else:
            expected_complete = None
        if self.source_complete is not expected_complete:
            raise ValueError("source_complete contradicts bounded page evidence")
        return self


class MarketTradeEventV1(BaseModel):
    """Typed public CLOB market-trade event with exact financial values."""

    user: str = Field(..., pattern=r"^0x[0-9a-fA-F]{40}$")
    condition_id: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("condition_id", "conditionId", "market"),
    )
    asset_id: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("asset_id", "assetId", "asset"),
    )
    side: Side
    size: Decimal = Field(..., gt=0)
    price: Decimal = Field(..., ge=0, le=1)
    timestamp: int = Field(..., ge=0)
    transaction_hash: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("transaction_hash", "transactionHash"),
    )

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, extra="allow")

    @model_validator(mode="before")
    @classmethod
    def project_official_nested_shape(cls, value: Any) -> Any:
        """Project the official nested wire shape onto the stable V1 fields.

        The public client returns condition/asset metadata under ``market`` and
        the observed actor address under ``user``.  Copy before normalizing so
        callers' raw dictionaries are never mutated, and discard the nested
        profile/market objects after extracting only the contract fields.
        """
        if not isinstance(value, Mapping):
            return value

        normalized = dict(value)
        user = normalized.get("user")
        if isinstance(user, Mapping):
            normalized["user"] = user.get("address")

        market = normalized.get("market")
        if isinstance(market, Mapping):
            def nested_identity(aliases: tuple[str, ...], label: str) -> Any:
                values = [market[key] for key in aliases if key in market]
                if not values or values[0] in (None, ""):
                    raise ValueError(f"nested market {label} is required")
                if any(value != values[0] for value in values[1:]):
                    raise ValueError(f"nested market {label} aliases conflict")
                return values[0]

            condition_id = nested_identity(
                ("condition_id", "conditionId"), "condition_id"
            )
            asset_id = nested_identity(("asset_id", "assetId", "asset"), "asset_id")

            for key in ("condition_id", "conditionId"):
                if key in normalized and normalized[key] != condition_id:
                    raise ValueError("flat condition_id conflicts with nested market")
                normalized.pop(key, None)
            for key in ("asset_id", "assetId", "asset"):
                if key in normalized and normalized[key] != asset_id:
                    raise ValueError("flat asset_id conflicts with nested market")
                normalized.pop(key, None)

            normalized["condition_id"] = condition_id
            normalized["asset_id"] = asset_id
            normalized.pop("market", None)

        return normalized

    @field_validator("size", "price", mode="before")
    @classmethod
    def validate_exact_numeric(cls, value: Any) -> Decimal:
        try:
            if isinstance(value, Decimal):
                decimal_value = value
            elif isinstance(value, str):
                decimal_value = Decimal(value)
            elif isinstance(value, (int, float)):
                decimal_value = Decimal(str(value))
            else:
                raise ValueError(f"Cannot convert {type(value)} to Decimal")
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {value!r}") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"Decimal value must be finite: {value!r}")
        return decimal_value


class MarketTradeEventsResultV1(BaseModel):
    """Truthful, typed, bounded result for public CLOB market-trade events."""

    contract_version: Literal["v1"] = "v1"
    source: Literal["clob.market_trades_events"] = "clob.market_trades_events"
    condition_id: str = Field(..., min_length=1)
    status: PublicDataStatus
    error: Optional[str] = None
    error_category: Optional[
        Literal["auth", "request", "non_list", "serialization", "bounds"]
    ] = None
    http_status: Optional[int] = Field(None, ge=100, le=599)
    request: PublicRequestEvidenceV1
    events: list[MarketTradeEventV1] = Field(default_factory=list)
    raw_count: int = Field(0, ge=0)
    parsed_count: int = Field(0, ge=0)
    parse_loss_count: int = Field(0, ge=0)
    parse_complete: bool = False
    source_complete: Optional[bool] = None
    max_event_count: Literal[1000] = 1000
    max_decoded_json_bytes: Literal[1048576] = 1_048_576
    decoded_json_bytes: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_result_truth(self) -> "MarketTradeEventsResultV1":
        _validate_public_result_truth(
            status=self.status,
            error=self.error,
            raw_count=self.raw_count,
            parsed_count=self.parsed_count,
            parse_loss_count=self.parse_loss_count,
            parsed_items=len(self.events),
            parse_complete=self.parse_complete,
            source_complete=self.source_complete,
        )
        if self.status == PublicDataStatus.SUCCESS and self.request.attempt_count == 0:
            raise ValueError("successful results require at least one request attempt")
        if self.status == PublicDataStatus.SUCCESS:
            if self.error_category is not None:
                raise ValueError("successful event results forbid error_category")
        elif self.error_category is None:
            raise ValueError("failed event results require error_category")
        expected_complete = (
            None
            if self.status == PublicDataStatus.SUCCESS and self.parse_complete
            else False
        )
        if self.source_complete is not expected_complete:
            raise ValueError(
                "market-event source_complete must stay unknown without retention proof"
            )
        if self.status == PublicDataStatus.SUCCESS:
            if self.decoded_json_bytes is None:
                raise ValueError("successful event results require decoded_json_bytes")
            if self.raw_count > self.max_event_count:
                raise ValueError("successful event results exceed max_event_count")
            if self.decoded_json_bytes > self.max_decoded_json_bytes:
                raise ValueError("successful event results exceed max_decoded_json_bytes")
            if any(event.condition_id != self.condition_id for event in self.events):
                raise ValueError("event condition_id must match the requested condition")
        return self


class ActivityType(str, Enum):
    """Onchain activity types."""

    TRADE = "TRADE"
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    REDEEM = "REDEEM"
    REWARD = "REWARD"
    CONVERSION = "CONVERSION"
    MAKER_REBATE = "MAKER_REBATE"
    TAKER_REBATE = "TAKER_REBATE"  # Observed live 2026-07-17 (fee-era rebates)
    REFERRAL_REWARD = "REFERRAL_REWARD"  # Observed live 2026-07-28
    YIELD = "YIELD"  # Interest/staking rewards


class Activity(BaseModel):
    """
    Onchain activity record from Data API /activity endpoint.

    Note: Many fields are nullable depending on activity type.
    TRADE activities have side, price, conditionId populated.
    YIELD activities may have nulls for market-specific fields.
    """

    # Required fields
    timestamp: int
    type: ActivityType
    transaction_hash: str = Field(..., alias="transactionHash")
    size: Decimal
    usdc_size: Decimal = Field(..., alias="usdcSize")

    # Wallet info
    proxy_wallet: Optional[str] = Field(None, alias="proxyWallet")

    # Market context (nullable for non-TRADE activities)
    condition_id: Optional[str] = Field(None, alias="conditionId")
    asset: Optional[str] = None
    title: Optional[str] = None
    outcome: Optional[str] = None
    outcome_index: Optional[int] = Field(None, alias="outcomeIndex")
    slug: Optional[str] = None
    event_slug: Optional[str] = Field(None, alias="eventSlug")
    icon: Optional[str] = None

    # Trade-specific (optional)
    side: Optional[Side] = None
    price: Optional[Decimal] = None

    # User profile (optional)
    name: Optional[str] = None
    pseudonym: Optional[str] = None
    bio: Optional[str] = None
    profile_image: Optional[str] = Field(None, alias="profileImage")

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("side", mode="before")
    @classmethod
    def coerce_empty_side(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("size", "usdc_size", "price", mode="before")
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
    """
    Market token holder from Data API /holders endpoint.

    Note: API returns nested structure { token: str, holders: [Holder] }.
    The get_holders method flattens this and adds token_id to each holder.
    """

    proxy_wallet: str = Field(..., alias="proxyWallet")
    amount: Decimal
    outcome_index: int = Field(..., alias="outcomeIndex")

    # Token info (added by parser from parent structure)
    token_id: Optional[str] = None
    asset: Optional[str] = None

    # Profile info (all optional)
    pseudonym: Optional[str] = None
    name: Optional[str] = None
    bio: Optional[str] = None
    profile_image: Optional[str] = Field(None, alias="profileImage")
    profile_image_optimized: Optional[str] = Field(None, alias="profileImageOptimized")
    display_username_public: bool = Field(False, alias="displayUsernamePublic")
    verified: bool = False

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
    user_id: str = Field(..., validation_alias=AliasChoices("user_id", "proxyWallet"))
    user_name: str = Field(..., validation_alias=AliasChoices("user_name", "userName"))
    vol: Decimal
    pnl: Decimal
    profile_image: Optional[str] = Field(
        None, validation_alias=AliasChoices("profile_image", "profileImage")
    )
    x_username: Optional[str] = Field(None, alias="xUsername")
    verified_badge: Optional[bool] = Field(None, alias="verifiedBadge")

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

    collateral: Decimal = Field(..., description="pUSD collateral balance")
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
class FeeSchedule(BaseModel):
    """Gamma's economic taker-fee schedule for one market."""

    rate: Decimal = Field(..., ge=0, lt=1)
    exponent: Decimal = Field(..., gt=0)
    taker_only: bool = Field(default=True, alias="takerOnly")
    rebate_rate: Decimal = Field(default=Decimal("0"), alias="rebateRate", ge=0, lt=1)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("rate", "exponent", "rebate_rate", mode="before")
    @classmethod
    def validate_decimal(cls, value: Any) -> Decimal:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("fee schedule values must be finite")
        return decimal_value


class FeeInfo(BaseModel):
    """Complete fee metadata needed by sizing and P&L code."""

    base_fee_bps: int = Field(..., ge=0)
    rate_bps: int = Field(..., ge=0)
    exponent: Decimal = Field(default=Decimal("1"), gt=0)
    taker_only: bool = True
    rebate_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)


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
    rewards_min_size: Optional[Decimal] = Field(
        None, alias="rewardsMinSize", description="Minimum size for rewards"
    )
    rewards_max_spread: Optional[Decimal] = Field(
        None, alias="rewardsMaxSpread", description="Maximum spread for rewards"
    )
    ticker: Optional[str] = Field(None, description="Short ticker/code for market")
    new: Optional[bool] = Field(None, description="Newly created market flag")
    featured: Optional[bool] = Field(None, description="Featured market flag")
    restricted: Optional[bool] = Field(None, description="Geographic/access restrictions")
    archived: Optional[bool] = Field(None, description="Archived/deprecated market")

    # Neg-risk CTF adapter fields
    neg_risk: Optional[bool] = Field(
        None, alias="negRisk", description="Negative risk market (mutually exclusive outcomes)"
    )
    enable_neg_risk: Optional[bool] = Field(
        None, alias="enableNegRisk", description="Neg-risk enabled for this market"
    )
    neg_risk_augmented: Optional[bool] = Field(
        None,
        alias="negRiskAugmented",
        description="Augmented neg-risk (incomplete outcome universe)",
    )
    neg_risk_market_id: Optional[str] = Field(
        None, alias="negRiskMarketID", description="Neg-risk CTF adapter market ID"
    )
    neg_risk_request_id: Optional[str] = Field(
        None, alias="negRiskRequestID", description="Neg-risk CTF adapter request ID"
    )

    # Grouped market fields (CRITICAL for correct resolution dates)
    group_item_title: Optional[str] = Field(
        None, alias="groupItemTitle", description="Resolution date/title for grouped markets"
    )
    group_item_threshold: Optional[int] = Field(
        None, alias="groupItemThreshold", description="Ordering threshold for grouped markets"
    )

    # Trading state fields
    best_bid: Optional[Decimal] = Field(None, alias="bestBid", description="Current best bid price")
    best_ask: Optional[Decimal] = Field(None, alias="bestAsk", description="Current best ask price")
    spread: Optional[Decimal] = Field(None, description="Current bid-ask spread")
    last_trade_price: Optional[Decimal] = Field(
        None, alias="lastTradePrice", description="Last trade price"
    )
    competitive: Optional[Decimal] = Field(None, description="Market competitiveness score (0-1)")

    # Trading constraints
    order_min_size: Optional[Decimal] = Field(
        None, alias="orderMinSize", description="Minimum order size in USDC"
    )
    order_price_min_tick_size: Optional[Decimal] = Field(
        None, alias="orderPriceMinTickSize", description="Minimum price tick size"
    )
    accepting_orders: Optional[bool] = Field(
        None, alias="acceptingOrders", description="Whether market is accepting orders"
    )
    fees_enabled: Optional[bool] = Field(
        None, alias="feesEnabled", description="Whether this market charges trading fees"
    )
    taker_base_fee: Optional[int] = Field(
        None, alias="takerBaseFee", description="CLOB protocol base fee value"
    )
    fee_schedule: Optional[FeeSchedule] = Field(
        None, alias="feeSchedule", description="Economic taker-fee curve"
    )

    # UMA oracle fields
    question_id: Optional[str] = Field(
        None, alias="questionID", description="UMA oracle question ID"
    )
    uma_bond: Optional[Decimal] = Field(None, alias="umaBond", description="UMA bond amount")
    uma_reward: Optional[Decimal] = Field(None, alias="umaReward", description="UMA reward amount")
    resolution_source: Optional[str] = Field(
        None, alias="resolutionSource", description="URL/source for market resolution"
    )

    # Time-windowed volumes
    volume_24h: Optional[Decimal] = Field(
        None, alias="volume24hr", description="24-hour trading volume"
    )
    volume_1wk: Optional[Decimal] = Field(
        None, alias="volume1wk", description="1-week trading volume"
    )
    volume_1mo: Optional[Decimal] = Field(
        None, alias="volume1mo", description="1-month trading volume"
    )

    # Server-computed price changes (Gamma).
    one_hour_price_change: Optional[Decimal] = Field(
        None, alias="oneHourPriceChange", description="1-hour price change"
    )
    one_day_price_change: Optional[Decimal] = Field(
        None, alias="oneDayPriceChange", description="1-day price change"
    )

    # Creator/resolver fields
    submitted_by: Optional[str] = Field(
        None, alias="submitted_by", description="Address that submitted the market"
    )
    resolved_by: Optional[str] = Field(
        None, alias="resolvedBy", description="Address that resolves the market"
    )

    # Date tracking
    has_reviewed_dates: Optional[bool] = Field(
        None, alias="hasReviewedDates", description="Whether dates have been reviewed"
    )

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

    @field_validator(
        "rewards_min_size",
        "rewards_max_spread",
        "best_bid",
        "best_ask",
        "spread",
        "last_trade_price",
        "competitive",
        "order_min_size",
        "order_price_min_tick_size",
        "uma_bond",
        "uma_reward",
        "volume_24h",
        "volume_1wk",
        "volume_1mo",
        "one_hour_price_change",
        "one_day_price_change",
        mode="before",
    )
    @classmethod
    def validate_optional_numeric(cls, v: Any) -> Optional[Decimal]:
        """Convert optional numeric fields to Decimal."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            try:
                return Decimal(v)
            except Exception:
                return None
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


class ResolutionPayouts(BaseModel):
    """Validated terminal payout vector for a resolved CTF condition.

    Returned only by :meth:`PolymarketClient.get_resolution_payouts` once the
    raw CLOB market payload has passed every validation rule: exact
    condition-id match, ``closed is True``, unique token ids, and boolean
    winner flags. It is never built from an unvalidated payload -- callers
    get either this (settled truth) or ``None`` (not yet known), never an
    inferred payout.
    """

    condition_id: str = Field(..., min_length=1)
    payouts: dict[str, Decimal]
    kind: Literal["winner", "fifty_fifty"]

    @model_validator(mode="after")
    def _validate_payout_shape(self) -> "ResolutionPayouts":
        """Reject any payout vector the documented resolution kinds cannot produce."""
        if not self.payouts:
            raise ValueError("resolution payouts must include at least one token")
        if self.kind == "fifty_fifty":
            if any(value != Decimal("0.5") for value in self.payouts.values()):
                raise ValueError(
                    "fifty_fifty resolutions must pay every token exactly 0.5"
                )
        else:
            winners = sum(1 for value in self.payouts.values() if value == Decimal("1"))
            losers = sum(1 for value in self.payouts.values() if value == Decimal("0"))
            if winners != 1 or winners + losers != len(self.payouts):
                raise ValueError(
                    "winner resolutions must pay exactly one token 1.0 and the rest 0.0"
                )
        return self


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
    markets: list["Market"] = Field(
        default_factory=list, description="Full market objects in this event"
    )

    # Negative risk indicator
    neg_risk: Optional[bool] = Field(None, alias="negRisk", description="Negative risk event")

    # Volume and liquidity (from /events/pagination endpoint)
    volume: float = Field(0.0, description="Total event volume in USD")
    liquidity: float = Field(0.0, description="Total event liquidity in USD")
    volume_24h: Optional[float] = Field(None, alias="volume24hr", description="24h volume")

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
    timestamp: Union[datetime, int] = Field(default_factory=lambda: datetime.now(timezone.utc))

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
            return (self.best_bid + self.best_ask) / Decimal("2")
        return None

    @property
    def spread(self) -> Optional[Decimal]:
        """Calculate bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            spread = self.best_ask - self.best_bid
            return spread.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return None


class PricePoint(BaseModel):
    """One point of CLOB /prices-history: unix-seconds timestamp + price."""

    timestamp: int = Field(alias="t", description="Unix timestamp (seconds)")
    price: Decimal = Field(alias="p", description="Outcome-token price")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: Any) -> Decimal:
        if isinstance(v, Decimal):
            return v
        elif isinstance(v, str):
            return Decimal(v)
        elif isinstance(v, (int, float)):
            return Decimal(str(v))
        else:
            raise ValueError(f"Cannot convert {type(v)} to Decimal")


class PriceHistoryPointV1(BaseModel):
    """Strict point contract for versioned CLOB price-history evidence."""

    timestamp: int = Field(alias="t", ge=0, description="Unix timestamp (seconds)")
    price: Decimal = Field(alias="p", ge=0, le=1, description="Outcome-token price")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: Any) -> Decimal:
        try:
            if isinstance(v, Decimal):
                value = v
            elif isinstance(v, str):
                value = Decimal(v)
            elif isinstance(v, (int, float)):
                value = Decimal(str(v))
            else:
                raise ValueError(f"Cannot convert {type(v)} to Decimal")
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {v!r}") from exc
        if not value.is_finite():
            raise ValueError(f"Decimal value must be finite: {v!r}")
        return value


class PriceHistoryQueryV1(BaseModel):
    """Exact query provenance for public CLOB ``/prices-history``."""

    token_id: str = Field(..., min_length=1)
    interval: Optional[Literal["max", "1w", "1d", "6h", "1h"]] = None
    start_ts: Optional[int] = Field(None, ge=0)
    end_ts: Optional[int] = Field(None, ge=0)
    fidelity: Optional[int] = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "PriceHistoryQueryV1":
        if self.interval and (self.start_ts is not None or self.end_ts is not None):
            raise ValueError("interval is mutually exclusive with start_ts/end_ts")
        if (
            self.start_ts is not None
            and self.end_ts is not None
            and self.start_ts > self.end_ts
        ):
            raise ValueError("start_ts must be less than or equal to end_ts")
        return self


class PriceHistoryCoverageV1(BaseModel):
    """Observed timestamp-grid coverage for an explicit price-history range."""

    explicit_range: bool
    fidelity_seconds: Optional[int] = Field(None, gt=0)
    observed_start_ts: Optional[int] = Field(None, ge=0)
    observed_end_ts: Optional[int] = Field(None, ge=0)
    timestamps_ordered: Optional[bool] = None
    duplicate_timestamp_count: int = Field(0, ge=0)
    out_of_range_count: int = Field(0, ge=0)
    maximum_gap_seconds: Optional[int] = Field(None, ge=0)
    start_boundary_covered: Optional[bool] = None
    end_boundary_covered: Optional[bool] = None
    full_bucket_coverage: Optional[bool] = None


class PriceHistoryResultV1(BaseModel):
    """Truthful typed result for public CLOB price history."""

    contract_version: Literal["v1"] = "v1"
    source: Literal["clob.prices_history"] = "clob.prices_history"
    query: PriceHistoryQueryV1
    status: PublicDataStatus
    error: Optional[str] = None
    http_status: Optional[int] = Field(None, ge=100, le=599)
    request: PublicRequestEvidenceV1
    coverage: PriceHistoryCoverageV1
    points: list[PriceHistoryPointV1] = Field(default_factory=list)
    raw_count: int = Field(0, ge=0)
    parsed_count: int = Field(0, ge=0)
    parse_loss_count: int = Field(0, ge=0)
    parse_complete: bool = False
    range_complete: Optional[bool] = None

    @model_validator(mode="after")
    def validate_result_truth(self) -> "PriceHistoryResultV1":
        _validate_public_result_truth(
            status=self.status,
            error=self.error,
            raw_count=self.raw_count,
            parsed_count=self.parsed_count,
            parse_loss_count=self.parse_loss_count,
            parsed_items=len(self.points),
            parse_complete=self.parse_complete,
            source_complete=self.range_complete,
        )
        if self.status == PublicDataStatus.SUCCESS and self.request.attempt_count == 0:
            raise ValueError("successful results require at least one request attempt")

        explicit_range = self.query.start_ts is not None and self.query.end_ts is not None
        fidelity_seconds = (
            self.query.fidelity * 60 if self.query.fidelity is not None else None
        )
        if self.coverage.explicit_range != explicit_range:
            raise ValueError("coverage.explicit_range contradicts the query")
        if self.coverage.fidelity_seconds != fidelity_seconds:
            raise ValueError("coverage.fidelity_seconds contradicts the query")

        if self.status == PublicDataStatus.SUCCESS:
            timestamps = [point.timestamp for point in self.points]
            unique_timestamps = sorted(set(timestamps))
            observed_start = unique_timestamps[0] if unique_timestamps else None
            observed_end = unique_timestamps[-1] if unique_timestamps else None
            timestamps_ordered: Optional[bool] = timestamps == sorted(timestamps)
            duplicate_count = len(timestamps) - len(unique_timestamps)
            maximum_gap = (
                max(
                    right - left
                    for left, right in zip(unique_timestamps, unique_timestamps[1:])
                )
                if len(unique_timestamps) > 1
                else 0 if unique_timestamps else None
            )
            if explicit_range:
                out_of_range_count = sum(
                    timestamp < self.query.start_ts or timestamp > self.query.end_ts
                    for timestamp in timestamps
                )
            else:
                out_of_range_count = 0

            if explicit_range and fidelity_seconds is not None:
                start_covered: Optional[bool] = bool(
                    observed_start is not None
                    and self.query.start_ts <= observed_start
                    and observed_start - self.query.start_ts <= fidelity_seconds
                )
                end_covered: Optional[bool] = bool(
                    observed_end is not None
                    and observed_end <= self.query.end_ts
                    and self.query.end_ts - observed_end <= fidelity_seconds
                )
                full_bucket_coverage: Optional[bool] = bool(
                    self.parse_complete
                    and timestamps_ordered
                    and duplicate_count == 0
                    and out_of_range_count == 0
                    and start_covered
                    and end_covered
                    and maximum_gap is not None
                    and maximum_gap <= fidelity_seconds
                )
            else:
                start_covered = None
                end_covered = None
                full_bucket_coverage = None
        else:
            observed_start = None
            observed_end = None
            timestamps_ordered = None
            duplicate_count = 0
            out_of_range_count = 0
            maximum_gap = None
            start_covered = None
            end_covered = None
            full_bucket_coverage = None

        expected_coverage = PriceHistoryCoverageV1(
            explicit_range=explicit_range,
            fidelity_seconds=fidelity_seconds,
            observed_start_ts=observed_start,
            observed_end_ts=observed_end,
            timestamps_ordered=timestamps_ordered,
            duplicate_timestamp_count=duplicate_count,
            out_of_range_count=out_of_range_count,
            maximum_gap_seconds=maximum_gap,
            start_boundary_covered=start_covered,
            end_boundary_covered=end_covered,
            full_bucket_coverage=full_bucket_coverage,
        )
        if self.coverage != expected_coverage:
            raise ValueError("coverage contradicts price-history points")

        if self.status != PublicDataStatus.SUCCESS or not self.parse_complete:
            expected_complete: Optional[bool] = False
        elif explicit_range and fidelity_seconds is not None:
            expected_complete = bool(full_bucket_coverage)
        else:
            expected_complete = None
        if self.range_complete is not expected_complete:
            raise ValueError("range_complete contradicts timestamp-grid coverage")
        return self


# Configuration Models
class WalletConfig(BaseModel):
    """Wallet configuration."""

    private_key: SecretStr = Field(..., description="Wallet private key (hex)")
    address: Optional[str] = Field(None, description="Wallet address (derived if not provided)")
    signature_type: SignatureType = Field(default=SignatureType.EOA)
    funder: Optional[str] = Field(None, description="Funder address for proxy wallets")


# NOTE: a stale ClientConfig model was removed; PolymarketSettings in
# config.py is the only client configuration surface.


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
