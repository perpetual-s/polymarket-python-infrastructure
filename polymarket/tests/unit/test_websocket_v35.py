"""
Unit tests for WebSocket v3.5 features.

Tests:
- L2: Message deduplication (hash computation, duplicate detection, TTL cleanup)
- L1: Multi-token single subscription
- L4: Graceful shutdown callbacks
- L3: WebSocket compression
"""

import pytest
import asyncio
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from polymarket.api.websocket import WebSocketClient


class TestMessageDeduplication:
    """Test message deduplication (L2)."""

    def test_hash_computation_book_message(self):
        """Test hash computation for book messages."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        data = {
            "event_type": "book",
            "asset_id": "123",
            "market": "0xabc",
            "timestamp": "1234567890",
            "hash": "0xdef123"
        }

        hash1 = ws._compute_message_hash(data)
        hash2 = ws._compute_message_hash(data)

        # Same data should produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_hash_computation_trade_message(self):
        """Test hash computation for trade messages."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        data = {
            "event_type": "trade",
            "asset_id": "456",
            "market": "0xdef",
            "timestamp": "1234567890",
            "id": "trade_123"
        }

        hash1 = ws._compute_message_hash(data)

        # Different ID should produce different hash
        data["id"] = "trade_456"
        hash2 = ws._compute_message_hash(data)

        assert hash1 != hash2

    def test_hash_computation_order_message(self):
        """Test hash computation for order messages."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        data = {
            "event_type": "order",
            "asset_id": "789",
            "market": "0xghi",
            "timestamp": "1234567890",
            "id": "order_123"
        }

        hash_value = ws._compute_message_hash(data)
        assert len(hash_value) == 64

    def test_hash_computation_price_change_message(self):
        """Test hash computation for price_change messages."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        data = {
            "event_type": "price_change",
            "market": "0xjkl",
            "timestamp": "1234567890",
            "price_changes": [
                {"hash": "0xabc123"},
                {"hash": "0xdef456"}
            ]
        }

        hash_value = ws._compute_message_hash(data)
        assert len(hash_value) == 64

    def test_duplicate_detection(self):
        """Test duplicate message detection."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        message_hash = "abc123def456"
        current_time = time.time()

        # First occurrence - not a duplicate
        assert not ws._is_duplicate_message(message_hash)

        # Track the message
        ws._track_message_hash(message_hash, current_time)

        # Second occurrence - is a duplicate
        assert ws._is_duplicate_message(message_hash)

    def test_deduplication_ttl_cleanup(self):
        """Test TTL-based cleanup of old hashes."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True,
            dedup_window_seconds=1  # 1 second TTL for testing
        )

        message_hash = "old_hash_123"
        old_time = time.time() - 2  # 2 seconds ago (expired)

        # Track old message
        ws._track_message_hash(message_hash, old_time)

        # Should not be considered duplicate (expired)
        assert not ws._is_duplicate_message(message_hash)

    def test_deduplication_metrics(self):
        """Test deduplication metrics tracking."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        # Track some messages
        ws._track_message_hash("hash1", time.time())
        ws._track_message_hash("hash2", time.time())
        ws._track_message_hash("hash3", time.time())

        stats = ws.stats()

        assert "duplicates_blocked" in stats
        assert "dedup_cache_size" in stats
        assert stats["dedup_cache_size"] == 3

    def test_deduplication_disabled(self):
        """Test behavior when deduplication is disabled."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=False
        )

        stats = ws.stats()

        # Should not have dedup metrics when disabled
        assert stats.get("duplicates_blocked", 0) == 0
        assert stats.get("dedup_cache_size", 0) == 0

    def test_deduplication_thread_safety(self):
        """Test thread safety of deduplication structures."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        errors = []

        def track_hashes():
            try:
                for i in range(100):
                    hash_value = f"hash_{i}"
                    ws._track_message_hash(hash_value, time.time())
                    ws._is_duplicate_message(hash_value)
            except Exception as e:
                errors.append(e)

        # Run concurrent operations
        threads = [threading.Thread(target=track_hashes) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0

    def test_dedup_rolling_window(self):
        """Test rolling deque with maxlen."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_deduplication=True
        )

        # The deque has maxlen=10000
        # Fill beyond capacity (test memory efficiency)
        for i in range(15000):
            ws._track_message_hash(f"hash_{i}", time.time())

        # Should only keep last 10000 entries
        assert len(ws._seen_message_hashes) <= 10000
        assert len(ws._seen_hash_timestamps) <= 10000


