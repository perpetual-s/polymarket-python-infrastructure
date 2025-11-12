"""
Configuration management for Polymarket client.

Loads settings from environment variables with validation.
"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PolymarketSettings(BaseSettings):
    """
    Polymarket client settings.

    Loads from environment variables with POLYMARKET_ prefix.
    """
    model_config = SettingsConfigDict(
        env_prefix="POLYMARKET_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # API URLs
    clob_url: str = Field(
        default="https://clob.polymarket.com",
        description="CLOB API URL"
    )
    gamma_url: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Gamma API URL"
    )

    # Chain configuration
    chain_id: int = Field(default=137, description="Polygon chain ID")
    rpc_url: Optional[str] = Field(None, description="Polygon RPC URL")

    # Timeouts and retries
    request_timeout: float = Field(default=30.0, ge=1.0, description="Request timeout (seconds)")
    connect_timeout: float = Field(default=10.0, ge=1.0, description="Connection timeout (seconds)")
    max_retries: int = Field(default=3, ge=0, le=10, description="Max retry attempts")
    retry_backoff_base: float = Field(default=2.0, ge=1.0, description="Backoff multiplier")
    retry_backoff_max: float = Field(default=60.0, ge=1.0, description="Max backoff delay")

    # Rate limiting
    enable_rate_limiting: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_margin: float = Field(default=0.8, ge=0.1, le=1.0,
                                     description="Use 80% of rate limits")

    # Circuit breaker
    circuit_breaker_threshold: int = Field(default=5, ge=1, description="Failures before opening")
    circuit_breaker_timeout: float = Field(default=60.0, ge=1.0, description="Reset timeout")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_requests: bool = Field(default=False, description="Log all HTTP requests")

    # Metrics
    enable_metrics: bool = Field(default=True, description="Enable Prometheus metrics")
    metrics_port: int = Field(default=9090, ge=1024, le=65535, description="Metrics server port")

    # WebSocket (CLOB orderbook and user orders)
    ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws",
        description="WebSocket URL"
    )
    ws_reconnect_delay: float = Field(default=5.0, ge=1.0, description="WS reconnect delay")
    ws_max_reconnects: int = Field(default=10, ge=0, description="Max WS reconnect attempts")

    # Real-Time Data Service (RTDS) - Live event streams
    rtds_url: str = Field(
        default="wss://ws-live-data.polymarket.com",
        description="RTDS WebSocket URL for real-time event streams"
    )
    rtds_auto_reconnect: bool = Field(
        default=True,
        description="Auto-reconnect RTDS on disconnect"
    )
    rtds_ping_interval: float = Field(
        default=5.0,
        ge=1.0,
        description="RTDS ping interval (seconds)"
    )
    rtds_connection_timeout: float = Field(
        default=30.0,
        ge=5.0,
        description="RTDS connection timeout (seconds)"
    )
    rtds_max_message_size: int = Field(
        default=1024 * 1024,  # 1MB
        ge=1024,
        description="Max RTDS message size (bytes)"
    )
    enable_rtds: bool = Field(
        default=True,
        description="Enable RTDS real-time event streams"
    )

    # Connection pooling (CRITICAL for multi-wallet performance)
    pool_connections: int = Field(default=50, ge=10, le=200,
                                  description="HTTP connection pool size")
    pool_maxsize: int = Field(default=100, ge=20, le=500,
                              description="Max connections per pool")

    # Batch operations (PERFORMANCE OPTIMIZATION: Increased from 10 to 20)
    # Higher worker count improves parallel I/O-bound operations (market fetches, orderbooks)
    # Safe for containerized bots: each container has isolated thread pool
    batch_max_workers: int = Field(default=20, ge=1, le=50,
                                   description="ThreadPoolExecutor workers for batch ops")

    # Validation
    validate_orders: bool = Field(default=True, description="Validate orders before sending")
    min_order_size: float = Field(default=1.0, ge=0.01, description="Minimum order size (USDC)")

    def __repr__(self) -> str:
        """Safe repr without sensitive data."""
        return (
            f"PolymarketSettings("
            f"clob_url={self.clob_url}, "
            f"chain_id={self.chain_id}, "
            f"rate_limiting={self.enable_rate_limiting}"
            ")"
        )


# Rate limit configurations per endpoint
# Source: https://docs.polymarket.com/quickstart/introduction/rate-limits
# Updated: October 28, 2025 to match official Polymarket docs
RATE_LIMITS = {
    # === CLOB API - Trading ===
    # Trading operations: 2,400 req/10s burst; 24,000 req/10 min sustained (40 req/s avg)
    "POST:/order": {"burst": 2400, "limit": 2400, "window": 10, "sustained": 24000, "sustained_window": 600},
    "DELETE:/order": {"burst": 2400, "limit": 2400, "window": 10, "sustained": 24000, "sustained_window": 600},
    "POST:/orders": {"burst": 2400, "limit": 2400, "window": 10, "sustained": 24000, "sustained_window": 600},
    "DELETE:/cancel-all": {"burst": 2400, "limit": 2400, "window": 10, "sustained": 24000, "sustained_window": 600},

    # === CLOB API - Market Data ===
    # Market data endpoints: 200 req/10s (official limit)
    "GET:/book": {"limit": 200, "window": 10},
    "GET:/midpoint": {"limit": 200, "window": 10},
    "GET:/price": {"limit": 200, "window": 10},
    "GET:/last_trade_price": {"limit": 200, "window": 10},
    "GET:/spread": {"limit": 200, "window": 10},

    # Order/Trade queries: 200 req/10s
    "GET:/data/order": {"limit": 200, "window": 10},
    "GET:/data/orders": {"limit": 200, "window": 10},
    "GET:/data/trades": {"limit": 75, "window": 10},  # Trades endpoint: 75 req/10s

    # === CLOB API - Balance Operations ===
    # Balance queries: 20-125 req/10s (using conservative 20)
    "GET:/balance": {"limit": 20, "window": 10},
    "GET:/balances": {"limit": 20, "window": 10},

    # === CLOB API - Authentication ===
    # Auth operations: 50 req/10s
    "POST:/auth/api-key": {"limit": 50, "window": 10},
    "GET:/auth/derive-api-key": {"limit": 50, "window": 10},
    "POST:/auth/nonce": {"limit": 50, "window": 10},

    # === CLOB API - General ===
    # General CLOB endpoints: 5,000 req/10s
    "GET:/ok": {"limit": 50, "window": 10},  # OK endpoint: 50 req/10s
    "CLOB:default": {"limit": 5000, "window": 10},

    # === Gamma API ===
    # Official Polymarket rate limits (updated 2025-10-29)
    "GET:/markets": {"limit": 125, "window": 10},  # Official: 125 req/10s
    "GET:/search": {"limit": 300, "window": 10},   # Official: 300 req/10s
    "GET:/events": {"limit": 100, "window": 10},   # Official: 100 req/10s
    "GET:/tags": {"limit": 100, "window": 10},     # Official: 100 req/10s (Tags endpoint)
    "GAMMA:default": {"limit": 750, "window": 10}, # Official: 750 req/10s (general)

    # === Data API ===
    # Data API: 200 req/10s general
    "DATA:default": {"limit": 200, "window": 10},

    # === Default fallback ===
    # Conservative fallback for unknown endpoints
    "default": {"limit": 100, "window": 10},
}


def get_settings() -> PolymarketSettings:
    """
    Get Polymarket settings singleton.

    Returns:
        Validated settings instance
    """
    return PolymarketSettings()


def get_rate_limit(endpoint: str) -> dict:
    """
    Get rate limit configuration for endpoint.

    Args:
        endpoint: Endpoint pattern (e.g., "POST:/order")

    Returns:
        Rate limit config dict
    """
    return RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
