"""
Unit tests for WebSocket queue behavior (Phase 3.3).

Tests message queue, consumer task, and async processing functionality.
"""

import pytest
import asyncio
import queue
import time
from unittest.mock import Mock, patch, MagicMock
from polymarket.api.websocket import WebSocketClient


class TestQueueInitialization:
    """Test queue initialization and configuration."""

    def test_queue_enabled_by_default(self):
        """Test queue is enabled by default."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        assert ws._enable_queue is True
        assert ws._message_queue is not None
        assert isinstance(ws._message_queue, queue.Queue)
        assert ws._message_queue.maxsize == 10000

    def test_queue_disabled(self):
        """Test queue can be disabled."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=False
        )

        assert ws._enable_queue is False
        assert ws._message_queue is None

    def test_custom_queue_size(self):
        """Test custom queue size configuration."""
        ws = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            queue_maxsize=5000
        )

        assert ws._message_queue.maxsize == 5000


class TestMessageQueuing:
    """Test message queuing behavior."""

    @pytest.fixture
    def ws_client(self):
        """Create WebSocket client with queue enabled."""
        return WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_maxsize=10
        )

    def test_message_enqueued_when_enabled(self, ws_client):
        """Test messages are enqueued when queue is enabled."""
        # Mock WebSocket message
        mock_message = '{"event_type": "book", "asset_id": "123", "market": "0xabc", "timestamp": "123", "hash": "0x123", "buys": [], "sells": []}'

        # Simulate message receipt (call _on_message)
        with patch.object(ws_client, '_invoke_callback'):
            ws_client._on_message(None, mock_message)

        # Check queue has message
        assert ws_client._message_queue.qsize() == 1

    def test_queue_full_drops_message(self, ws_client):
        """Test queue full drops messages and increments counter."""
        # Fill queue
        for i in range(10):
            try:
                ws_client._message_queue.put_nowait({"test": i})
            except queue.Full:
                pass

        initial_drops = ws_client._queue_drops

        # Try to add one more (should drop)
        mock_message = '{"event_type": "book", "asset_id": "123", "market": "0xabc", "timestamp": "123", "hash": "0x123", "buys": [], "sells": []}'
        with patch.object(ws_client, '_invoke_callback'):
            ws_client._on_message(None, mock_message)

        # Check drop counter increased
        assert ws_client._queue_drops > initial_drops

    def test_message_processed_directly_when_queue_disabled(self):
        """Test messages processed directly when queue disabled."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=False
        )

        # Mock callback
        callback_called = False
        def test_callback(msg):
            nonlocal callback_called
            callback_called = True

        # Set up subscription (market:asset_id format - use how WebSocketClient formats it internally)
        from polymarket.api.websocket import ChannelType
        channel = f"{ChannelType.MARKET}:123"
        ws_client._subscriptions = {channel: test_callback}

        # Mock message
        mock_message = '{"event_type": "book", "asset_id": "123", "market": "0xabc", "timestamp": "123", "hash": "0x123", "buys": [], "sells": []}'

        # Process message
        ws_client._on_message(None, mock_message)

        # Callback should be called directly (no queue)
        assert callback_called is True


class TestConsumerTask:
    """Test consumer task functionality."""

    @pytest.mark.asyncio
    async def test_consumer_task_starts_with_event_loop(self):
        """Test consumer task starts when event loop provided."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True
        )

        # Get running event loop
        loop = asyncio.get_running_loop()

        # Mock websocket run_forever to prevent actual connection
        with patch('websocket.WebSocketApp.run_forever'):
            ws_client.connect(event_loop=loop)

            # Give consumer task a moment to start
            await asyncio.sleep(0.1)

            # Check consumer task is running
            assert ws_client._consumer_task is not None
            assert not ws_client._consumer_task.done()

            # Cleanup
            ws_client.disconnect()

    @pytest.mark.asyncio
    async def test_consumer_task_processes_messages(self):
        """Test consumer task processes queued messages."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_maxsize=10
        )

        # Setup callback
        messages_received = []
        def test_callback(msg):
            messages_received.append(msg)

        from polymarket.api.websocket import ChannelType
        channel = f"{ChannelType.MARKET}:123"
        ws_client._subscriptions = {channel: test_callback}

        # Get running event loop
        loop = asyncio.get_running_loop()

        # Start consumer task
        ws_client._running = True
        ws_client._event_loop = loop
        ws_client._consumer_task = loop.create_task(ws_client._consume_messages())

        # Enqueue test message
        test_data = {
            "event_type": "book",
            "asset_id": "123",
            "market": "0xabc",
            "timestamp": "123",
            "hash": "0x123",
            "buys": [],
            "sells": []
        }

        message_item = {
            "typed_message": None,
            "event_type": "book",
            "data": test_data,
            "processing_start": time.time(),
        }

        ws_client._message_queue.put_nowait(message_item)

        # Wait for consumer to process
        await asyncio.sleep(0.2)

        # Check callback was invoked
        assert len(messages_received) > 0

        # Cleanup
        ws_client._running = False
        if ws_client._consumer_task and not ws_client._consumer_task.done():
            ws_client._consumer_task.cancel()
            try:
                await ws_client._consumer_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_consumer_task_stops_on_disconnect(self):
        """Test consumer task stops when disconnecting."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True
        )

        loop = asyncio.get_running_loop()

        # Start consumer task
        ws_client._running = True
        ws_client._event_loop = loop
        ws_client._consumer_task = loop.create_task(ws_client._consume_messages())

        await asyncio.sleep(0.1)

        # Disconnect
        ws_client.disconnect()

        # Wait a bit
        await asyncio.sleep(0.1)

        # Check task is cancelled
        assert ws_client._consumer_task.done()


