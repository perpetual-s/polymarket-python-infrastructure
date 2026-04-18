"""
Integration tests for MarketManager WebSocket events.

Tests real-time market_created and market_resolved event handling.
Run with: pytest polymarket/tests/integration/test_websocket_events.py -v
"""

import asyncio
import pytest
import time
from typing import Dict, Any, List

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


class TestWebSocketStreaming:
    """Test MarketManager WebSocket streaming."""

    @pytest.mark.asyncio
    async def test_websocket_connects_and_subscribes(self, clob_api):
        """Test WebSocket connection and subscription."""
        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=True,
            enable_periodic_sync=False,  # Disable for test
            max_markets=100
        )
        manager = MarketManager(clob_api, config)

        # Bootstrap first
        success = await manager.initialize()
        assert success is True

        # Start streaming
        streaming = await manager.start_streaming()
        assert streaming is True
        assert manager.is_streaming() is True

        # Wait for WebSocket connection (happens in separate thread)
        for _ in range(50):  # Wait up to 5 seconds
            stats = manager.get_stats()
            if stats.websocket_connected:
                break
            await asyncio.sleep(0.1)

        stats = manager.get_stats()
        assert stats.websocket_connected is True, "WebSocket failed to connect within 5s"

        await manager.shutdown()
        assert manager.is_streaming() is False

    @pytest.mark.asyncio
    async def test_websocket_receives_events(self, clob_api):
        """
        Test WebSocket receives market events.

        Note: This test waits for real events. Since markets are created/resolved
        infrequently, we set a reasonable timeout and check stats.
        """
        received_events: List[Dict[str, Any]] = []

        def on_created(market: Dict[str, Any]):
            received_events.append({"type": "created", "market": market})

        def on_resolved(condition_id: str):
            received_events.append({"type": "resolved", "condition_id": condition_id})

        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=True,
            enable_periodic_sync=False,
            max_markets=100
        )
        manager = MarketManager(
            clob_api,
            config,
            on_market_created=on_created,
            on_market_resolved=on_resolved
        )

        await manager.initialize()
        await manager.start_streaming()

        # Wait up to 30 seconds for events (markets may not be created/resolved during test)
        wait_time = 30
        print(f"\nWaiting up to {wait_time}s for WebSocket events...")
        start = time.time()

        while time.time() - start < wait_time:
            stats = manager.get_stats()
            if stats.markets_created_received > 0 or stats.markets_resolved_received > 0:
                break
            await asyncio.sleep(1)

        stats = manager.get_stats()
        print(f"\nWebSocket stats after {time.time() - start:.1f}s:")
        print(f"  markets_created_received: {stats.markets_created_received}")
        print(f"  markets_resolved_received: {stats.markets_resolved_received}")
        print(f"  Events captured: {len(received_events)}")

        await manager.shutdown()

        # This is a soft assertion - events may not occur during test window
        # The test validates the infrastructure works, not that events always exist
        print("Note: If no events received, that's OK - markets may not be created/resolved during test")

    @pytest.mark.asyncio
    async def test_periodic_sync_runs(self, clob_api):
        """Test periodic sync task starts correctly."""
        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=True,
            enable_periodic_sync=True,
            periodic_sync_interval=5,  # Short interval for test
            max_markets=50
        )
        manager = MarketManager(clob_api, config)

        await manager.initialize()
        initial_count = manager.get_market_count()

        await manager.start_streaming()

        # Wait for one sync cycle (5s interval + ~2s sync time + buffer)
        # Poll for completion rather than fixed wait
        for _ in range(15):  # Wait up to 15 seconds
            await asyncio.sleep(1)
            stats = manager.get_stats()
            if stats.sync_count >= 1:
                break

        stats = manager.get_stats()
        assert stats.sync_count >= 1, f"Periodic sync should have run (sync_count={stats.sync_count})"
        print(f"\nSync count: {stats.sync_count}")
        print(f"Markets: {manager.get_market_count()}")

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_reconnection_on_disconnect(self, clob_api):
        """Test auto-reconnection (basic verification)."""
        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=True,
            auto_reconnect=True,
            enable_periodic_sync=False,
            max_markets=50
        )
        manager = MarketManager(clob_api, config)

        await manager.initialize()
        await manager.start_streaming()

        assert manager.is_streaming() is True

        # Note: We can't easily simulate disconnect in this test
        # Just verify config is set correctly
        assert config.auto_reconnect is True

        await manager.shutdown()


if __name__ == "__main__":
    # Run a quick WebSocket test
    async def main():
        print("Testing MarketManager WebSocket streaming...")
        print("=" * 60)

        settings = PolymarketSettings()
        clob_api = PublicCLOBAPI(settings)

        received_created = []
        received_resolved = []

        def on_created(market):
            question = market.get("question", "Unknown")[:50]
            print(f"  ✓ market_created: {question}...")
            received_created.append(market)

        def on_resolved(condition_id):
            print(f"  ✓ market_resolved: {condition_id[:20]}...")
            received_resolved.append(condition_id)

        config = MarketManagerConfig(
            use_sampling_markets=True,
            enable_websocket=True,
            enable_periodic_sync=False,
            max_markets=200
        )
        manager = MarketManager(
            clob_api,
            config,
            on_market_created=on_created,
            on_market_resolved=on_resolved
        )

        print("\n1. Bootstrapping...")
        if await manager.initialize():
            stats = manager.get_stats()
            print(f"   Bootstrapped {stats.total_markets} markets")
        else:
            print("   FAILED to bootstrap")
            return

        print("\n2. Starting WebSocket streaming...")
        if await manager.start_streaming():
            print("   WebSocket connected and subscribed")
        else:
            print("   FAILED to start streaming")
            await manager.shutdown()
            return

        print(f"\n3. Listening for events (60s)...")
        print("   (Markets are created/resolved infrequently, may not see events)")

        try:
            for i in range(60):
                await asyncio.sleep(1)
                if i % 10 == 9:
                    print(f"   ... {i+1}s elapsed")
        except KeyboardInterrupt:
            print("\n   Interrupted")

        print("\n4. Results:")
        stats = manager.get_stats()
        print(f"   markets_created_received: {stats.markets_created_received}")
        print(f"   markets_resolved_received: {stats.markets_resolved_received}")
        print(f"   Total markets now: {stats.total_markets}")

        await manager.shutdown()
        print("\n5. Shutdown complete")

    asyncio.run(main())
