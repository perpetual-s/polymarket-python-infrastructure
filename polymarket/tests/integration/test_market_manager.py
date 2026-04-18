"""
Integration tests for MarketManager.

Tests the real-time market data architecture against live CLOB API.
Run with: pytest polymarket/tests/integration/test_market_manager.py -v
"""

import asyncio
import pytest
from decimal import Decimal
import time

from polymarket.market_manager import MarketManager, MarketManagerConfig
from polymarket.api.clob_public import PublicCLOBAPI
from polymarket.config import PolymarketSettings


@pytest.fixture
def settings():
    """Create settings for CLOB API."""
    return PolymarketSettings()


@pytest.fixture
async def clob_api(settings):
    """Create CLOB API client (async context required for aiohttp)."""
    api = PublicCLOBAPI(settings)
    yield api
    await api.close()


@pytest.fixture
def manager_config():
    """Create MarketManager config for testing.

    Uses sampling_markets=True for faster tests (~200 markets vs ~14k).
    Production S1 uses sampling_markets=False (all active markets).
    """
    return MarketManagerConfig(
        use_sampling_markets=True,  # Faster tests (fewer markets)
        bootstrap_timeout=60.0,  # 1 minute timeout for tests
        enable_periodic_sync=False,  # Disable for tests
        enable_websocket=False,  # Disable for unit tests
        max_markets=1000  # Limit for faster tests
    )


class TestMarketManagerBootstrap:
    """Test MarketManager bootstrap from CLOB API."""

    @pytest.mark.asyncio
    async def test_initialize_fetches_markets(self, clob_api, manager_config):
        """Test that initialize fetches markets from CLOB API."""
        manager = MarketManager(clob_api, manager_config)

        success = await manager.initialize()

        assert success is True
        assert manager.is_initialized() is True
        assert manager.get_market_count() > 0

        stats = manager.get_stats()
        assert stats.total_markets > 0
        assert stats.bootstrap_time_seconds > 0
        print(f"Bootstrapped {stats.total_markets} markets in {stats.bootstrap_time_seconds:.1f}s")

    @pytest.mark.asyncio
    async def test_bootstrap_creates_token_index(self, clob_api, manager_config):
        """Test that bootstrap creates token index for O(1) lookups."""
        manager = MarketManager(clob_api, manager_config)
        await manager.initialize()

        # Get some markets
        markets = manager.get_tradeable_markets(limit=10)
        assert len(markets) > 0

        # Verify token index works
        for market in markets:
            tokens = market.get("tokens", [])
            if tokens:
                if isinstance(tokens[0], dict):
                    token_id = tokens[0].get("token_id")
                else:
                    token_id = str(tokens[0])

                if token_id:
                    found_market = manager.get_market_by_token(token_id)
                    assert found_market is not None
                    assert found_market.get("condition_id") == market.get("condition_id")


