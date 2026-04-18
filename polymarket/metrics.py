"""
Prometheus metrics for monitoring.

Critical for production observability.
"""

import time
from typing import Optional
from functools import wraps
import logging

logger = logging.getLogger(__name__)

# Try to import prometheus_client, but don't fail if not installed
try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not installed - metrics disabled")


class Metrics:
    """
    Prometheus metrics collector.

    Tracks:
    - API request latency
    - Order placement success/failure
    - Rate limiter queue depth
    - Circuit breaker state
    - Balance levels
    """

    def __init__(self, enabled: bool = True, port: int = 9090):
        """
        Initialize metrics.

        Args:
            enabled: Enable metrics collection
            port: Metrics HTTP server port
        """
        self.enabled = enabled and PROMETHEUS_AVAILABLE

        if not self.enabled:
            return

        # API metrics
        self.api_requests = Counter(
            'polymarket_api_requests_total',
            'Total API requests',
            ['method', 'endpoint', 'status']
        )

        self.api_latency = Histogram(
            'polymarket_api_latency_seconds',
            'API request latency',
            ['method', 'endpoint']
        )

        # Trading metrics
        self.orders_placed = Counter(
            'polymarket_orders_placed_total',
            'Total orders placed',
            ['wallet', 'side', 'status']
        )

        self.order_latency = Histogram(
            'polymarket_order_latency_seconds',
            'Order placement latency',
            ['wallet']
        )

        # System metrics
        self.rate_limit_queue = Gauge(
            'polymarket_rate_limit_queue_depth',
            'Rate limiter queue depth',
            ['endpoint']
        )

        self.circuit_breaker_state = Gauge(
            'polymarket_circuit_breaker_state',
            'Circuit breaker state (0=closed, 1=open, 2=half-open)',
            ['name']
        )

        self.balance_usdc = Gauge(
            'polymarket_balance_usdc',
            'USDC balance',
            ['wallet']
        )

        # WebSocket metrics (v3.2)
        self.websocket_messages = Counter(
            'polymarket_websocket_messages_total',
            'Total WebSocket messages received',
            ['channel', 'event_type']
        )

        self.websocket_connections = Gauge(
            'polymarket_websocket_connections_active',
            'Active WebSocket connections',
            ['channel']
        )

        self.websocket_reconnections = Counter(
            'polymarket_websocket_reconnections_total',
            'Total WebSocket reconnections',
            ['channel']
        )

        self.websocket_processing_time = Histogram(
            'polymarket_websocket_processing_seconds',
            'Time to process WebSocket message',
            ['channel', 'event_type']
        )

        self.websocket_uptime = Gauge(
            'polymarket_websocket_uptime_seconds',
            'WebSocket connection uptime',
            ['channel']
        )

        # WebSocket queue metrics (v3.3)
        self.websocket_queue_drops = Counter(
            'polymarket_websocket_queue_drops_total',
            'Total messages dropped due to full queue',
            ['channel']
        )

        self.websocket_queue_lag = Histogram(
            'polymarket_websocket_queue_lag_seconds',
            'Queue processing lag (time from enqueue to dequeue)',
            ['channel']
        )

        # WebSocket deduplication metrics (v3.5 - L2)
        self.websocket_duplicates = Counter(
            'polymarket_websocket_duplicates_total',
            'Total duplicate messages blocked by deduplication',
            ['channel']
        )

        # Start metrics server
        try:
            start_http_server(port)
            logger.info(f"Metrics server started on port {port}")
        except Exception as e:
            logger.error(f"Failed to start metrics server: {e}")
            self.enabled = False

    def track_api_request(self, method: str, endpoint: str, status: str) -> None:
        """Record API request."""
        if self.enabled:
            self.api_requests.labels(method=method, endpoint=endpoint, status=status).inc()

    def track_api_latency(self, method: str, endpoint: str, duration: float) -> None:
        """Record API latency."""
        if self.enabled:
            self.api_latency.labels(method=method, endpoint=endpoint).observe(duration)

    def track_order(self, wallet: str, side: str, status: str) -> None:
        """Record order placement."""
        if self.enabled:
            self.orders_placed.labels(wallet=wallet, side=side, status=status).inc()

    def track_order_latency(self, wallet: str, duration: float) -> None:
        """Record order latency."""
        if self.enabled:
            self.order_latency.labels(wallet=wallet).observe(duration)

    def set_rate_limit_queue(self, endpoint: str, depth: int) -> None:
        """Set rate limiter queue depth."""
        if self.enabled:
            self.rate_limit_queue.labels(endpoint=endpoint).set(depth)

    def set_circuit_breaker_state(self, name: str, state: str) -> None:
        """Set circuit breaker state."""
        if self.enabled:
            state_map = {"CLOSED": 0, "OPEN": 1, "HALF_OPEN": 2}
            self.circuit_breaker_state.labels(name=name).set(state_map.get(state, 0))

    def set_balance(self, wallet: str, usdc: float) -> None:
        """Set USDC balance."""
        if self.enabled:
            self.balance_usdc.labels(wallet=wallet).set(usdc)

    # ========== WebSocket Metrics (v3.2) ==========

    def track_websocket_message(self, channel: str, event_type: str) -> None:
        """Record WebSocket message received."""
        if self.enabled:
            self.websocket_messages.labels(channel=channel, event_type=event_type).inc()

    def track_websocket_processing(self, channel: str, event_type: str, duration: float) -> None:
        """Record WebSocket message processing time."""
        if self.enabled:
            self.websocket_processing_time.labels(channel=channel, event_type=event_type).observe(duration)

    def set_websocket_connection(self, channel: str, connected: bool) -> None:
        """Set WebSocket connection state."""
        if self.enabled:
            self.websocket_connections.labels(channel=channel).set(1 if connected else 0)

    def track_websocket_duplicate(self, channel: str) -> None:
        """Record duplicate WebSocket message blocked (v3.5 - L2)."""
        if self.enabled:
            self.websocket_duplicates.labels(channel=channel).inc()

    def track_websocket_reconnection(self, channel: str) -> None:
        """Record WebSocket reconnection."""
        if self.enabled:
            self.websocket_reconnections.labels(channel=channel).inc()

    def set_websocket_uptime(self, channel: str, uptime_seconds: int) -> None:
        """Set WebSocket connection uptime."""
        if self.enabled:
            self.websocket_uptime.labels(channel=channel).set(uptime_seconds)

    def track_websocket_queue_drop(self, channel: str) -> None:
        """Record WebSocket message dropped due to full queue (v3.3)."""
        if self.enabled:
            self.websocket_queue_drops.labels(channel=channel).inc()

    def track_websocket_queue_lag(self, channel: str, lag_seconds: float) -> None:
        """Record WebSocket queue processing lag (v3.3)."""
        if self.enabled:
            self.websocket_queue_lag.labels(channel=channel).observe(lag_seconds)


# Global metrics instance
_metrics: Optional[Metrics] = None


def get_metrics(enabled: bool = True, port: int = 9090) -> Metrics:
    """Get or create metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = Metrics(enabled=enabled, port=port)
    return _metrics


def track_time(metric_name: str, **labels):
    """Decorator to track function execution time."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not _metrics or not _metrics.enabled:
                return func(*args, **kwargs)

            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                if metric_name == "api":
                    _metrics.track_api_latency(
                        labels.get("method", "unknown"),
                        labels.get("endpoint", "unknown"),
                        duration
                    )
                elif metric_name == "order":
                    _metrics.track_order_latency(
                        labels.get("wallet", "unknown"),
                        duration
                    )
        return wrapper
    return decorator
