"""
Tests for reserved balance tracking.

Tests CRITICAL-4: Reserved balance tracking to prevent over-ordering.
"""

import pytest
import threading
import time
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch
from ..client import PolymarketClient
from ..models import OrderRequest, Side, OrderType, OrderResponse, Balance


@pytest.fixture
def mock_client():
    """Create a mocked PolymarketClient for testing."""
    with patch('shared.polymarket.client.KeyManager'), \
         patch('shared.polymarket.client.CLOBAPI'), \
         patch('shared.polymarket.client.GammaAPI'):

        client = PolymarketClient()

        # Mock get_balances to return consistent balance
        mock_balance = Balance(collateral=1000.0, tokens={})
        client.get_balances = Mock(return_value=mock_balance)

        # Mock CLOB API methods
        client.clob = Mock()
        client.clob.post_order = Mock(return_value={
            'orderID': 'test-order-123',
            'status': 'live'
        })

        return client


def test_reserve_balance_on_buy_order(mock_client):
    """Test that balance is reserved when BUY order is placed."""
    # Arrange
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )

    # Act
    response = mock_client.place_order(order, wallet_id="test_wallet")

    # Assert
    assert mock_client.get_reserved_balance("test_wallet") == 100.0
    assert response.success is True


def test_no_reserve_on_sell_order(mock_client):
    """Test that balance is NOT reserved for SELL orders."""
    # Arrange
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.SELL
    )

    # Act
    mock_client.place_order(order, wallet_id="test_wallet")

    # Assert
    assert mock_client.get_reserved_balance("test_wallet") == 0.0


def test_release_reserved_balance(mock_client):
    """Test that reserved balance can be released."""
    # Arrange - place order to reserve balance
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act
    mock_client.release_reserved_balance(100.0, wallet_id="test_wallet")

    # Assert
    assert mock_client.get_reserved_balance("test_wallet") == 0.0


def test_partial_release_reserved_balance(mock_client):
    """Test partial release of reserved balance."""
    # Arrange
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - release half
    mock_client.release_reserved_balance(50.0, wallet_id="test_wallet")

    # Assert
    assert mock_client.get_reserved_balance("test_wallet") == 50.0


def test_multiple_orders_accumulate_reserved(mock_client):
    """Test that multiple orders accumulate reserved balance."""
    # Arrange & Act
    for i in range(3):
        order = OrderRequest(
            token_id="123456",
            price=0.50,
            size=50.0,
            side=Side.BUY
        )
        mock_client.place_order(order, wallet_id="test_wallet")

    # Assert
    assert mock_client.get_reserved_balance("test_wallet") == 150.0


def test_over_release_protection(mock_client):
    """Test that releasing more than reserved is clamped to zero."""
    # Arrange
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=50.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - try to release more than reserved
    mock_client.release_reserved_balance(100.0, wallet_id="test_wallet")

    # Assert - should be zero, not negative
    assert mock_client.get_reserved_balance("test_wallet") == 0.0


def test_balance_check_considers_reserved(mock_client):
    """Test that balance check considers reserved balance."""
    # Arrange - set balance to 100 and reserve 60
    mock_client.get_balances = Mock(return_value=Balance(collateral=100.0, tokens={}))

    order1 = OrderRequest(
        token_id="123456",
        price=0.50,
        size=60.0,
        side=Side.BUY
    )
    mock_client.place_order(order1, wallet_id="test_wallet")

    # Act - try to place order that would exceed available
    order2 = OrderRequest(
        token_id="123456",
        price=0.50,
        size=50.0,  # Would need 110 total, but only 100 available
        side=Side.BUY
    )

    # Assert - should raise InsufficientBalanceError
    from ..exceptions import InsufficientBalanceError
    with pytest.raises(InsufficientBalanceError):
        mock_client.place_order(order2, wallet_id="test_wallet")


