"""
Typed models for CLOB WebSocket messages.

Based on official Polymarket API specifications:
- User channel: https://docs.polymarket.com/developers/CLOB/websocket/user-channel
- Market channel: https://docs.polymarket.com/developers/CLOB/websocket/market-channel

All field types match official API (string for numeric values, as per Polymarket convention).
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Union
from decimal import Decimal


# ========== Enums ==========

class CLOBEventType(str, Enum):
    """WebSocket message event types."""
    BOOK = "book"
    TRADE = "trade"
    ORDER = "order"
    PRICE_CHANGE = "price_change"
    TICK_SIZE_CHANGE = "tick_size_change"
    LAST_TRADE_PRICE = "last_trade_price"


class TradeStatus(str, Enum):
    """Trade execution status."""
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"


class OrderEventType(str, Enum):
    """Order event types."""
    PLACEMENT = "PLACEMENT"
    UPDATE = "UPDATE"
    CANCELLATION = "CANCELLATION"


# ========== Market Channel Messages ==========

@dataclass
class OrderLevel:
    """Single orderbook level (price + size)."""
    price: str  # Decimal as string
    size: str   # Decimal as string

    def to_decimal(self) -> tuple[Decimal, Decimal]:
        """Convert to Decimal for calculations."""
        return Decimal(self.price), Decimal(self.size)


@dataclass
class OrderbookMessage:
    """
    Orderbook snapshot message.

    Sent on initial subscription and when trades affect the orderbook.
    """
    event_type: str  # "book"
    asset_id: str
    market: str
    timestamp: str  # Unix milliseconds
    hash: str
    buys: List[OrderLevel]
    sells: List[OrderLevel]

    @property
    def best_bid(self) -> Optional[Decimal]:
        """Best bid price."""
        if not self.buys:
            return None
        return Decimal(self.buys[0].price)

    @property
    def best_ask(self) -> Optional[Decimal]:
        """Best ask price."""
        if not self.sells:
            return None
        return Decimal(self.sells[0].price)

    @property
    def spread(self) -> Optional[Decimal]:
        """Bid-ask spread."""
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid


@dataclass
class PriceChange:
    """Single price change in orderbook."""
    asset_id: str
    price: str
    size: str  # New aggregate size at this level
    side: str  # "BUY" or "SELL"
    hash: str
    best_bid: str
    best_ask: str


@dataclass
class PriceChangeMessage:
    """
    Price change message.

    Sent when orders are placed or cancelled.

    Schema versions:
    - v1 (deprecated Sept 15, 2025): Root-level asset_id/hash, 'changes' array
    - v2 (current): Nested structure, 'price_changes' array with best_bid/best_ask

    This implementation supports v2 only (post-migration format).
    """
    event_type: str  # "price_change"
    market: str
    timestamp: str
    price_changes: List[PriceChange]
    schema_version: str = "v2"  # Track schema version for monitoring


@dataclass
class TickSizeChangeMessage:
    """
    Tick size change message.

    Sent when minimum tick size adjusts (price > 0.96 or < 0.04).
    """
    event_type: str  # "tick_size_change"
    asset_id: str
    market: str
    old_tick_size: str
    new_tick_size: str
    side: str  # "buy" or "sell"
    timestamp: str


@dataclass
class LastTradePriceMessage:
    """
    Last trade price message.

    Sent when maker and taker orders match.
    """
    event_type: str  # "last_trade_price"
    asset_id: str
    market: str
    price: str
    side: str  # "BUY" or "SELL"
    size: str
    fee_rate_bps: str  # Fee rate in basis points
    timestamp: str  # Unix milliseconds


# ========== User Channel Messages ==========

@dataclass
class MakerOrder:
    """Maker order in a trade match."""
    asset_id: str
    matched_amount: str
    order_id: str
    outcome: str
    owner: str  # API key
    price: str


@dataclass
class TradeMessage:
    """
    Trade execution message.

    Sent when:
    - Market order matches
    - Limit order matches
    - Trade status changes (MATCHED → MINED → CONFIRMED, or RETRYING/FAILED)
    """
    event_type: str  # "trade"
    type: str  # "TRADE"
    id: str  # Trade ID
    asset_id: str
    market: str
    status: TradeStatus
    side: str  # "BUY" or "SELL"
    size: str
    price: str
    outcome: str
    owner: str  # API key owner
    trade_owner: str  # API key of trade owner
    taker_order_id: str
    maker_orders: List[MakerOrder]
    timestamp: str
    last_update: str
    matchtime: str


@dataclass
class OrderMessage:
    """
    Order event message.

    Sent on:
    - PLACEMENT: Order placed
    - UPDATE: Partial fill
    - CANCELLATION: Order cancelled
    """
    event_type: str  # "order"
    type: OrderEventType
    id: str  # Order ID
    asset_id: str
    market: str
    outcome: str
    side: str  # "BUY" or "SELL"
    price: str
    original_size: str  # Initial order size
    size_matched: str   # Amount matched so far
    owner: str          # Owner API key
    order_owner: str    # Order owner API key
    associate_trades: List[str]  # Related trade IDs
    timestamp: str


# ========== Message Union Type ==========

WebSocketMessage = Union[
    OrderbookMessage,
    PriceChangeMessage,
    TickSizeChangeMessage,
    LastTradePriceMessage,
    TradeMessage,
    OrderMessage
]


# ========== Message Parser ==========

def _detect_price_change_schema(data: dict) -> str:
    """
    Detect price_change message schema version.

    Schema evolution:
    - v1 (pre-Sept 15, 2025): 'changes' array, root-level asset_id/hash
    - v2 (post-Sept 15, 2025): 'price_changes' array, nested asset_id/hash/best_bid/best_ask

    Args:
        data: Raw price_change message dict

    Returns:
        "v1" - Legacy format (DEPRECATED, should not occur after Sept 15, 2025)
        "v2" - Current format (expected format)
        "unknown" - Unrecognized structure

    Note:
        v1 is not supported. If detected, parsing will fail with clear error message.
    """
    # Check for v1 indicators (deprecated)
    has_changes = "changes" in data
    has_root_asset_id = "asset_id" in data
    has_price_changes = "price_changes" in data

    if has_changes and not has_price_changes:
        return "v1"  # Legacy format
    elif has_price_changes:
        return "v2"  # Current format
    else:
        return "unknown"


def _validate_required_fields(data: dict, required_fields: list[str], message_type: str) -> None:
    """
    Validate that all required fields are present in message data.

    Args:
        data: Raw message dict from WebSocket
        required_fields: List of required field names
        message_type: Message type name for error reporting

    Raises:
        ValueError: If any required fields are missing
    """
    missing = [f for f in required_fields if f not in data]
    if missing:
        present_fields = list(data.keys())
        raise ValueError(
            f"Missing required fields in {message_type}: {missing}. "
            f"Present fields: {present_fields}"
        )


def parse_websocket_message(data: dict) -> Optional[WebSocketMessage]:
    """
    Parse raw WebSocket message into typed model.

    Args:
        data: Raw message dict from WebSocket

    Returns:
        Typed message or None if unknown type

    Raises:
        ValueError: If message structure is invalid (includes field details)
    """
    event_type = data.get("event_type")

    if not event_type:
        return None

    try:
        # Market channel messages
        if event_type == CLOBEventType.BOOK:
            _validate_required_fields(
                data,
                ["asset_id", "market", "timestamp", "hash"],
                "OrderbookMessage"
            )
            # Parse order levels
            buys = [OrderLevel(**level) for level in data.get("buys", [])]
            sells = [OrderLevel(**level) for level in data.get("sells", [])]

            # CRITICAL: Polymarket WebSocket may return bids LOW→HIGH and asks HIGH→LOW
            # We need: buys (bids) HIGH→LOW (best bid first), sells (asks) LOW→HIGH (best ask first)
            buys.sort(key=lambda x: Decimal(x.price), reverse=True)
            sells.sort(key=lambda x: Decimal(x.price))

            return OrderbookMessage(
                event_type=event_type,
                asset_id=data["asset_id"],
                market=data["market"],
                timestamp=data["timestamp"],
                hash=data["hash"],
                buys=buys,
                sells=sells
            )

        elif event_type == CLOBEventType.PRICE_CHANGE:
            # Detect schema version (defensive check for deprecated format)
            schema_version = _detect_price_change_schema(data)

            if schema_version == "v1":
                raise ValueError(
                    "Received legacy price_change format (v1) with 'changes' array. "
                    "This format was deprecated on September 15, 2025 at 11 PM UTC. "
                    "Current implementation only supports v2 format with 'price_changes' array. "
                    "Please ensure your Polymarket WebSocket connection is up to date."
                )
            elif schema_version == "unknown":
                present_fields = list(data.keys())
                raise ValueError(
                    f"Unknown price_change schema format. "
                    f"Expected 'price_changes' array (v2 format). "
                    f"Present fields: {present_fields}"
                )

            # Validate v2 format fields
            _validate_required_fields(
                data,
                ["market", "timestamp", "price_changes"],
                "PriceChangeMessage"
            )

            return PriceChangeMessage(
                event_type=event_type,
                market=data["market"],
                timestamp=data["timestamp"],
                price_changes=[PriceChange(**pc) for pc in data["price_changes"]],
                schema_version=schema_version
            )

        elif event_type == CLOBEventType.TICK_SIZE_CHANGE:
            _validate_required_fields(
                data,
                ["asset_id", "market", "old_tick_size", "new_tick_size", "side", "timestamp"],
                "TickSizeChangeMessage"
            )
            return TickSizeChangeMessage(
                event_type=event_type,
                asset_id=data["asset_id"],
                market=data["market"],
                old_tick_size=data["old_tick_size"],
                new_tick_size=data["new_tick_size"],
                side=data["side"],
                timestamp=data["timestamp"]
            )

        elif event_type == CLOBEventType.LAST_TRADE_PRICE:
            _validate_required_fields(
                data,
                ["asset_id", "market", "price", "side", "size", "fee_rate_bps", "timestamp"],
                "LastTradePriceMessage"
            )
            return LastTradePriceMessage(
                event_type=event_type,
                asset_id=data["asset_id"],
                market=data["market"],
                price=data["price"],
                side=data["side"],
                size=data["size"],
                fee_rate_bps=data["fee_rate_bps"],
                timestamp=data["timestamp"]
            )

        # User channel messages
        elif event_type == CLOBEventType.TRADE:
            _validate_required_fields(
                data,
                ["type", "id", "asset_id", "market", "status", "side", "size", "price",
                 "outcome", "owner", "trade_owner", "taker_order_id", "timestamp",
                 "last_update", "matchtime"],
                "TradeMessage"
            )
            return TradeMessage(
                event_type=event_type,
                type=data["type"],
                id=data["id"],
                asset_id=data["asset_id"],
                market=data["market"],
                status=TradeStatus(data["status"]),
                side=data["side"],
                size=data["size"],
                price=data["price"],
                outcome=data["outcome"],
                owner=data["owner"],
                trade_owner=data["trade_owner"],
                taker_order_id=data["taker_order_id"],
                maker_orders=[MakerOrder(**mo) for mo in data.get("maker_orders", [])],
                timestamp=data["timestamp"],
                last_update=data["last_update"],
                matchtime=data["matchtime"]
            )

        elif event_type == CLOBEventType.ORDER:
            _validate_required_fields(
                data,
                ["type", "id", "asset_id", "market", "outcome", "side", "price",
                 "original_size", "size_matched", "owner", "order_owner", "timestamp"],
                "OrderMessage"
            )
            return OrderMessage(
                event_type=event_type,
                type=OrderEventType(data["type"]),
                id=data["id"],
                asset_id=data["asset_id"],
                market=data["market"],
                outcome=data["outcome"],
                side=data["side"],
                price=data["price"],
                original_size=data["original_size"],
                size_matched=data["size_matched"],
                owner=data["owner"],
                order_owner=data["order_owner"],
                associate_trades=data.get("associate_trades", []),
                timestamp=data["timestamp"]
            )

        else:
            # Unknown event type
            return None

    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"Failed to parse WebSocket message: {e}") from e
