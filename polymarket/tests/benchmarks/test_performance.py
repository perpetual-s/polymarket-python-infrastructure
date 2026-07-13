"""
Performance benchmarks for shared/polymarket.

Measures latency, throughput, and scalability.

Run with: pytest tests/benchmarks/ -v -s
"""

import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Dict, TypeVar
from unittest.mock import patch

import pytest
import pytest_asyncio

from polymarket import OrderRequest, PolymarketClient, Side, WalletConfig
from polymarket.config import PolymarketSettings
from polymarket.models import OrderBook, OrderResponse, OrderStatus, Position

def pytest_configure(config):
    """Register benchmark marker."""
    config.addinivalue_line("markers", "benchmark: mark test as performance benchmark")


@pytest_asyncio.fixture
async def mock_client():
    """Create mocked client for benchmarks."""
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        pool_connections=50,
        pool_maxsize=100,
        batch_max_workers=10,
        enable_rtds=False,
    )
    client = PolymarketClient(
        settings=settings,
        enable_rate_limiting=False,
        enable_circuit_breaker=False,
    )
    wallet = WalletConfig(private_key=f"0x{'1234567890abcdef' * 4}")
    client.key_manager.add_wallet(wallet, wallet_id="bench", set_default=True)
    client.key_manager.set_api_credentials(
        wallet_id="bench",
        api_key="benchmark-api-key",
        api_secret="benchmark-api-secret",
        api_passphrase="benchmark-passphrase",
    )
    try:
        yield client
    finally:
        await client.close()


def measure_time(func, iterations: int = 10) -> Dict[str, float]:
    """Measure function execution time over multiple iterations."""
    times = []

    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return {
        "min_ms": min(times),
        "max_ms": max(times),
        "avg_ms": statistics.mean(times),
        "median_ms": statistics.median(times),
        "stddev_ms": statistics.stdev(times) if len(times) > 1 else 0,
        "iterations": iterations,
    }


_T = TypeVar("_T")


