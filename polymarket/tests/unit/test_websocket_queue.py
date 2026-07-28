"""
Unit tests for WebSocket queue behavior (Phase 3.3).

Tests message queue, consumer task, and async processing functionality.
"""

import asyncio
import json
import queue
import threading
import time
from unittest.mock import Mock, patch

import pytest

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

    def test_sync_connect_falls_back_to_direct_callbacks_and_still_starts_transport(self):
        """No running asyncio loop must not turn a subscription into a no-op."""
        ws = WebSocketClient(enable_metrics=False, enable_queue=True)

        with patch("polymarket.api.websocket.threading.Thread") as thread_cls:
            ws.connect()

        assert ws._enable_queue is False
        assert ws.telemetry_snapshot_v1().running is True
        thread_cls.return_value.start.assert_called_once_with()

        ws.disconnect()


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

    def test_array_frame_enqueues_valid_items_in_order_and_isolates_bad_items(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=10,
        )

        def book(message_hash):
            return {
                "event_type": "book",
                "asset_id": "123",
                "market": "0xabc",
                "timestamp": "123",
                "hash": message_hash,
                "buys": [],
                "sells": [],
            }

        ws_client._on_message(
            None,
            json.dumps([book("0xfirst"), None, {"event_type": "future"}, book("0xsecond")]),
        )

        queued = [ws_client._message_queue.get_nowait() for _ in range(2)]
        for _ in queued:
            ws_client._message_queue.task_done()
        snapshot = ws_client.telemetry_snapshot_v1()
        assert [item["typed_message"].hash for item in queued] == ["0xfirst", "0xsecond"]
        assert snapshot.messages_received == 1
        assert snapshot.messages_parsed == 2
        assert snapshot.parse_failures == 1
        assert snapshot.unknown_messages == 1

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

    def test_queue_full_drop_does_not_deduplicate_same_lifecycle_retry(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=1,
            enable_deduplication=True,
        )
        first = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"1","hash":"0xfirst","buys":[],"sells":[]}'
        )
        retryable = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"2","hash":"0xretry","buys":[],"sells":[]}'
        )

        ws_client._on_message(None, first)
        ws_client._on_message(None, retryable)
        assert ws_client._message_queue.qsize() == 1
        assert ws_client.telemetry_snapshot_v1().queue_drops == 1

        ws_client._message_queue.get_nowait()
        ws_client._message_queue.task_done()
        ws_client._on_message(None, retryable)

        queued = ws_client._message_queue.get_nowait()
        ws_client._message_queue.task_done()
        assert queued["data"]["hash"] == "0xretry"
        assert ws_client.telemetry_snapshot_v1().duplicates_blocked == 0

    def test_user_preservation_eviction_keeps_market_retry_admissible(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=1,
            enable_deduplication=True,
        )
        book = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"1","hash":"0xevicted","buys":[],"sells":[]}'
        )
        ws_client._on_message(None, book)

        generation = ws_client._lifecycle_generation
        user_item = {
            "typed_message": None,
            "event_type": "trade",
            "data": {},
            "processing_start": time.time(),
            "is_user_channel": True,
            "lifecycle_generation": generation,
            "dedup_hash": None,
        }
        assert ws_client._enqueue_message(user_item, True, "trade") is True

        queued_user = ws_client._message_queue.get_nowait()
        ws_client._message_queue.task_done()
        assert queued_user["event_type"] == "trade"
        ws_client._on_message(None, book)

        queued_market = ws_client._message_queue.get_nowait()
        ws_client._message_queue.task_done()
        assert queued_market["data"]["hash"] == "0xevicted"
        snapshot = ws_client.telemetry_snapshot_v1()
        assert snapshot.duplicates_blocked == 0
        assert snapshot.queue_drops == 1

    def test_flushed_book_hash_does_not_poison_fresh_lifecycle(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=1,
            enable_deduplication=True,
        )
        book = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"1","hash":"0xsnapshot","buys":[],"sells":[]}'
        )
        ws_client._running = True
        ws_client._lifecycle_generation = 1

        ws_client._on_message(None, book)
        assert ws_client._message_queue.qsize() == 1
        ws_client.disconnect()
        assert ws_client._message_queue.qsize() == 0

        # Model the next explicit connect generation without starting transport.
        with ws_client._lock:
            ws_client._lifecycle_generation += 1
            ws_client._running = True
        ws_client._on_message(None, book)

        assert ws_client._message_queue.qsize() == 1
        assert ws_client.telemetry_snapshot_v1().duplicates_blocked == 0

    def test_stale_in_flight_hash_after_teardown_cannot_poison_restart(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=1,
            enable_deduplication=True,
        )
        book = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"1","hash":"0xsnapshot","buys":[],"sells":[]}'
        )
        ws_client._running = True
        ws_client._lifecycle_generation = 1
        entered_hash = threading.Event()
        resume_hash = threading.Event()
        original_compute = ws_client._compute_message_hash

        def blocked_compute(data):
            entered_hash.set()
            assert resume_hash.wait(timeout=1)
            return original_compute(data)

        with patch.object(ws_client, "_compute_message_hash", side_effect=blocked_compute):
            stale_message = threading.Thread(
                target=ws_client._on_message,
                args=(None, book),
            )
            stale_message.start()
            assert entered_hash.wait(timeout=1)
            ws_client.disconnect()
            with ws_client._lock:
                ws_client._lifecycle_generation += 1
                ws_client._running = True
            resume_hash.set()
            stale_message.join(timeout=1)
            assert not stale_message.is_alive()

        assert ws_client._message_queue.qsize() == 0
        ws_client._on_message(None, book)
        assert ws_client._message_queue.qsize() == 1
        snapshot = ws_client.telemetry_snapshot_v1()
        assert snapshot.duplicates_blocked == 0
        assert snapshot.stale_lifecycle_messages_dropped == 1

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
        channel = f"{ChannelType.MARKET.value}:123"
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

        # Keep the background transport thread inert; this test covers only
        # event-loop consumer ownership.
        with patch("polymarket.api.websocket.threading.Thread"):
            ws_client.connect(event_loop=loop)

            # Give consumer task a moment to start
            await asyncio.sleep(0.1)

            # Check consumer task is running
            assert ws_client._consumer_task is not None
            assert not ws_client._consumer_task.done()
            assert ws_client.telemetry_snapshot_v1().queue_consumer_running is True

            consumer_task = ws_client._consumer_task
            ws_client.disconnect()
            await asyncio.sleep(0)
            assert consumer_task.done()
            assert ws_client.telemetry_snapshot_v1().queue_consumer_running is False

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
        channel = f"{ChannelType.MARKET.value}:123"
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
    async def test_sustained_queue_yields_to_event_loop_before_drain(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=256,
            enable_deduplication=False,
        )
        callback = Mock()
        ws_client._subscriptions = {"market:123": callback}
        ws_client._running = True
        generation = ws_client._lifecycle_generation
        for index in range(256):
            ws_client._message_queue.put_nowait(
                {
                    "typed_message": None,
                    "event_type": "book",
                    "data": {"asset_id": "123", "sequence": index},
                    "processing_start": time.time(),
                    "is_user_channel": False,
                    "lifecycle_generation": generation,
                    "dedup_hash": None,
                }
            )

        consumer = asyncio.create_task(ws_client._consume_messages(generation))

        async def observe_and_stop() -> int:
            observed = callback.call_count
            with ws_client._lock:
                ws_client._running = False
            return observed

        observer = asyncio.create_task(observe_and_stop())
        observed_before_stop = await observer
        await consumer

        assert 0 < observed_before_stop < 256
        ws_client.disconnect()

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

        consumer_task = ws_client._consumer_task
        ws_client.disconnect()

        # Wait a bit
        await asyncio.sleep(0.1)

        # Check task is cancelled
        assert consumer_task.done()

    @pytest.mark.asyncio
    async def test_old_consumer_generation_cannot_resume_after_restart(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=10,
        )
        ws_client._running = True
        old_generation = ws_client._lifecycle_generation
        old_consumer = asyncio.create_task(ws_client._consume_messages(old_generation))
        ws_client._consumer_task = old_consumer
        ws_client._event_loop = asyncio.get_running_loop()
        await asyncio.sleep(0.02)

        with ws_client._lock:
            ws_client._lifecycle_generation += 1
            ws_client._running = True
        ws_client._message_queue.put_nowait(
            {
                "typed_message": None,
                "event_type": "book",
                "data": {},
                "processing_start": time.time(),
            }
        )

        await asyncio.sleep(0.03)
        assert old_consumer.done()
        assert ws_client._message_queue.qsize() == 1
        assert ws_client.telemetry_snapshot_v1().queue_consumer_running is False

    @pytest.mark.asyncio
    async def test_intentional_restart_discards_and_counts_old_queue_backlog(self):
        from polymarket.api.websocket import ChannelType

        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=10,
            enable_deduplication=False,
        )
        callback = Mock()
        ws_client._subscriptions = {"market:123": callback}
        ws_client._channel_type = ChannelType.MARKET
        ws_client._running = True
        ws_client._lifecycle_generation = 1
        ws_client._on_message(
            None,
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"123","hash":"0x123","buys":[],"sells":[]}',
        )
        assert ws_client._message_queue.qsize() == 1

        ws_client.disconnect()
        stopped = ws_client.telemetry_snapshot_v1()
        assert stopped.queue_size == 0
        assert stopped.queue_drops == 1
        assert stopped.stale_lifecycle_messages_dropped == 1

        loop = asyncio.get_running_loop()
        with patch("polymarket.api.websocket.threading.Thread"):
            ws_client.connect(event_loop=loop)
            await asyncio.sleep(0.02)
            callback.assert_not_called()
            assert ws_client._message_queue.qsize() == 0
            ws_client.disconnect()
            await asyncio.sleep(0)


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

    def test_snapshot_reports_exact_queue_high_water_and_drops(self):
        ws_client = WebSocketClient(
            enable_metrics=False,
            enable_queue=True,
            queue_maxsize=2,
            enable_deduplication=False,
        )
        message = (
            '{"event_type":"book","asset_id":"123","market":"0xabc",'
            '"timestamp":"123","hash":"0x123","buys":[],"sells":[]}'
        )

        ws_client._on_message(None, message)
        ws_client._on_message(None, message)
        ws_client._on_message(None, message)

        snapshot = ws_client.telemetry_snapshot_v1()
        assert snapshot.messages_received == 3
        assert snapshot.messages_parsed == 3
        assert snapshot.queue_capacity == 2
        assert snapshot.queue_drop_threshold == 1000
        assert snapshot.queue_size == 2
        assert snapshot.queue_high_water == 2
        assert snapshot.queue_drops == 1


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
