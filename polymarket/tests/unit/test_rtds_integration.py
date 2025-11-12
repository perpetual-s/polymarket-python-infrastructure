"""
Unit tests for RTDS integration in PolymarketClient.

Tests cover:
- Lazy initialization
- Input validation
- Thread safety
- Callback error isolation
- Resource cleanup
"""

import pytest
import threading
import time
from unittest.mock import Mock, patch, MagicMock, call

from shared.polymarket import PolymarketClient
from shared.polymarket.api.real_time_data import Message, ConnectionStatus
from shared.polymarket.config import PolymarketSettings


class TestRTDSInitialization:
    """Test RTDS lazy initialization."""

    def test_rtds_not_initialized_by_default(self):
        """RTDS should not be initialized on client creation."""
        client = PolymarketClient()
        assert client._rtds is None

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_rtds_lazy_initialization(self, mock_rtds_class):
        """RTDS should initialize on first subscription."""
        client = PolymarketClient()

        # Mock RTDS instance
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Subscribe to trigger initialization
        callback = Mock()
        client.subscribe_crypto_prices(callback, symbol="btcusdt")

        # Verify RTDS was initialized
        mock_rtds_class.assert_called_once()
        mock_rtds.connect.assert_called_once()
        assert client._rtds is not None

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_rtds_initialization_uses_settings(self, mock_rtds_class):
        """RTDS should use configuration from settings."""
        settings = PolymarketSettings(
            rtds_url="wss://test.example.com",
            rtds_auto_reconnect=False,
            rtds_ping_interval=10.0
        )
        client = PolymarketClient(settings=settings)

        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Trigger initialization
        callback = Mock()
        client.subscribe_crypto_prices(callback)

        # Verify settings were passed
        mock_rtds_class.assert_called_once_with(
            host="wss://test.example.com",
            on_connect=client._on_rtds_connect,
            on_message=None,
            on_status_change=client._on_rtds_status_change,
            auto_reconnect=False,
            ping_interval=10.0
        )

    def test_rtds_disabled_raises_error(self):
        """Should raise RuntimeError if RTDS disabled."""
        settings = PolymarketSettings(enable_rtds=False)
        client = PolymarketClient(settings=settings)

        with pytest.raises(RuntimeError, match="RTDS is disabled"):
            client.subscribe_crypto_prices(Mock())

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_rtds_initialization_failure_allows_retry(self, mock_rtds_class):
        """Failed initialization should allow retry."""
        client = PolymarketClient()

        # First attempt fails
        mock_rtds_class.side_effect = [
            Exception("Connection failed"),
            Mock()  # Second attempt succeeds
        ]

        # First attempt should fail
        with pytest.raises(RuntimeError, match="RTDS initialization failed"):
            client.subscribe_crypto_prices(Mock())

        assert client._rtds is None

        # Second attempt should succeed
        mock_rtds_class.side_effect = None
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_crypto_prices(Mock())
        assert client._rtds is not None