class TestMarketManagerQueries:
    """Test MarketManager query methods."""

    @pytest.fixture
    async def initialized_manager(self, clob_api, manager_config):
        """Create and initialize a MarketManager."""
        manager = MarketManager(clob_api, manager_config)
        await manager.initialize()
        return manager

    @pytest.mark.asyncio
    async def test_get_tradeable_markets_returns_active_markets(self, initialized_manager):
        """Test get_tradeable_markets returns active markets."""
        markets = initialized_manager.get_tradeable_markets(limit=50)

        assert len(markets) > 0
        assert len(markets) <= 50

        for market in markets:
            assert market.get("active", True) is True
            assert market.get("closed", False) is False
            assert market.get("tokens") is not None

    @pytest.mark.asyncio
    async def test_get_tradeable_markets_filters_by_volume(self, initialized_manager):
        """Test volume filtering."""
        # Get markets with high volume
        high_volume_markets = initialized_manager.get_tradeable_markets(
            min_volume=Decimal("10000"),
            limit=10
        )

        for market in high_volume_markets:
            volume = Decimal(str(market.get("volume", 0) or 0))
            assert volume >= Decimal("10000"), f"Volume {volume} < 10000"

    @pytest.mark.asyncio
    async def test_get_tradeable_markets_filters_by_rewards(self, initialized_manager):
        """Test reward filtering."""
        # Get only reward markets
        reward_markets = initialized_manager.get_tradeable_markets(
            has_rewards=True,
            limit=50
        )

        # Should have fewer or equal markets than total
        all_markets = initialized_manager.get_tradeable_markets(
            has_rewards=False,
            limit=1000
        )

        assert len(reward_markets) <= len(all_markets)

    @pytest.mark.asyncio
    async def test_get_market_by_condition(self, initialized_manager):
        """Test condition ID lookup."""
        markets = initialized_manager.get_tradeable_markets(limit=5)
        assert len(markets) > 0

        for market in markets:
            condition_id = market.get("condition_id")
            if condition_id:
                found = initialized_manager.get_market_by_condition(condition_id)
                assert found is not None
                assert found.get("condition_id") == condition_id

    @pytest.mark.asyncio
    async def test_query_performance(self, initialized_manager):
        """Test that queries are fast (<10ms)."""
        # Warm up
        initialized_manager.get_tradeable_markets(limit=50)

        # Time 100 queries
        start = time.time()
        for _ in range(100):
            initialized_manager.get_tradeable_markets(
                min_volume=Decimal("10000"),
                has_rewards=True,
                limit=50
            )
        elapsed = time.time() - start

        avg_ms = (elapsed / 100) * 1000
        print(f"Average query time: {avg_ms:.2f}ms")
        assert avg_ms < 10, f"Query too slow: {avg_ms:.2f}ms (expected <10ms)"

    @pytest.mark.asyncio
    async def test_results_sorted_by_volume_descending(self, initialized_manager):
        """Test that results are sorted by volume (highest first)."""
        markets = initialized_manager.get_tradeable_markets(limit=20)

        assert len(markets) > 1, "Need multiple markets to test sorting"

        # Extract volumes
        volumes = [float(m.get("volume", 0) or 0) for m in markets]

        # Verify descending order
        for i in range(len(volumes) - 1):
            assert volumes[i] >= volumes[i + 1], (
                f"Results not sorted: volume[{i}]={volumes[i]} < volume[{i+1}]={volumes[i+1]}"
            )

        print(f"Top 5 volumes: {volumes[:5]}")

    @pytest.mark.asyncio
    async def test_empty_results_on_impossible_filter(self, initialized_manager):
        """Test graceful handling when no markets match filters."""
        # Use impossibly high volume filter
        markets = initialized_manager.get_tradeable_markets(
            min_volume=Decimal("999999999999"),  # $999 billion
            limit=50
        )

        assert markets == [], f"Expected empty list, got {len(markets)} markets"


class TestMarketManagerLifecycle:
    """Test MarketManager lifecycle management."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self, clob_api, manager_config):
        """Test that shutdown clears all state."""
        manager = MarketManager(clob_api, manager_config)
        await manager.initialize()

        assert manager.get_market_count() > 0

        await manager.shutdown()

        assert manager.is_initialized() is False
        assert manager.is_streaming() is False

    @pytest.mark.asyncio
    async def test_refresh_reloads_data(self, clob_api, manager_config):
        """Test that refresh reloads market data."""
        manager = MarketManager(clob_api, manager_config)
        await manager.initialize()

        initial_count = manager.get_market_count()

        # Refresh
        success = await manager.refresh()

        assert success is True
        assert manager.get_market_count() > 0

        print(f"Initial: {initial_count}, After refresh: {manager.get_market_count()}")


class TestMarketManagerStats:
    """Test MarketManager statistics."""

    @pytest.mark.asyncio
    async def test_stats_after_bootstrap(self, clob_api, manager_config):
        """Test statistics are populated after bootstrap."""
        manager = MarketManager(clob_api, manager_config)
        await manager.initialize()

        stats = manager.get_stats()

        assert stats.total_markets > 0
        assert stats.bootstrap_time_seconds > 0
        assert stats.last_bootstrap_at is not None
        assert stats.bootstrap_pages_fetched > 0

        print(f"Stats: {stats}")


if __name__ == "__main__":
    # Run a quick integration test
    async def main():
        print("Testing MarketManager integration with CLOB API...")

        settings = PolymarketSettings()
        clob_api = PublicCLOBAPI(settings)
        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=False,
            enable_periodic_sync=False,
            max_markets=500  # Limit for quick test
        )

        manager = MarketManager(clob_api, config)

        print("\n1. Bootstrapping...")
        start = time.time()
        success = await manager.initialize()
        elapsed = time.time() - start
        print(f"   Bootstrap: {'OK' if success else 'FAILED'} ({elapsed:.1f}s)")

        if success:
            stats = manager.get_stats()
            print(f"   Markets: {stats.total_markets}")
            print(f"   With rewards: {stats.reward_markets}")

            print("\n2. Testing queries...")
            start = time.time()
            markets = manager.get_tradeable_markets(
                min_volume=Decimal("10000"),
                has_rewards=True,
                limit=20
            )
            elapsed = (time.time() - start) * 1000
            print(f"   Query: {len(markets)} markets in {elapsed:.2f}ms")

            if markets:
                print("\n3. Sample markets:")
                for m in markets[:3]:
                    print(f"   - {m.get('question', 'Unknown')[:60]}...")
                    print(f"     Volume: ${m.get('volume', 0):,.0f}")

        await manager.shutdown()
        print("\n4. Shutdown complete")

    asyncio.run(main())
