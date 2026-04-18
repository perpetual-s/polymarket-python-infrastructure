"""
Unit tests for WebSocket message models.

Tests message parsing and validation for all CLOB WebSocket message types.
"""

import pytest
from polymarket.api.websocket_models import (
    parse_websocket_message,
    OrderbookMessage,
    PriceChangeMessage,
    TickSizeChangeMessage,
    LastTradePriceMessage,
    TradeMessage,
    OrderMessage,
    CLOBEventType,
    TradeStatus,
    OrderEventType,
)


class TestOrderbookMessage:
    """Test orderbook message parsing."""

    def test_parse_orderbook_message(self):
        """Test parsing book message."""
        data = {
            "event_type": "book",
            "asset_id": "123456",
            "market": "0xabc...",
            "timestamp": "1234567890000",
            "hash": "0x123...",
            "buys": [
                {"price": "0.48", "size": "30"},
                {"price": "0.47", "size": "50"}
            ],
            "sells": [
                {"price": "0.52", "size": "25"},
                {"price": "0.53", "size": "40"}
            ]
        }

        message = parse_websocket_message(data)

        assert isinstance(message, OrderbookMessage)
        assert message.event_type == "book"
        assert message.asset_id == "123456"
        assert message.market == "0xabc..."
        assert len(message.buys) == 2
        assert len(message.sells) == 2
        assert message.buys[0].price == "0.48"
        assert message.buys[0].size == "30"

    def test_orderbook_properties(self):
        """Test orderbook computed properties."""
        from decimal import Decimal

        data = {
            "event_type": "book",
            "asset_id": "123456",
            "market": "0xabc...",
            "timestamp": "1234567890000",
            "hash": "0x123...",
            "buys": [{"price": "0.48", "size": "30"}],
            "sells": [{"price": "0.52", "size": "25"}]
        }

        message = parse_websocket_message(data)

        assert message.best_bid == Decimal("0.48")
        assert message.best_ask == Decimal("0.52")
        assert message.spread == Decimal("0.04")


class TestTradeMessage:
    """Test trade message parsing."""

    def test_parse_trade_message(self):
        """Test parsing trade message."""
        data = {
            "event_type": "trade",
            "type": "TRADE",
            "id": "trade123",
            "asset_id": "123456",
            "market": "0xabc...",
            "status": "MATCHED",
            "side": "BUY",
            "size": "10",
            "price": "0.57",
            "outcome": "YES",
            "owner": "apikey1",
            "trade_owner": "apikey1",
            "taker_order_id": "order123",
            "maker_orders": [
                {
                    "asset_id": "123456",
                    "matched_amount": "10",
                    "order_id": "order456",
                    "outcome": "YES",
                    "owner": "apikey2",
                    "price": "0.57"
                }
            ],
            "timestamp": "1234567890000",
            "last_update": "1234567890000",
            "matchtime": "1234567890000"
        }

        message = parse_websocket_message(data)

        assert isinstance(message, TradeMessage)
        assert message.event_type == "trade"
        assert message.status == TradeStatus.MATCHED
        assert message.side == "BUY"
        assert message.price == "0.57"
        assert len(message.maker_orders) == 1
        assert message.maker_orders[0].order_id == "order456"


class TestOrderMessage:
    """Test order message parsing."""

    def test_parse_order_placement(self):
        """Test parsing order placement message."""
        data = {
            "event_type": "order",
            "type": "PLACEMENT",
            "id": "order123",
            "asset_id": "123456",
            "market": "0xabc...",
            "outcome": "YES",
            "side": "SELL",
            "price": "0.57",
            "original_size": "10",
            "size_matched": "0",
            "owner": "apikey1",
            "order_owner": "apikey1",
            "associate_trades": [],
            "timestamp": "1234567890000"
        }

        message = parse_websocket_message(data)

        assert isinstance(message, OrderMessage)
        assert message.event_type == "order"
        assert message.type == OrderEventType.PLACEMENT
        assert message.side == "SELL"
        assert message.original_size == "10"
        assert message.size_matched == "0"

    def test_parse_order_update(self):
        """Test parsing order update message (partial fill)."""
        data = {
            "event_type": "order",
            "type": "UPDATE",
            "id": "order123",
            "asset_id": "123456",
            "market": "0xabc...",
            "outcome": "YES",
            "side": "BUY",
            "price": "0.55",
            "original_size": "100",
            "size_matched": "50",
            "owner": "apikey1",
            "order_owner": "apikey1",
            "associate_trades": ["trade123", "trade456"],
            "timestamp": "1234567890000"
        }

        message = parse_websocket_message(data)

        assert isinstance(message, OrderMessage)
        assert message.type == OrderEventType.UPDATE
        assert message.size_matched == "50"
        assert len(message.associate_trades) == 2


