"""
WebSocket client for real-time updates.

Critical for high-frequency trading - much faster than polling.

v3.2: Added typed message models, health monitoring, and metrics.
v3.3: Added message queue/buffer for async processing (Phase 3).
"""

import asyncio
import hashlib
import json
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

from ..metrics import get_metrics
from .websocket_logging import (
    install_websocket_transient_disconnect_filter,
    is_transient_websocket_disconnect,
)
from .websocket_models import (
    CLOBEventType,
    WebSocketMessage,
    parse_websocket_message,
)

logger = logging.getLogger(__name__)

_CONSUMER_FAIRNESS_BATCH_SIZE = 64


@dataclass(frozen=True)
class WebSocketObservedSilenceV1:
    """One transport-observed disconnect-to-reopen silence interval.

    ``duration_seconds`` is measured with a monotonic clock. The wall-clock
    timestamps are included only to place the observation in time. This is not
    evidence of source sequence completeness or of how many source messages may
    have occurred while the transport was disconnected.
    """

    connection_generation: int
    started_at: float
    ended_at: Optional[float]
    duration_seconds: float


@dataclass(frozen=True)
class WebSocketTelemetrySnapshotV1:
    """Immutable, identifier-free CLOB WebSocket telemetry snapshot.

    ``callbacks_*`` describes message-delivery callbacks only; permanent-failure
    callbacks have separate counters. ``disconnects`` counts actual
    connected-to-disconnected transitions, while ``intentional_disconnects``
    counts explicit local teardown calls. Reconnect silence is transport-observed
    and does not attest source sequence completeness.
    """

    captured_at: float
    running: bool
    connected: bool
    connection_generation: int
    subscriptions: int
    messages_received: int
    messages_parsed: int
    parse_failures: int
    unknown_messages: int
    duplicates_blocked: int
    last_receipt_time: Optional[float]
    last_receipt_age_seconds: Optional[float]
    queue_enabled: bool
    queue_capacity: int
    queue_drop_threshold: int
    queue_consumer_running: bool
    queue_size: int
    queue_high_water: int
    queue_drops: int
    stale_lifecycle_messages_dropped: int
    callbacks_invoked: int
    callback_failures: int
    last_callback_completed_time: Optional[float]
    failure_callbacks_invoked: int
    failure_callback_failures: int
    reconnect_delay_seconds: float
    max_reconnects: int
    reconnect_attempts: int
    reconnect_completions: int
    reconnect_exhaustions: int
    current_reconnect_attempts: int
    disconnects: int
    intentional_disconnects: int
    reconnect_silence_threshold_seconds: float
    reconnect_silence_history_limit: int
    observed_reconnect_silence_count: int
    observed_reconnect_silences_discarded: int
    observed_reconnect_silences: tuple[WebSocketObservedSilenceV1, ...]
    current_reconnect_silence_started_at: Optional[float]
    local_send_failures: int
    local_subscribe_send_failures: int
    local_unsubscribe_send_failures: int
    last_local_send_failure_time: Optional[float]

    @classmethod
    def disconnected(cls) -> "WebSocketTelemetrySnapshotV1":
        """Return the stable-schema snapshot for a client with no transport."""
        return cls(
            captured_at=time.time(),
            running=False,
            connected=False,
            connection_generation=0,
            subscriptions=0,
            messages_received=0,
            messages_parsed=0,
            parse_failures=0,
            unknown_messages=0,
            duplicates_blocked=0,
            last_receipt_time=None,
            last_receipt_age_seconds=None,
            queue_enabled=False,
            queue_capacity=0,
            queue_drop_threshold=0,
            queue_consumer_running=False,
            queue_size=0,
            queue_high_water=0,
            queue_drops=0,
            stale_lifecycle_messages_dropped=0,
            callbacks_invoked=0,
            callback_failures=0,
            last_callback_completed_time=None,
            failure_callbacks_invoked=0,
            failure_callback_failures=0,
            reconnect_delay_seconds=0.0,
            max_reconnects=0,
            reconnect_attempts=0,
            reconnect_completions=0,
            reconnect_exhaustions=0,
            current_reconnect_attempts=0,
            disconnects=0,
            intentional_disconnects=0,
            reconnect_silence_threshold_seconds=0.0,
            reconnect_silence_history_limit=100,
            observed_reconnect_silence_count=0,
            observed_reconnect_silences_discarded=0,
            observed_reconnect_silences=(),
            current_reconnect_silence_started_at=None,
            local_send_failures=0,
            local_subscribe_send_failures=0,
            local_unsubscribe_send_failures=0,
            last_local_send_failure_time=None,
        )


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
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
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
        dedup_window_seconds: int = 300,
        reconnect_silence_threshold_seconds: float = 0.0,
        reconnect_silence_history_size: int = 100,
    ):
        """
        Initialize WebSocket client.

        Args:
            ws_url: WebSocket URL
            api_key: API key for user channel (optional)
            api_secret: API secret for user channel (optional)
            api_passphrase: API passphrase for user channel (optional)
            reconnect_delay: Delay between reconnects
            max_reconnects: Max reconnect attempts
            enable_metrics: Enable Prometheus metrics tracking
            enable_queue: Enable message queue for async processing (v3.3)
            queue_maxsize: Maximum queue size (default: 10000 messages)
            ping_interval: WebSocket ping interval in seconds (default: 20)
            ping_timeout: WebSocket ping timeout in seconds (default: 10)
            queue_drop_threshold: Maximum queue drops before the failure callback (default: 1000)
            enable_compression: Enable permessage-deflate compression (default: True, 50-70% bandwidth reduction)
            on_failure_callback: Optional callback invoked on permanent failure (max reconnects exceeded or fatal error)
                                Receives failure reason as string argument
            enable_deduplication: Enable message deduplication using hash tracking (default: True)
            dedup_window_seconds: Time window for dedup tracking in seconds (default: 300 = 5 minutes)
            reconnect_silence_threshold_seconds: Minimum monotonic disconnect-to-reopen
                duration retained as an observed reconnect silence. Zero records every
                observed transport disconnect (default: 0.0).
            reconnect_silence_history_size: Maximum completed reconnect-silence
                intervals retained in snapshots (default: 100).
        """
        if reconnect_silence_threshold_seconds < 0:
            raise ValueError("reconnect_silence_threshold_seconds must be non-negative")
        if reconnect_silence_history_size <= 0:
            raise ValueError("reconnect_silence_history_size must be positive")

        self.ws_url = ws_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.reconnect_delay = reconnect_delay
        self.max_reconnects = max_reconnects
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.queue_drop_threshold = queue_drop_threshold
        self.enable_compression = enable_compression
        self.on_failure_callback = on_failure_callback
        self.enable_deduplication = enable_deduplication
        self.dedup_window_seconds = dedup_window_seconds
        self.reconnect_silence_threshold_seconds = reconnect_silence_threshold_seconds
        self.reconnect_silence_history_size = reconnect_silence_history_size
        install_websocket_transient_disconnect_filter()

        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._lifecycle_generation = 0
        self._subscriptions: Dict[str, Callable] = {}
        self._lock = threading.RLock()
        self._reconnect_count = 0
        self._channel_type: Optional[ChannelType] = (
            None  # Channel type determined by subscriptions
        )

        # Health monitoring (v3.2)
        self._message_count = 0
        self._last_message_time = time.time()
        self._messages_parsed = 0
        self._parse_failures = 0
        self._unknown_messages = 0
        self._last_receipt_time: Optional[float] = None
        self._last_receipt_monotonic: Optional[float] = None
        self._connection_start_time: Optional[float] = None
        self._total_reconnections = 0
        self._total_reconnect_attempts = 0
        self._reconnect_exhaustions = 0
        self._disconnect_count = 0
        self._intentional_disconnect_count = 0
        self._connection_generation = 0
        self._current_reconnect_silence: Optional[tuple[int, float, float]] = None
        self._observed_reconnect_silence_count = 0
        self._observed_reconnect_silences_discarded = 0
        self._observed_reconnect_silences: deque[WebSocketObservedSilenceV1] = deque(
            maxlen=reconnect_silence_history_size
        )

        # Metrics (v3.2)
        self._metrics = get_metrics() if enable_metrics else None

        # Message queue for async processing (v3.3)
        self._enable_queue = enable_queue
        self._message_queue: Optional[queue.Queue] = (
            queue.Queue(maxsize=queue_maxsize) if enable_queue else None
        )
        self._consumer_task: Optional[asyncio.Task] = None
        self._queue_drops = 0  # Track dropped messages due to full queue
        self._stale_lifecycle_messages_dropped = 0
        self._queue_high_water = 0
        self._queue_lock = threading.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Aggregate-only callback and local send observability.
        self._callbacks_invoked = 0
        self._callback_failures = 0
        self._last_callback_completed_time: Optional[float] = None
        self._failure_callbacks_invoked = 0
        self._failure_callback_failures = 0
        self._local_send_failures = 0
        self._local_subscribe_send_failures = 0
        self._local_unsubscribe_send_failures = 0
        self._last_local_send_failure_time: Optional[float] = None

        # Message deduplication (v3.5 - L2)
        # Keys are scoped to one explicit connect/disconnect lifecycle. Automatic
        # transport reconnects keep the same lifecycle generation (and therefore
        # keep suppressing replays), while a fresh explicit session cannot be
        # poisoned by an undelivered hash from the prior session.
        self._seen_message_hashes: deque = deque(maxlen=10000)
        self._seen_hash_timestamps: deque = deque(
            maxlen=10000
        )  # Corresponding timestamps
        self._dedup_lock = threading.Lock()  # Protect dedup structures
        self._duplicate_count = 0  # Track duplicate messages blocked

        logger.info(
            f"WebSocket client initialized: {ws_url} (queue={'enabled' if enable_queue else 'disabled'})"
        )

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

            previous_thread = self._thread
            if previous_thread is not None:
                try:
                    previous_thread_alive = previous_thread.is_alive() is True
                except Exception:
                    previous_thread_alive = False
                if previous_thread_alive:
                    raise RuntimeError("Previous WebSocket thread is still stopping")
                self._thread = None

            self._stop_event.clear()
            self._lifecycle_generation += 1
            lifecycle_generation = self._lifecycle_generation
            self._running = True
            self._reconnect_count = 0

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
                        # and preserve the synchronous direct-callback connection path.
                        logger.warning(
                            "No running event loop found. Disabling queue mode - "
                            "messages will be processed via direct callbacks. "
                            "For async processing, call connect() from async context or provide event_loop."
                        )
                        self._enable_queue = False
                        self._event_loop = None

                if self._event_loop:
                    self._consumer_task = self._event_loop.create_task(
                        self._consume_messages(lifecycle_generation)
                    )
                    logger.info("Message consumer task started")

            # Thread creation inside lock to ensure consistent state
            self._thread = threading.Thread(target=self._run, daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._running = False
                self._stop_event.set()
                if self._consumer_task and not self._consumer_task.done():
                    self._consumer_task.cancel()
                self._consumer_task = None
                raise
            logger.info("WebSocket connection started")

    def disconnect(self) -> None:
        """Stop WebSocket connection."""
        stale_queue_drops = 0
        # Atomically get thread reference and set running=False
        with self._lock:
            self._running = False
            self._stop_event.set()
            self._connected_event.clear()
            self._lifecycle_generation += 1
            self._reconnect_count = 0
            self._intentional_disconnect_count += 1
            if self._connected:
                self._connected = False
                self._disconnect_count += 1
            # A deliberate stop ends recovery intent. Do not expose its remaining
            # offline time as an open reconnect-silence observation.
            self._current_reconnect_silence = None
            if self._message_queue is not None:
                with self._queue_lock:
                    while True:
                        try:
                            self._message_queue.get_nowait()
                        except queue.Empty:
                            break
                        else:
                            self._message_queue.task_done()
                            stale_queue_drops += 1
                self._queue_drops += stale_queue_drops
                self._stale_lifecycle_messages_dropped += stale_queue_drops
            thread = self._thread
            ws = self._ws
            consumer_task = self._consumer_task
            event_loop = self._event_loop

        if stale_queue_drops and self._metrics:
            for _ in range(stale_queue_drops):
                self._metrics.track_websocket_queue_drop("clob")

        # Stop consumer task (v3.3) - outside lock
        if consumer_task and not consumer_task.done():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if event_loop is running_loop:
                consumer_task.cancel()
            elif event_loop and event_loop.is_running():
                event_loop.call_soon_threadsafe(consumer_task.cancel)
            else:
                consumer_task.cancel()
            logger.info("Consumer task cancelled")

        # Close WebSocket - outside lock
        if ws:
            try:
                ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")

        # Join thread - outside lock (avoid deadlock)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5.0)

        thread_alive = False
        if thread is threading.current_thread():
            thread_alive = True
        elif thread:
            try:
                thread_alive = thread.is_alive() is True
            except Exception:
                thread_alive = False

        # Clear references atomically
        with self._lock:
            if self._thread is thread and not thread_alive:
                self._thread = None
            if self._ws is ws and not thread_alive:
                self._ws = None
            self._consumer_task = None
            self._event_loop = None

        if thread_alive:
            logger.error("WebSocket thread did not stop within the disconnect timeout")

        # Update connection state
        if self._metrics:
            self._metrics.set_websocket_connection("clob", connected=False)

        logger.info("WebSocket disconnected")

    def subscribe_market(
        self, token_id: str, callback: Callable[[WebSocketMessage], None]
    ) -> None:
        """
        Subscribe to market updates.

        Args:
            token_id: Token ID to track
            callback: Function called on updates (receives typed message)
        """
        with self._lock:
            if self._channel_type == ChannelType.USER:
                raise RuntimeError(
                    "MARKET and USER subscriptions require separate "
                    "Polymarket WebSocket transports"
                )
            channel = f"{ChannelType.MARKET.value}:{token_id}"
            already_registered = channel in self._subscriptions
            self._subscriptions[channel] = callback

            # The endpoint is fixed for the lifetime of this transport.
            if self._channel_type is None:
                self._channel_type = ChannelType.MARKET

            # Lazy connect: start connection if not running
            if not self._running:
                self.connect()
            elif self._ws and self._connected and not already_registered:
                self._send_subscribe(ChannelType.MARKET, token_id)

        logger.info(f"Subscribed to market {token_id}")

    def subscribe_user(self, callback: Callable[[WebSocketMessage], None]) -> None:
        """
        Subscribe to user order/fill updates.

        Args:
            callback: Function called on updates (receives typed message)
        """
        if not all((self.api_key, self.api_secret, self.api_passphrase)):
            raise ValueError(
                "API key, secret, and passphrase required for user channel"
            )

        with self._lock:
            if self._channel_type == ChannelType.MARKET:
                raise RuntimeError(
                    "MARKET and USER subscriptions require separate "
                    "Polymarket WebSocket transports"
                )
            channel = ChannelType.USER.value
            self._subscriptions[channel] = callback

            # The endpoint is fixed for the lifetime of this transport.
            self._channel_type = ChannelType.USER

            # Lazy connect: start connection if not running
            if not self._running:
                self.connect()
            elif self._ws and self._connected:
                self._send_subscribe(ChannelType.USER, None)

        logger.info("Subscribed to user updates")

    def wait_until_connected(self, timeout: float = 5.0) -> bool:
        """Wait until the WebSocket transport has actually opened."""
        return self._connected_event.wait(timeout)

    def subscribe_markets_multi(
        self, token_ids: list[str], callback: Callable[[WebSocketMessage], None]
    ) -> None:
        """
        Subscribe to multiple markets using SINGLE subscription message (v3.5 - L1).

        Reduces overhead by sending one official dynamic update for multiple tokens.
        More efficient than subscribe_markets_batch() which sends separate subscriptions per token.

        Args:
            token_ids: List of token IDs to subscribe to in single message
            callback: Function called on updates for all markets
        """
        if not token_ids:
            logger.warning("No token IDs provided for multi-subscription")
            return

        with self._lock:
            if self._channel_type == ChannelType.USER:
                raise RuntimeError(
                    "MARKET and USER subscriptions require separate "
                    "Polymarket WebSocket transports"
                )
            if self._channel_type is None:
                self._channel_type = ChannelType.MARKET
            # Register all tokens with callback
            new_token_ids = []
            for token_id in token_ids:
                channel = f"{ChannelType.MARKET.value}:{token_id}"
                if channel not in self._subscriptions:
                    new_token_ids.append(token_id)
                self._subscriptions[channel] = callback

            # Send one dynamic subscription update with all asset IDs.
            if self._ws and self._connected and new_token_ids:
                self._send_subscribe_multi(ChannelType.MARKET, new_token_ids)

        logger.info(f"Subscribed to {len(token_ids)} markets in single message")

    def subscribe_markets_batch(
        self, token_ids: list[str], callback: Callable[[WebSocketMessage], None]
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
            return {
                "success": False,
                "succeeded": [],
                "failed": [],
                "error": "No token IDs provided",
            }

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
                    logger.warning(
                        f"Rolling back {len(succeeded)} successful subscriptions"
                    )
                    for success_token in succeeded:
                        try:
                            channel = f"{ChannelType.MARKET.value}:{success_token}"
                            self.unsubscribe(channel)
                        except Exception as rollback_err:
                            logger.error(
                                f"Error during rollback for {success_token}: {rollback_err}"
                            )

                    return {
                        "success": False,
                        "succeeded": [],
                        "failed": failed,
                        "error": error_msg,
                    }

            # All succeeded
            logger.info(f"Successfully subscribed to {len(succeeded)} markets")
            return {
                "success": True,
                "succeeded": succeeded,
                "failed": [],
                "error": None,
            }

        except Exception as e:
            error_msg = f"Batch subscription error: {e}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "succeeded": [],
                "failed": token_ids,
                "error": error_msg,
            }

    def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from channel."""
        with self._lock:
            if channel in self._subscriptions:
                del self._subscriptions[channel]

                if self._ws and self._connected:
                    self._send_unsubscribe(channel)

        logger.info(f"Unsubscribed from {channel}")

    def _run(self) -> None:
        """Main WebSocket loop (runs in background thread)."""
        try:
            import websocket
        except ImportError:
            logger.error("websocket-client not installed: pip install websocket-client")
            with self._lock:
                self._running = False
                self._connected = False
                self._stop_event.set()
            return

        reconnect_pending = False
        try:
            while True:
                with self._lock:
                    if not self._running:
                        break
                    channel_type = self._channel_type

                if channel_type is None:
                    logger.error(
                        "Channel type not set - cannot connect. Call subscribe_user() or subscribe_market() first."
                    )
                    break

                if reconnect_pending:
                    with self._lock:
                        exhausted = self._reconnect_count >= self.max_reconnects
                        next_attempt = self._reconnect_count + 1

                    if exhausted:
                        reason = (
                            f"Max reconnects exceeded ({self.max_reconnects} attempts)"
                        )
                        logger.error(reason)
                        with self._lock:
                            self._reconnect_exhaustions += 1
                        self._invoke_failure_callback(reason)
                        break

                    logger.warning(
                        f"Reconnecting in {self.reconnect_delay}s "
                        f"(attempt {next_attempt}/{self.max_reconnects})"
                    )
                    if self._stop_event.wait(self.reconnect_delay):
                        break

                    with self._lock:
                        if not self._running:
                            break
                        self._reconnect_count += 1
                        self._total_reconnect_attempts += 1
                        channel_type = self._channel_type

                # Construct full URL: base + "/" + channel
                full_url = f"{self.ws_url}/{channel_type.value}"
                logger.info(
                    f"Connecting to {full_url} ({channel_type.value.upper()} channel)"
                )

                ws = None
                try:
                    ws = websocket.WebSocketApp(
                        full_url,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                        on_open=self._on_open,
                    )
                    with self._lock:
                        if not self._running:
                            break
                        self._ws = ws

                    run_forever_kwargs = {
                        "ping_interval": self.ping_interval,
                        "ping_timeout": self.ping_timeout,
                    }
                    logger.info(
                        f"Connecting to WebSocket (compression={'enabled' if self.enable_compression else 'disabled'})..."
                    )
                    ws.run_forever(**run_forever_kwargs)
                except Exception as e:
                    if is_transient_websocket_disconnect(e):
                        logger.warning(f"WebSocket transient disconnect: {e}")
                    else:
                        logger.error(f"WebSocket error: {e}")
                finally:
                    # Always cleanup WebSocket to prevent socket leaks. The close
                    # callback and this fallback transition are idempotent.
                    if ws:
                        try:
                            ws.close()
                        except Exception as e:
                            logger.debug(f"Error closing WebSocket in cleanup: {e}")
                        self._record_transport_disconnect(ws)

                    with self._lock:
                        if self._ws is ws:
                            self._ws = None

                with self._lock:
                    if not self._running:
                        break
                reconnect_pending = True
        finally:
            current_thread = threading.current_thread()
            with self._lock:
                owns_run = self._thread is None or self._thread is current_thread
                if owns_run:
                    self._running = False
                    self._connected = False
                    self._connected_event.clear()
                    self._stop_event.set()
                    if self._thread is current_thread:
                        self._thread = None
            if owns_run and self._metrics:
                self._metrics.set_websocket_connection("clob", connected=False)

    def _record_transport_disconnect(self, ws: Any) -> None:
        """Record one actual connected-to-disconnected transport transition."""
        observed_at = time.time()
        observed_monotonic = time.monotonic()
        with self._lock:
            if (self._ws is not None and ws is not self._ws) or not self._connected:
                return

            self._connected = False
            self._connected_event.clear()
            self._disconnect_count += 1
            if self._running and self._current_reconnect_silence is None:
                self._current_reconnect_silence = (
                    self._connection_generation,
                    observed_at,
                    observed_monotonic,
                )

        if self._metrics:
            self._metrics.set_websocket_connection("clob", connected=False)

    def _on_open(self, ws) -> None:
        """Handle connection open."""
        with self._lock:
            if not self._running or ws is not self._ws:
                logger.debug("Ignoring stale WebSocket open callback")
                return

            observed_at = time.time()
            observed_monotonic = time.monotonic()
            is_reconnect = self._reconnect_count > 0
            self._connection_generation += 1
            self._connected = True
            self._connected_event.set()

            if self._current_reconnect_silence is not None:
                generation, started_at, started_monotonic = (
                    self._current_reconnect_silence
                )
                duration_seconds = max(0.0, observed_monotonic - started_monotonic)
                if duration_seconds >= self.reconnect_silence_threshold_seconds:
                    if (
                        len(self._observed_reconnect_silences)
                        == self.reconnect_silence_history_size
                    ):
                        self._observed_reconnect_silences_discarded += 1
                    self._observed_reconnect_silences.append(
                        WebSocketObservedSilenceV1(
                            connection_generation=generation,
                            started_at=started_at,
                            ended_at=observed_at,
                            duration_seconds=duration_seconds,
                        )
                    )
                    self._observed_reconnect_silence_count += 1
                self._current_reconnect_silence = None

            if is_reconnect:
                self._total_reconnections += 1
            self._reconnect_count = 0
            self._connection_start_time = observed_at

        connection_type = "reconnected" if is_reconnect else "connected"
        logger.info(f"WebSocket {connection_type}")

        # Revalidate ownership after the state transition. A concurrent explicit
        # disconnect must not be overwritten by late metrics or resubscription.
        with self._lock:
            if not self._running or not self._connected or ws is not self._ws:
                return

            if self._metrics:
                if is_reconnect:
                    self._metrics.track_websocket_reconnection("clob")
                self._metrics.set_websocket_connection("clob", connected=True)

            # Restore all channels. The public Market Channel has a distinct
            # initial frame; dynamic ``operation`` frames are valid only after
            # the connection is open.
            subscription_count = len(self._subscriptions)
            if subscription_count > 0:
                logger.info(f"Resubscribing to {subscription_count} channel(s)")

                market_token_ids = [
                    channel.split(":", 1)[1]
                    for channel in self._subscriptions
                    if channel.startswith(f"{ChannelType.MARKET.value}:")
                ]
                if market_token_ids and self._channel_type == ChannelType.MARKET:
                    self._send_initial_market_subscribe(market_token_ids)
                if ChannelType.USER.value in self._subscriptions:
                    logger.debug("Resubscribing to USER channel")
                    self._send_subscribe(ChannelType.USER, None)

                logger.info(
                    f"Resubscription complete: {subscription_count} channel(s) restored"
                )

    def _on_message(self, ws, message: str) -> None:
        """Handle incoming message."""
        # websocket-client always supplies its WebSocketApp. ``None`` remains a
        # unit-level injection seam; real callbacks must belong to the current,
        # open run so stale generations cannot enter accounting or dispatch.
        if ws is not None:
            with self._lock:
                if not self._running or not self._connected or ws is not self._ws:
                    logger.debug("Ignoring stale WebSocket message callback")
                    return
                lifecycle_generation = self._lifecycle_generation
        else:
            with self._lock:
                lifecycle_generation = self._lifecycle_generation

        processing_start = time.time()
        receipt_monotonic = time.monotonic()
        with self._lock:
            self._message_count += 1
            self._last_message_time = processing_start
            self._last_receipt_time = processing_start
            self._last_receipt_monotonic = receipt_monotonic

        try:
            decoded = json.loads(message)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            with self._lock:
                self._parse_failures += 1
            logger.warning(f"Failed to decode WebSocket message: {exc}")
            return

        if isinstance(decoded, dict):
            payloads = (decoded,)
        elif isinstance(decoded, list):
            payloads = decoded
        else:
            with self._lock:
                self._parse_failures += 1
            logger.warning(
                "WebSocket message root must be an object or array of objects"
            )
            return

        # Initial Market Channel snapshots may arrive as one JSON array. Parse
        # each element independently so one malformed entry cannot discard
        # valid siblings from the same wire frame.
        for data in payloads:
            if not isinstance(data, dict):
                with self._lock:
                    self._parse_failures += 1
                logger.warning("Ignoring non-object item in WebSocket message array")
                continue
            self._process_decoded_message(data, processing_start, lifecycle_generation)

    def _process_decoded_message(
        self,
        data: dict,
        processing_start: float,
        lifecycle_generation: int,
    ) -> None:
        """Parse and dispatch one object from a scalar or array wire frame."""
        try:
            typed_message = parse_websocket_message(data)
        except Exception as exc:
            with self._lock:
                self._parse_failures += 1
            logger.warning(f"Failed to parse message: {exc}")
            return

        if typed_message is None:
            with self._lock:
                self._unknown_messages += 1
            logger.debug(f"Unknown message type: {data.get('event_type')}")
            return

        with self._lock:
            self._messages_parsed += 1

        # Message deduplication (v3.5 - L2). Official last-trade prints
        # have no documented stable event ID, so content hashes could create
        # false data loss and are intentionally bypassed for those prints.
        try:
            message_hash: Optional[str] = None
            if (
                self.enable_deduplication
                and data.get("event_type") != CLOBEventType.LAST_TRADE_PRICE.value
            ):
                message_hash = self._compute_message_hash(data)

                # Check if we've seen this message recently
                if self._is_duplicate_message(
                    message_hash,
                    lifecycle_generation=lifecycle_generation,
                ):
                    with self._lock:
                        self._duplicate_count += 1
                    logger.debug(
                        f"Duplicate message detected (hash={message_hash[:8]}...), skipping"
                    )
                    if self._metrics:
                        self._metrics.track_websocket_duplicate("clob")
                    return

            # Determine channel
            event_type = data.get("event_type")

            # Track message metrics
            if self._metrics and event_type:
                channel_label = (
                    "market"
                    if event_type
                    in [
                        CLOBEventType.BOOK,
                        CLOBEventType.PRICE_CHANGE,
                        CLOBEventType.TICK_SIZE_CHANGE,
                        CLOBEventType.LAST_TRADE_PRICE,
                    ]
                    else "user"
                )
                self._metrics.track_websocket_message(channel_label, event_type)

            # Queue message for async processing (v3.3) or invoke callback directly
            queued_delivery = self._enable_queue and self._message_queue is not None
            if queued_delivery:
                # Determine if message is from USER channel (critical: trade/order updates)
                is_user_channel = event_type in [
                    CLOBEventType.TRADE,
                    CLOBEventType.ORDER,
                ]

                # Enqueue message with metadata for consumer task
                message_item = {
                    "typed_message": typed_message,
                    "event_type": event_type,
                    "data": data,
                    "processing_start": processing_start,
                    "is_user_channel": is_user_channel,
                    "lifecycle_generation": lifecycle_generation,
                    "dedup_hash": message_hash,
                }
                accepted = self._enqueue_message(
                    message_item, is_user_channel, event_type
                )
            else:
                # Direct callback invocation (legacy behavior)
                accepted = self._invoke_callback(
                    typed_message,
                    event_type,
                    data,
                    lifecycle_generation=lifecycle_generation,
                )

            # Record a hash only after the message was admitted to the queue or
            # accepted by the current direct-dispatch lifecycle. A locally
            # dropped message must remain retryable.
            if not queued_delivery and accepted and message_hash is not None:
                self._track_message_hash(
                    message_hash,
                    processing_start,
                    lifecycle_generation=lifecycle_generation,
                )

            # Track processing time (for enqueue time, not callback time)
            if self._metrics and event_type:
                duration = time.time() - processing_start
                self._metrics.track_websocket_processing(
                    channel_label, event_type, duration
                )

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _enqueue_message(
        self,
        message_item: dict,
        is_user_channel: bool,
        event_type: str,
    ) -> bool:
        """Enqueue one item, returning whether the incoming item was accepted."""
        if self._message_queue is None:
            return False

        dropped = 0
        stale_drop = False
        accepted = False
        with self._lock:
            if message_item["lifecycle_generation"] != self._lifecycle_generation:
                dropped = 1
                stale_drop = True
                queue_size = self._message_queue.qsize()
            else:
                # Keep lock ordering consistent with the consumer and teardown:
                # lifecycle lock first, then the compound queue-operation lock.
                with self._queue_lock:
                    try:
                        self._message_queue.put_nowait(message_item)
                        accepted = True
                    except queue.Full:
                        if not is_user_channel:
                            dropped = 1
                        else:
                            # Preserve USER messages by replacing the oldest MARKET item.
                            try:
                                oldest = self._message_queue.get_nowait()
                            except queue.Empty:
                                oldest = None

                            if oldest is None:
                                try:
                                    self._message_queue.put_nowait(message_item)
                                    accepted = True
                                except queue.Full:
                                    dropped = 1
                            elif oldest.get("is_user_channel", False):
                                # Offset the old unfinished-task entry before restoring it.
                                self._message_queue.task_done()
                                try:
                                    self._message_queue.put_nowait(oldest)
                                except queue.Full:
                                    dropped += 1
                                dropped += 1  # Incoming USER message was not inserted.
                            else:
                                self._message_queue.task_done()
                                dropped += 1  # Replaced one MARKET message.
                                evicted_hash = oldest.get("dedup_hash")
                                if evicted_hash is not None:
                                    self._forget_message_hash(
                                        evicted_hash,
                                        lifecycle_generation=oldest.get(
                                            "lifecycle_generation"
                                        ),
                                    )
                                try:
                                    self._message_queue.put_nowait(message_item)
                                    accepted = True
                                except queue.Full:
                                    dropped += 1

                    queue_size = self._message_queue.qsize()

                # Admission and dedupe recording share lifecycle/queue
                # ownership so an immediate USER-preservation eviction can
                # reliably roll back an undelivered MARKET hash.
                dedup_hash = message_item.get("dedup_hash")
                if accepted and dedup_hash is not None:
                    self._track_message_hash(
                        dedup_hash,
                        message_item["processing_start"],
                        lifecycle_generation=message_item["lifecycle_generation"],
                    )

            self._queue_high_water = max(self._queue_high_water, queue_size)
            self._queue_drops += dropped
            if stale_drop:
                self._stale_lifecycle_messages_dropped += 1
            total_drops = self._queue_drops

        if dropped:
            level = logging.CRITICAL if is_user_channel else logging.WARNING
            logger.log(
                level,
                "WebSocket queue dropped %s message(s) while handling %s (total drops: %s)",
                dropped,
                event_type,
                total_drops,
            )
            if self._metrics:
                for _ in range(dropped):
                    self._metrics.track_websocket_queue_drop("clob")

            if total_drops >= self.queue_drop_threshold:
                logger.critical(
                    "Queue drops (%s) exceeded threshold (%s); processing is backlogged",
                    total_drops,
                    self.queue_drop_threshold,
                )

        return accepted

    def _on_error(self, ws, error) -> None:
        """Handle error."""
        if is_transient_websocket_disconnect(error):
            logger.warning(f"WebSocket transient disconnect: {error}")
            return
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        """Handle connection close."""
        self._record_transport_disconnect(ws)
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")

    def _send_subscribe(
        self, channel_type: ChannelType, asset_id: Optional[str]
    ) -> None:
        """
        Send subscribe message using official Polymarket format (single asset).

        Official Market update format:
        ``{"operation": "subscribe", "assets_ids": ["token_id"]}``.

        The authenticated USER frame remains on its existing compatibility
        path; this repair changes only the public Market Channel.

        For multi-asset subscriptions, use _send_subscribe_multi().
        """
        if not self._ws:
            return

        if channel_type == ChannelType.MARKET and asset_id:
            msg = {
                "operation": "subscribe",
                "assets_ids": [asset_id],
                "custom_feature_enabled": False,
            }
        elif channel_type == ChannelType.USER and all(
            (self.api_key, self.api_secret, self.api_passphrase)
        ):
            msg = {
                "auth": {
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.api_passphrase,
                },
                "markets": [],
                "type": channel_type.value,
            }
        else:
            return

        try:
            self._ws.send(json.dumps(msg))
            logger.debug("Sent %s subscribe", channel_type.value.upper())
        except Exception as e:
            self._record_local_send_failure("subscribe")
            logger.error(f"Failed to send subscribe: {e}")

    def _send_initial_market_subscribe(self, asset_ids: list[str]) -> None:
        """Send the official initial public Market Channel frame once per open."""
        if not self._ws or not asset_ids:
            return

        msg = {
            "type": ChannelType.MARKET.value,
            "assets_ids": sorted(asset_ids),
            "custom_feature_enabled": False,
        }
        try:
            self._ws.send(json.dumps(msg))
            logger.debug("Sent initial MARKET subscribe for %s assets", len(asset_ids))
        except Exception as e:
            self._record_local_send_failure("subscribe")
            logger.error(f"Failed to send initial market subscribe: {e}")

    def _send_subscribe_multi(
        self, channel_type: ChannelType, asset_ids: list[str]
    ) -> None:
        """
        Send subscribe message for multiple assets in single message (v3.5 - L1).

        Official dynamic format:
        ``{"operation": "subscribe", "assets_ids": ["id1", "id2"]}``.

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

        msg = {
            "operation": "subscribe",
            "assets_ids": asset_ids,
            "custom_feature_enabled": False,
        }

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent multi-subscribe for {len(asset_ids)} assets: {msg}")
        except Exception as e:
            self._record_local_send_failure("subscribe")
            logger.error(f"Failed to send multi-subscribe: {e}")

    def _send_unsubscribe(self, channel: str) -> None:
        """Send unsubscribe message."""
        if not self._ws:
            return

        if channel.startswith(f"{ChannelType.MARKET.value}:"):
            msg = {
                "operation": "unsubscribe",
                "assets_ids": [channel.split(":", 1)[1]],
            }
        else:
            # Preserve the pre-existing authenticated USER compatibility frame.
            msg = {"type": "unsubscribe", "channel": channel}

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent unsubscribe: {msg}")
        except Exception as e:
            self._record_local_send_failure("unsubscribe")
            logger.error(f"Failed to send unsubscribe: {e}")

    def _record_local_send_failure(self, operation: str) -> None:
        """Record only locally observed ``ws.send`` exceptions."""
        with self._lock:
            self._local_send_failures += 1
            if operation == "subscribe":
                self._local_subscribe_send_failures += 1
            elif operation == "unsubscribe":
                self._local_unsubscribe_send_failures += 1
            self._last_local_send_failure_time = time.time()

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

    def _is_duplicate_message(
        self,
        message_hash: str,
        lifecycle_generation: Optional[int] = None,
    ) -> bool:
        """
        Check if message hash was seen recently (v3.5 - L2).

        Args:
            message_hash: Message hash from _compute_message_hash()
            lifecycle_generation: Explicit connection lifecycle. Defaults to
                the current lifecycle for compatibility with direct callers.

        Returns:
            True if duplicate, False if new message
        """
        if lifecycle_generation is None:
            with self._lock:
                lifecycle_generation = self._lifecycle_generation
        dedup_key = (lifecycle_generation, message_hash)

        with self._dedup_lock:
            # Clean up old hashes outside time window
            current_time = time.time()
            while (
                self._seen_hash_timestamps
                and current_time - self._seen_hash_timestamps[0]
                > self.dedup_window_seconds
            ):
                self._seen_hash_timestamps.popleft()
                self._seen_message_hashes.popleft()

            # Check if hash exists in recent messages
            return dedup_key in self._seen_message_hashes

    def _track_message_hash(
        self,
        message_hash: str,
        timestamp: float,
        lifecycle_generation: Optional[int] = None,
    ) -> None:
        """
        Track message hash for deduplication (v3.5 - L2).

        Args:
            message_hash: Message hash from _compute_message_hash()
            timestamp: Message processing timestamp
            lifecycle_generation: Explicit connection lifecycle. Defaults to
                the current lifecycle for compatibility with direct callers.
        """
        if lifecycle_generation is None:
            with self._lock:
                lifecycle_generation = self._lifecycle_generation
        with self._dedup_lock:
            self._seen_message_hashes.append((lifecycle_generation, message_hash))
            self._seen_hash_timestamps.append(timestamp)

    def _forget_message_hash(
        self,
        message_hash: str,
        lifecycle_generation: Optional[int] = None,
    ) -> None:
        """Forget one undelivered queued message so a retry remains admissible."""
        if lifecycle_generation is None:
            with self._lock:
                lifecycle_generation = self._lifecycle_generation
        dedup_key = (lifecycle_generation, message_hash)
        with self._dedup_lock:
            try:
                index = self._seen_message_hashes.index(dedup_key)
            except ValueError:
                return
            del self._seen_message_hashes[index]
            del self._seen_hash_timestamps[index]

    def _invoke_failure_callback(self, reason: str) -> None:
        """
        Invoke failure callback if registered.

        Args:
            reason: Failure reason (e.g., "Max reconnects exceeded (10 attempts)")
        """
        if self.on_failure_callback:
            with self._lock:
                self._failure_callbacks_invoked += 1
            try:
                self.on_failure_callback(reason)
            except Exception as e:
                with self._lock:
                    self._failure_callback_failures += 1
                logger.error(f"Error in failure callback: {e}", exc_info=True)

    def _invoke_callback(
        self,
        typed_message: WebSocketMessage,
        event_type: str,
        data: dict,
        lifecycle_generation: Optional[int] = None,
    ) -> bool:
        """
        Invoke registered callbacks for message.

        Args:
            typed_message: Parsed typed message
            event_type: Event type from message
            data: Raw message data
            lifecycle_generation: Lifecycle that received the message, when known
        """
        with self._lock:
            if (
                lifecycle_generation is not None
                and lifecycle_generation != self._lifecycle_generation
            ):
                self._stale_lifecycle_messages_dropped += 1
                return False

            callback_invoked = False

            # Market channel messages
            if event_type in [
                CLOBEventType.BOOK,
                CLOBEventType.PRICE_CHANGE,
                CLOBEventType.TICK_SIZE_CHANGE,
                CLOBEventType.LAST_TRADE_PRICE,
            ]:
                # Find matching market subscription
                asset_id = data.get("asset_id")
                if asset_id:
                    channel = f"{ChannelType.MARKET.value}:{asset_id}"
                    callback = self._subscriptions.get(channel)
                    if callback:
                        callback_invoked = True
                        self._callbacks_invoked += 1
                        try:
                            callback(typed_message)
                        except Exception as e:
                            self._callback_failures += 1
                            logger.error(
                                f"Error in callback for {channel}: {e}", exc_info=True
                            )
                        finally:
                            self._last_callback_completed_time = time.time()

            # User channel messages
            elif event_type in [CLOBEventType.TRADE, CLOBEventType.ORDER]:
                callback = self._subscriptions.get(ChannelType.USER)
                if callback:
                    callback_invoked = True
                    self._callbacks_invoked += 1
                    try:
                        callback(typed_message)
                    except Exception as e:
                        self._callback_failures += 1
                        logger.error(
                            f"Error in callback for USER channel: {e}", exc_info=True
                        )
                    finally:
                        self._last_callback_completed_time = time.time()

            return callback_invoked

    async def _consume_messages(
        self, lifecycle_generation: Optional[int] = None
    ) -> None:
        """
        Consumer task that processes messages from queue asynchronously.

        Runs continuously until cancelled. Prevents WebSocket callback
        thread from blocking on async I/O operations.
        """
        logger.info("Starting message consumer task")
        with self._lock:
            if lifecycle_generation is None:
                lifecycle_generation = self._lifecycle_generation
            message_queue = self._message_queue
        if message_queue is None:
            logger.info("Message consumer task stopped: queue unavailable")
            return

        consecutive_acquisitions = 0
        try:
            while True:
                message_acquired = False
                try:
                    # Pin lifecycle ownership through queue acquisition and
                    # synchronous callback dispatch. A restart cannot revive an
                    # old delayed consumer against the new generation.
                    with self._lock:
                        if (
                            not self._running
                            or lifecycle_generation != self._lifecycle_generation
                        ):
                            break
                        with self._queue_lock:
                            message_item = message_queue.get_nowait()
                            message_acquired = True

                        item_generation = message_item.get(
                            "lifecycle_generation", lifecycle_generation
                        )
                        if (
                            item_generation != lifecycle_generation
                            or lifecycle_generation != self._lifecycle_generation
                        ):
                            self._queue_drops += 1
                            self._stale_lifecycle_messages_dropped += 1
                            if self._metrics:
                                self._metrics.track_websocket_queue_drop("clob")
                            continue

                        typed_message = message_item["typed_message"]
                        event_type = message_item["event_type"]
                        data = message_item["data"]
                        processing_start = message_item["processing_start"]

                        queue_lag = time.time() - processing_start
                        if self._metrics:
                            self._metrics.track_websocket_queue_lag("clob", queue_lag)

                        self._invoke_callback(typed_message, event_type, data)

                except queue.Empty:
                    # Queue is empty, sleep briefly to avoid busy-wait
                    await asyncio.sleep(0.01)  # 10ms polling

                except Exception as e:
                    logger.error(f"Error in consumer task: {e}", exc_info=True)
                    # Continue processing other messages
                    await asyncio.sleep(0.1)
                finally:
                    if message_acquired:
                        message_queue.task_done()
                        consecutive_acquisitions += 1
                        if consecutive_acquisitions >= _CONSUMER_FAIRNESS_BATCH_SIZE:
                            consecutive_acquisitions = 0
                            # A continuously nonempty queue must not monopolize
                            # the event loop; probe deadlines and teardown tasks
                            # need a scheduling point under sustained traffic.
                            await asyncio.sleep(0)

        except asyncio.CancelledError:
            logger.info("Consumer task cancelled")
            raise
        finally:
            logger.info("Consumer task stopped")

    # ========== Health Monitoring (v3.2) ==========

    def telemetry_snapshot_v1(self) -> WebSocketTelemetrySnapshotV1:
        """Return bounded aggregate telemetry without subscription identifiers.

        Reconnect silences cover only locally observed transport disconnect-to-open
        intervals. They cannot establish source sequence completeness or count
        messages that the source may have emitted while this client was offline.
        """
        captured_at = time.time()
        captured_monotonic = time.monotonic()

        with self._lock:
            queue_enabled = self._enable_queue and self._message_queue is not None
            queue_size = self._message_queue.qsize() if queue_enabled else 0
            queue_high_water = max(self._queue_high_water, queue_size)
            queue_consumer_running = bool(
                queue_enabled
                and self._running
                and self._consumer_task is not None
                and not self._consumer_task.done()
                and self._event_loop is not None
                and self._event_loop.is_running()
            )

            observed_silences = list(self._observed_reconnect_silences)
            observed_count = self._observed_reconnect_silence_count
            observed_discarded = self._observed_reconnect_silences_discarded
            current_silence_started_at = None
            if self._current_reconnect_silence is not None:
                generation, started_at, started_monotonic = (
                    self._current_reconnect_silence
                )
                current_silence_started_at = started_at
                duration_seconds = max(0.0, captured_monotonic - started_monotonic)
                if duration_seconds >= self.reconnect_silence_threshold_seconds:
                    current = WebSocketObservedSilenceV1(
                        connection_generation=generation,
                        started_at=started_at,
                        ended_at=None,
                        duration_seconds=duration_seconds,
                    )
                    if len(observed_silences) >= self.reconnect_silence_history_size:
                        keep = self.reconnect_silence_history_size - 1
                        observed_silences = observed_silences[-keep:] if keep else []
                        observed_discarded += 1
                    observed_silences.append(current)
                    observed_count += 1

            last_receipt_age_seconds = None
            if self._last_receipt_monotonic is not None:
                last_receipt_age_seconds = max(
                    0.0, captured_monotonic - self._last_receipt_monotonic
                )

            return WebSocketTelemetrySnapshotV1(
                captured_at=captured_at,
                running=self._running,
                connected=self._connected,
                connection_generation=self._connection_generation,
                subscriptions=len(self._subscriptions),
                messages_received=self._message_count,
                messages_parsed=self._messages_parsed,
                parse_failures=self._parse_failures,
                unknown_messages=self._unknown_messages,
                duplicates_blocked=self._duplicate_count,
                last_receipt_time=self._last_receipt_time,
                last_receipt_age_seconds=last_receipt_age_seconds,
                queue_enabled=queue_enabled,
                queue_capacity=(self._message_queue.maxsize if queue_enabled else 0),
                queue_drop_threshold=self.queue_drop_threshold,
                queue_consumer_running=queue_consumer_running,
                queue_size=queue_size,
                queue_high_water=queue_high_water,
                queue_drops=self._queue_drops,
                stale_lifecycle_messages_dropped=(
                    self._stale_lifecycle_messages_dropped
                ),
                callbacks_invoked=self._callbacks_invoked,
                callback_failures=self._callback_failures,
                last_callback_completed_time=self._last_callback_completed_time,
                failure_callbacks_invoked=self._failure_callbacks_invoked,
                failure_callback_failures=self._failure_callback_failures,
                reconnect_delay_seconds=self.reconnect_delay,
                max_reconnects=self.max_reconnects,
                reconnect_attempts=self._total_reconnect_attempts,
                reconnect_completions=self._total_reconnections,
                reconnect_exhaustions=self._reconnect_exhaustions,
                current_reconnect_attempts=self._reconnect_count,
                disconnects=self._disconnect_count,
                intentional_disconnects=self._intentional_disconnect_count,
                reconnect_silence_threshold_seconds=(
                    self.reconnect_silence_threshold_seconds
                ),
                reconnect_silence_history_limit=self.reconnect_silence_history_size,
                observed_reconnect_silence_count=observed_count,
                observed_reconnect_silences_discarded=observed_discarded,
                observed_reconnect_silences=tuple(observed_silences),
                current_reconnect_silence_started_at=current_silence_started_at,
                local_send_failures=self._local_send_failures,
                local_subscribe_send_failures=self._local_subscribe_send_failures,
                local_unsubscribe_send_failures=self._local_unsubscribe_send_failures,
                last_local_send_failure_time=self._last_local_send_failure_time,
            )

    def stats(self) -> Dict[str, Any]:
        """
        Get connection statistics for monitoring.

        Returns:
            dict: Metrics including uptime, message count, reconnections, queue stats (v3.3), etc.
        """
        snapshot = self.telemetry_snapshot_v1()
        uptime_seconds = None
        if self._connection_start_time and snapshot.connected:
            uptime_seconds = int(time.time() - self._connection_start_time)

        stats_dict = {
            "status": (
                "connected"
                if snapshot.connected
                else "reconnecting"
                if snapshot.running
                else "disconnected"
            ),
            "connected": snapshot.connected,
            "running": snapshot.running,
            "uptime_seconds": uptime_seconds,
            "messages_received": snapshot.messages_received,
            "reconnections": snapshot.reconnect_completions,
            "current_reconnect_attempts": snapshot.current_reconnect_attempts,
            "subscriptions": snapshot.subscriptions,
            "last_message_seconds_ago": (
                int(snapshot.last_receipt_age_seconds)
                if snapshot.last_receipt_age_seconds is not None
                else None
            ),
        }

        # Add queue stats if queue enabled (v3.3)
        if snapshot.queue_enabled:
            stats_dict["queue_enabled"] = True
            stats_dict["queue_size"] = snapshot.queue_size
            stats_dict["queue_drops"] = snapshot.queue_drops
            stats_dict["consumer_task_running"] = snapshot.queue_consumer_running
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
        snapshot = self.telemetry_snapshot_v1()
        if not snapshot.running:
            return {"status": "disconnected"}

        if not snapshot.connected:
            return {"status": "degraded", "reason": "transport_not_connected"}

        # Check message freshness (no messages for 60s = stale)
        if snapshot.last_receipt_age_seconds is None:
            return {"status": "degraded", "reason": "no_messages_received"}
        time_since_last = snapshot.last_receipt_age_seconds
        if time_since_last > 60:
            return {
                "status": "degraded",
                "reason": "no_recent_messages",
                "last_message_seconds_ago": int(time_since_last),
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
