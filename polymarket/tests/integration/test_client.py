"""
Integration tests for PolymarketClient.

Tests core functionality with mocked API responses.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    OrderType
)
from shared.polymarket.models import OrderResponse, OrderStatus


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch('shared.polymarket.client.get_settings') as mock:
        settings = Mock()
        settings.enable_rate_limiting = False
        settings.enable_metrics = False
        settings.pool_connections = 10
        settings.pool_maxsize = 20
        settings.batch_max_workers = 5
        mock.return_value = settings
        yield settings


@pytest.fixture
def client(mock_settings):
    """Create test client."""
    return PolymarketClient(
        enable_rate_limiting=False,
        enable_circuit_breaker=False
    )


@pytest.fixture
def test_wallet():
    """Test wallet configuration."""
    return WalletConfig(
        private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    )


class TestWalletManagement:
    """Test wallet management."""

    def test_add_wallet(self, client, test_wallet):
        """Test adding wallet."""
        client.add_wallet(test_wallet, wallet_id="test", set_default=True)

        assert client.key_manager.has_wallet("test")
        assert client.key_manager.default_wallet == "test"

    def test_add_multiple_wallets(self, client, test_wallet):
        """Test adding multiple wallets."""
        wallet1 = test_wallet
        wallet2 = WalletConfig(
            private_key="0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        client.add_wallet(wallet1, wallet_id="wallet1")
        client.add_wallet(wallet2, wallet_id="wallet2")

        assert client.key_manager.has_wallet("wallet1")
        assert client.key_manager.has_wallet("wallet2")


class TestOrderPlacement:
    """Test order placement."""

    @patch('shared.polymarket.client.PolymarketClient._build_signed_order')
    @patch('shared.polymarket.api.clob.CLOBAPI.post_order')
    def test_place_order_success(self, mock_post, mock_build, client, test_wallet):
        """Test successful order placement."""
        # Setup
        client.add_wallet(test_wallet, wallet_id="test")

        mock_build.return_value = {"order": "signed"}
        mock_post.return_value = OrderResponse(
            success=True,
            order_id="order_123",
            status=OrderStatus.LIVE
        )

        # Execute
        order = OrderRequest(
            token_id="123",
            price=0.55,
            size=10.0,
            side=Side.BUY
        )

        response = client.place_order(order, wallet_id="test", skip_balance_check=True)

        # Verify
        assert response.success
        assert response.order_id == "order_123"
        assert response.status == OrderStatus.LIVE
        mock_post.assert_called_once()

    @patch('shared.polymarket.client.PolymarketClient._build_signed_order')
    @patch('shared.polymarket.api.clob.CLOBAPI.post_orders_batch')
    def test_place_orders_batch(self, mock_post_batch, mock_build, client, test_wallet):
        """Test batch order placement."""
        # Setup
        client.add_wallet(test_wallet, wallet_id="test")

        mock_build.return_value = {"order": "signed"}
        mock_post_batch.return_value = [
            OrderResponse(success=True, order_id="order_1", status=OrderStatus.LIVE),
            OrderResponse(success=True, order_id="order_2", status=OrderStatus.LIVE),
        ]

        # Execute
        orders = [
            OrderRequest(token_id="123", price=0.55, size=10.0, side=Side.BUY),
            OrderRequest(token_id="456", price=0.60, size=20.0, side=Side.BUY),
        ]

        responses = client.place_orders_batch(orders, wallet_id="test")

        # Verify
        assert len(responses) == 2
        assert all(r.success for r in responses)
        mock_post_batch.assert_called_once()


class TestBatchOperations:
    """Test batch operations for Strategy-3."""

    @patch('shared.polymarket.api.data_api.DataAPI.get_positions')
    def test_get_positions_batch(self, mock_get_positions, client):
        """Test batch position fetching."""
        # Setup
        mock_get_positions.return_value = []

        wallets = [f"0x{i:040x}" for i in range(5)]

        # Execute
        positions = client.get_positions_batch(wallets)

        # Verify
        assert len(positions) == 5
        assert mock_get_positions.call_count == 5

    @patch('shared.polymarket.api.clob.CLOBAPI.get_orderbook')
    def test_get_orderbooks_batch(self, mock_get_orderbook, client):
        """Test batch orderbook fetching."""
        # Setup
        from shared.polymarket.models import OrderBook

        mock_get_orderbook.return_value = OrderBook(
            token_id="123",
            bids=[(0.55, 100.0)],
            asks=[(0.56, 100.0)]
        )

        token_ids = ["123", "456", "789"]

        # Execute
        books = client.get_orderbooks_batch(token_ids)

        # Verify
        assert len(books) == 3
        assert "123" in books
        assert mock_get_orderbook.call_count == 3


class TestErrorHandling:
    """Test error handling."""

    def test_invalid_order_price(self, client, test_wallet):
        """Test validation of invalid order price."""
        from shared.polymarket.exceptions import ValidationError

        client.add_wallet(test_wallet, wallet_id="test")

        order = OrderRequest(
            token_id="123",
            price=1.50,  # Invalid: > 0.99
            size=10.0,
            side=Side.BUY
        )

        with pytest.raises(ValidationError):
            client.place_order(order, wallet_id="test", skip_balance_check=True)

    def test_invalid_order_size(self, client, test_wallet):
        """Test validation of invalid order size."""
        from shared.polymarket.exceptions import ValidationError

        client.add_wallet(test_wallet, wallet_id="test")

        order = OrderRequest(
            token_id="123",
            price=0.55,
            size=0.0,  # Invalid: must be > 0
            side=Side.BUY
        )

        with pytest.raises(ValidationError):
            client.place_order(order, wallet_id="test", skip_balance_check=True)


class TestWebSocket:
    """Test WebSocket integration."""

    @patch('shared.polymarket.api.websocket.WebSocketClient')
    def test_subscribe_orderbook(self, mock_ws_class, client):
        """Test orderbook subscription."""
        # Setup
        mock_ws = Mock()
        mock_ws_class.return_value = mock_ws

        callback = Mock()

        # Execute
        client.subscribe_orderbook("123", callback)

        # Verify
        mock_ws.connect.assert_called_once()
        mock_ws.subscribe_market.assert_called_once()

    @patch('shared.polymarket.api.websocket.WebSocketClient')
    def test_unsubscribe_all(self, mock_ws_class, client):
        """Test unsubscribe from all feeds."""
        # Setup
        mock_ws = Mock()
        mock_ws_class.return_value = mock_ws

        callback = Mock()
        client.subscribe_orderbook("123", callback)

        # Execute
        client.unsubscribe_all()

        # Verify
        mock_ws.disconnect.assert_called_once()


class TestHealthCheck:
    """Test health check functionality."""

    @patch('shared.polymarket.api.clob.CLOBAPI.health_check')
    def test_health_check(self, mock_health, client):
        """Test health check."""
        # Setup
        mock_health.return_value = {"status": "healthy"}

        # Execute
        status = client.health_check()

        # Verify
        assert status["status"] in ["healthy", "degraded"]
        mock_health.assert_called_once()
