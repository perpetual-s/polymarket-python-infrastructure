"""
Testnet integration tests with REAL API calls.

Requires:
- TESTNET_PRIVATE_KEY in .env (Polygon Amoy testnet)
- Testnet MATIC for gas (~0.1 MATIC)
- Testnet USDC for trading (~100 USDC)

Run with: pytest tests/testnet/ -v --testnet
"""

import os
import pytest
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    OrderType
)
from shared.polymarket.exceptions import ValidationError


def pytest_configure(config):
    """Register testnet marker."""
    config.addinivalue_line("markers", "testnet: mark test as testnet integration test")


@pytest.fixture(scope="module")
def testnet_enabled():
    """Check if testnet tests should run."""
    if not os.getenv("TESTNET_PRIVATE_KEY"):
        pytest.skip("Testnet tests require TESTNET_PRIVATE_KEY in .env")
    return True


@pytest.fixture(scope="module")
def testnet_client(testnet_enabled):
    """Create testnet client."""
    client = PolymarketClient(
        chain_id=80002,  # Polygon Amoy testnet
        enable_rate_limiting=True,
        enable_circuit_breaker=True
    )

    # Add testnet wallet
    private_key = os.getenv("TESTNET_PRIVATE_KEY")
    wallet = WalletConfig(private_key=private_key)
    client.add_wallet(wallet, wallet_id="testnet", set_default=True)

    yield client

    # Cleanup
    client.close()


@pytest.mark.testnet
class TestLiveMarketData:
    """Test live market data endpoints."""

    def test_get_markets(self, testnet_client):
        """Test fetching markets from testnet."""
        markets = testnet_client.get_markets(limit=10, active=True)

        assert isinstance(markets, list)
        # Testnet may have 0 markets
        if len(markets) > 0:
            market = markets[0]
            assert hasattr(market, 'question')
            assert hasattr(market, 'tokens')
            print(f"✓ Found {len(markets)} testnet markets")

    def test_search_markets(self, testnet_client):
        """Test market search."""
        results = testnet_client.search_markets("test", limit=5)

        assert isinstance(results, list)
        print(f"✓ Search returned {len(results)} results")

    def test_get_orderbook(self, testnet_client):
        """Test orderbook fetching."""
        # First get a market
        markets = testnet_client.get_markets(limit=1, active=True)

        if not markets or not markets[0].tokens:
            pytest.skip("No active testnet markets with tokens")

        token_id = markets[0].tokens[0]
        orderbook = testnet_client.get_orderbook(token_id)

        assert orderbook.token_id == token_id
        assert isinstance(orderbook.bids, list)
        assert isinstance(orderbook.asks, list)
        print(f"✓ Orderbook: {len(orderbook.bids)} bids, {len(orderbook.asks)} asks")


@pytest.mark.testnet
class TestLiveWalletOperations:
    """Test live wallet operations."""

    def test_get_balances(self, testnet_client):
        """Test balance fetching."""
        balance = testnet_client.get_balances("testnet")

        assert hasattr(balance, 'collateral')
        print(f"✓ Testnet balance: {balance.collateral} USDC")

        if balance.collateral < 10.0:
            print(f"⚠ Low testnet balance: {balance.collateral} USDC")

    def test_get_positions(self, testnet_client):
        """Test position fetching."""
        positions = testnet_client.get_positions("testnet")

        assert isinstance(positions, list)
        print(f"✓ Found {len(positions)} positions")

        if len(positions) > 0:
            pos = positions[0]
            assert hasattr(pos, 'title')
            assert hasattr(pos, 'size')
            print(f"  - {pos.title}: {pos.size} shares")

    def test_get_trades(self, testnet_client):
        """Test trade history."""
        trades = testnet_client.get_trades("testnet", limit=10)

        assert isinstance(trades, list)
        print(f"✓ Found {len(trades)} trades")

    def test_get_activity(self, testnet_client):
        """Test activity log."""
        activity = testnet_client.get_activity("testnet", limit=10)

        assert isinstance(activity, list)
        print(f"✓ Found {len(activity)} activity events")


@pytest.mark.testnet
class TestLiveOrderPlacement:
    """Test live order placement (CAREFUL: Uses real testnet funds)."""

    def test_order_validation(self, testnet_client):
        """Test order validation without placing."""
        # Invalid price
        with pytest.raises(ValidationError):
            OrderRequest(
                token_id="123",
                price=1.50,  # > 0.99
                size=10.0,
                side=Side.BUY
            )

        # Invalid size
        with pytest.raises(ValidationError):
            OrderRequest(
                token_id="123",
                price=0.55,
                size=0.0,  # Must be > 0
                side=Side.BUY
            )

        print("✓ Order validation working")

    @pytest.mark.skip(reason="Requires manual enabling - uses real testnet funds")
    def test_place_small_order(self, testnet_client):
        """
        Test placing a real order on testnet.

        ONLY ENABLE THIS MANUALLY when you want to test live order placement.
        Uses real testnet USDC.
        """
        # Get a testnet market
        markets = testnet_client.get_markets(limit=1, active=True)
        if not markets or not markets[0].tokens:
            pytest.skip("No active testnet markets")

        token_id = markets[0].tokens[0]

        # Place very small order
        order = OrderRequest(
            token_id=token_id,
            price=0.01,  # Very low price
            size=1.0,    # Minimum size
            side=Side.BUY,
            order_type=OrderType.GTC
        )

        response = testnet_client.place_order(order, wallet_id="testnet")

        print(f"✓ Order placed: {response.order_id}")
        print(f"  Status: {response.status}")

        if response.success:
            # Try to cancel it
            cancelled = testnet_client.cancel_order(response.order_id, wallet_id="testnet")
            print(f"  Cancelled: {cancelled}")


@pytest.mark.testnet
class TestLiveHealthCheck:
    """Test live health checks."""

    def test_health_check(self, testnet_client):
        """Test health check endpoint."""
        status = testnet_client.health_check()

        assert "status" in status
        assert status["status"] in ["healthy", "degraded"]
        print(f"✓ Health status: {status['status']}")


@pytest.mark.testnet
class TestLiveWebSocket:
    """Test live WebSocket connections."""

    @pytest.mark.skip(reason="WebSocket tests require manual verification")
    def test_subscribe_orderbook(self, testnet_client):
        """
        Test WebSocket orderbook subscription.

        Skipped by default - requires manual verification of updates.
        """
        import time

        markets = testnet_client.get_markets(limit=1, active=True)
        if not markets or not markets[0].tokens:
            pytest.skip("No active testnet markets")

        token_id = markets[0].tokens[0]
        updates_received = []

        def on_update(book):
            updates_received.append(book)
            print(f"  Update: Bid={book.best_bid:.4f} Ask={book.best_ask:.4f}")

        # Subscribe
        testnet_client.subscribe_orderbook(token_id, on_update)

        # Wait for updates
        time.sleep(5)

        # Unsubscribe
        testnet_client.unsubscribe_all()

        print(f"✓ Received {len(updates_received)} WebSocket updates")
        assert len(updates_received) > 0


if __name__ == "__main__":
    print("Run with: pytest tests/testnet/ -v --testnet")
