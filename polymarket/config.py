"""
Configuration management for Polymarket client.

Loads settings from environment variables with validation.
"""

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
        extra="ignore",
        validate_assignment=True,
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
    min_order_size: float = Field(default=0.01, ge=0.01, description="Minimum order size (tokens, actual min is per-market)")

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
# Audited against official docs on 2026-04-23.
# Every rate_limit_key passed from polymarket/api/*.py is listed below;
# unknown keys fall through to "default" which is intentionally conservative.
RATE_LIMITS = {
    # === CLOB API - Trading (burst + sustained) ===
    # Official limits have increased since the earlier defaults.
    "POST:/order": {"burst": 3500, "limit": 3500, "window": 10, "sustained": 36000, "sustained_window": 600},
    "DELETE:/order": {"burst": 3000, "limit": 3000, "window": 10, "sustained": 30000, "sustained_window": 600},
    "POST:/orders": {"burst": 1000, "limit": 1000, "window": 10, "sustained": 15000, "sustained_window": 600},
    "DELETE:/orders": {"burst": 1000, "limit": 1000, "window": 10, "sustained": 15000, "sustained_window": 600},
    "DELETE:/cancel-all": {"burst": 250, "limit": 250, "window": 10, "sustained": 6000, "sustained_window": 600},
    "DELETE:/cancel-market-orders": {"burst": 1000, "limit": 1000, "window": 10, "sustained": 1500, "sustained_window": 600},

    # === CLOB API - Market Data ===
    # Singles: 1,500 req/10s; batch/plural variants: 500 req/10s.
    "GET:/book": {"limit": 1500, "window": 10},
    "GET:/books": {"limit": 500, "window": 10},
    "POST:/books": {"limit": 500, "window": 10},              # Batch books via POST body
    "GET:/midpoint": {"limit": 1500, "window": 10},
    "GET:/midpoints": {"limit": 500, "window": 10},
    "GET:/price": {"limit": 1500, "window": 10},
    "GET:/prices": {"limit": 500, "window": 10},
    "GET:/last-trade-price": {"limit": 1500, "window": 10},
    "POST:/last-trades-prices": {"limit": 500, "window": 10},
    "GET:/prices-history": {"limit": 1000, "window": 10},
    "GET:/spread": {"limit": 1500, "window": 10},             # Docs bucket with singles
    "GET:/tick-size": {"limit": 200, "window": 10},
    "GET:/neg-risk": {"limit": 200, "window": 10},            # No doc entry; tick-size bucket
    "GET:/simplified-markets": {"limit": 500, "window": 10},

    # === CLOB API - Ledger (order/trade queries) ===
    "GET:/data/order": {"limit": 900, "window": 10},
    "GET:/data/orders": {"limit": 500, "window": 10},
    "GET:/data/trades": {"limit": 500, "window": 10},
    "GET:/notifications": {"limit": 125, "window": 10},
    "GET:/order-scoring": {"limit": 900, "window": 10},       # Ledger bucket
    "POST:/orders-scoring": {"limit": 900, "window": 10},     # Ledger bucket

    # === CLOB API - Balance ===
    "GET:/balance-allowance": {"limit": 200, "window": 10},
    "GET:/balance-allowance/update": {"limit": 50, "window": 10},

    # === CLOB API - Authentication ===
    "POST:/auth/api-key": {"limit": 100, "window": 10},
    "GET:/auth/derive-api-key": {"limit": 100, "window": 10},
    "POST:/auth/nonce": {"limit": 100, "window": 10},

    # === CLOB API - General ===
    "GET:/ok": {"limit": 100, "window": 10},
    "GET:/": {"limit": 100, "window": 10},                    # Root / health-adjacent
    "GET:/time": {"limit": 100, "window": 10},
    "CLOB:default": {"limit": 9000, "window": 10},

    # === Gamma API ===
    "GET:/markets": {"limit": 300, "window": 10},
    "GET:/events": {"limit": 500, "window": 10},
    "GET:/events/pagination": {"limit": 500, "window": 10},
    "GET:/comments": {"limit": 200, "window": 10},
    "GET:/tags": {"limit": 200, "window": 10},
    "GET:/search": {"limit": 300, "window": 10},              # /public-search in docs = 350; keep 300 for /search
    "GET:/public-profile": {"limit": 100, "window": 10},
    "GAMMA:default": {"limit": 4000, "window": 10},

    # === Data API ===
    "GET:/positions": {"limit": 150, "window": 10},
    "GET:/closed-positions": {"limit": 150, "window": 10},
    "GET:/trades": {"limit": 200, "window": 10},
    "GET:/v1/leaderboard": {"limit": 200, "window": 10},
    "GET:/activity": {"limit": 1000, "window": 10},           # Data API default bucket
    "GET:/holders": {"limit": 1000, "window": 10},
    "GET:/value": {"limit": 1000, "window": 10},
    "DATA:default": {"limit": 1000, "window": 10},

    # === Default fallback ===
    # Intentionally conservative. Every known endpoint above should hit its own
    # entry; falling through to "default" means the endpoint is new or the key
    # is misnamed - in either case, stay well below platform limits.
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
