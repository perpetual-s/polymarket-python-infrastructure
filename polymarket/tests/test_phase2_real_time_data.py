"""
Tests for Phase 2: Real-Time Data Client

Tests RealTimeDataClient functionality:
- Connection management
- Subscription handling
- Message parsing
- Auto-reconnect
- Ping/pong mechanism
- Stream helpers
"""

import pytest
import time
import json
from unittest.mock import Mock, patch, MagicMock
from shared.polymarket.api.real_time_data import (
    RealTimeDataClient,
    ConnectionStatus,
    ClobApiKeyCreds,
    Subscription,
    Message,
    StreamHelpers
)


class TestRealTimeDataClientInit:
    """Test client initialization."""

    def test_client_creation_with_defaults(self):
        """Test client with default settings."""
        client = RealTimeDataClient()
        assert client.host == RealTimeDataClient.DEFAULT_HOST
        assert client.ping_interval == RealTimeDataClient.DEFAULT_PING_INTERVAL
        assert client.auto_reconnect is True

    def test_client_creation_with_custom_host(self):
        """Test client with custom host."""
        custom_host = "wss://custom.example.com"
        client = RealTimeDataClient(host=custom_host)
        assert client.host == custom_host

    def test_client_creation_with_callbacks(self):
        """Test client with callbacks."""
        on_connect = Mock()
        on_message = Mock()
        on_status = Mock()

        client = RealTimeDataClient(
            on_connect=on_connect,
            on_message=on_message,
            on_status_change=on_status
        )

        assert client.on_connect == on_connect
        assert client.on_custom_message == on_message
        assert client.on_status_change_callback == on_status

    def test_client_creation_with_custom_ping_interval(self):
        """Test client with custom ping interval."""
        client = RealTimeDataClient(ping_interval=10.0)
        assert client.ping_interval == 10.0

    def test_client_creation_with_auto_reconnect_disabled(self):
        """Test client with auto-reconnect disabled."""
        client = RealTimeDataClient(auto_reconnect=False)
        assert client.auto_reconnect is False


class TestConnectionManagement:
    """Test connection lifecycle."""

    def test_initial_status_is_disconnected(self):
        """Test initial status."""
        client = RealTimeDataClient()
        assert client._status == ConnectionStatus.DISCONNECTED

    def test_disconnect_sets_shutdown_flag(self):
        """Test disconnect sets shutdown flag."""
        client = RealTimeDataClient()
        client.disconnect()
        assert client._shutdown_requested is True
        assert client.auto_reconnect is False

    def test_status_change_callback_invoked(self):
        """Test status change callback."""
        status_changes = []

        def on_status(status):
            status_changes.append(status)

        client = RealTimeDataClient(on_status_change=on_status)
        client._notify_status_change(ConnectionStatus.CONNECTING)
        client._notify_status_change(ConnectionStatus.CONNECTED)

        assert len(status_changes) == 2
        assert status_changes[0] == ConnectionStatus.CONNECTING
        assert status_changes[1] == ConnectionStatus.CONNECTED


class TestSubscriptionHandling:
    """Test subscription methods."""

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_basic(self, mock_ws_class):
        """Test basic subscription."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        client.subscribe(topic="activity", type="trades")

        # Verify send was called
        assert mock_ws.send.called
        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["action"] == "subscribe"
        assert len(data["subscriptions"]) == 1
        assert data["subscriptions"][0]["topic"] == "activity"
        assert data["subscriptions"][0]["type"] == "trades"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_with_filters(self, mock_ws_class):
        """Test subscription with filters."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        filters = '{"market_slug":"trump-2024"}'
        client.subscribe(topic="activity", type="trades", filters=filters)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["filters"] == filters

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_with_clob_auth(self, mock_ws_class):
        """Test subscription with CLOB auth."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        clob_auth = ClobApiKeyCreds(
            key="test-key",
            secret="test-secret",
            passphrase="test-pass"
        )

        client.subscribe(topic="clob_user", type="order", clob_auth=clob_auth)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert "clob_auth" in data["subscriptions"][0]
        assert data["subscriptions"][0]["clob_auth"]["key"] == "test-key"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_unsubscribe(self, mock_ws_class):
        """Test unsubscribe."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        client.unsubscribe(topic="activity", type="trades")

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["action"] == "unsubscribe"