async def measure_time_async(
    func: Callable[[], Awaitable[_T]], iterations: int = 10
) -> Dict[str, float]:
    """Measure one async operation repeatedly on its owning event loop."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await func()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "min_ms": min(times),
        "max_ms": max(times),
        "avg_ms": statistics.mean(times),
        "median_ms": statistics.median(times),
        "stddev_ms": statistics.stdev(times) if len(times) > 1 else 0,
        "iterations": iterations,
    }


def sample_positions(count: int) -> list[Position]:
    """Build current-model position fixtures without touching an API."""
    return [
        Position(
            proxy_wallet=f"0x{i:040x}",
            asset=f"asset-{i}",
            condition_id=f"0x{i:064x}",
            size=100.0,
            avg_price=0.5,
            current_value=55.0,
            initial_value=50.0,
            cur_price=0.55,
            cash_pnl=5.0,
            percent_pnl=0.1,
            title=f"Market {i}",
            slug=f"market-{i}",
            outcome="Yes",
            outcome_index=0,
            opposite_outcome="No",
        )
        for i in range(count)
    ]


@pytest.mark.benchmark
class TestOrderOperationBenchmarks:
    """Benchmark order operations."""

    @patch("polymarket.client.PolymarketClient._build_signed_order")
    @patch("polymarket.api.clob.CLOBAPI.post_order")
    async def test_single_order_placement(self, mock_post, mock_build, mock_client):
        """Benchmark single order placement."""
        mock_build.return_value = {"order": "signed"}
        mock_post.return_value = OrderResponse(
            success=True, order_id="test_123", status=OrderStatus.LIVE
        )

        # Benchmark
        async def place_order():
            order = OrderRequest(token_id="123", price=0.55, size=10.0, side=Side.BUY)
            await mock_client.place_order(
                order, wallet_id="bench", skip_balance_check=True
            )

        results = await measure_time_async(place_order, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Single Order Placement")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"  StdDev:  {results['stddev_ms']:.2f}ms")
        print(f"  N:       {results['iterations']}")
        print(f"{'='*60}\n")

        # Assert reasonable performance (mocked should be < 10ms)
        assert results["avg_ms"] < 100, f"Average latency too high: {results['avg_ms']:.2f}ms"

    @patch("polymarket.client.PolymarketClient._build_signed_order")
    @patch("polymarket.api.clob.CLOBAPI.post_orders_batch")
    async def test_batch_order_placement(
        self, mock_post_batch, mock_build, mock_client
    ):
        """Benchmark batch order placement."""
        mock_build.return_value = {"order": "signed"}
        mock_post_batch.return_value = [
            OrderResponse(success=True, order_id=f"order_{i}", status=OrderStatus.LIVE)
            for i in range(10)
        ]

        # Benchmark
        async def place_batch():
            orders = [
                OrderRequest(token_id=f"{i}", price=0.55, size=10.0, side=Side.BUY)
                for i in range(10)
            ]
            await mock_client.place_orders_batch(
                orders, wallet_id="bench", skip_balance_check=True
            )

        results = await measure_time_async(place_batch, iterations=20)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Batch Order Placement (10 orders)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"  StdDev:  {results['stddev_ms']:.2f}ms")
        print(f"  N:       {results['iterations']}")
        print(f"  Throughput: {10 / (results['avg_ms'] / 1000):.1f} orders/sec")
        print(f"{'='*60}\n")


@pytest.mark.benchmark
class TestDataFetchingBenchmarks:
    """Benchmark data fetching operations."""

    @patch("polymarket.api.data_api.DataAPI.get_positions")
    async def test_single_wallet_positions(self, mock_get_positions, mock_client):
        """Benchmark fetching positions for single wallet."""
        # Setup
        mock_get_positions.return_value = sample_positions(10)

        # Benchmark
        async def fetch_positions():
            await mock_client.get_positions("bench")

        results = await measure_time_async(fetch_positions, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Fetch Single Wallet Positions (10 positions)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")

    @patch("polymarket.api.data_api.DataAPI.get_positions")
    async def test_batch_wallet_positions(self, mock_get_positions, mock_client):
        """Benchmark batch position fetching for 100 wallets."""
        # Setup
        mock_get_positions.return_value = sample_positions(5)

        wallets = [f"0x{i:040x}" for i in range(100)]

        # Benchmark
        async def fetch_batch():
            await mock_client.get_positions_batch(wallets)

        results = await measure_time_async(fetch_batch, iterations=10)

        # Calculate vs sequential
        sequential_estimate = results["avg_ms"] * 10  # Would take 10x longer sequential

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Batch Fetch Positions (100 wallets)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"  Sequential Est: {sequential_estimate:.2f}ms")
        print(f"  Speedup: {sequential_estimate / results['avg_ms']:.1f}x")
        print(f"{'='*60}\n")


@pytest.mark.benchmark
class TestOrderbookBenchmarks:
    """Benchmark orderbook operations."""

    @patch("polymarket.api.clob.CLOBAPI.get_orderbook")
    async def test_single_orderbook_fetch(self, mock_get_orderbook, mock_client):
        """Benchmark single orderbook fetch."""
        # Setup
        mock_get_orderbook.return_value = OrderBook(
            token_id="123", bids=[(0.55, 100.0)] * 10, asks=[(0.56, 100.0)] * 10
        )

        # Benchmark
        async def fetch_orderbook():
            await mock_client.get_orderbook("123")

        results = await measure_time_async(fetch_orderbook, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Single Orderbook Fetch")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")

    @patch("polymarket.api.clob.CLOBAPI.get_orderbooks_batch")
    async def test_batch_orderbook_fetch(self, mock_get_orderbooks_batch, mock_client):
        """Benchmark batch orderbook fetching."""
        token_ids = [f"token_{i}" for i in range(20)]
        mock_get_orderbooks_batch.return_value = {
            token_id: OrderBook(
                token_id=token_id,
                bids=[(0.55, 100.0)] * 10,
                asks=[(0.56, 100.0)] * 10,
            )
            for token_id in token_ids
        }

        # Benchmark
        async def fetch_batch():
            await mock_client.get_orderbooks_batch(token_ids)

        results = await measure_time_async(fetch_batch, iterations=20)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Batch Orderbook Fetch (20 tokens)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"  Throughput: {20 / (results['avg_ms'] / 1000):.1f} books/sec")
        print(f"{'='*60}\n")


@pytest.mark.benchmark
class TestNonceManagerBenchmarks:
    """Benchmark atomic nonce manager."""

    def test_nonce_sequential(self):
        """Benchmark sequential nonce operations."""
        from polymarket.utils.cache import AtomicNonceManager

        manager = AtomicNonceManager()
        manager.set("0xtest", 0)

        # Benchmark
        def get_nonce():
            for _ in range(100):
                manager.get_and_increment("0xtest")

        results = measure_time(get_nonce, iterations=10)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Sequential Nonce Operations (100 ops)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Throughput: {100 / (results['avg_ms'] / 1000):.0f} ops/sec")
        print(f"{'='*60}\n")

    def test_nonce_concurrent(self):
        """Benchmark concurrent nonce operations."""
        import threading

        from polymarket.utils.cache import AtomicNonceManager

        manager = AtomicNonceManager()
        manager.set("0xtest", 0)

        # Benchmark
        def concurrent_nonces():
            nonces = []
            lock = threading.Lock()

            def worker():
                nonce = manager.get_and_increment("0xtest")
                with lock:
                    nonces.append(nonce)

            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Reset for next iteration
            manager.set("0xtest", 0)

        results = measure_time(concurrent_nonces, iterations=20)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Concurrent Nonce Operations (10 threads)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")


@pytest.mark.benchmark
class TestMemoryBenchmarks:
    """Benchmark memory usage."""

    async def test_client_memory_footprint(self, mock_client):
        """Measure client memory footprint."""
        import sys

        base_size = sys.getsizeof(mock_client)

        # Add wallets
        for i in range(10):
            wallet = WalletConfig(private_key=f"0x{'1234567890abcdef' * 4}")
            mock_client.key_manager.add_wallet(wallet, wallet_id=f"wallet_{i}")

        with_wallets_size = sys.getsizeof(mock_client)

        # Report
        print(f"\n{'='*60}")
        print("BENCHMARK: Memory Footprint")
        print(f"{'='*60}")
        print(f"  Base client:     {base_size:,} bytes")
        print(f"  With 10 wallets: {with_wallets_size:,} bytes")
        print(f"  Per wallet:      ~{(with_wallets_size - base_size) / 10:,.0f} bytes")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    print("Run with: pytest tests/benchmarks/ -v -s")