def test_thread_safety_concurrent_reserves(mock_client):
    """Test thread-safety of reserved balance tracking."""
    # Arrange
    num_threads = 10
    size_per_order = 10.0
    results = []

    def place_order_thread():
        try:
            order = OrderRequest(
                token_id="123456",
                price=0.50,
                size=size_per_order,
                side=Side.BUY
            )
            mock_client.place_order(order, wallet_id="test_wallet")
            results.append("success")
        except Exception as e:
            results.append(f"error: {e}")

    # Act - place orders concurrently
    threads = [threading.Thread(target=place_order_thread) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert - all should succeed and total reserved should be correct
    assert len([r for r in results if r == "success"]) == num_threads
    expected_total = num_threads * size_per_order
    assert mock_client.get_reserved_balance("test_wallet") == expected_total


def test_thread_safety_concurrent_releases(mock_client):
    """Test thread-safety of balance release."""
    # Arrange - reserve initial balance
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    num_threads = 10
    release_per_thread = 10.0

    def release_thread():
        mock_client.release_reserved_balance(release_per_thread, wallet_id="test_wallet")

    # Act - release concurrently
    threads = [threading.Thread(target=release_thread) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert - should be fully released
    assert mock_client.get_reserved_balance("test_wallet") == 0.0


def test_separate_wallets_tracked_independently(mock_client):
    """Test that different wallets have independent reserved balances."""
    # Arrange & Act
    order1 = OrderRequest(
        token_id="123456",
        price=0.50,
        size=50.0,
        side=Side.BUY
    )
    mock_client.place_order(order1, wallet_id="wallet_a")

    order2 = OrderRequest(
        token_id="123456",
        price=0.50,
        size=75.0,
        side=Side.BUY
    )
    mock_client.place_order(order2, wallet_id="wallet_b")

    # Assert
    assert mock_client.get_reserved_balance("wallet_a") == 50.0
    assert mock_client.get_reserved_balance("wallet_b") == 75.0


def test_default_wallet_handling(mock_client):
    """Test that None wallet_id uses 'default' key."""
    # Arrange & Act
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id=None)

    # Assert - should be accessible with None or "default"
    assert mock_client.get_reserved_balance(None) == 100.0
    assert mock_client.get_reserved_balance("default") == 100.0


def test_release_balance_accepts_decimal(mock_client):
    """Test that release_reserved_balance accepts Decimal type (financial precision)."""
    # Arrange - reserve balance
    order = OrderRequest(
        token_id="123456",
        price=0.50,
        size=100.0,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - release with Decimal (should not raise TypeError)
    amount_to_release = Decimal("50.00")
    mock_client.release_reserved_balance(amount_to_release, wallet_id="test_wallet")

    # Assert - should work correctly
    assert mock_client.get_reserved_balance("test_wallet") == 50.0


def test_release_balance_preserves_decimal_precision(mock_client):
    """Test that Decimal precision is preserved (no float rounding errors)."""
    # Arrange - reserve precise amount
    order = OrderRequest(
        token_id="123456",
        price=Decimal("0.123456"),
        size=Decimal("100.0"),
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - release with high precision Decimal
    amount = Decimal("12.345600")  # Exact amount
    mock_client.release_reserved_balance(amount, wallet_id="test_wallet")

    # Assert - should have exact precision (within float tolerance)
    remaining = mock_client.get_reserved_balance("test_wallet")
    expected = float(Decimal("12.3456") - Decimal("12.3456"))  # Should be 0
    assert abs(remaining) < 0.01


def test_decimal_precision_no_cumulative_errors(mock_client):
    """Test that repeated Decimal operations don't accumulate rounding errors."""
    # Arrange - start with precise balance
    # Note: Polymarket prices must be 0.01-0.99, so we use price * size for total
    price = Decimal("0.50")
    initial_size = Decimal("200.0")  # Total: 100.0 USD
    initial_total = price * initial_size  # Decimal("100.0")

    order = OrderRequest(
        token_id="123456",
        price=price,
        size=initial_size,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - do many small operations that would accumulate float errors
    # Add and remove 0.1 many times (classic float precision issue)
    increment = Decimal("0.1")
    iterations = 100

    for _ in range(iterations):
        mock_client.release_reserved_balance(increment, wallet_id="test_wallet")

    for _ in range(iterations):
        # Re-reserve by placing small orders
        small_order = OrderRequest(
            token_id="123456",
            price=Decimal("0.50"),
            size=Decimal("0.2"),  # 0.50 * 0.2 = 0.1 USD
            side=Side.BUY
        )
        mock_client.place_order(small_order, wallet_id="test_wallet")

    # Assert - should be back to original (with Decimal precision)
    final_balance = Decimal(str(mock_client.get_reserved_balance("test_wallet")))
    # With float arithmetic: 100.0 - (0.1 * 100) + (0.1 * 100) ≠ 100.0
    # With Decimal arithmetic: should be exactly 100.0
    assert final_balance == initial_total


def test_decimal_precision_with_realistic_prices(mock_client):
    """Test Decimal precision with realistic Polymarket prices (0.01-0.99)."""
    # Arrange - use realistic market price and size
    price = Decimal("0.573")  # Typical market price
    size = Decimal("147.82")  # Typical position size
    expected_reserved = price * size  # Decimal("84.70086")

    order = OrderRequest(
        token_id="123456",
        price=price,
        size=size,
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act - get reserved balance
    reserved = Decimal(str(mock_client.get_reserved_balance("test_wallet")))

    # Assert - should preserve precision (not lose cents to rounding)
    # With float: might get 84.700859999 or 84.700860000
    # With Decimal: should get exactly 84.70086
    difference = abs(reserved - expected_reserved)
    assert difference < Decimal("0.0001")  # Within 0.01 cents


def test_no_balance_leak_from_precision_loss(mock_client):
    """Test that precision loss doesn't cause balance leaks over time."""
    # Arrange - simulate 100 order cycles
    wallet = "test_wallet"

    # Track total reserved over many cycles
    for i in range(100):
        # Place order
        order = OrderRequest(
            token_id="123456",
            price=Decimal("0.33333333"),  # Repeating decimal (hard for floats)
            size=Decimal("1.0"),
            side=Side.BUY
        )
        mock_client.place_order(order, wallet_id=wallet)

        # Immediately release
        amount = Decimal("0.33333333")
        mock_client.release_reserved_balance(amount, wallet_id=wallet)

    # Assert - should be zero (no accumulated rounding errors)
    final = mock_client.get_reserved_balance(wallet)
    # With float arithmetic: might accumulate to 0.00000033 * 100 = 0.000033
    # With Decimal: should be exactly 0.0
    assert abs(final) < 0.00001  # Very tight tolerance


def test_over_release_error_raised(mock_client):
    """Test that over-releasing balance raises an error instead of silent clamping."""
    # Arrange
    order = OrderRequest(
        token_id="123456",
        price=Decimal("0.50"),
        size=Decimal("50.0"),
        side=Side.BUY
    )
    mock_client.place_order(order, wallet_id="test_wallet")

    # Act & Assert - trying to release more should raise error
    # This tests the audit recommendation: "Raise instead of warning"
    from ..exceptions import BalanceTrackingError
    with pytest.raises(BalanceTrackingError, match="Over-release detected"):
        mock_client.release_reserved_balance(Decimal("100.0"), wallet_id="test_wallet")
