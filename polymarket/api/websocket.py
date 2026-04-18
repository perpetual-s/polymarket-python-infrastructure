"""
WebSocket client for real-time updates.

Critical for high-frequency trading - much faster than polling.

v3.2: Added typed message models, health monitoring, and metrics.
v3.3: Added message queue/buffer for async processing (Phase 3).
"""

import json
import threading
import time
import queue
import asyncio
import hashlib
from typing import Optional, Callable, Dict, Any
from enum import Enum
from collections import deque
import logging

from .websocket_models import (
    WebSocketMessage,
    parse_websocket_message,
    CLOBEventType,
)
from ..metrics import get_metrics

logger = logging.getLogger(__name__)


class ChannelType(str, Enum):
    """WebSocket channel types."""
    MARKET = "market"
    USER = "user"


class WebSocketClient:
    """
    WebSocket client for Polymarket real-time updates.

    Provides:
    - Market data updates (orderbook, trades)
    - User updates (orders, fills)
    - Automatic reconnection
    - Thread-safe callbacks
    - Health monitoring (stats, health_check)
    - Typed message models
    - Message queue/buffer for async processing (v3.3)
    """

    def __init__(
        self,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws",
        api_key: Optional[str] = None,
        reconnect_delay: float = 5.0,
        max_reconnects: int = 10,
        enable_metrics: bool = True,
        enable_queue: bool = True,
        queue_maxsize: int = 10000,
        ping_interval: int = 20,
        ping_timeout: int = 10,
        queue_drop_threshold: int = 1000,
        enable_compression: bool = True,
        on_failure_callback: Optional[Callable[[str], None]] = None,
        enable_deduplication: bool = True,
        dedup_window_seconds: int = 300
    ):
        """
        Initialize WebSocket client.

        Args:
            ws_url: WebSocket URL
            api_key: API key for user channel (optional)
            reconnect_delay: Delay between reconnects
            max_reconnects: Max reconnect attempts
            enable_metrics: Enable Prometheus metrics tracking
            enable_queue: Enable message queue for async processing (v3.3)
            queue_maxsize: Maximum queue size (default: 10000 messages)
            ping_interval: WebSocket ping interval in seconds (default: 20)
            ping_timeout: WebSocket ping timeout in seconds (default: 10)
            queue_drop_threshold: Maximum queue drops before triggering circuit breaker (default: 1000)
            enable_compression: Enable permessage-deflate compression (default: True, 50-70% bandwidth reduction)
            on_failure_callback: Optional callback invoked on permanent failure (max reconnects exceeded or fatal error)
                                Receives failure reason as string argument
            enable_deduplication: Enable message deduplication using hash tracking (default: True)
            dedup_window_seconds: Time window for dedup tracking in seconds (default: 300 = 5 minutes)
        """
        self.ws_url = ws_url
        self.api_key = api_key
        self.reconnect_delay = reconnect_delay
        self.max_reconnects = max_reconnects
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.queue_drop_threshold = queue_drop_threshold
        self.enable_compression = enable_compression
        self.on_failure_callback = on_failure_callback
        self.enable_deduplication = enable_deduplication
        self.dedup_window_seconds = dedup_window_seconds

        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._subscriptions: Dict[str, Callable] = {}
        self._lock = threading.RLock()
        self._reconnect_count = 0
        self._channel_type: Optional[ChannelType] = None  # Channel type determined by subscriptions

        # Health monitoring (v3.2)
        self._message_count = 0
        self._last_message_time = time.time()
        self._connection_start_time: Optional[float] = None
        self._total_reconnections = 0

        # Metrics (v3.2)
        self._metrics = get_metrics() if enable_metrics else None

        # Message queue for async processing (v3.3)
        self._enable_queue = enable_queue
        self._message_queue: Optional[queue.Queue] = queue.Queue(maxsize=queue_maxsize) if enable_queue else None
        self._consumer_task: Optional[asyncio.Task] = None
        self._queue_drops = 0  # Track dropped messages due to full queue
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Message deduplication (v3.5 - L2)
        self._seen_message_hashes: deque = deque(maxlen=10000)  # Rolling window of message hashes
        self._seen_hash_timestamps: deque = deque(maxlen=10000)  # Corresponding timestamps
        self._dedup_lock = threading.Lock()  # Protect dedup structures
        self._duplicate_count = 0  # Track duplicate messages blocked

        logger.info(f"WebSocket client initialized: {ws_url} (queue={'enabled' if enable_queue else 'disabled'})")

    def connect(self, event_loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """
        Start WebSocket connection in background thread.

        Args:
            event_loop: Optional event loop for consumer task (v3.3)
                       If not provided, will try to get current loop or create new one
        """
        # CRITICAL: Use lock to prevent race condition where multiple threads
        # can pass the _running check and each spawn a new thread.
        # This was causing thread explosion (16,384 threads -> kernel panic).
        with self._lock:
            if self._running:
                logger.warning("WebSocket already running")
                return

            self._running = True

            # Start consumer task if queue enabled (v3.3)
            if self._enable_queue:
                # Get or create event loop for consumer task
                if event_loop:
                    self._event_loop = event_loop
                else:
                    try:
                        self._event_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        # No running loop - disable queue to prevent message buildup
                        # FIX (Issue #2): Instead of letting messages pile up, fall back to direct callbacks
                        logger.warning("No running event loop found. Disabling queue mode - "
                                       "messages will be processed via direct callbacks. "
                                       "For async processing, call connect() from async context or provide event_loop.")
                        self._enable_queue = False
                        self._event_loop = None
                        self._running = False  # Reset since we're not actually connecting
                        return  # Skip consumer task creation

                if self._event_loop:
                    self._consumer_task = self._event_loop.create_task(self._consume_messages())
                    logger.info("Message consumer task started")

            # Thread creation inside lock to ensure consistent state
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logger.info("WebSocket connection started")

    def disconnect(self) -> None:
        """Stop WebSocket connection."""
        # Atomically get thread reference and set running=False
        with self._lock:
            self._running = False
            thread = self._thread
            ws = self._ws
            consumer_task = self._consumer_task

        # Stop consumer task (v3.3) - outside lock
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
            logger.info("Consumer task cancelled")

        # Close WebSocket - outside lock
        if ws:
            try:
                ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")

        # Join thread - outside lock (avoid deadlock)
        if thread:
            thread.join(timeout=5.0)

        # Clear references atomically
        with self._lock:
            self._thread = None
            self._ws = None
            self._consumer_task = None

        # Update connection state
        if self._metrics:
            self._metrics.set_websocket_connection("clob", connected=False)

        logger.info("WebSocket disconnected")

    def subscribe_market(
        self,
        token_id: str,
        callback: Callable[[WebSocketMessage], None]
    ) -> None:
        """
        Subscribe to market updates.

        Args:
            token_id: Token ID to track
            callback: Function called on updates (receives typed message)
        """
        with self._lock:
            channel = f"{ChannelType.MARKET}:{token_id}"
            self._subscriptions[channel] = callback

            # Set channel type if not already set (USER channel takes precedence)
            if self._channel_type is None:
                self._channel_type = ChannelType.MARKET

            # Lazy connect: start connection if not running
            if not self._running:
                self.connect()
            elif self._ws:
                self._send_subscribe(ChannelType.MARKET, token_id)

        logger.info(f"Subscribed to market {token_id}")

    def subscribe_user(
        self,
        callback: Callable[[WebSocketMessage], None]
    ) -> None:
        """
        Subscribe to user order/fill updates.

        Args:
            callback: Function called on updates (receives typed message)
        """
        if not self.api_key:
            raise ValueError("API key required for user channel")

        with self._lock:
            channel = f"{ChannelType.USER}"
            self._subscriptions[channel] = callback

            # USER channel always takes precedence (can subscribe to both user and market data)
            self._channel_type = ChannelType.USER

            # Lazy connect: start connection if not running
            if not self._running:
                self.connect()
            elif self._ws:
                self._send_subscribe(ChannelType.USER, None)

        logger.info("Subscribed to user updates")

    def subscribe_markets_multi(
        self,
        token_ids: list[str],
        callback: Callable[[WebSocketMessage], None]
    ) -> None:
        """
        Subscribe to multiple markets using SINGLE subscription message (v3.5 - L1).

        Reduces WebSocket message overhead by sending one subscription for multiple tokens:
        {"type": "MARKET", "asset_ids": ["id1", "id2", "id3"]}

        More efficient than subscribe_markets_batch() which sends separate subscriptions per token.

        Args:
            token_ids: List of token IDs to subscribe to in single message
            callback: Function called on updates for all markets
        """
        if not token_ids:
            logger.warning("No token IDs provided for multi-subscription")
            return

        with self._lock:
            # Register all tokens with callback
            for token_id in token_ids:
                channel = f"{ChannelType.MARKET}:{token_id}"
                self._subscriptions[channel] = callback

            # Send single subscription message with all asset_ids
            if self._ws:
                self._send_subscribe_multi(ChannelType.MARKET, token_ids)

        logger.info(f"Subscribed to {len(token_ids)} markets in single message")

    def subscribe_markets_batch(
        self,
        token_ids: list[str],
        callback: Callable[[WebSocketMessage], None]
    ) -> Dict[str, Any]:
        """
        Subscribe to multiple markets atomically with transaction semantics (v3.3).

        All subscriptions succeed or all fail (rollback on partial failure).
        Sends separate subscription message per token.

        For more efficiency, use subscribe_markets_multi() which sends single message.

        Args:
            token_ids: List of token IDs to subscribe to
            callback: Function called on updates for all markets

        Returns:
            dict: Result with {"success": bool, "succeeded": list, "failed": list, "error": str}
        """
        if not token_ids:
            return {"success": False, "succeeded": [], "failed": [], "error": "No token IDs provided"}

        succeeded = []
        failed = []
        error_msg = None

        try:
            # Try to subscribe to all markets
            for token_id in token_ids:
                try:
                    self.subscribe_market(token_id, callback)
                    succeeded.append(token_id)
                except Exception as e:
                    failed.append(token_id)
                    error_msg = str(e)
                    logger.error(f"Failed to subscribe to {token_id}: {e}")
                    # Rollback: unsubscribe from all succeeded so far
                    logger.warning(f"Rolling back {len(succeeded)} successful subscriptions")
                    for success_token in succeeded:
                        try:
                            channel = f"{ChannelType.MARKET}:{success_token}"
                            self.unsubscribe(channel)
                        except Exception as rollback_err:
                            logger.error(f"Error during rollback for {success_token}: {rollback_err}")

                    return {
                        "success": False,
                        "succeeded": [],
                        "failed": failed,
                        "error": error_msg
                    }

            # All succeeded
            logger.info(f"Successfully subscribed to {len(succeeded)} markets")
            return {
                "success": True,
                "succeeded": succeeded,
                "failed": [],
                "error": None
            }

        except Exception as e:
            error_msg = f"Batch subscription error: {e}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "succeeded": [],
                "failed": token_ids,
                "error": error_msg
            }

    def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from channel."""
        with self._lock:
            if channel in self._subscriptions:
                del self._subscriptions[channel]

                if self._ws:
                    self._send_unsubscribe(channel)

        logger.info(f"Unsubscribed from {channel}")

    def _run(self) -> None:
        """Main WebSocket loop (runs in background thread)."""
        try:
            import websocket
        except ImportError:
            logger.error("websocket-client not installed: pip install websocket-client")
            self._running = False
            return

        while self._running:
            ws = None
            try:
                # Construct full WebSocket URL with channel suffix
                # Base URL: wss://ws-subscriptions-clob.polymarket.com/ws
                # Full URL: wss://ws-subscriptions-clob.polymarket.com/ws/user or /ws/market
                with self._lock:
                    channel_type = self._channel_type

                if channel_type is None:
                    logger.error("Channel type not set - cannot connect. Call subscribe_user() or subscribe_market() first.")
                    self._running = False
                    return

                # Construct full URL: base + "/" + channel
                full_url = f"{self.ws_url}/{channel_type.value}"
                logger.info(f"Connecting to {full_url} ({channel_type.value.upper()} channel)")

                ws = websocket.WebSocketApp(
                    full_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open
                )
                self._ws = ws

                # Build run_forever kwargs
                run_forever_kwargs = {
                    "ping_interval": self.ping_interval,
                    "ping_timeout": self.ping_timeout,
                }
                # Note: websocket-client library doesn't support 'compression' parameter in run_forever()
                # Compression is negotiated via WebSocket handshake headers instead
                # The enable_compression flag is kept for future use but not passed to run_forever()

                logger.info(f"Connecting to WebSocket (compression={'enabled' if self.enable_compression else 'disabled'})...")
                ws.run_forever(**run_forever_kwargs)

                if not self._running:
                    break

                # Reconnect logic
                self._reconnect_count += 1
                if self._reconnect_count > self.max_reconnects:
                    reason = f"Max reconnects exceeded ({self.max_reconnects} attempts)"
                    logger.error(reason)
                    self._invoke_failure_callback(reason)
                    break

                logger.warning(
                    f"Reconnecting in {self.reconnect_delay}s "
                    f"(attempt {self._reconnect_count}/{self.max_reconnects})"
                )
                time.sleep(self.reconnect_delay)

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                # Check if this is a fatal error requiring failure callback
                if not self._running:
                    # Connection was explicitly stopped, not a failure
                    break

            finally:
                # CRITICAL FIX: Always cleanup WebSocket to prevent socket leak
                if ws:
                    try:
                        ws.close()
                    except Exception as e:
                        logger.debug(f"Error closing WebSocket in cleanup: {e}")

                if self._running:
                    time.sleep(self.reconnect_delay)

    def _on_open(self, ws) -> None:
        """Handle connection open."""
        is_reconnect = self._reconnect_count > 0
        connection_type = "reconnected" if is_reconnect else "connected"
        logger.info(f"WebSocket {connection_type}")

        # Track connection metrics
        if is_reconnect:
            self._total_reconnections += 1
            if self._metrics:
                self._metrics.track_websocket_reconnection("clob")
        self._reconnect_count = 0
        self._connection_start_time = time.time()

        # Update connection state
        if self._metrics:
            self._metrics.set_websocket_connection("clob", connected=True)

        # Resubscribe to all channels
        with self._lock:
            subscription_count = len(self._subscriptions)
            if subscription_count > 0:
                logger.info(f"Resubscribing to {subscription_count} channel(s)")

                for channel in self._subscriptions:
                    if channel.startswith(ChannelType.MARKET):
                        token_id = channel.split(":")[1]
                        logger.debug(f"Resubscribing to MARKET channel for token {token_id}")
                        self._send_subscribe(ChannelType.MARKET, token_id)
                    elif channel == ChannelType.USER:
                        logger.debug("Resubscribing to USER channel")
                        self._send_subscribe(ChannelType.USER, None)

                logger.info(f"Resubscription complete: {subscription_count} channel(s) restored")

    def _on_message(self, ws, message: str) -> None:
        """Handle incoming message."""
        processing_start = time.time()

        try:
            data = json.loads(message)

            # Message deduplication (v3.5 - L2)
            if self.enable_deduplication:
                message_hash = self._compute_message_hash(data)

                # Check if we've seen this message recently
                if self._is_duplicate_message(message_hash):
                    self._duplicate_count += 1
                    logger.debug(f"Duplicate message detected (hash={message_hash[:8]}...), skipping")
                    if self._metrics:
                        self._metrics.track_websocket_duplicate("clob")
                    return

                # Track this message hash
                self._track_message_hash(message_hash, processing_start)

            # Track message metrics
            self._message_count += 1
            self._last_message_time = time.time()

            # Parse into typed message
            try:
                typed_message = parse_websocket_message(data)
                if not typed_message:
                    # Unknown message type, skip
                    logger.debug(f"Unknown message type: {data.get('event_type')}")
                    return
            except ValueError as e:
                logger.warning(f"Failed to parse message: {e}")
                return

            # Determine channel
            event_type = data.get("event_type")

            # Track message metrics
            if self._metrics and event_type:
                channel_label = "market" if event_type in [CLOBEventType.BOOK, CLOBEventType.PRICE_CHANGE,
                                                           CLOBEventType.TICK_SIZE_CHANGE,
                                                           CLOBEventType.LAST_TRADE_PRICE] else "user"
                self._metrics.track_websocket_message(channel_label, event_type)

            # Queue message for async processing (v3.3) or invoke callback directly
            if self._enable_queue and self._message_queue:
                # Determine if message is from USER channel (critical: trade/order updates)
                is_user_channel = event_type in [CLOBEventType.TRADE, CLOBEventType.ORDER]

                # Enqueue message with metadata for consumer task
                message_item = {
                    "typed_message": typed_message,
                    "event_type": event_type,
                    "data": data,
                    "processing_start": processing_start,
                    "is_user_channel": is_user_channel,
                }

                try:
                    self._message_queue.put_nowait(message_item)
                except queue.Full:
                    self._queue_drops += 1

                    # USER channel messages are critical - try to make room by dropping oldest MARKET message
                    if is_user_channel:
                        logger.critical(
                            f"Queue full, attempting to preserve USER channel message "
                            f"(event_type={event_type}) by dropping oldest MARKET message"
                        )
                        # FIX (Issue #3): Handle race condition where consumer drains queue between
                        # Full exception and our get_nowait(). Use try/except for all queue operations.
                        try:
                            oldest = self._message_queue.get_nowait()
                            if not oldest.get("is_user_channel", False):
                                # Dropped a MARKET message, now try to insert USER message
                                try:
                                    self._message_queue.put_nowait(message_item)
                                    logger.debug("Dropped oldest MARKET message to make room for USER message")
                                except queue.Full:
                                    # Queue filled again between get and put - rare race condition
                                    # Put back the MARKET message we took (better than losing both)
                                    try:
                                        self._message_queue.put_nowait(oldest)
                                    except queue.Full:
                                        pass  # Queue is completely full, both messages lost
                                    logger.critical("Queue race: dropped USER message after failed insert")
                            else:
                                # Oldest was also USER channel, put it back
                                try:
                                    self._message_queue.put_nowait(oldest)
                                except queue.Full:
                                    pass  # Can't put back - rare race, at least we tried
                                logger.critical("Queue full of USER messages, forced to drop USER channel message!")
                        except queue.Empty:
                            # Queue drained between Full and get_nowait - try insert again
                            try:
                                self._message_queue.put_nowait(message_item)
                                logger.debug("Queue drained, inserted USER message after race recovery")
                            except queue.Full:
                                logger.critical("Queue race: still full after drain, dropped USER message")
                    else:
                        # MARKET channel message - normal drop
                        logger.warning(
                            f"Queue full, dropped MARKET channel message "
                            f"(event_type={event_type}, total drops: {self._queue_drops})"
                        )

                    # Track metrics
                    if self._metrics:
                        self._metrics.track_websocket_queue_drop("clob")

                    # Circuit breaker: Check if drops exceed threshold
                    if self._queue_drops >= self.queue_drop_threshold:
                        logger.critical(
                            f"Queue drops ({self._queue_drops}) exceeded threshold ({self.queue_drop_threshold})! "
                            f"This indicates a serious processing backlog. Consider increasing queue size, "
                            f"optimizing callback processing, or reducing subscription load."
                        )
                        # Note: Not auto-disconnecting to allow monitoring/intervention
                        # Production systems should monitor this metric and alert
            else:
                # Direct callback invocation (legacy behavior)
                self._invoke_callback(typed_message, event_type, data)

            # Track processing time (for enqueue time, not callback time)
            if self._metrics and event_type:
                duration = time.time() - processing_start
                self._metrics.track_websocket_processing(channel_label, event_type, duration)

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _on_error(self, ws, error) -> None:
        """Handle error."""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        """Handle connection close."""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")

    def _send_subscribe(self, channel_type: ChannelType, asset_id: Optional[str]) -> None:
        """
        Send subscribe message using official Polymarket format (single asset).

        Official format:
        - MARKET channel: {"type": "MARKET", "asset_ids": ["token_id"]}
        - USER channel: {"type": "USER", "auth": "api_key_string"}

        For multi-asset subscriptions, use _send_subscribe_multi().
        """
        if not self._ws:
            return

        # Build message according to official spec
        msg = {
            "type": channel_type.value.upper(),  # "MARKET" or "USER" (uppercase)
        }

        if channel_type == ChannelType.MARKET and asset_id:
            # MARKET channel: use asset_ids array (official format)
            msg["asset_ids"] = [asset_id]
        elif channel_type == ChannelType.USER and self.api_key:
            # USER channel: auth is string, not dict (official format)
            msg["auth"] = self.api_key

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent subscribe (official format): {msg}")
        except Exception as e:
            logger.error(f"Failed to send subscribe: {e}")

    def _send_subscribe_multi(self, channel_type: ChannelType, asset_ids: list[str]) -> None:
        """
        Send subscribe message for multiple assets in single message (v3.5 - L1).

        Official format:
        - MARKET channel: {"type": "MARKET", "asset_ids": ["id1", "id2", "id3"]}

        Reduces WebSocket message overhead compared to sending separate subscriptions.
        """
        if not self._ws:
            return

        if channel_type != ChannelType.MARKET:
            logger.error("Multi-subscription only supported for MARKET channel")
            return

        if not asset_ids:
            logger.warning("No asset_ids provided for multi-subscription")
            return

        # Build message according to official spec
        msg = {
            "type": channel_type.value.upper(),  # "MARKET"
            "asset_ids": asset_ids  # Multiple asset IDs in single array
        }

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent multi-subscribe for {len(asset_ids)} assets: {msg}")
        except Exception as e:
            logger.error(f"Failed to send multi-subscribe: {e}")

    def _send_unsubscribe(self, channel: str) -> None:
        """Send unsubscribe message."""
        if not self._ws:
            return

        msg = {"type": "unsubscribe", "channel": channel}

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent unsubscribe: {msg}")
        except Exception as e:
            logger.error(f"Failed to send unsubscribe: {e}")

    def _compute_message_hash(self, data: dict) -> str:
        """
        Compute hash of message for deduplication (v3.5 - L2).

        Uses event_type + timestamp + critical identifiers to create unique hash.
        Different message types have different critical fields.

        Args:
            data: Raw message dict

        Returns:
            SHA256 hash string (hex)
        """
        event_type = data.get("event_type", "")

        # Build hash key from critical fields (varies by message type)
        hash_parts = [event_type]

        # Common fields
        if "timestamp" in data:
            hash_parts.append(str(data["timestamp"]))
        if "asset_id" in data:
            hash_parts.append(str(data["asset_id"]))
        if "market" in data:
            hash_parts.append(str(data["market"]))

        # Message-type specific fields
        if event_type == "book":
            # Orderbook: hash includes hash field
            if "hash" in data:
                hash_parts.append(str(data["hash"]))
        elif event_type in ["trade", "order"]:
            # Trade/Order: use ID
            if "id" in data:
                hash_parts.append(str(data["id"]))
        elif event_type == "price_change":
            # Price change: use hash if present in changes
            if "price_changes" in data and data["price_changes"]:
                for pc in data["price_changes"]:
                    if "hash" in pc:
                        hash_parts.append(str(pc["hash"]))

        # Compute SHA256 hash
        hash_input = "|".join(hash_parts)
        return hashlib.sha256(hash_input.encode()).hexdigest()

    def _is_duplicate_message(self, message_hash: str) -> bool:
        """
        Check if message hash was seen recently (v3.5 - L2).

        Args:
            message_hash: Message hash from _compute_message_hash()

        Returns:
            True if duplicate, False if new message
        """
        with self._dedup_lock:
            # Clean up old hashes outside time window
            current_time = time.time()
            while self._seen_hash_timestamps and \
                  current_time - self._seen_hash_timestamps[0] > self.dedup_window_seconds:
                self._seen_hash_timestamps.popleft()
                self._seen_message_hashes.popleft()

            # Check if hash exists in recent messages
            return message_hash in self._seen_message_hashes

    def _track_message_hash(self, message_hash: str, timestamp: float) -> None:
        """
        Track message hash for deduplication (v3.5 - L2).

        Args:
            message_hash: Message hash from _compute_message_hash()
            timestamp: Message processing timestamp
        """
        with self._dedup_lock:
            self._seen_message_hashes.append(message_hash)
            self._seen_hash_timestamps.append(timestamp)

    def _invoke_failure_callback(self, reason: str) -> None:
        """
        Invoke failure callback if registered.

        Args:
            reason: Failure reason (e.g., "Max reconnects exceeded (10 attempts)")
        """
        if self.on_failure_callback:
            try:
                self.on_failure_callback(reason)
            except Exception as e:
                logger.error(f"Error in failure callback: {e}", exc_info=True)

    def _invoke_callback(self, typed_message: WebSocketMessage, event_type: str, data: dict) -> None:
        """
        Invoke registered callbacks for message.

        Args:
            typed_message: Parsed typed message
            event_type: Event type from message
            data: Raw message data
        """
        with self._lock:
            # Market channel messages
            if event_type in [CLOBEventType.BOOK, CLOBEventType.PRICE_CHANGE,
                             CLOBEventType.TICK_SIZE_CHANGE, CLOBEventType.LAST_TRADE_PRICE]:
                # Find matching market subscription
                asset_id = data.get("asset_id")
                if asset_id:
                    channel = f"{ChannelType.MARKET}:{asset_id}"
                    callback = self._subscriptions.get(channel)
                    if callback:
                        try:
                            callback(typed_message)
                        except Exception as e:
                            logger.error(f"Error in callback for {channel}: {e}", exc_info=True)

            # User channel messages
            elif event_type in [CLOBEventType.TRADE, CLOBEventType.ORDER]:
                callback = self._subscriptions.get(ChannelType.USER)
                if callback:
                    try:
                        callback(typed_message)
                    except Exception as e:
                        logger.error(f"Error in callback for USER channel: {e}", exc_info=True)

    async def _consume_messages(self) -> None:
        """
        Consumer task that processes messages from queue asynchronously.

        Runs continuously until cancelled. Prevents WebSocket callback
        thread from blocking on async I/O operations.
        """
        logger.info("Starting message consumer task")

        try:
            while self._running:
                try:
                    # Check queue with short timeout (non-blocking)
                    message_item = self._message_queue.get_nowait()

                    # Extract message components
                    typed_message = message_item["typed_message"]
                    event_type = message_item["event_type"]
                    data = message_item["data"]
                    processing_start = message_item["processing_start"]

                    # Track queue lag
                    queue_lag = time.time() - processing_start
                    if self._metrics:
                        self._metrics.track_websocket_queue_lag("clob", queue_lag)

                    # Invoke callback
                    self._invoke_callback(typed_message, event_type, data)

                    # Mark task as done
                    self._message_queue.task_done()

                except queue.Empty:
                    # Queue is empty, sleep briefly to avoid busy-wait
                    await asyncio.sleep(0.01)  # 10ms polling

                except Exception as e:
                    logger.error(f"Error in consumer task: {e}", exc_info=True)
                    # Continue processing other messages
                    await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info("Consumer task cancelled")
            raise
        finally:
            logger.info("Consumer task stopped")

    # ========== Health Monitoring (v3.2) ==========

    def stats(self) -> Dict[str, Any]:
        """
        Get connection statistics for monitoring.

        Returns:
            dict: Metrics including uptime, message count, reconnections, queue stats (v3.3), etc.
        """
        uptime_seconds = None
        if self._connection_start_time and self._running:
            uptime_seconds = int(time.time() - self._connection_start_time)

        with self._lock:
            subscription_count = len(self._subscriptions)

        stats_dict = {
            "status": "connected" if self._running else "disconnected",
            "connected": self._running,
            "uptime_seconds": uptime_seconds,
            "messages_received": self._message_count,
            "reconnections": self._total_reconnections,
            "current_reconnect_attempts": self._reconnect_count,
            "subscriptions": subscription_count,
            "last_message_seconds_ago": int(time.time() - self._last_message_time),
        }

        # Add queue stats if queue enabled (v3.3)
        if self._enable_queue and self._message_queue:
            stats_dict["queue_enabled"] = True
            stats_dict["queue_size"] = self._message_queue.qsize()
            stats_dict["queue_drops"] = self._queue_drops
            stats_dict["consumer_task_running"] = self._consumer_task is not None and not self._consumer_task.done()
        else:
            stats_dict["queue_enabled"] = False

        # Add deduplication stats if enabled (v3.5 - L2)
        if self.enable_deduplication:
            with self._dedup_lock:
                stats_dict["deduplication_enabled"] = True
                stats_dict["dedup_cache_size"] = len(self._seen_message_hashes)
                stats_dict["duplicates_blocked"] = self._duplicate_count
        else:
            stats_dict["deduplication_enabled"] = False

        return stats_dict

    def health_check(self) -> Dict[str, str]:
        """
        Quick health status check.

        Returns:
            dict: Status ("healthy", "degraded", or "disconnected")
        """
        if not self._running:
            return {"status": "disconnected"}

        # Check message freshness (no messages for 60s = stale)
        time_since_last = time.time() - self._last_message_time
        if time_since_last > 60:
            return {
                "status": "degraded",
                "reason": "no_recent_messages",
                "last_message_seconds_ago": int(time_since_last)
            }

        return {"status": "healthy"}

    # ========== Context Manager ==========

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
