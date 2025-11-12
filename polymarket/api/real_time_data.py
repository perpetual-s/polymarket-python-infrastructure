"""
Real-Time Data Client for Polymarket.

Python port of @polymarket/real-time-data-client (TypeScript).
Supports 12+ WebSocket streams for live data.

Based on: https://github.com/Polymarket/real-time-data-client
"""

import json
import time
import logging
import threading
from typing import Optional, Callable, Dict, List, Any
from enum import Enum
from dataclasses import dataclass

try:
    import websocket  # websocket-client library
except ImportError:
    raise ImportError(
        "websocket-client required for real-time data. "
        "Install with: pip install websocket-client"
    )

logger = logging.getLogger(__name__)


class ConnectionStatus(str, Enum):
    """WebSocket connection status."""
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class ClobApiKeyCreds:
    """API key credentials for CLOB authentication."""
    key: str
    secret: str
    passphrase: str


@dataclass
class Subscription:
    """Subscription configuration."""
    topic: str
    type: str
    filters: Optional[str] = None
    clob_auth: Optional[ClobApiKeyCreds] = None


@dataclass
class Message:
    """Real-time message from WebSocket."""
    topic: str
    type: str
    timestamp: int
    payload: Dict[str, Any]
    connection_id: str


class RealTimeDataClient:
    """
    Real-Time Data Client for Polymarket WebSocket streams.

    Supports 12+ data streams:
    - activity: trades, orders_matched
    - comments: comment_created, comment_removed, reactions
    - rfq: request/quote lifecycle
    - crypto_prices: BTC/ETH/SOL prices
    - clob_user: user orders and trades (authenticated)
    - clob_market: price changes, orderbook, market lifecycle

    Example:
        ```python
        def on_trade(client, message):
            print(f"Trade: {message.payload}")

        client = RealTimeDataClient(
            on_connect=lambda c: c.subscribe("activity", "trades"),
            on_message=on_trade
        )
        client.connect()
        ```
    """

    DEFAULT_HOST = "wss://ws-live-data.polymarket.com"
    DEFAULT_PING_INTERVAL = 5.0  # seconds

    def __init__(
        self,
        host: Optional[str] = None,
        on_connect: Optional[Callable[['RealTimeDataClient'], None]] = None,
        on_message: Optional[Callable[['RealTimeDataClient', Message], None]] = None,
        on_status_change: Optional[Callable[[ConnectionStatus], None]] = None,
        auto_reconnect: bool = True,
        ping_interval: float = DEFAULT_PING_INTERVAL
    ):
        """
        Initialize Real-Time Data Client.

        Args:
            host: WebSocket server URL (default: wss://ws-live-data.polymarket.com)
            on_connect: Callback when connection established
            on_message: Callback for incoming messages
            on_status_change: Callback for connection status changes
            auto_reconnect: Automatically reconnect on disconnect
            ping_interval: Ping interval in seconds (default: 5.0)
        """
        self.host = host or self.DEFAULT_HOST
        self.on_connect = on_connect
        self.on_custom_message = on_message
        self.on_status_change_callback = on_status_change
        self.auto_reconnect = auto_reconnect
        self.ping_interval = ping_interval

        # WebSocket instance
        self.ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None

        # Connection state
        self._status = ConnectionStatus.DISCONNECTED
        self._shutdown_requested = False

        # Ping management
        self._ping_timer: Optional[threading.Timer] = None
        self._last_pong = time.time()

        # Reconnection management (exponential backoff)
        self._reconnect_attempts = 0
        self._max_reconnect_delay = 300  # 5 minutes max
        self._reconnect_timer: Optional[threading.Timer] = None

        # Subscription tracking (for resubscription after reconnect)
        self._active_subscriptions = []
        self._subscriptions_lock = threading.RLock()  # Thread-safe access to subscriptions

        # Monitoring metrics
        self._connection_start_time: Optional[float] = None
        self._total_messages_received = 0
        self._total_reconnections = 0

        logger.info(f"Initialized RealTimeDataClient: {self.host}")

    def connect(self) -> 'RealTimeDataClient':
        """
        Establish WebSocket connection.

        Returns:
            Self for chaining
        """
        if self._shutdown_requested:
            logger.warning("Cannot connect: shutdown requested")
            return self

        self._notify_status_change(ConnectionStatus.CONNECTING)

        # Create WebSocket app
        self.ws = websocket.WebSocketApp(
            self.host,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_pong=self._on_pong
        )

        # Run in separate thread
        self._ws_thread = threading.Thread(
            target=self.ws.run_forever,
            kwargs={"ping_interval": 0},  # We handle pings manually
            daemon=True
        )
        self._ws_thread.start()

        logger.info("WebSocket connection initiated")
        return self

    def disconnect(self):
        """Close WebSocket connection."""
        logger.info("Disconnecting...")
        self._shutdown_requested = True
        self.auto_reconnect = False

        # Cancel ping timer
        if self._ping_timer:
            self._ping_timer.cancel()
            self._ping_timer = None

        # Cancel reconnect timer
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None

        # Close WebSocket
        if self.ws:
            self.ws.close()
            self.ws = None

        logger.info("Disconnected")

    def subscribe(
        self,
        topic: str,
        type: str = "*",
        filters: Optional[str] = None,
        clob_auth: Optional[ClobApiKeyCreds] = None
    ):
        """
        Subscribe to a data stream.

        Args:
            topic: Topic name (e.g., "activity", "comments", "clob_market")
            type: Message type (e.g., "trades", "*" for all)
            filters: JSON filter string (e.g., '{"market_slug":"trump-2024"}')
            clob_auth: CLOB API credentials (required for clob_user topic)

        Example:
            # Subscribe to all trades
            client.subscribe("activity", "trades")

            # Subscribe to specific market trades
            client.subscribe(
                "activity",
                "trades",
                filters='{"market_slug":"trump-2024"}'
            )

            # Subscribe to user orders (authenticated)
            client.subscribe(
                "clob_user",
                "order",
                clob_auth=ClobApiKeyCreds(key="...", secret="...", passphrase="...")
            )
        """
        if not self.ws or not self._is_connected():
            logger.warning("Cannot subscribe: not connected")
            return

        subscription = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": topic,
                    "type": type,
                }
            ]
        }

        # Add filters if provided
        if filters:
            subscription["subscriptions"][0]["filters"] = filters

        # Add CLOB auth if provided
        if clob_auth:
            subscription["subscriptions"][0]["clob_auth"] = {
                "key": clob_auth.key,
                "secret": clob_auth.secret,
                "passphrase": clob_auth.passphrase
            }

        try:
            self.ws.send(json.dumps(subscription))
            logger.info(f"Subscribed: {topic}/{type}")

            # CRITICAL: Track subscription for resubscription after reconnect
            sub_record = {
                "topic": topic,
                "type": type,
                "filters": filters,
                "clob_auth": clob_auth,
            }

            # Avoid duplicates (check if already tracked) - THREAD-SAFE
            sub_key = (topic, type, filters)
            with self._subscriptions_lock:
                if not any(
                    (s["topic"], s["type"], s["filters"]) == sub_key
                    for s in self._active_subscriptions
                ):
                    self._active_subscriptions.append(sub_record)
                    logger.debug(f"Tracked subscription: {topic}/{type} (total: {len(self._active_subscriptions)})")

        except Exception as e:
            logger.error(f"Subscribe failed: {e}")

    def unsubscribe(
        self,
        topic: str,
        type: str = "*",
        filters: Optional[str] = None
    ):
        """
        Unsubscribe from a data stream.

        Args:
            topic: Topic name
            type: Message type
            filters: JSON filter string (must match subscription)
        """
        if not self.ws or not self._is_connected():
            logger.warning("Cannot unsubscribe: not connected")
            return

        unsubscription = {
            "action": "unsubscribe",
            "subscriptions": [
                {
                    "topic": topic,
                    "type": type,
                }
            ]
        }

        if filters:
            unsubscription["subscriptions"][0]["filters"] = filters

        try:
            self.ws.send(json.dumps(unsubscription))
            logger.info(f"Unsubscribed: {topic}/{type}")

            # Remove from tracked subscriptions - THREAD-SAFE
            sub_key = (topic, type, filters)
            with self._subscriptions_lock:
                self._active_subscriptions = [
                    s for s in self._active_subscriptions
                    if (s["topic"], s["type"], s["filters"]) != sub_key
                ]
                logger.debug(f"Removed subscription tracking: {topic}/{type} (total: {len(self._active_subscriptions)})")

        except Exception as e:
            logger.error(f"Unsubscribe failed: {e}")

    # ========== Private Methods ==========

    def _on_open(self, ws):
        """WebSocket open handler."""
        logger.info("WebSocket connected")
        self._notify_status_change(ConnectionStatus.CONNECTED)

        # Reset reconnection attempts on successful connection
        if self._reconnect_attempts > 0:
            self._total_reconnections += 1
        self._reconnect_attempts = 0

        # Track connection start time for uptime monitoring
        self._connection_start_time = time.time()

        # CRITICAL: Resubscribe to all active subscriptions after reconnect - THREAD-SAFE
        with self._subscriptions_lock:
            subscriptions_to_restore = self._active_subscriptions.copy()

        if subscriptions_to_restore:
            logger.info(f"Resubscribing to {len(subscriptions_to_restore)} streams...")
            for sub in subscriptions_to_restore:
                try:
                    # Build subscription message
                    subscription = {
                        "action": "subscribe",
                        "subscriptions": [{
                            "topic": sub["topic"],
                            "type": sub["type"],
                        }]
                    }

                    # Add filters if provided
                    if sub["filters"]:
                        subscription["subscriptions"][0]["filters"] = sub["filters"]

                    # Add CLOB auth if provided
                    if sub["clob_auth"]:
                        subscription["subscriptions"][0]["clob_auth"] = {
                            "key": sub["clob_auth"].key,
                            "secret": sub["clob_auth"].secret,
                            "passphrase": sub["clob_auth"].passphrase
                        }

                    # Send subscription (use ws directly to avoid re-tracking)
                    self.ws.send(json.dumps(subscription))
                    logger.info(f"Resubscribed: {sub['topic']}/{sub['type']}")

                except Exception as e:
                    logger.error(f"Failed to resubscribe to {sub['topic']}: {e}")

        # Start ping mechanism
        self._schedule_ping()

        # Notify application
        if self.on_connect:
            try:
                self.on_connect(self)
            except Exception as e:
                logger.error(f"on_connect callback error: {e}")

    def _on_message(self, ws, raw_message: str):
        """WebSocket message handler."""
        try:
            # Parse message
            if not raw_message or not isinstance(raw_message, str):
                return

            # Skip non-JSON messages
            if not raw_message.strip().startswith("{"):
                return

            data = json.loads(raw_message)
            self._total_messages_received += 1

            # Check if this is a data message (has payload)
            if "payload" in data:
                message = Message(
                    topic=data.get("topic", ""),
                    type=data.get("type", ""),
                    timestamp=data.get("timestamp", int(time.time() * 1000)),
                    payload=data.get("payload", {}),
                    connection_id=data.get("connection_id", "")
                )

                # Notify application
                if self.on_custom_message:
                    try:
                        self.on_custom_message(self, message)
                    except Exception as e:
                        logger.error(f"on_message callback error: {e}")
            else:
                # System message (subscription confirmation, etc.)
                logger.debug(f"System message: {data}")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Message handler error: {e}")

    def _on_error(self, ws, error):
        """WebSocket error handler."""
        logger.error(f"WebSocket error: {error}")

        if self.auto_reconnect and not self._shutdown_requested:
            # Exponential backoff: 2, 4, 8, 16, 32, 64, 128, 256, 300 max
            delay = min(2 ** self._reconnect_attempts, self._max_reconnect_delay)
            self._reconnect_attempts += 1

            logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})...")

            def reconnect():
                if not self._shutdown_requested:
                    self.connect()

            self._reconnect_timer = threading.Timer(delay, reconnect)
            self._reconnect_timer.daemon = True
            self._reconnect_timer.start()

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket close handler."""
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._notify_status_change(ConnectionStatus.DISCONNECTED)

        # Cancel ping timer
        if self._ping_timer:
            self._ping_timer.cancel()
            self._ping_timer = None

        if self.auto_reconnect and not self._shutdown_requested:
            # Exponential backoff: 2, 4, 8, 16, 32, 64, 128, 256, 300 max
            delay = min(2 ** self._reconnect_attempts, self._max_reconnect_delay)
            self._reconnect_attempts += 1

            logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})...")

            def reconnect():
                if not self._shutdown_requested:
                    self.connect()

            self._reconnect_timer = threading.Timer(delay, reconnect)
            self._reconnect_timer.daemon = True
            self._reconnect_timer.start()

    def _on_pong(self, ws, data):
        """WebSocket pong handler."""
        self._last_pong = time.time()
        logger.debug("Pong received")

        # Schedule next ping
        self._schedule_ping()

    def _schedule_ping(self):
        """Schedule next ping."""
        if self._shutdown_requested:
            return

        def send_ping():
            if self.ws and self._is_connected() and not self._shutdown_requested:
                try:
                    self.ws.send("ping")
                    logger.debug("Ping sent")
                except Exception as e:
                    logger.error(f"Ping failed: {e}")

        self._ping_timer = threading.Timer(self.ping_interval, send_ping)
        self._ping_timer.daemon = True
        self._ping_timer.start()

    def _is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.ws is not None and self.ws.sock and self.ws.sock.connected

    def _notify_status_change(self, status: ConnectionStatus):
        """Notify status change."""
        self._status = status
        if self.on_status_change_callback:
            try:
                self.on_status_change_callback(status)
            except Exception as e:
                logger.error(f"on_status_change callback error: {e}")

    # ========== Monitoring Methods ==========

    def get_status(self) -> ConnectionStatus:
        """Get current connection status."""
        return self._status

    def stats(self) -> dict:
        """
        Get connection statistics for monitoring.

        Returns:
            dict: Connection metrics including uptime, message count, reconnections, etc.
        """
        uptime_seconds = None
        if self._connection_start_time and self._is_connected():
            uptime_seconds = int(time.time() - self._connection_start_time)

        # Get subscription count thread-safely
        with self._subscriptions_lock:
            active_sub_count = len(self._active_subscriptions)

        return {
            "status": self._status.value,
            "connected": self._is_connected(),
            "uptime_seconds": uptime_seconds,
            "active_subscriptions": active_sub_count,
            "total_messages_received": self._total_messages_received,
            "total_reconnections": self._total_reconnections,
            "current_reconnect_attempts": self._reconnect_attempts,
            "last_pong_seconds_ago": int(time.time() - self._last_pong) if self._last_pong else None,
            "auto_reconnect_enabled": self.auto_reconnect,
        }


# ========== Stream-Specific Helpers ==========

class StreamHelpers:
    """Helper functions for common streaming patterns."""

    @staticmethod
    def subscribe_to_market_trades(
        client: RealTimeDataClient,
        market_slug: str
    ):
        """Subscribe to trades for a specific market."""
        client.subscribe(
            topic="activity",
            type="trades",
            filters=json.dumps({"market_slug": market_slug})
        )

    @staticmethod
    def subscribe_to_event_trades(
        client: RealTimeDataClient,
        event_slug: str
    ):
        """Subscribe to trades for all markets in an event."""
        client.subscribe(
            topic="activity",
            type="trades",
            filters=json.dumps({"event_slug": event_slug})
        )

    @staticmethod
    def subscribe_to_event_comments(
        client: RealTimeDataClient,
        event_id: int,
        parent_type: str = "Event"
    ):
        """Subscribe to comments for an event."""
        client.subscribe(
            topic="comments",
            type="*",
            filters=json.dumps({
                "parentEntityID": event_id,
                "parentEntityType": parent_type
            })
        )

    @staticmethod
    def subscribe_to_crypto_price(
        client: RealTimeDataClient,
        symbol: str
    ):
        """
        Subscribe to crypto price updates.

        Args:
            symbol: "btcusdt", "ethusdt", "solusdt", "xrpusdt"
        """
        client.subscribe(
            topic="crypto_prices",
            type="update",
            filters=json.dumps({"symbol": symbol.lower()})
        )

    @staticmethod
    def subscribe_to_market_orderbook(
        client: RealTimeDataClient,
        token_ids: List[str]
    ):
        """
        Subscribe to aggregated orderbook updates.

        Args:
            token_ids: List of token IDs to monitor
        """
        client.subscribe(
            topic="clob_market",
            type="agg_orderbook",
            filters=json.dumps(token_ids)
        )

    @staticmethod
    def subscribe_to_price_changes(
        client: RealTimeDataClient,
        token_ids: List[str]
    ):
        """Subscribe to price change events for tokens."""
        client.subscribe(
            topic="clob_market",
            type="price_change",
            filters=json.dumps(token_ids)
        )

    @staticmethod
    def subscribe_to_new_markets(client: RealTimeDataClient):
        """Subscribe to new market creation events."""
        client.subscribe(
            topic="clob_market",
            type="market_created"
        )

    @staticmethod
    def subscribe_to_market_resolutions(client: RealTimeDataClient):
        """Subscribe to market resolution events."""
        client.subscribe(
            topic="clob_market",
            type="market_resolved"
        )

    # ========== RFQ (Request for Quote) Streams ==========

    @staticmethod
    def subscribe_to_rfq_requests(
        client: RealTimeDataClient,
        market: Optional[str] = None
    ):
        """
        Subscribe to RFQ request events (OTC block trading).

        Args:
            client: RealTimeDataClient instance
            market: Filter by market condition ID (optional)

        Events:
            - request_created: New RFQ request
            - request_edited: RFQ request updated
            - request_canceled: RFQ request canceled
            - request_expired: RFQ request expired
        """
        filters = json.dumps({"market": market}) if market else None
        client.subscribe(
            topic="rfq",
            type="*",  # All request events
            filters=filters
        )

    @staticmethod
    def subscribe_to_rfq_quotes(
        client: RealTimeDataClient,
        request_id: Optional[str] = None
    ):
        """
        Subscribe to RFQ quote events.

        Args:
            client: RealTimeDataClient instance
            request_id: Filter by specific request ID (optional)

        Events:
            - quote_created: New quote for RFQ
            - quote_edited: Quote updated
            - quote_canceled: Quote canceled
            - quote_expired: Quote expired
        """
        filters = json.dumps({"requestId": request_id}) if request_id else None
        client.subscribe(
            topic="rfq",
            type="quote_*",  # All quote events
            filters=filters
        )

    @staticmethod
    def subscribe_to_all_rfq_events(client: RealTimeDataClient):
        """
        Subscribe to all RFQ events (requests and quotes).

        Useful for monitoring OTC trading activity across all markets.
        """
        client.subscribe(
            topic="rfq",
            type="*"
        )