class TestPriceChangeMessage:
    """Test price change message parsing."""

    def test_parse_price_change(self):
        """Test parsing price change message (v2 format)."""
        data = {
            "event_type": "price_change",
            "market": "0xabc...",
            "timestamp": "1234567890000",
            "price_changes": [
                {
                    "asset_id": "123456",
                    "price": "0.50",
                    "size": "100",
                    "side": "BUY",
                    "hash": "0x123...",
                    "best_bid": "0.48",
                    "best_ask": "0.52"
                }
            ]
        }

        message = parse_websocket_message(data)

        assert isinstance(message, PriceChangeMessage)
        assert message.event_type == "price_change"
        assert len(message.price_changes) == 1
        assert message.price_changes[0].price == "0.50"
        assert message.schema_version == "v2"

    def test_parse_price_change_v1_legacy_rejected(self):
        """Test that legacy v1 format is rejected with clear error."""
        # Legacy format (pre-Sept 15, 2025) - should be rejected
        data = {
            "event_type": "price_change",
            "market": "0xabc...",
            "timestamp": "1234567890000",
            "asset_id": "123456",  # Root level (v1)
            "hash": "0xdef",       # Root level (v1)
            "changes": [            # 'changes' not 'price_changes' (v1)
                {
                    "price": "0.65",
                    "size": "100",
                    "side": "BUY"
                }
            ]
        }

        with pytest.raises(ValueError) as exc_info:
            parse_websocket_message(data)

        error_msg = str(exc_info.value)
        assert "legacy price_change format (v1)" in error_msg.lower()
        assert "deprecated on September 15, 2025" in error_msg
        assert "v2 format" in error_msg

    def test_parse_price_change_unknown_format_rejected(self):
        """Test that unknown format is rejected."""
        data = {
            "event_type": "price_change",
            "market": "0xabc...",
            "timestamp": "1234567890000",
            # Missing both 'changes' and 'price_changes'
        }

        with pytest.raises(ValueError) as exc_info:
            parse_websocket_message(data)

        error_msg = str(exc_info.value)
        assert "unknown price_change schema format" in error_msg.lower()
        assert "expected 'price_changes' array" in error_msg.lower()


class TestTickSizeChangeMessage:
    """Test tick size change message parsing."""

    def test_parse_tick_size_change(self):
        """Test parsing tick size change message."""
        data = {
            "event_type": "tick_size_change",
            "asset_id": "123456",
            "market": "0xabc...",
            "old_tick_size": "0.01",
            "new_tick_size": "0.001",
            "side": "buy",
            "timestamp": "1234567890000"
        }

        message = parse_websocket_message(data)

        assert isinstance(message, TickSizeChangeMessage)
        assert message.event_type == "tick_size_change"
        assert message.old_tick_size == "0.01"
        assert message.new_tick_size == "0.001"


class TestLastTradePriceMessage:
    """Test last trade price message parsing."""

    def test_parse_last_trade_price(self):
        """Test parsing last trade price message."""
        data = {
            "event_type": "last_trade_price",
            "asset_id": "123456",
            "market": "0xabc...",
            "price": "0.55",
            "side": "BUY",
            "size": "100",
            "fee_rate_bps": "200",
            "timestamp": "1234567890000"
        }

        message = parse_websocket_message(data)

        assert isinstance(message, LastTradePriceMessage)
        assert message.event_type == "last_trade_price"
        assert message.price == "0.55"
        assert message.side == "BUY"
        assert message.fee_rate_bps == "200"


class TestMessageParsingErrors:
    """Test error handling in message parsing."""

    def test_unknown_event_type(self):
        """Test parsing unknown event type returns None."""
        data = {"event_type": "unknown_type"}
        message = parse_websocket_message(data)
        assert message is None

    def test_missing_event_type(self):
        """Test parsing message without event_type returns None."""
        data = {"some_field": "some_value"}
        message = parse_websocket_message(data)
        assert message is None

    def test_invalid_message_structure(self):
        """Test parsing malformed message raises ValueError."""
        data = {
            "event_type": "book",
            # Missing required fields
        }

        with pytest.raises(ValueError, match="Failed to parse WebSocket message"):
            parse_websocket_message(data)

    def test_invalid_enum_value(self):
        """Test parsing invalid enum value raises ValueError."""
        data = {
            "event_type": "trade",
            "type": "TRADE",
            "id": "trade123",
            "asset_id": "123456",
            "market": "0xabc...",
            "status": "INVALID_STATUS",  # Invalid enum value
            "side": "BUY",
            "size": "10",
            "price": "0.57",
            "outcome": "YES",
            "owner": "apikey1",
            "trade_owner": "apikey1",
            "taker_order_id": "order123",
            "maker_orders": [],
            "timestamp": "1234567890000",
            "last_update": "1234567890000",
            "matchtime": "1234567890000"
        }

        with pytest.raises(ValueError):
            parse_websocket_message(data)