class TestMessageParsing:
    """Test message parsing."""

    def test_message_dataclass_creation(self):
        """Test Message dataclass."""
        message = Message(
            topic="activity",
            type="trades",
            timestamp=1234567890,
            payload={"price": 0.55},
            connection_id="conn-123"
        )

        assert message.topic == "activity"
        assert message.type == "trades"
        assert message.timestamp == 1234567890
        assert message.payload["price"] == 0.55
        assert message.connection_id == "conn-123"

    def test_on_message_parses_json(self):
        """Test _on_message parses JSON correctly."""
        received_messages = []

        def on_message(client, message):
            received_messages.append(message)

        client = RealTimeDataClient(on_message=on_message)

        raw_message = json.dumps({
            "topic": "activity",
            "type": "trades",
            "timestamp": 1234567890,
            "payload": {"price": 0.55, "size": 100},
            "connection_id": "conn-123"
        })

        client._on_message(None, raw_message)

        assert len(received_messages) == 1
        msg = received_messages[0]
        assert msg.topic == "activity"
        assert msg.type == "trades"
        assert msg.payload["price"] == 0.55

    def test_on_message_skips_non_json(self):
        """Test _on_message skips non-JSON messages."""
        received_messages = []

        def on_message(client, message):
            received_messages.append(message)

        client = RealTimeDataClient(on_message=on_message)

        # Send non-JSON message
        client._on_message(None, "ping")

        assert len(received_messages) == 0

    def test_on_message_skips_system_messages(self):
        """Test _on_message skips messages without payload."""
        received_messages = []

        def on_message(client, message):
            received_messages.append(message)

        client = RealTimeDataClient(on_message=on_message)

        # System message (no payload)
        raw_message = json.dumps({
            "status": "subscribed",
            "topic": "activity"
        })

        client._on_message(None, raw_message)

        assert len(received_messages) == 0


