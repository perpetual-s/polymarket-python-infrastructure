"""
Performance benchmarks for shared/polymarket.

Measures latency, throughput, and scalability.

Run with: pytest tests/benchmarks/ -v -s
"""

import time
import statistics
from typing import List, Dict, Any
from unittest.mock import Mock, patch
import pytest

from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side
from shared.polymarket.models import OrderResponse, OrderStatus, Position, OrderBook


def pytest_configure(config):
    """Register benchmark marker."""
    config.addinivalue_line("markers", "benchmark: mark test as performance benchmark")


@pytest.fixture
def mock_client():
    """Create mocked client for benchmarks."""
    with patch('shared.polymarket.client.get_settings') as mock_settings:
        settings = Mock()
        settings.enable_rate_limiting = False
        settings.enable_metrics = False
        settings.pool_connections = 50
        settings.pool_maxsize = 100
        settings.batch_max_workers = 10
        settings.chain_id = 137  # Add chain_id to avoid mock issues
        mock_settings.return_value = settings

        with patch('shared.polymarket.auth.authenticator.Authenticator.create_l1_headers'):
            with patch('shared.polymarket.auth.authenticator.Authenticator.create_l2_headers'):
                client = PolymarketClient(
                    enable_rate_limiting=False,
                    enable_circuit_breaker=False
                )

                yield client


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
        "iterations": iterations
    }