class TestQueueMetrics:
    """Test queue metrics and stats."""

    def test_stats_includes_queue_metrics_when_enabled(self):
        """Test stats() includes queue metrics when enabled."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_maxsize=1000
        )

        stats = ws_client.stats()

        assert "queue_enabled" in stats
        assert stats["queue_enabled"] is True
        assert "queue_size" in stats
        assert "queue_drops" in stats
        assert "consumer_task_running" in stats

    def test_stats_excludes_queue_when_disabled(self):
        """Test stats() excludes queue metrics when disabled."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=False
        )

        stats = ws_client.stats()

        assert stats["queue_enabled"] is False
        assert "queue_size" not in stats or stats.get("queue_size") is None

    def test_queue_drop_counter_increments(self):
        """Test queue drop counter increments on full queue."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_maxsize=2,
            enable_deduplication=False  # Disable to test queue behavior in isolation
        )

        # Fill queue
        mock_message = '{"event_type": "book", "asset_id": "123", "market": "0xabc", "timestamp": "123", "hash": "0x123", "buys": [], "sells": []}'

        with patch.object(ws_client, '_invoke_callback'):
            # First two messages should queue
            ws_client._on_message(None, mock_message)
            ws_client._on_message(None, mock_message)

            initial_drops = ws_client._queue_drops

            # Third message should drop
            ws_client._on_message(None, mock_message)

            # Check drop counter
            assert ws_client._queue_drops > initial_drops


class TestBackwardCompatibility:
    """Test backward compatibility with queue parameter."""

    def test_default_behavior_unchanged(self):
        """Test default behavior matches old API."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key"
        )

        # Queue should be enabled by default (new behavior)
        # But API remains compatible
        assert hasattr(ws_client, '_message_queue')

    def test_explicit_disable_works(self):
        """Test explicitly disabling queue works."""
        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=False
        )

        # Should behave like old sync mode
        assert ws_client._enable_queue is False
        assert ws_client._message_queue is None


class TestPrometheusMetrics:
    """Test Prometheus metrics integration."""

    def test_queue_drop_metric_tracked(self):
        """Test queue drops are tracked in metrics."""
        # Mock metrics
        mock_metrics = Mock()

        ws_client = WebSocketClient(
            ws_url="wss://ws-subscriptions-clob.polymarket.com/ws",
            api_key="test_key",
            enable_queue=True,
            queue_maxsize=1,
            enable_deduplication=False  # Disable to test queue behavior in isolation
        )

        ws_client._metrics = mock_metrics

        # Fill queue
        mock_message = '{"event_type": "book", "asset_id": "123", "market": "0xabc", "timestamp": "123", "hash": "0x123", "buys": [], "sells": []}'

        with patch.object(ws_client, '_invoke_callback'):
            # First message queues
            ws_client._on_message(None, mock_message)

            # Second message drops
            ws_client._on_message(None, mock_message)

        # Check metric was tracked
        if hasattr(mock_metrics, 'track_websocket_queue_drop'):
            mock_metrics.track_websocket_queue_drop.assert_called()