class TestRTDSThreadSafety:
    """Test thread safety of RTDS operations."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_concurrent_initialization_safe(self, mock_rtds_class):
        """Concurrent subscriptions should only initialize once."""
        client = PolymarketClient()

        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Concurrent subscriptions
        def subscribe():
            client.subscribe_crypto_prices(Mock())

        threads = [threading.Thread(target=subscribe) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # RTDS should only be initialized once
        mock_rtds_class.assert_called_once()


class TestRTDSSubscriptionMethods:
    """Test individual subscription methods."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_activity_trades_validation(self, mock_rtds_class):
        """subscribe_activity_trades should validate inputs."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject both market_slug and event_slug
        with pytest.raises(ValueError, match="Cannot specify both"):
            client.subscribe_activity_trades(
                Mock(),
                market_slug="trump-2024",
                event_slug="election-2024"
            )

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_crypto_prices_validation(self, mock_rtds_class):
        """subscribe_crypto_prices should validate symbol."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject invalid symbol
        with pytest.raises(ValueError, match="Invalid symbol"):
            client.subscribe_crypto_prices(Mock(), symbol="invalid")

        # Should accept valid symbols
        for symbol in ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]:
            client.subscribe_crypto_prices(Mock(), symbol=symbol)

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_market_price_changes_validation(self, mock_rtds_class):
        """subscribe_market_price_changes should validate token_ids."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject empty token_ids
        with pytest.raises(ValueError, match="cannot be empty"):
            client.subscribe_market_price_changes(Mock(), token_ids=[])

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_market_orderbook_rtds_validation(self, mock_rtds_class):
        """subscribe_market_orderbook_rtds should validate token_ids."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject empty token_ids
        with pytest.raises(ValueError, match="cannot be empty"):
            client.subscribe_market_orderbook_rtds(Mock(), token_ids=[])

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_crypto_prices_chainlink_validation(self, mock_rtds_class):
        """subscribe_crypto_prices_chainlink should validate symbol."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject invalid symbol
        with pytest.raises(ValueError, match="Invalid symbol"):
            client.subscribe_crypto_prices_chainlink(Mock(), symbol="invalid")

        # Should accept valid symbols
        for symbol in ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]:
            client.subscribe_crypto_prices_chainlink(Mock(), symbol=symbol)

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_market_last_trade_price_validation(self, mock_rtds_class):
        """subscribe_market_last_trade_price should validate token_ids."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject empty token_ids
        with pytest.raises(ValueError, match="cannot be empty"):
            client.subscribe_market_last_trade_price(Mock(), token_ids=[])

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_subscribe_market_tick_size_change_validation(self, mock_rtds_class):
        """subscribe_market_tick_size_change should validate token_ids."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Should reject empty token_ids
        with pytest.raises(ValueError, match="cannot be empty"):
            client.subscribe_market_tick_size_change(Mock(), token_ids=[])


class TestRTDSCallbackErrorHandling:
    """Test callback error isolation."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_callback_error_isolated(self, mock_rtds_class):
        """Callback errors should not crash client."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Callback that raises exception
        def bad_callback(msg: Message):
            raise ValueError("Callback error")

        client.subscribe_crypto_prices(bad_callback)

        # Get the wrapped callback
        wrapped_callback = mock_rtds.on_custom_message

        # Simulate message delivery
        test_message = Message(
            topic="crypto_prices",
            type="update",
            timestamp=int(time.time() * 1000),
            payload={"price": 50000.0},
            connection_id="test123"
        )

        # Should not raise exception
        wrapped_callback(mock_rtds, test_message)

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_multiple_callbacks(self, mock_rtds_class):
        """Multiple subscriptions should work."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Subscribe to multiple streams
        callback1 = Mock()
        callback2 = Mock()
        callback3 = Mock()

        client.subscribe_crypto_prices(callback1, symbol="btcusdt")
        client.subscribe_market_created(callback2)
        client.subscribe_market_resolved(callback3)

        # All subscriptions should be registered
        assert mock_rtds.subscribe.call_count == 3


class TestRTDSCleanup:
    """Test RTDS cleanup and resource management."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_close_disconnects_rtds(self, mock_rtds_class):
        """close() should disconnect RTDS."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        # Initialize RTDS
        client.subscribe_crypto_prices(Mock())

        # Close client
        client.close()

        # RTDS should be disconnected
        mock_rtds.disconnect.assert_called_once()
        assert client._rtds is None

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_close_handles_rtds_error(self, mock_rtds_class):
        """close() should handle RTDS disconnect errors."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds.disconnect.side_effect = Exception("Disconnect failed")
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_crypto_prices(Mock())

        # Should not raise exception
        client.close()

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_unsubscribe_rtds_all(self, mock_rtds_class):
        """unsubscribe_rtds_all should disconnect RTDS."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_crypto_prices(Mock())
        client.unsubscribe_rtds_all()

        mock_rtds.disconnect.assert_called_once()
        assert client._rtds is None

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_unsubscribe_rtds_all_safe_when_not_initialized(self, mock_rtds_class):
        """unsubscribe_rtds_all should be safe when RTDS not initialized."""
        client = PolymarketClient()

        # Should not raise exception
        client.unsubscribe_rtds_all()