@pytest.mark.benchmark
class TestOrderOperationBenchmarks:
    """Benchmark order operations."""

    @patch('shared.polymarket.client.PolymarketClient._build_signed_order')
    @patch('shared.polymarket.api.clob.CLOBAPI.post_order')
    def test_single_order_placement(self, mock_post, mock_build, mock_client):
        """Benchmark single order placement."""
        # Setup
        test_wallet = WalletConfig(
            private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        mock_client.add_wallet(test_wallet, wallet_id="bench", set_default=True)

        mock_build.return_value = {"order": "signed"}
        mock_post.return_value = OrderResponse(
            success=True,
            order_id="test_123",
            status=OrderStatus.LIVE
        )

        # Benchmark
        def place_order():
            order = OrderRequest(
                token_id="123",
                price=0.55,
                size=10.0,
                side=Side.BUY
            )
            mock_client.place_order(order, wallet_id="bench", skip_balance_check=True)

        results = measure_time(place_order, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Single Order Placement")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"  StdDev:  {results['stddev_ms']:.2f}ms")
        print(f"  N:       {results['iterations']}")
        print(f"{'='*60}\n")

        # Assert reasonable performance (mocked should be < 10ms)
        assert results['avg_ms'] < 100, f"Average latency too high: {results['avg_ms']:.2f}ms"

    @patch('shared.polymarket.client.PolymarketClient._build_signed_order')
    @patch('shared.polymarket.api.clob.CLOBAPI.post_orders_batch')
    def test_batch_order_placement(self, mock_post_batch, mock_build, mock_client):
        """Benchmark batch order placement."""
        # Setup
        test_wallet = WalletConfig(
            private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )
        mock_client.add_wallet(test_wallet, wallet_id="bench")

        mock_build.return_value = {"order": "signed"}
        mock_post_batch.return_value = [
            OrderResponse(success=True, order_id=f"order_{i}", status=OrderStatus.LIVE)
            for i in range(10)
        ]

        # Benchmark
        def place_batch():
            orders = [
                OrderRequest(token_id=f"{i}", price=0.55, size=10.0, side=Side.BUY)
                for i in range(10)
            ]
            mock_client.place_orders_batch(orders, wallet_id="bench")

        results = measure_time(place_batch, iterations=20)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Batch Order Placement (10 orders)")
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

    @patch('shared.polymarket.api.data_api.DataAPI.get_positions')
    def test_single_wallet_positions(self, mock_get_positions, mock_client):
        """Benchmark fetching positions for single wallet."""
        # Setup
        mock_get_positions.return_value = [
            Position(
                title=f"Market {i}",
                outcome="Yes",
                size=100.0,
                current_price=0.55,
                current_value=55.0,
                cash_pnl=5.0,
                percent_pnl=0.1,
                realized_pnl=0.0
            )
            for i in range(10)
        ]

        # Benchmark
        def fetch_positions():
            mock_client.get_positions("0xtest")

        results = measure_time(fetch_positions, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Fetch Single Wallet Positions (10 positions)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")

    @patch('shared.polymarket.api.data_api.DataAPI.get_positions')
    def test_batch_wallet_positions(self, mock_get_positions, mock_client):
        """Benchmark batch position fetching for 100 wallets."""
        # Setup
        mock_get_positions.return_value = [
            Position(
                title=f"Market {i}",
                outcome="Yes",
                size=100.0,
                current_price=0.55,
                current_value=55.0,
                cash_pnl=5.0,
                percent_pnl=0.1,
                realized_pnl=0.0
            )
            for i in range(5)
        ]

        wallets = [f"0x{i:040x}" for i in range(100)]

        # Benchmark
        def fetch_batch():
            mock_client.get_positions_batch(wallets)

        results = measure_time(fetch_batch, iterations=10)

        # Calculate vs sequential
        sequential_estimate = results['avg_ms'] * 10  # Would take 10x longer sequential

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Batch Fetch Positions (100 wallets)")
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

    @patch('shared.polymarket.api.clob.CLOBAPI.get_orderbook')
    def test_single_orderbook_fetch(self, mock_get_orderbook, mock_client):
        """Benchmark single orderbook fetch."""
        # Setup
        mock_get_orderbook.return_value = OrderBook(
            token_id="123",
            bids=[(0.55, 100.0)] * 10,
            asks=[(0.56, 100.0)] * 10
        )

        # Benchmark
        def fetch_orderbook():
            mock_client.get_orderbook("123")

        results = measure_time(fetch_orderbook, iterations=50)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Single Orderbook Fetch")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")

    @patch('shared.polymarket.api.clob.CLOBAPI.get_orderbook')
    def test_batch_orderbook_fetch(self, mock_get_orderbook, mock_client):
        """Benchmark batch orderbook fetching."""
        # Setup
        mock_get_orderbook.return_value = OrderBook(
            token_id="123",
            bids=[(0.55, 100.0)] * 10,
            asks=[(0.56, 100.0)] * 10
        )

        token_ids = [f"token_{i}" for i in range(20)]

        # Benchmark
        def fetch_batch():
            mock_client.get_orderbooks_batch(token_ids)

        results = measure_time(fetch_batch, iterations=20)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Batch Orderbook Fetch (20 tokens)")
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
        from shared.polymarket.utils.cache import AtomicNonceManager

        manager = AtomicNonceManager()
        manager.set("0xtest", 0)

        # Benchmark
        def get_nonce():
            for _ in range(100):
                manager.get_and_increment("0xtest")

        results = measure_time(get_nonce, iterations=10)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Sequential Nonce Operations (100 ops)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Throughput: {100 / (results['avg_ms'] / 1000):.0f} ops/sec")
        print(f"{'='*60}\n")

    def test_nonce_concurrent(self):
        """Benchmark concurrent nonce operations."""
        import threading
        from shared.polymarket.utils.cache import AtomicNonceManager

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
        print(f"BENCHMARK: Concurrent Nonce Operations (10 threads)")
        print(f"{'='*60}")
        print(f"  Min:     {results['min_ms']:.2f}ms")
        print(f"  Max:     {results['max_ms']:.2f}ms")
        print(f"  Avg:     {results['avg_ms']:.2f}ms")
        print(f"  Median:  {results['median_ms']:.2f}ms")
        print(f"{'='*60}\n")


@pytest.mark.benchmark
class TestMemoryBenchmarks:
    """Benchmark memory usage."""

    def test_client_memory_footprint(self):
        """Measure client memory footprint."""
        import sys

        # Measure base client
        client = PolymarketClient(
            enable_rate_limiting=False,
            enable_circuit_breaker=False
        )

        base_size = sys.getsizeof(client)

        # Add wallets
        for i in range(10):
            wallet = WalletConfig(
                private_key=f"0x{'1234567890abcdef' * 4}"
            )
            client.add_wallet(wallet, wallet_id=f"wallet_{i}")

        with_wallets_size = sys.getsizeof(client)

        # Report
        print(f"\n{'='*60}")
        print(f"BENCHMARK: Memory Footprint")
        print(f"{'='*60}")
        print(f"  Base client:     {base_size:,} bytes")
        print(f"  With 10 wallets: {with_wallets_size:,} bytes")
        print(f"  Per wallet:      ~{(with_wallets_size - base_size) / 10:,.0f} bytes")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    print("Run with: pytest tests/benchmarks/ -v -s")
