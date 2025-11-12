"""
Tests for Decimal precision in balance tracking (Issue #5 from audit).

Tests that Decimal precision is preserved throughout balance operations
to prevent cumulative rounding errors.
"""

import pytest
from decimal import Decimal

from shared.polymarket.client import PolymarketClient
from shared.polymarket.exceptions import BalanceTrackingError


class TestDecimalPrecisionInBalanceTracking:
    """Test Decimal precision in reserved balance tracking."""

    @pytest.mark.asyncio
    async def test_reserve_and_release_preserves_precision(self):
        """Test that reserving and releasing maintains Decimal precision."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Reserve precise amount
        amount = Decimal("84.70086")  # Realistic: 147.82 shares * $0.573
        client._reserved_balances[wallet] = amount

        # Get balance - should return Decimal
        reserved = await client.get_reserved_balance(wallet)
        assert isinstance(reserved, Decimal)
        assert reserved == amount

        # Release partial amount
        to_release = Decimal("42.35043")  # Exactly half
        await client.release_reserved_balance(to_release, wallet_id=wallet)

        # Check remaining - should be exactly half
        remaining = await client.get_reserved_balance(wallet)
        assert isinstance(remaining, Decimal)
        assert remaining == Decimal("42.35043")

    @pytest.mark.asyncio
    async def test_no_cumulative_rounding_errors(self):
        """Test that repeated operations don't accumulate rounding errors."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Do many small reserve/release cycles (would accumulate errors with float)
        increment = Decimal("0.1")
        iterations = 100

        # Start at 0
        client._reserved_balances[wallet] = Decimal("0")

        # Reserve 0.1 a hundred times
        for _ in range(iterations):
            current = client._reserved_balances[wallet]
            client._reserved_balances[wallet] = current + increment

        # Should be exactly 10.0 (0.1 * 100)
        reserved = await client.get_reserved_balance(wallet)
        assert reserved == Decimal("10.0")

        # Now release 0.1 a hundred times
        for _ in range(iterations):
            await client.release_reserved_balance(increment, wallet_id=wallet)

        # Should be exactly 0
        final = await client.get_reserved_balance(wallet)
        assert final == Decimal("0")

    @pytest.mark.asyncio
    async def test_realistic_polymarket_precision(self):
        """Test precision with realistic Polymarket prices."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Realistic scenario: 147.82 shares at $0.573 = $84.70086
        price = Decimal("0.573")
        size = Decimal("147.82")
        expected = price * size

        client._reserved_balances[wallet] = expected

        # Get balance - should preserve exact precision
        reserved = await client.get_reserved_balance(wallet)
        assert reserved == Decimal("84.70086")

        # Release exact amount
        await client.release_reserved_balance(expected, wallet_id=wallet)

        # Should be exactly 0
        final = await client.get_reserved_balance(wallet)
        assert final == Decimal("0")

    @pytest.mark.asyncio
    async def test_over_release_raises_error(self):
        """Test that over-releasing raises BalanceTrackingError."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Reserve $50
        client._reserved_balances[wallet] = Decimal("50.0")

        # Try to release $100 - should raise error
        with pytest.raises(BalanceTrackingError, match="Over-release detected"):
            await client.release_reserved_balance(Decimal("100.0"), wallet_id=wallet)

        # Balance should be unchanged (transaction-like behavior)
        reserved = await client.get_reserved_balance(wallet)
        assert reserved == Decimal("50.0")

    @pytest.mark.asyncio
    async def test_decimal_with_repeating_fractions(self):
        """Test Decimal handles repeating fractions without error accumulation."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Use 1/3 which is problematic for floats
        one_third = Decimal("0.33333333")

        # Reserve and release 100 times
        for _ in range(100):
            client._reserved_balances[wallet] = one_third
            await client.release_reserved_balance(one_third, wallet_id=wallet)

        # Should be exactly 0 (no accumulated error)
        final = await client.get_reserved_balance(wallet)
        assert final == Decimal("0")

    @pytest.mark.asyncio
    async def test_float_to_decimal_conversion(self):
        """Test that float inputs are safely converted to Decimal."""
        client = PolymarketClient()
        wallet = "test_wallet"

        # Reserve with float (should convert safely)
        client._reserved_balances[wallet] = Decimal("100.0")

        # Release with float - should convert
        await client.release_reserved_balance(Decimal("50.0"), wallet_id=wallet)

        # Should work correctly
        remaining = await client.get_reserved_balance(wallet)
        assert remaining == Decimal("50.0")
