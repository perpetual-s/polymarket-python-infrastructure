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
