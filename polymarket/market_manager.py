"""
Real-time market data manager with WebSocket streaming.

Replaces stale database-first market discovery with:
- Bootstrap from CLOB API (real-time market data)
- WebSocket streaming (market_created, market_resolved events)
- In-memory cache for fast queries (<10ms vs ~200ms DB)

Usage:
    from polymarket import MarketManager, MarketManagerConfig

    manager = MarketManager(clob_api, config)
    await manager.initialize()
    await manager.start_streaming()

    markets = manager.get_tradeable_markets(min_volume=Decimal("10000"))
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, List, Set, Any, Callable

from .api.clob_public import PublicCLOBAPI
from .api.real_time_data import RealTimeDataClient, StreamHelpers, Message, ConnectionStatus

logger = logging.getLogger(__name__)


@dataclass
class MarketManagerConfig:
    """Configuration for MarketManager."""

    # Bootstrap settings
    use_sampling_markets: bool = True
    bootstrap_timeout: float = 120.0

    # Sync settings
    periodic_sync_interval: float = 300.0
    enable_periodic_sync: bool = True

    # WebSocket settings
    enable_websocket: bool = True
    auto_reconnect: bool = True

    # Cache settings
    max_markets: int = 50000

    # Database sync settings (keeps database fresh)
    enable_database_sync: bool = False
    database_sync_batch_size: int = 100


@dataclass
class MarketStats:
    """Market manager statistics (thread-safe via lock)."""

    total_markets: int = 0
    reward_markets: int = 0

    # Bootstrap stats
    bootstrap_time_seconds: float = 0.0
    last_bootstrap_at: Optional[float] = None
    bootstrap_pages_fetched: int = 0

    # WebSocket stats
    websocket_connected: bool = False
    markets_created_received: int = 0
    markets_resolved_received: int = 0

    # Sync stats
    last_sync_at: Optional[float] = None
    sync_count: int = 0

    # Database sync stats
    last_db_sync_at: Optional[float] = None
    db_sync_count: int = 0
    db_sync_markets_written: int = 0


class MarketManager:
    """
    Real-time market data manager with WebSocket streaming.

    Thread Safety:
    - All state access protected by threading.RLock (works across async + WebSocket threads)
    - Query methods return snapshots to avoid iteration issues
    """

    def __init__(
        self,
        clob_api: PublicCLOBAPI,
        config: Optional[MarketManagerConfig] = None,
        on_market_created: Optional[Callable[[Dict], None]] = None,
        on_market_resolved: Optional[Callable[[str], None]] = None,
        db: Optional[Any] = None,
    ):
        """
        Initialize MarketManager.

        Args:
            clob_api: Public CLOB API client
            config: Optional configuration
            on_market_created: Callback for new market events
            on_market_resolved: Callback for market resolution events
            db: Optional database client for syncing markets to persistent storage
        """
        self.clob_api = clob_api
        self.config = config or MarketManagerConfig()
        self._db = db  # Optional database for sync

        # Callbacks
        self._on_market_created = on_market_created
        self._on_market_resolved = on_market_resolved

        # Single lock for all state (works across threads)
        self._lock = threading.RLock()

        # Primary storage
        self._markets: Dict[str, Dict[str, Any]] = {}
        self._token_index: Dict[str, str] = {}
        self._reward_markets: Set[str] = set()

        # State
        self._initialized = False
        self._streaming = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # WebSocket
        self._ws_client: Optional[RealTimeDataClient] = None
        self._sync_task: Optional[asyncio.Task] = None

        # Stats
        self._stats = MarketStats()

    # ========== Lifecycle ==========

    async def initialize(self) -> bool:
        """Bootstrap market data from CLOB API."""
        # Capture event loop for WebSocket callbacks
        self._event_loop = asyncio.get_running_loop()

        with self._lock:
            if self._initialized:
                logger.warning("MarketManager already initialized")
                return True

        logger.info("Bootstrapping market data from CLOB API...")
        start_time = time.time()

        try:
            # Fetch all markets into temp storage (don't clear existing yet)
            new_markets: Dict[str, Dict[str, Any]] = {}
            new_token_index: Dict[str, str] = {}
            new_reward_markets: Set[str] = set()

            next_cursor = "MA=="
            pages_fetched = 0

            while next_cursor != "LTE=":
                # Timeout check
                if time.time() - start_time > self.config.bootstrap_timeout:
                    logger.error(f"Bootstrap timeout after {len(new_markets)} markets")
                    break

                # Fetch page
                if self.config.use_sampling_markets:
                    result = await self.clob_api.get_sampling_markets(next_cursor)
                else:
                    result = await self.clob_api.get_markets(next_cursor)

                markets = result.get("data", [])
                next_cursor = result.get("next_cursor", "LTE=")
                pages_fetched += 1

                # Process markets
                for market in markets:
                    condition_id = market.get("condition_id")
                    if not condition_id:
                        continue

                    new_markets[condition_id] = market

                    # Index tokens
                    for token in market.get("tokens", []):
                        token_id = token.get("token_id") if isinstance(token, dict) else str(token)
                        if token_id:
                            new_token_index[token_id] = condition_id

                    # Index rewards
                    rewards = market.get("rewards", {})
                    if rewards and rewards.get("min_size"):
                        new_reward_markets.add(condition_id)

                    # Safety limit
                    if len(new_markets) >= self.config.max_markets:
                        logger.warning(f"Reached max_markets limit ({self.config.max_markets})")
                        next_cursor = "LTE="
                        break

                if pages_fetched % 10 == 0:
                    logger.info(f"Bootstrap progress: {len(new_markets)} markets")

            # Atomic swap (under lock)
            elapsed = time.time() - start_time
            with self._lock:
                self._markets = new_markets
                self._token_index = new_token_index
                self._reward_markets = new_reward_markets
                self._initialized = True

                self._stats.bootstrap_time_seconds = elapsed
                self._stats.last_bootstrap_at = time.time()
                self._stats.bootstrap_pages_fetched = pages_fetched
                self._stats.total_markets = len(new_markets)
                self._stats.reward_markets = len(new_reward_markets)

            logger.info(
                f"Bootstrap complete: {len(new_markets)} markets, "
                f"{len(new_reward_markets)} with rewards ({elapsed:.1f}s)"
            )

            # Initial database sync after bootstrap
            if self.config.enable_database_sync and self._db:
                await self.sync_to_database()

            return True

        except Exception as e:
            logger.error(f"Bootstrap failed: {e}", exc_info=True)
            return False

    async def start_streaming(self) -> bool:
        """Start WebSocket streaming for real-time updates."""
        if not self.config.enable_websocket:
            logger.info("WebSocket streaming disabled")
            return False

        with self._lock:
            if self._streaming:
                return True

        # Ensure event loop is captured
        if not self._event_loop:
            self._event_loop = asyncio.get_running_loop()

        logger.info("Starting WebSocket streaming...")

        try:
            self._ws_client = RealTimeDataClient(
                on_connect=self._on_ws_connect,
                on_message=self._on_ws_message,
                on_status_change=self._on_ws_status_change,
                auto_reconnect=self.config.auto_reconnect
            )
            self._ws_client.connect()

            with self._lock:
                self._streaming = True

            # Start periodic sync
            if self.config.enable_periodic_sync:
                self._sync_task = asyncio.create_task(self._periodic_sync_loop())

            logger.info("WebSocket streaming started")
            return True

        except Exception as e:
            logger.error(f"Failed to start streaming: {e}", exc_info=True)
            return False

    async def shutdown(self) -> None:
        """Gracefully shutdown MarketManager."""
        logger.info("Shutting down MarketManager...")

        # Cancel sync task
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        # Disconnect WebSocket
        if self._ws_client:
            self._ws_client.disconnect()
            self._ws_client = None

        with self._lock:
            self._streaming = False
            self._initialized = False
            self._stats.websocket_connected = False

        logger.info("MarketManager shutdown complete")

    # ========== Query Methods (Thread-Safe) ==========

    def get_tradeable_markets(
        self,
        min_volume: Optional[Decimal] = None,
        min_liquidity: Optional[Decimal] = None,
        has_rewards: bool = False,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get tradeable markets from cache (thread-safe snapshot).

        Returns markets sorted by volume (highest first).
        """
        # Take snapshot under lock
        with self._lock:
            if has_rewards:
                candidates = [self._markets[cid] for cid in self._reward_markets if cid in self._markets]
            else:
                candidates = list(self._markets.values())

        # Filter (on snapshot, no lock needed)
        results = []
        for market in candidates:
            if not market.get("active", True) or market.get("closed", False):
                continue
            if not market.get("tokens"):
                continue

            if min_volume is not None:
                volume = Decimal(str(market.get("volume", 0) or 0))
                if volume < min_volume:
                    continue

            if min_liquidity is not None:
                liquidity = Decimal(str(market.get("liquidity", 0) or 0))
                if liquidity < min_liquidity:
                    continue

            results.append(market)

        # Sort by volume descending, return top N
        results.sort(key=lambda m: float(m.get("volume", 0) or 0), reverse=True)
        return results[:limit]

    def get_market_by_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Get market by token ID (O(1) lookup, thread-safe)."""
        with self._lock:
            condition_id = self._token_index.get(token_id)
            if condition_id:
                market = self._markets.get(condition_id)
                return dict(market) if market else None  # Return copy
        return None

    def get_market_by_condition(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Get market by condition ID (O(1) lookup, thread-safe)."""
        with self._lock:
            market = self._markets.get(condition_id)
            return dict(market) if market else None  # Return copy

    def get_market_count(self) -> int:
        """Get total cached markets."""
        with self._lock:
            return len(self._markets)

    def get_stats(self) -> MarketStats:
        """Get statistics snapshot."""
        with self._lock:
            self._stats.total_markets = len(self._markets)
            self._stats.reward_markets = len(self._reward_markets)
            # Return a copy to avoid mutations
            return MarketStats(
                total_markets=self._stats.total_markets,
                reward_markets=self._stats.reward_markets,
                bootstrap_time_seconds=self._stats.bootstrap_time_seconds,
                last_bootstrap_at=self._stats.last_bootstrap_at,
                bootstrap_pages_fetched=self._stats.bootstrap_pages_fetched,
                websocket_connected=self._stats.websocket_connected,
                markets_created_received=self._stats.markets_created_received,
                markets_resolved_received=self._stats.markets_resolved_received,
                last_sync_at=self._stats.last_sync_at,
                sync_count=self._stats.sync_count,
            )

    def is_initialized(self) -> bool:
        with self._lock:
            return self._initialized

    def is_streaming(self) -> bool:
        with self._lock:
            return self._streaming

    def is_data_fresh(self, max_staleness_seconds: float = 600.0) -> bool:
        """
        Check if market data is fresh enough for trading.

        Args:
            max_staleness_seconds: Maximum acceptable data age (default 10 minutes)

        Returns:
            True if data was recently bootstrapped/synced
        """
        with self._lock:
            if not self._initialized:
                return False

            # Check last sync/bootstrap time (primary freshness indicator)
            now = time.time()
            last_update = self._stats.last_sync_at or self._stats.last_bootstrap_at

            if last_update is None:
                logger.warning("Market data stale: No sync time recorded")
                return False

            age_seconds = now - last_update
            if age_seconds > max_staleness_seconds:
                logger.warning(
                    f"Market data stale: {age_seconds:.0f}s old (max {max_staleness_seconds:.0f}s)"
                )
                return False

            # WebSocket connection is helpful but not required
            if not self._stats.websocket_connected:
                logger.debug("WebSocket not connected (using bootstrap data)")

            return True

    def get_data_age_seconds(self) -> float:
        """Get age of market data in seconds."""
        with self._lock:
            last_update = self._stats.last_sync_at or self._stats.last_bootstrap_at
            if last_update is None:
                return float('inf')
            return time.time() - last_update

    # ========== WebSocket Callbacks ==========

    def _on_ws_connect(self, client: RealTimeDataClient) -> None:
        """WebSocket connected - subscribe to events."""
        logger.info("WebSocket connected, subscribing...")
        StreamHelpers.subscribe_to_new_markets(client)
        StreamHelpers.subscribe_to_market_resolutions(client)

        with self._lock:
            self._stats.websocket_connected = True

        logger.info("Subscribed to market_created and market_resolved")

    def _on_ws_status_change(self, status: ConnectionStatus) -> None:
        """WebSocket status changed."""
        with self._lock:
            if status == ConnectionStatus.CONNECTED:
                self._stats.websocket_connected = True
            elif status == ConnectionStatus.DISCONNECTED:
                logger.warning("WebSocket disconnected")
                self._stats.websocket_connected = False

    def _on_ws_message(self, client: RealTimeDataClient, message: Message) -> None:
        """Handle WebSocket message (runs in WebSocket thread)."""
        try:
            if message.topic != "clob_market":
                return

            if message.type == "market_created":
                self._handle_market_created(message.payload)
            elif message.type == "market_resolved":
                self._handle_market_resolved(message.payload)

        except Exception as e:
            logger.error(f"WebSocket message error: {e}")

    def _handle_market_created(self, payload: Dict[str, Any]) -> None:
        """Handle market_created event (thread-safe)."""
        market = payload.get("market") or payload
        condition_id = market.get("market") or market.get("condition_id")

        if not condition_id:
            logger.warning("market_created missing condition_id")
            return

        logger.info(f"New market: {condition_id[:16]}...")

        # Add to cache (thread-safe)
        with self._lock:
            self._stats.markets_created_received += 1
            self._markets[condition_id] = market

            # Index tokens
            for token in market.get("tokens", []):
                token_id = token.get("token_id") if isinstance(token, dict) else str(token)
                if token_id:
                    self._token_index[token_id] = condition_id

            # Index rewards
            rewards = market.get("rewards", {})
            if rewards and rewards.get("min_size"):
                self._reward_markets.add(condition_id)

        # Notify callback (outside lock)
        if self._on_market_created:
            try:
                self._on_market_created(market)
            except Exception as e:
                logger.error(f"on_market_created callback error: {e}")

    def _handle_market_resolved(self, payload: Dict[str, Any]) -> None:
        """Handle market_resolved event (thread-safe)."""
        condition_id = payload.get("market") or payload.get("condition_id")

        if not condition_id:
            logger.warning("market_resolved missing condition_id")
            return

        logger.info(f"Market resolved: {condition_id[:16]}...")

        # Remove from cache (thread-safe)
        with self._lock:
            self._stats.markets_resolved_received += 1
            market = self._markets.pop(condition_id, None)

            if market:
                # Remove token index entries
                for token in market.get("tokens", []):
                    token_id = token.get("token_id") if isinstance(token, dict) else str(token)
                    if token_id:
                        self._token_index.pop(token_id, None)

                self._reward_markets.discard(condition_id)

        # Notify callback (outside lock)
        if self._on_market_resolved:
            try:
                self._on_market_resolved(condition_id)
            except Exception as e:
                logger.error(f"on_market_resolved callback error: {e}")

    # ========== Periodic Sync ==========

    async def _periodic_sync_loop(self) -> None:
        """Periodic incremental sync for consistency."""
        while True:
            try:
                await asyncio.sleep(self.config.periodic_sync_interval)

                logger.info("Starting periodic sync...")
                start_time = time.time()

                # Re-bootstrap (builds new dict, then atomic swap)
                success = await self._do_sync()

                if success:
                    with self._lock:
                        self._stats.sync_count += 1
                        self._stats.last_sync_at = time.time()
                    logger.info(f"Periodic sync complete ({time.time() - start_time:.1f}s)")

                    # Sync to database if enabled
                    if self.config.enable_database_sync and self._db:
                        await self.sync_to_database()
                else:
                    logger.error("Periodic sync failed")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic sync error: {e}")
                await asyncio.sleep(60)

    async def _do_sync(self) -> bool:
        """
        Perform incremental sync without clearing cache.

        Builds new data structures, then atomically swaps.
        Existing queries continue working during sync.
        """
        try:
            new_markets: Dict[str, Dict[str, Any]] = {}
            new_token_index: Dict[str, str] = {}
            new_reward_markets: Set[str] = set()

            next_cursor = "MA=="
            while next_cursor != "LTE=":
                if self.config.use_sampling_markets:
                    result = await self.clob_api.get_sampling_markets(next_cursor)
                else:
                    result = await self.clob_api.get_markets(next_cursor)

                for market in result.get("data", []):
                    condition_id = market.get("condition_id")
                    if not condition_id:
                        continue

                    new_markets[condition_id] = market

                    for token in market.get("tokens", []):
                        token_id = token.get("token_id") if isinstance(token, dict) else str(token)
                        if token_id:
                            new_token_index[token_id] = condition_id

                    rewards = market.get("rewards", {})
                    if rewards and rewards.get("min_size"):
                        new_reward_markets.add(condition_id)

                    if len(new_markets) >= self.config.max_markets:
                        next_cursor = "LTE="
                        break

                next_cursor = result.get("next_cursor", "LTE=")

            # Atomic swap
            with self._lock:
                self._markets = new_markets
                self._token_index = new_token_index
                self._reward_markets = new_reward_markets
                self._stats.total_markets = len(new_markets)
                self._stats.reward_markets = len(new_reward_markets)

            return True

        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            return False

    # ========== Utility ==========

    async def refresh(self) -> bool:
        """Force refresh market data."""
        return await self._do_sync()

    def get_token_ids(self, condition_id: str) -> List[str]:
        """Get all token IDs for a market."""
        with self._lock:
            market = self._markets.get(condition_id)
            if not market:
                return []

            result = []
            for token in market.get("tokens", []):
                token_id = token.get("token_id") if isinstance(token, dict) else str(token)
                if token_id:
                    result.append(token_id)
            return result

    # ========== Database Sync ==========

    async def sync_to_database(self) -> int:
        """
        Sync in-memory markets to database for persistence.

        Returns:
            Number of markets written to database
        """
        if not self._db:
            logger.debug("Database sync skipped: no database configured")
            return 0

        if not self.config.enable_database_sync:
            return 0

        # Take snapshot of markets under lock
        with self._lock:
            markets_snapshot = list(self._markets.values())

        if not markets_snapshot:
            return 0

        logger.info(f"Syncing {len(markets_snapshot)} markets to database...")
        start_time = time.time()
        written = 0

        try:
            batch_size = self.config.database_sync_batch_size

            for i in range(0, len(markets_snapshot), batch_size):
                batch = markets_snapshot[i:i + batch_size]

                for market in batch:
                    try:
                        await self._upsert_market_to_db(market)
                        written += 1
                    except Exception as e:
                        logger.warning(f"Failed to upsert market {market.get('condition_id')}: {e}")

                # Log progress every 1000 markets
                if written > 0 and written % 1000 == 0:
                    logger.info(f"Database sync progress: {written}/{len(markets_snapshot)}")

            elapsed = time.time() - start_time
            with self._lock:
                self._stats.last_db_sync_at = time.time()
                self._stats.db_sync_count += 1
                self._stats.db_sync_markets_written = written

            logger.info(f"Database sync complete: {written} markets ({elapsed:.1f}s)")
            return written

        except Exception as e:
            logger.error(f"Database sync failed: {e}", exc_info=True)
            return written

    async def _upsert_market_to_db(self, market: Dict[str, Any]) -> None:
        """Upsert a single market to database."""
        # Map CLOB API fields to database schema
        condition_id = market.get("condition_id")
        if not condition_id:
            return

        # Extract token info
        tokens = market.get("tokens", [])
        outcomes = market.get("outcomes", [])
        outcome_prices = market.get("outcomePrices") or market.get("outcome_prices")

        # Parse dates
        end_date = None
        if market.get("end_date_iso") or market.get("endDateIso"):
            from datetime import datetime
            date_str = market.get("end_date_iso") or market.get("endDateIso")
            try:
                end_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Upsert to database
        await self._db.execute(
            """
            INSERT INTO markets (
                condition_id, market_id, slug, question, volume, liquidity,
                outcomes, outcome_prices, tokens, active, closed, category,
                best_bid, best_ask, spread, last_trade_price, competitive,
                rewards_min_size, rewards_max_spread, end_date,
                group_item_title, group_item_threshold, accepting_orders,
                neg_risk, volume_24h, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, NOW()
            )
            ON CONFLICT (condition_id) DO UPDATE SET
                market_id = EXCLUDED.market_id,
                slug = EXCLUDED.slug,
                question = EXCLUDED.question,
                volume = EXCLUDED.volume,
                liquidity = EXCLUDED.liquidity,
                outcomes = EXCLUDED.outcomes,
                outcome_prices = EXCLUDED.outcome_prices,
                tokens = EXCLUDED.tokens,
                active = EXCLUDED.active,
                closed = EXCLUDED.closed,
                category = EXCLUDED.category,
                best_bid = EXCLUDED.best_bid,
                best_ask = EXCLUDED.best_ask,
                spread = EXCLUDED.spread,
                last_trade_price = EXCLUDED.last_trade_price,
                competitive = EXCLUDED.competitive,
                rewards_min_size = EXCLUDED.rewards_min_size,
                rewards_max_spread = EXCLUDED.rewards_max_spread,
                end_date = EXCLUDED.end_date,
                group_item_title = EXCLUDED.group_item_title,
                group_item_threshold = EXCLUDED.group_item_threshold,
                accepting_orders = EXCLUDED.accepting_orders,
                neg_risk = EXCLUDED.neg_risk,
                volume_24h = EXCLUDED.volume_24h,
                updated_at = NOW()
            """,
            condition_id,
            market.get("id"),  # market_id
            market.get("slug"),
            market.get("question"),
            self._to_decimal(market.get("volume") or market.get("volumeNum")),
            self._to_decimal(market.get("liquidity") or market.get("liquidityNum")),
            json.dumps(outcomes) if isinstance(outcomes, list) else None,
            json.dumps(outcome_prices) if isinstance(outcome_prices, (list, dict)) else None,
            json.dumps(tokens) if isinstance(tokens, list) else None,
            market.get("active", True),
            market.get("closed", False),
            market.get("category"),
            self._to_decimal(market.get("bestBid") or market.get("best_bid")),
            self._to_decimal(market.get("bestAsk") or market.get("best_ask")),
            self._to_decimal(market.get("spread")),
            self._to_decimal(market.get("lastTradePrice") or market.get("last_trade_price")),
            self._to_decimal(market.get("competitive")),
            self._to_decimal(market.get("rewards", {}).get("min_size") if isinstance(market.get("rewards"), dict) else None),
            self._to_decimal(market.get("rewards", {}).get("max_spread") if isinstance(market.get("rewards"), dict) else None),
            end_date,
            market.get("groupItemTitle") or market.get("group_item_title"),
            market.get("groupItemThreshold") or market.get("group_item_threshold"),
            market.get("accepting_orders", True),
            market.get("neg_risk", False) or market.get("negRisk", False),
            self._to_decimal(market.get("volume24hr") or market.get("volume_24h")),
        )

    @staticmethod
    def _to_decimal(value) -> Optional[Decimal]:
        """Convert value to Decimal, return None if invalid."""
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (ValueError, TypeError):
            return None