class TestStreamHelpers:
    """Test StreamHelpers convenience methods."""

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_market_trades(self, mock_ws_class):
        """Test market trades helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_market_trades(client, "trump-2024")

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "activity"
        assert data["subscriptions"][0]["type"] == "trades"
        filters = json.loads(data["subscriptions"][0]["filters"])
        assert filters["market_slug"] == "trump-2024"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_event_trades(self, mock_ws_class):
        """Test event trades helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_event_trades(client, "election-2024")

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        filters = json.loads(data["subscriptions"][0]["filters"])
        assert filters["event_slug"] == "election-2024"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_event_comments(self, mock_ws_class):
        """Test event comments helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_event_comments(client, event_id=100)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "comments"
        filters = json.loads(data["subscriptions"][0]["filters"])
        assert filters["parentEntityID"] == 100
        assert filters["parentEntityType"] == "Event"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_crypto_price(self, mock_ws_class):
        """Test crypto price helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_crypto_price(client, "btcusdt")

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "crypto_prices"
        assert data["subscriptions"][0]["type"] == "update"
        filters = json.loads(data["subscriptions"][0]["filters"])
        assert filters["symbol"] == "btcusdt"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_market_orderbook(self, mock_ws_class):
        """Test market orderbook helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        token_ids = ["token1", "token2"]
        StreamHelpers.subscribe_to_market_orderbook(client, token_ids)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "clob_market"
        assert data["subscriptions"][0]["type"] == "agg_orderbook"
        filters = json.loads(data["subscriptions"][0]["filters"])
        assert filters == token_ids

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_price_changes(self, mock_ws_class):
        """Test price changes helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        token_ids = ["token1"]
        StreamHelpers.subscribe_to_price_changes(client, token_ids)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "clob_market"
        assert data["subscriptions"][0]["type"] == "price_change"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_new_markets(self, mock_ws_class):
        """Test new markets helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_new_markets(client)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "clob_market"
        assert data["subscriptions"][0]["type"] == "market_created"

    @patch('shared.polymarket.api.real_time_data.websocket.WebSocketApp')
    def test_subscribe_to_market_resolutions(self, mock_ws_class):
        """Test market resolutions helper."""
        mock_ws = MagicMock()
        mock_ws.sock = MagicMock()
        mock_ws.sock.connected = True

        client = RealTimeDataClient()
        client.ws = mock_ws

        StreamHelpers.subscribe_to_market_resolutions(client)

        call_args = mock_ws.send.call_args[0][0]
        data = json.loads(call_args)

        assert data["subscriptions"][0]["topic"] == "clob_market"
        assert data["subscriptions"][0]["type"] == "market_resolved"


class TestDataTypes:
    """Test data types and dataclasses."""

    def test_clob_api_key_creds(self):
        """Test ClobApiKeyCreds dataclass."""
        creds = ClobApiKeyCreds(
            key="test-key",
            secret="test-secret",
            passphrase="test-pass"
        )

        assert creds.key == "test-key"
        assert creds.secret == "test-secret"
        assert creds.passphrase == "test-pass"

    def test_subscription_dataclass(self):
        """Test Subscription dataclass."""
        sub = Subscription(
            topic="activity",
            type="trades",
            filters='{"market_slug":"test"}',
            clob_auth=None
        )

        assert sub.topic == "activity"
        assert sub.type == "trades"
        assert sub.filters is not None

    def test_connection_status_enum(self):
        """Test ConnectionStatus enum."""
        assert ConnectionStatus.CONNECTING.value == "CONNECTING"
        assert ConnectionStatus.CONNECTED.value == "CONNECTED"
        assert ConnectionStatus.DISCONNECTED.value == "DISCONNECTED"


class TestErrorHandling:
    """Test error handling."""

    def test_subscribe_when_not_connected_logs_warning(self):
        """Test subscribe without connection."""
        client = RealTimeDataClient()
        # ws is None - not connected

        # Should not raise, just log warning
        client.subscribe("activity", "trades")
        # Test passes if no exception raised

    def test_unsubscribe_when_not_connected_logs_warning(self):
        """Test unsubscribe without connection."""
        client = RealTimeDataClient()

        # Should not raise, just log warning
        client.unsubscribe("activity", "trades")
        # Test passes if no exception raised

    def test_on_message_handles_invalid_json(self):
        """Test message handler with invalid JSON."""
        received_messages = []

        def on_message(client, message):
            received_messages.append(message)

        client = RealTimeDataClient(on_message=on_message)

        # Send invalid JSON
        client._on_message(None, "{invalid json")

        # Should not crash, just skip
        assert len(received_messages) == 0

    def test_on_message_callback_exception_handled(self):
        """Test exception in message callback is caught."""
        def bad_callback(client, message):
            raise ValueError("Test error")

        client = RealTimeDataClient(on_message=bad_callback)

        raw_message = json.dumps({
            "topic": "activity",
            "type": "trades",
            "timestamp": 1234567890,
            "payload": {"price": 0.55},
            "connection_id": "conn-123"
        })

        # Should not crash
        client._on_message(None, raw_message)
        # Test passes if no exception propagates


class TestIntegration:
    """Integration tests (require actual connection)."""

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires live WebSocket connection")
    def test_connect_and_disconnect(self):
        """Test actual connection (integration test)."""
        connected = []

        def on_connect(client):
            connected.append(True)
            # Disconnect after connect
            client.disconnect()

        client = RealTimeDataClient(on_connect=on_connect)
        client.connect()

        # Wait for connection
        time.sleep(2)

        assert len(connected) > 0

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires live WebSocket connection")
    def test_receive_crypto_price_updates(self):
        """Test receiving real crypto price updates."""
        messages = []

        def on_message(client, message):
            messages.append(message)
            if len(messages) >= 3:
                client.disconnect()

        client = RealTimeDataClient(
            on_connect=lambda c: StreamHelpers.subscribe_to_crypto_price(c, "btcusdt"),
            on_message=on_message
        )

        client.connect()

        # Wait for messages
        time.sleep(10)

        assert len(messages) > 0
        # Check message structure
        msg = messages[0]
        assert msg.topic == "crypto_prices"
        assert "value" in msg.payload


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
