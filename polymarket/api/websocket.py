"""
WebSocket client for real-time updates.

Critical for high-frequency trading - much faster than polling.
"""

import json
import threading
import time
from typing import Optional, Callable, Dict, Any
from enum import Enum
import logging

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
    """

    def __init__(
        self,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws",
        api_key: Optional[str] = None,
        reconnect_delay: float = 5.0,
        max_reconnects: int = 10
    ):
        """
        Initialize WebSocket client.

        Args:
            ws_url: WebSocket URL
            api_key: API key for user channel (optional)
            reconnect_delay: Delay between reconnects
            max_reconnects: Max reconnect attempts
        """
        self.ws_url = ws_url
        self.api_key = api_key
        self.reconnect_delay = reconnect_delay
        self.max_reconnects = max_reconnects

        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._subscriptions: Dict[str, Callable] = {}
        self._lock = threading.RLock()
        self._reconnect_count = 0

        logger.info(f"WebSocket client initialized: {ws_url}")

    def connect(self) -> None:
        """Start WebSocket connection in background thread."""
        if self._running:
            logger.warning("WebSocket already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("WebSocket connection started")

    def disconnect(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("WebSocket disconnected")

    def subscribe_market(
        self,
        token_id: str,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Subscribe to market updates.

        Args:
            token_id: Token ID to track
            callback: Function called on updates
        """
        with self._lock:
            channel = f"{ChannelType.MARKET}:{token_id}"
            self._subscriptions[channel] = callback

            if self._ws:
                self._send_subscribe(ChannelType.MARKET, token_id)

        logger.info(f"Subscribed to market {token_id}")

    def subscribe_user(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Subscribe to user order/fill updates.

        Args:
            callback: Function called on updates
        """
        if not self.api_key:
            raise ValueError("API key required for user channel")

        with self._lock:
            channel = f"{ChannelType.USER}"
            self._subscriptions[channel] = callback

            if self._ws:
                self._send_subscribe(ChannelType.USER, None)

        logger.info("Subscribed to user updates")

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
                ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open
                )
                self._ws = ws

                logger.info("Connecting to WebSocket...")
                ws.run_forever(ping_interval=30, ping_timeout=10)

                if not self._running:
                    break

                # Reconnect logic
                self._reconnect_count += 1
                if self._reconnect_count > self.max_reconnects:
                    logger.error("Max reconnects exceeded")
                    break

                logger.warning(
                    f"Reconnecting in {self.reconnect_delay}s "
                    f"(attempt {self._reconnect_count}/{self.max_reconnects})"
                )
                time.sleep(self.reconnect_delay)

            except Exception as e:
                logger.error(f"WebSocket error: {e}")

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
        logger.info("WebSocket connected")
        self._reconnect_count = 0

        # Resubscribe to all channels
        with self._lock:
            for channel in self._subscriptions:
                if channel.startswith(ChannelType.MARKET):
                    token_id = channel.split(":")[1]
                    self._send_subscribe(ChannelType.MARKET, token_id)
                elif channel == ChannelType.USER:
                    self._send_subscribe(ChannelType.USER, None)

    def _on_message(self, ws, message: str) -> None:
        """Handle incoming message."""
        try:
            data = json.loads(message)

            # Determine channel
            channel_type = data.get("channel")
            if not channel_type:
                return

            # Find matching subscription
            with self._lock:
                if channel_type == ChannelType.MARKET:
                    token_id = data.get("market", {}).get("token_id")
                    if token_id:
                        channel = f"{ChannelType.MARKET}:{token_id}"
                        callback = self._subscriptions.get(channel)
                        if callback:
                            callback(data)
                elif channel_type == ChannelType.USER:
                    callback = self._subscriptions.get(ChannelType.USER)
                    if callback:
                        callback(data)

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _on_error(self, ws, error) -> None:
        """Handle error."""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        """Handle connection close."""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")

    def _send_subscribe(self, channel_type: ChannelType, asset_id: Optional[str]) -> None:
        """Send subscribe message."""
        if not self._ws:
            return

        msg = {
            "type": "subscribe",
            "channel": channel_type.value,
        }

        if channel_type == ChannelType.MARKET and asset_id:
            msg["market"] = asset_id
        elif channel_type == ChannelType.USER and self.api_key:
            msg["auth"] = {"apiKey": self.api_key}

        try:
            self._ws.send(json.dumps(msg))
            logger.debug(f"Sent subscribe: {msg}")
        except Exception as e:
            logger.error(f"Failed to send subscribe: {e}")

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

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