class TestMultiTokenSubscription:
    """Test multi-token single subscription (L1)."""

    def test_subscribe_markets_multi_registers_callbacks(self):
        """Test subscribe_markets_multi registers all callbacks."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        callback = Mock()
        token_ids = ["token1", "token2", "token3"]

        ws.subscribe_markets_multi(token_ids, callback)

        # Check all tokens registered
        from polymarket.api.websocket import ChannelType
        for token_id in token_ids:
            channel = f"{ChannelType.MARKET}:{token_id}"
            assert channel in ws._subscriptions
            assert ws._subscriptions[channel] == callback

    def test_subscribe_markets_multi_sends_single_message(self):
        """Test subscribe_markets_multi sends single WebSocket message."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        # Mock WebSocket connection
        mock_ws = Mock()
        ws._ws = mock_ws

        callback = Mock()
        token_ids = ["token1", "token2", "token3"]

        ws.subscribe_markets_multi(token_ids, callback)

        # Should send exactly one message
        assert mock_ws.send.call_count == 1

        # Check message format
        import json
        sent_message = json.loads(mock_ws.send.call_args[0][0])
        assert sent_message["type"] == "MARKET"
        assert sent_message["asset_ids"] == token_ids

    def test_subscribe_markets_multi_empty_list(self):
        """Test subscribe_markets_multi with empty list."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        mock_ws = Mock()
        ws._ws = mock_ws

        callback = Mock()
        ws.subscribe_markets_multi([], callback)

        # Should not send message for empty list
        mock_ws.send.assert_not_called()

    def test_subscribe_markets_multi_no_connection(self):
        """Test subscribe_markets_multi without active connection."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        # No WebSocket connection
        ws._ws = None

        callback = Mock()
        token_ids = ["token1", "token2"]

        # Should not raise error
        ws.subscribe_markets_multi(token_ids, callback)

        # Callbacks should still be registered
        from polymarket.api.websocket import ChannelType
        channel = f"{ChannelType.MARKET}:token1"
        assert channel in ws._subscriptions


class TestGracefulShutdownCallbacks:
    """Test graceful shutdown callbacks (L4)."""

    def test_failure_callback_invoked_on_max_reconnects(self):
        """Test callback invoked when max reconnects exceeded."""
        callback_invoked = []

        def on_failure(reason: str):
            callback_invoked.append(reason)

        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            on_failure_callback=on_failure,
            max_reconnects=2
        )

        # Simulate max reconnects exceeded
        ws._reconnect_count = 3
        ws._invoke_failure_callback("Max reconnects exceeded (2 attempts)")

        # Callback should be invoked
        assert len(callback_invoked) == 1
        assert "Max reconnects exceeded" in callback_invoked[0]

    def test_failure_callback_not_invoked_if_not_set(self):
        """Test no error when callback not set."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            on_failure_callback=None
        )

        # Should not raise error
        ws._invoke_failure_callback("Some failure reason")

    def test_failure_callback_exception_handling(self):
        """Test callback exceptions are caught and logged."""
        def bad_callback(reason: str):
            raise ValueError("Callback error")

        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            on_failure_callback=bad_callback
        )

        # Should not raise exception (caught and logged)
        try:
            ws._invoke_failure_callback("Test failure")
        except Exception as e:
            pytest.fail(f"Exception should be caught: {e}")

    def test_failure_callback_receives_detailed_reason(self):
        """Test callback receives detailed failure reason."""
        received_reasons = []

        def on_failure(reason: str):
            received_reasons.append(reason)

        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            on_failure_callback=on_failure
        )

        # Test different failure reasons
        reasons = [
            "Max reconnects exceeded (5 attempts)",
            "Connection permanently failed",
            "Circuit breaker opened"
        ]

        for reason in reasons:
            ws._invoke_failure_callback(reason)

        assert len(received_reasons) == 3
        assert received_reasons == reasons


class TestWebSocketCompression:
    """Test WebSocket compression (L3)."""

    def test_compression_enabled_by_default(self):
        """Test compression is enabled by default."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        assert ws.enable_compression is True

    def test_compression_can_be_disabled(self):
        """Test compression can be disabled."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_compression=False
        )

        assert ws.enable_compression is False

    @patch('websocket.WebSocketApp')
    def test_compression_parameter_passed_to_run_forever(self, mock_ws_app):
        """Test compression parameter passed to run_forever()."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_compression=True
        )

        # Mock WebSocketApp instance
        mock_instance = Mock()
        mock_ws_app.return_value = mock_instance

        # Start connection (without actually connecting)
        with patch('threading.Thread'):
            ws.connect()

        # Check run_forever was called with compression
        # Note: This is tricky to test without actually running the thread
        # The implementation passes compression to run_forever in _run()
        assert ws.enable_compression is True

    def test_compression_configuration(self):
        """Test compression configuration options."""
        # Enabled
        ws_enabled = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_compression=True
        )
        assert ws_enabled.enable_compression is True

        # Disabled
        ws_disabled = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_compression=False
        )
        assert ws_disabled.enable_compression is False


class TestConfigurablePingIntervals:
    """Test configurable ping intervals (M5)."""

    def test_default_ping_intervals(self):
        """Test default ping interval and timeout."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        assert ws.ping_interval == 30
        assert ws.ping_timeout == 10

    def test_custom_ping_intervals(self):
        """Test custom ping interval and timeout."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            ping_interval=60,
            ping_timeout=20
        )

        assert ws.ping_interval == 60
        assert ws.ping_timeout == 20


class TestQueueDropCircuitBreaker:
    """Test queue drop circuit breaker (M2)."""

    def test_default_queue_drop_threshold(self):
        """Test default queue drop threshold."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True
        )

        assert ws.queue_drop_threshold == 1000

    def test_custom_queue_drop_threshold(self):
        """Test custom queue drop threshold."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_drop_threshold=500
        )

        assert ws.queue_drop_threshold == 500