class TestRTDSCallbacks:
    """Test RTDS connection callbacks."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_on_rtds_connect_logs(self, mock_rtds_class):
        """_on_rtds_connect should log successfully."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds.host = "wss://test.example.com"

        # Should not raise exception
        client._on_rtds_connect(mock_rtds)

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_on_rtds_connect_handles_errors(self, mock_rtds_class):
        """_on_rtds_connect should handle callback errors."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds.host = None  # Trigger potential error

        # Should not raise exception
        client._on_rtds_connect(mock_rtds)

    def test_on_rtds_status_change_logs(self):
        """_on_rtds_status_change should log status changes."""
        client = PolymarketClient()

        # Should not raise exception for all statuses
        for status in ConnectionStatus:
            client._on_rtds_status_change(status)


class TestRTDSSubscriptionFilters:
    """Test subscription filter construction."""

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_activity_trades_market_filter(self, mock_rtds_class):
        """subscribe_activity_trades should construct market filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_activity_trades(Mock(), market_slug="trump-2024")

        # Verify subscribe called with correct filters
        mock_rtds.subscribe.assert_called_once()
        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'activity'
        assert call_kwargs['type'] == 'trades'
        assert '"market_slug": "trump-2024"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_activity_trades_event_filter(self, mock_rtds_class):
        """subscribe_activity_trades should construct event filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_activity_trades(Mock(), event_slug="election-2024")

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert '"event_slug": "election-2024"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_crypto_prices_symbol_filter(self, mock_rtds_class):
        """subscribe_crypto_prices should construct symbol filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_crypto_prices(Mock(), symbol="BTCUSDT")  # Uppercase

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'crypto_prices'
        assert call_kwargs['type'] == 'update'
        # Should lowercase symbol
        assert '"symbol": "btcusdt"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_market_price_changes_token_filter(self, mock_rtds_class):
        """subscribe_market_price_changes should construct token filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        token_ids = ["12345", "67890"]
        client.subscribe_market_price_changes(Mock(), token_ids=token_ids)

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'clob_market'
        assert call_kwargs['type'] == 'price_change'
        # Filter should be JSON list
        assert '"12345"' in call_kwargs['filters']
        assert '"67890"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_crypto_prices_chainlink_symbol_filter(self, mock_rtds_class):
        """subscribe_crypto_prices_chainlink should construct symbol filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        client.subscribe_crypto_prices_chainlink(Mock(), symbol="ETHUSDT")  # Uppercase

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'crypto_prices_chainlink'
        assert call_kwargs['type'] == 'update'
        # Should lowercase symbol
        assert '"symbol": "ethusdt"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_market_last_trade_price_token_filter(self, mock_rtds_class):
        """subscribe_market_last_trade_price should construct token filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        token_ids = ["11111", "22222"]
        client.subscribe_market_last_trade_price(Mock(), token_ids=token_ids)

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'clob_market'
        assert call_kwargs['type'] == 'last_trade_price'
        # Filter should be JSON list
        assert '"11111"' in call_kwargs['filters']
        assert '"22222"' in call_kwargs['filters']

    @patch('shared.polymarket.client.RealTimeDataClient')
    def test_market_tick_size_change_token_filter(self, mock_rtds_class):
        """subscribe_market_tick_size_change should construct token filter."""
        client = PolymarketClient()
        mock_rtds = Mock()
        mock_rtds_class.return_value = mock_rtds

        token_ids = ["33333", "44444"]
        client.subscribe_market_tick_size_change(Mock(), token_ids=token_ids)

        call_kwargs = mock_rtds.subscribe.call_args[1]
        assert call_kwargs['topic'] == 'clob_market'
        assert call_kwargs['type'] == 'tick_size_change'
        # Filter should be JSON list
        assert '"33333"' in call_kwargs['filters']
        assert '"44444"' in call_kwargs['filters']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
