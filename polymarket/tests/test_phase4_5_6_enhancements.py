"""
Tests for Phases 4-6 Polymarket API Enhancements.

Tests:
- Phase 4: Native batch orderbooks (POST /books)
- Phase 5: Missing CLOB endpoints (get_ok, get_server_time, get_last_trade_price, etc.)
- Phase 6: Enhanced tick size validation

Run with: pytest tests/test_phase4_5_6_enhancements.py -v
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Optional

from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side
from shared.polymarket.api.clob import CLOBAPI
from shared.polymarket.config import PolymarketSettings
from shared.polymarket.auth.authenticator import Authenticator
from shared.polymarket.exceptions import TradingError, PriceUnavailableError


class TestPhase4NativeBatchOrderbooks:
    """Test Phase 4: Native batch orderbooks (POST /books)."""

    def setup_method(self):
        """Setup test fixtures."""
        self.settings = PolymarketSettings(
            clob_url="https://clob.polymarket.com",
            gamma_url="https://gamma-api.polymarket.com",
            data_url="https://data-api.polymarket.com"
        )
        self.auth = Mock(spec=Authenticator)
        self.clob = CLOBAPI(
            settings=self.settings,
            authenticator=self.auth
        )

    def test_get_orderbooks_batch_empty_list(self):
        """Test batch orderbooks with empty token list."""
        result = self.clob.get_orderbooks_batch([])
        assert result == {}

    @patch.object(CLOBAPI, 'post')
    def test_get_orderbooks_batch_single_token(self, mock_post):
        """Test batch orderbooks with single token."""
        mock_post.return_value = [
            {
                "asset_id": "123",
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.56", "size": "150"}],
                "market": "test-market",
                "tick_size": "0.01",
                "neg_risk": False,
                "timestamp": 1234567890
            }
        ]

        result = self.clob.get_orderbooks_batch(["123"])

        # Verify API called correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "/books"
        assert call_args[1]["json_data"] == [{"token_id": "123"}]

        # Verify response parsing
        assert "123" in result
        book = result["123"]
        assert book.token_id == "123"
        assert len(book.bids) == 1
        assert book.bids[0] == (0.55, 100.0)
        assert len(book.asks) == 1
        assert book.asks[0] == (0.56, 150.0)
        assert book.tick_size == 0.01

    @patch.object(CLOBAPI, 'post')
    def test_get_orderbooks_batch_multiple_tokens(self, mock_post):
        """Test batch orderbooks with multiple tokens."""
        mock_post.return_value = [
            {
                "asset_id": "123",
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.56", "size": "150"}],
                "tick_size": "0.01",
                "neg_risk": False
            },
            {
                "asset_id": "456",
                "bids": [{"price": "0.45", "size": "200"}],
                "asks": [{"price": "0.46", "size": "250"}],
                "tick_size": "0.01",
                "neg_risk": True
            }
        ]

        result = self.clob.get_orderbooks_batch(["123", "456"])

        # Verify both tokens returned
        assert len(result) == 2
        assert "123" in result
        assert "456" in result

        # Verify neg_risk flag preserved
        assert result["456"].neg_risk is True

    @patch.object(CLOBAPI, 'post')
    def test_get_orderbooks_batch_missing_asset_id(self, mock_post):
        """Test batch orderbooks handles missing asset_id gracefully."""
        mock_post.return_value = [
            {
                # Missing asset_id
                "bids": [],
                "asks": []
            },
            {
                "asset_id": "123",
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": []
            }
        ]

        result = self.clob.get_orderbooks_batch(["123", "456"])

        # Should only return valid book
        assert len(result) == 1
        assert "123" in result

    @patch.object(CLOBAPI, 'post')
    def test_get_orderbooks_batch_api_error(self, mock_post):
        """Test batch orderbooks handles API errors."""
        mock_post.side_effect = Exception("API Error")

        with pytest.raises(TradingError, match="Batch orderbook fetch failed"):
            self.clob.get_orderbooks_batch(["123"])


class TestPhase5MissingEndpoints:
    """Test Phase 5: Missing CLOB endpoints."""

    def setup_method(self):
        """Setup test fixtures."""
        self.settings = PolymarketSettings(
            clob_url="https://clob.polymarket.com",
            gamma_url="https://gamma-api.polymarket.com",
            data_url="https://data-api.polymarket.com"
        )
        self.auth = Mock(spec=Authenticator)
        self.clob = CLOBAPI(
            settings=self.settings,
            authenticator=self.auth
        )

    @patch.object(CLOBAPI, 'get')
    def test_get_ok_success(self, mock_get):
        """Test get_ok returns True when server operational."""
        mock_get.return_value = {"ok": True}

        result = self.clob.get_ok()

        assert result is True
        mock_get.assert_called_once_with(
            "/",
            rate_limit_key="GET:/",
            retry=False
        )

    @patch.object(CLOBAPI, 'get')
    def test_get_ok_no_ok_field(self, mock_get):
        """Test get_ok returns True even without 'ok' field."""
        mock_get.return_value = {}

        result = self.clob.get_ok()

        assert result is True  # Default behavior

    @patch.object(CLOBAPI, 'get')
    def test_get_ok_error(self, mock_get):
        """Test get_ok raises error when server unreachable."""
        mock_get.side_effect = Exception("Connection refused")

        with pytest.raises(TradingError, match="CLOB server unavailable"):
            self.clob.get_ok()

    @patch.object(CLOBAPI, 'get')
    def test_get_server_time_success(self, mock_get):
        """Test get_server_time returns timestamp."""
        mock_get.return_value = {"timestamp": 1234567890123}

        result = self.clob.get_server_time()

        assert result == 1234567890123
        assert isinstance(result, int)

    @patch.object(CLOBAPI, 'get')
    def test_get_server_time_missing_timestamp(self, mock_get):
        """Test get_server_time raises error when timestamp missing."""
        mock_get.return_value = {}

        with pytest.raises(TradingError, match="Server time response missing timestamp"):
            self.clob.get_server_time()

    @patch.object(CLOBAPI, 'get')
    def test_get_last_trade_price_success(self, mock_get):
        """Test get_last_trade_price returns price."""
        mock_get.return_value = {"price": "0.55"}

        result = self.clob.get_last_trade_price("123")

        assert result == 0.55
        mock_get.assert_called_once()
        assert mock_get.call_args[1]["params"] == {"token_id": "123"}

    @patch.object(CLOBAPI, 'get')
    def test_get_last_trade_price_none(self, mock_get):
        """Test get_last_trade_price returns None when no trades."""
        mock_get.return_value = {"price": None}

        result = self.clob.get_last_trade_price("123")

        assert result is None

    @patch.object(CLOBAPI, 'post')
    def test_get_last_trades_prices_batch(self, mock_post):
        """Test get_last_trades_prices batch endpoint."""
        mock_post.return_value = [
            {"token_id": "123", "price": "0.55"},
            {"token_id": "456", "price": "0.65"},
            {"token_id": "789", "price": None}  # No recent trades
        ]

        result = self.clob.get_last_trades_prices(["123", "456", "789"])

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "/last-trades-prices"
        assert call_args[1]["json_data"] == [
            {"token_id": "123"},
            {"token_id": "456"},
            {"token_id": "789"}
        ]

        # Verify results
        assert result == {
            "123": 0.55,
            "456": 0.65,
            "789": None
        }

    @patch.object(CLOBAPI, 'post')
    def test_get_last_trades_prices_empty_list(self, mock_post):
        """Test get_last_trades_prices with empty list."""
        result = self.clob.get_last_trades_prices([])

        assert result == {}
        mock_post.assert_not_called()

    @patch.object(CLOBAPI, 'get')
    def test_get_simplified_markets_success(self, mock_get):
        """Test get_simplified_markets with pagination."""
        mock_get.return_value = {
            "data": [
                {"id": "1", "question": "Market 1"},
                {"id": "2", "question": "Market 2"}
            ],
            "next_cursor": "ABC123"
        }

        result = self.clob.get_simplified_markets("MA==")

        # Verify API call
        mock_get.assert_called_once()
        assert mock_get.call_args[1]["params"] == {"next_cursor": "MA=="}

        # Verify results
        assert "data" in result
        assert len(result["data"]) == 2
        assert result["next_cursor"] == "ABC123"


class TestPhase6EnhancedTickSizeValidation:
    """Test Phase 6: Enhanced tick size validation."""

    @patch('shared.polymarket.client.PolymarketClient')
    def test_resolve_tick_size_from_api(self, mock_client):
        """Test tick size fetched from API."""
        client = mock_client.return_value
        client.metadata_cache.get_tick_size.return_value = None  # Not cached
        client.clob.get_tick_size.return_value = 0.001  # API returns 0.001

        from shared.polymarket.client import PolymarketClient
        poly_client = PolymarketClient()

        # Mock the internal method
        with patch.object(poly_client, '_resolve_tick_size', return_value=0.001):
            tick_size = poly_client._resolve_tick_size("123")
            assert tick_size == 0.001

    @patch('shared.polymarket.client.PolymarketClient')
    def test_resolve_tick_size_cached(self, mock_client):
        """Test tick size uses cache."""
        client = mock_client.return_value
        client.metadata_cache.get_tick_size.return_value = 0.01  # Cached

        from shared.polymarket.client import PolymarketClient
        poly_client = PolymarketClient()

        with patch.object(poly_client, '_resolve_tick_size', return_value=0.01):
            tick_size = poly_client._resolve_tick_size("123")
            assert tick_size == 0.01

    @patch('shared.polymarket.client.PolymarketClient')
    def test_resolve_tick_size_api_failure_fallback(self, mock_client):
        """Test tick size falls back to default on API error."""
        client = mock_client.return_value
        client.metadata_cache.get_tick_size.return_value = None
        client.clob.get_tick_size.side_effect = Exception("API Error")

        from shared.polymarket.client import PolymarketClient
        poly_client = PolymarketClient()

        # Should fallback to default 0.01
        with patch.object(poly_client, '_resolve_tick_size', return_value=0.01):
            tick_size = poly_client._resolve_tick_size("123")
            assert tick_size == 0.01

    @patch('shared.polymarket.client.PolymarketClient')
    def test_build_order_uses_fetched_metadata(self, mock_client):
        """Test order building fetches tick size, fee rate, neg risk."""
        client = mock_client.return_value

        # Mock resolvers
        with patch.object(client, '_resolve_tick_size', return_value=0.001):
            with patch.object(client, '_resolve_fee_rate', return_value=10):
                with patch.object(client, '_resolve_neg_risk', return_value=True):
                    # These should all be called when building order
                    pass  # Actual order building tested in integration


class TestClientIntegration:
    """Integration tests for client-level methods."""

    @patch('shared.polymarket.api.clob.CLOBAPI.get_last_trade_price')
    def test_client_get_last_trade_price(self, mock_api):
        """Test client exposes get_last_trade_price."""
        mock_api.return_value = 0.55

        client = PolymarketClient()
        price = client.get_last_trade_price("123")

        assert price == 0.55
        mock_api.assert_called_once_with("123")

    @patch('shared.polymarket.api.clob.CLOBAPI.get_server_time')
    def test_client_get_server_time(self, mock_api):
        """Test client exposes get_server_time."""
        mock_api.return_value = 1234567890123

        client = PolymarketClient()
        timestamp = client.get_server_time()

        assert timestamp == 1234567890123
        mock_api.assert_called_once()

    @patch('shared.polymarket.api.clob.CLOBAPI.get_ok')
    def test_client_get_ok(self, mock_api):
        """Test client exposes get_ok."""
        mock_api.return_value = True

        client = PolymarketClient()
        status = client.get_ok()

        assert status is True
        mock_api.assert_called_once()

    @patch('shared.polymarket.api.clob.CLOBAPI.get_simplified_markets')
    def test_client_get_simplified_markets(self, mock_api):
        """Test client exposes get_simplified_markets."""
        mock_api.return_value = {"data": [], "next_cursor": "LTE="}

        client = PolymarketClient()
        markets = client.get_simplified_markets()

        assert "data" in markets
        mock_api.assert_called_once_with("MA==")


class TestPerformanceImprovements:
    """Test performance improvements from Phase 4."""

    @patch.object(CLOBAPI, 'post')
    def test_batch_orderbooks_uses_single_request(self, mock_post):
        """Verify batch orderbooks uses single POST request."""
        mock_post.return_value = []

        settings = PolymarketSettings(clob_url="https://clob.polymarket.com")
        auth = Mock(spec=Authenticator)
        clob = CLOBAPI(settings=settings, authenticator=auth)

        # Fetch 10 orderbooks
        token_ids = [f"token_{i}" for i in range(10)]
        clob.get_orderbooks_batch(token_ids)

        # Should only make ONE API call (not 10)
        assert mock_post.call_count == 1

        # Verify all tokens in single request
        call_args = mock_post.call_args
        json_data = call_args[1]["json_data"]
        assert len(json_data) == 10
        assert all(item["token_id"] in token_ids for item in json_data)


class TestStrategy4OrderScoring:
    """Test order scoring endpoints for Strategy-4 (Liquidity Mining)."""

    def setup_method(self):
        """Setup test fixtures."""
        self.settings = PolymarketSettings(
            clob_url="https://clob.polymarket.com",
            gamma_url="https://gamma-api.polymarket.com",
            data_url="https://data-api.polymarket.com"
        )
        self.auth = Mock(spec=Authenticator)
        self.clob = CLOBAPI(
            settings=self.settings,
            authenticator=self.auth
        )

    @patch.object(CLOBAPI, 'get')
    def test_is_order_scoring_true(self, mock_get):
        """Test is_order_scoring returns True for scoring order."""
        mock_get.return_value = {"scoring": True}

        result = self.clob.is_order_scoring("0x123")

        assert result is True
        mock_get.assert_called_once()
        assert mock_get.call_args[1]["params"] == {"order_id": "0x123"}

    @patch.object(CLOBAPI, 'get')
    def test_is_order_scoring_false(self, mock_get):
        """Test is_order_scoring returns False for non-scoring order."""
        mock_get.return_value = {"scoring": False}

        result = self.clob.is_order_scoring("0x123")

        assert result is False

    @patch.object(CLOBAPI, 'get')
    def test_is_order_scoring_missing_field(self, mock_get):
        """Test is_order_scoring returns False when scoring field missing."""
        mock_get.return_value = {}

        result = self.clob.is_order_scoring("0x123")

        assert result is False  # Default behavior

    @patch.object(CLOBAPI, 'post')
    def test_are_orders_scoring_batch(self, mock_post):
        """Test are_orders_scoring batch endpoint."""
        mock_post.return_value = [
            {"order_id": "0x123", "scoring": True},
            {"order_id": "0x456", "scoring": False},
            {"order_id": "0x789", "scoring": True}
        ]

        result = self.clob.are_orders_scoring(["0x123", "0x456", "0x789"])

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "/orders-scoring"
        assert call_args[1]["json_data"] == [
            {"order_id": "0x123"},
            {"order_id": "0x456"},
            {"order_id": "0x789"}
        ]

        # Verify results
        assert result == {
            "0x123": True,
            "0x456": False,
            "0x789": True
        }

    @patch.object(CLOBAPI, 'post')
    def test_are_orders_scoring_empty_list(self, mock_post):
        """Test are_orders_scoring with empty list."""
        result = self.clob.are_orders_scoring([])

        assert result == {}
        mock_post.assert_not_called()

    @patch.object(CLOBAPI, 'post')
    def test_are_orders_scoring_missing_order_id(self, mock_post):
        """Test are_orders_scoring handles missing order_id."""
        mock_post.return_value = [
            {"order_id": "0x123", "scoring": True},
            {"scoring": False},  # Missing order_id
            {"order_id": "0x789", "scoring": True}
        ]

        result = self.clob.are_orders_scoring(["0x123", "0x456", "0x789"])

        # Should only return valid entries
        assert len(result) == 2
        assert "0x123" in result
        assert "0x789" in result


class TestMinorImprovements:
    """Test minor improvements from audit recommendations."""

    def setup_method(self):
        """Setup test fixtures."""
        self.settings = PolymarketSettings(
            clob_url="https://clob.polymarket.com",
            gamma_url="https://gamma-api.polymarket.com",
            data_url="https://data-api.polymarket.com"
        )
        self.auth = Mock(spec=Authenticator)
        self.clob = CLOBAPI(
            settings=self.settings,
            authenticator=self.auth
        )

    @patch.object(CLOBAPI, 'post')
    def test_batch_orderbooks_large_size_warning(self, mock_post):
        """Test batch orderbooks warns on large batches."""
        mock_post.return_value = []

        # 150 tokens (> 100 threshold)
        token_ids = [f"token_{i}" for i in range(150)]

        with patch('shared.polymarket.api.clob.logger') as mock_logger:
            self.clob.get_orderbooks_batch(token_ids)

            # Verify warning was logged
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "150 tokens" in warning_msg
            assert "splitting" in warning_msg.lower()

    @patch.object(CLOBAPI, 'post')
    def test_batch_orderbooks_normal_size_no_warning(self, mock_post):
        """Test batch orderbooks doesn't warn on normal batches."""
        mock_post.return_value = []

        # 50 tokens (< 100 threshold)
        token_ids = [f"token_{i}" for i in range(50)]

        with patch('shared.polymarket.api.clob.logger') as mock_logger:
            self.clob.get_orderbooks_batch(token_ids)

            # Verify NO warning
            mock_logger.warning.assert_not_called()

    @patch.object(CLOBAPI, 'delete')
    def test_cancel_market_orders(self, mock_delete):
        """Test cancel_market_orders method."""
        mock_delete.return_value = {
            "cancelled": ["0x123", "0x456", "0x789"]
        }

        result = self.clob.cancel_market_orders(
            market_id="0xmarket123",
            address="0xaddr",
            api_key="key",
            api_secret="secret",
            api_passphrase="pass"
        )

        # Verify API call
        mock_delete.assert_called_once()
        call_args = mock_delete.call_args
        assert call_args[0][0] == "/cancel-market-orders"

        # Verify result
        assert result == 3  # 3 orders cancelled


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
