"""
Unit tests for Decimal precision in shared/polymarket.

Validates the financial-grade precision guarantees of Decimal migration.

Tests:
- Float precision failures vs Decimal precision
- Wei conversion exactness
- Quantize rounding behavior
- Pydantic validator conversions
- Financial calculation accuracy
"""

import pytest
from decimal import Decimal, ROUND_HALF_UP
from pydantic import ValidationError

from shared.polymarket.models import OrderRequest, Side, OrderType, Position, Balance
from shared.polymarket.utils.fees import (
    calculate_net_cost,
    calculate_profit_after_fees,
    estimate_breakeven_exit,
    get_effective_spread,
)
from shared.polymarket.utils.validation import (
    validate_balance,
    check_order_profitability,
)


class TestFloatVsDecimalPrecision:
    """Demonstrate why Decimal is required for financial calculations."""

    def test_float_precision_failure(self):
        """Float arithmetic fails basic precision tests."""
        # Classic float precision issue
        result = 0.1 + 0.2
        assert result != 0.3  # ❌ Float fails!
        assert abs(result - 0.3) < 1e-10  # Need epsilon comparison

        # Financial example: $0.60 * 100 tokens
        price_float = 0.60
        quantity = 100
        expected = 60.0

        # Float can have precision errors
        result_float = price_float * quantity
        # This might pass, but isn't guaranteed for all values
        assert isinstance(result_float, float)

    def test_decimal_precision_success(self):
        """Decimal arithmetic is exact for decimal values."""
        # Decimal handles this correctly
        result = Decimal("0.1") + Decimal("0.2")
        assert result == Decimal("0.3")  # ✅ Exact!

        # Financial example: $0.60 * 100 tokens
        price = Decimal("0.60")
        quantity = Decimal("100")
        expected = Decimal("60.0")

        result = price * quantity
        assert result == expected  # ✅ Exact!
        assert isinstance(result, Decimal)

    def test_float_literal_vs_string_constructor(self):
        """String constructor prevents float precision loss."""
        # Float literal loses precision
        d1 = Decimal(0.1)  # ❌ Bad: 0.1000000000000000055511151231257827021181583404541015625
        d2 = Decimal("0.1")  # ✅ Good: 0.1

        # They are NOT equal
        assert d1 != d2
        assert str(d1).startswith("0.1000000000000000")
        assert str(d2) == "0.1"

        # Always use string constructor for literals
        price = Decimal("0.65")  # ✅ Correct
        assert str(price) == "0.65"


class TestWeiConversions:
    """Test USDC wei conversion exactness (6 decimals)."""

    def test_usdc_to_wei_conversion(self):
        """USDC to wei conversion is exact."""
        usdc_amounts = [
            Decimal("1.0"),
            Decimal("0.01"),
            Decimal("100.50"),
            Decimal("0.123456"),  # 6 decimals
        ]

        for usdc in usdc_amounts:
            wei = usdc * Decimal("1000000")
            # Wei should be whole number
            assert wei == wei.to_integral_value()
            # Round-trip should be exact
            usdc_back = wei / Decimal("1000000")
            assert usdc_back == usdc

    def test_wei_to_usdc_conversion(self):
        """Wei to USDC conversion is exact."""
        wei_amounts = [
            Decimal("1000000"),  # 1.0 USDC
            Decimal("10000"),    # 0.01 USDC
            Decimal("100500000"),  # 100.50 USDC
            Decimal("123456"),   # 0.123456 USDC
        ]

        for wei in wei_amounts:
            usdc = wei / Decimal("1000000")
            # Should have at most 6 decimals
            quantized = usdc.quantize(Decimal("0.000001"))
            assert usdc == quantized
            # Round-trip should be exact
            wei_back = usdc * Decimal("1000000")
            assert wei_back == wei

    def test_price_precision_4_decimals(self):
        """Prices use 4 decimal precision."""
        prices = [
            Decimal("0.6500"),
            Decimal("0.1234"),
            Decimal("0.9999"),
        ]

        for price in prices:
            # Quantize to 4 decimals
            quantized = price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            assert quantized == price
            # Should not have more than 4 decimals
            assert len(str(price).split(".")[-1]) <= 4


class TestQuantizeRounding:
    """Test quantize() rounding behavior."""

    def test_quantize_usdc_precision(self):
        """USDC amounts quantize to 6 decimals."""
        # 7 decimals → 6 decimals
        amount = Decimal("123.4567891")
        quantized = amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        assert quantized == Decimal("123.456789")
        assert len(str(quantized).split(".")[-1]) == 6

    def test_quantize_price_precision(self):
        """Prices quantize to 4 decimals."""
        # 6 decimals → 4 decimals
        price = Decimal("0.654321")
        quantized = price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        assert quantized == Decimal("0.6543")
        assert len(str(quantized).split(".")[-1]) == 4

    def test_quantize_rounding_half_up(self):
        """ROUND_HALF_UP rounds 0.5 up."""
        # Round up
        assert Decimal("0.12345").quantize(Decimal("0.0001"), ROUND_HALF_UP) == Decimal("0.1235")
        assert Decimal("0.65").quantize(Decimal("0.1"), ROUND_HALF_UP) == Decimal("0.7")

        # Round down
        assert Decimal("0.12344").quantize(Decimal("0.0001"), ROUND_HALF_UP) == Decimal("0.1234")
        assert Decimal("0.64").quantize(Decimal("0.1"), ROUND_HALF_UP) == Decimal("0.6")


class TestPydanticValidatorConversion:
    """Test Pydantic models handle various input types."""

    def test_decimal_input(self):
        """Pydantic accepts Decimal directly."""
        order = OrderRequest(
            token_id="123",
            price=Decimal("0.65"),
            size=Decimal("100.0"),
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        assert isinstance(order.price, Decimal)
        assert isinstance(order.size, Decimal)
        assert order.price == Decimal("0.65")
        assert order.size == Decimal("100.0")

    def test_string_input_conversion(self):
        """Pydantic converts string to Decimal."""
        order = OrderRequest(
            token_id="123",
            price="0.65",  # String
            size="100.0",  # String
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        assert isinstance(order.price, Decimal)
        assert isinstance(order.size, Decimal)
        assert order.price == Decimal("0.65")
        assert order.size == Decimal("100.0")

    def test_int_input_conversion(self):
        """Pydantic converts int to Decimal (with valid price as float)."""
        # Note: price must be within 0.01-0.99, so we use float 0.5
        # size can be int
        order = OrderRequest(
            token_id="123",
            price=0.5,  # Valid price as float
            size=100,  # Int
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        assert isinstance(order.price, Decimal)
        assert isinstance(order.size, Decimal)
        assert order.price == Decimal("0.5")
        assert order.size == Decimal("100")

    def test_float_input_conversion(self):
        """Pydantic converts float to Decimal via string (safe)."""
        order = OrderRequest(
            token_id="123",
            price=0.65,  # Float
            size=100.0,  # Float
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        assert isinstance(order.price, Decimal)
        assert isinstance(order.size, Decimal)
        # Converted via str() to avoid precision loss
        assert order.price == Decimal("0.65")
        assert order.size == Decimal("100.0")

    def test_position_model_conversion(self):
        """Position model converts API response (float) to Decimal."""
        # Simulate API response with floats (all required fields)
        position_data = {
            # Identity
            "proxyWallet": "0x1234567890abcdef",
            "asset": "USDC",
            "conditionId": "0xabcdef123456",
            # Position metrics
            "size": 1000.0,  # Float from API
            "avgPrice": 0.65,  # Float from API
            "currentValue": 700.0,  # Float from API
            "initialValue": 650.0,  # Float from API
            "curPrice": 0.70,  # Float from API
            # P&L metrics
            "cashPnl": 50.0,  # Float from API
            "percentPnl": 7.69,  # Float from API
            # Market details
            "title": "Test Market",
            "slug": "test-market",
            "outcome": "Yes",
            "outcomeIndex": 0,
            "oppositeOutcome": "No",
            # Status flags
            "redeemable": False,
        }

        position = Position(**position_data)

        # All numeric fields are Decimal
        assert isinstance(position.size, Decimal)
        assert isinstance(position.avg_price, Decimal)
        assert isinstance(position.current_value, Decimal)
        assert isinstance(position.initial_value, Decimal)
        assert isinstance(position.cur_price, Decimal)
        assert isinstance(position.cash_pnl, Decimal)
        assert isinstance(position.percent_pnl, Decimal)

        # Values are correct
        assert position.size == Decimal("1000.0")
        assert position.avg_price == Decimal("0.65")
        assert position.cur_price == Decimal("0.70")
        assert position.current_value == Decimal("700.0")
        assert position.initial_value == Decimal("650.0")

    def test_balance_model_conversion(self):
        """Balance model converts API response to Decimal."""
        balance_data = {
            "collateral": 1000.50,  # Float from API
        }

        balance = Balance(**balance_data)
        assert isinstance(balance.collateral, Decimal)
        assert balance.collateral == Decimal("1000.50")


class TestFinancialCalculationAccuracy:
    """Test fee and profit calculations maintain precision."""

    def test_net_cost_precision(self):
        """Net cost calculation is exact."""
        price = Decimal("0.60")
        size = Decimal("100.0")
        fee_rate_bps = 0  # Polymarket has 0% fees

        net_cost, fee = calculate_net_cost(Side.BUY, price, size, fee_rate_bps)

        # Should be exact
        assert isinstance(net_cost, Decimal)
        assert isinstance(fee, Decimal)
        assert net_cost == Decimal("100.0")
        assert fee == Decimal("0.0")

    def test_profit_calculation_precision(self):
        """P&L calculation is exact."""
        entry_price = Decimal("0.60")
        exit_price = Decimal("0.70")
        size = Decimal("100.0")
        fee_rate_bps = 0

        pnl = calculate_profit_after_fees(
            Side.BUY, entry_price, exit_price, size, fee_rate_bps, fee_rate_bps
        )

        # All values should be Decimal
        assert isinstance(pnl["gross_profit"], Decimal)
        assert isinstance(pnl["net_profit"], Decimal)
        assert isinstance(pnl["entry_cost"], Decimal)
        assert isinstance(pnl["exit_proceeds"], Decimal)
        assert isinstance(pnl["token_count"], Decimal)

        # Verify calculations are exact
        token_count = size / entry_price  # 100 / 0.60 = 166.666667 tokens
        expected_gross = token_count * (exit_price - entry_price)  # 166.666667 * 0.10
        expected_gross = expected_gross.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        assert pnl["gross_profit"] == expected_gross
        assert pnl["net_profit"] == expected_gross  # No fees

    def test_breakeven_calculation_precision(self):
        """Breakeven calculation is exact."""
        entry_price = Decimal("0.60")
        size = Decimal("100.0")

        breakeven, total_fees = estimate_breakeven_exit(
            Side.BUY, entry_price, size, 0, 0
        )

        assert isinstance(breakeven, Decimal)
        assert isinstance(total_fees, Decimal)
        assert breakeven == Decimal("0.6000")  # Quantized to 4 decimals
        assert total_fees == Decimal("0.0")

    def test_effective_spread_precision(self):
        """Effective spread calculation is exact."""
        bid = Decimal("0.59")
        ask = Decimal("0.61")
        size = Decimal("100.0")

        spread = get_effective_spread(bid, ask, size, 0)

        # All numeric values should be Decimal
        assert isinstance(spread["raw_spread"], Decimal)
        assert isinstance(spread["buy_cost"], Decimal)
        assert isinstance(spread["sell_proceeds"], Decimal)
        assert isinstance(spread["effective_spread"], Decimal)

        # Verify calculations
        assert spread["raw_spread"] == Decimal("0.0200")
        assert spread["buy_cost"] == ask * size  # 0.61 * 100 = 61.0
        assert spread["sell_proceeds"] == bid * size  # 0.59 * 100 = 59.0

    def test_balance_validation_precision(self):
        """Balance validation uses exact arithmetic."""
        price = Decimal("0.60")
        size = Decimal("100.0")
        available_usdc = Decimal("100.0")

        valid, error = validate_balance(
            Side.BUY, price, size, available_usdc, Decimal("0.0"), 0
        )

        # Should pass with exact balance
        assert valid is True
        assert error is None

        # Fail with insufficient balance (even by tiny amount)
        available_usdc = Decimal("99.999999")
        valid, error = validate_balance(
            Side.BUY, price, size, available_usdc, Decimal("0.0"), 0
        )
        assert valid is False
        assert "Insufficient USDC" in error

    def test_profitability_check_precision(self):
        """Profitability check uses exact arithmetic."""
        entry_price = Decimal("0.60")
        exit_price = Decimal("0.70")
        size = Decimal("100.0")
        min_profit = Decimal("1.0")

        profitable, profit = check_order_profitability(
            entry_price, exit_price, size, 0, min_profit
        )

        assert isinstance(profit, Decimal)
        assert profitable is True
        # Exact calculation: (100 / 0.60) * (0.70 - 0.60) = 16.666667
        expected_profit = (size / entry_price) * (exit_price - entry_price)
        expected_profit = expected_profit.quantize(Decimal("0.000001"), ROUND_HALF_UP)
        assert profit == expected_profit


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_small_amounts(self):
        """Handle very small amounts correctly."""
        price = Decimal("0.01")  # Minimum price
        size = Decimal("0.01")  # Minimum size

        net_cost, fee = calculate_net_cost(Side.BUY, price, size, 0)
        assert net_cost == Decimal("0.01")
        assert fee == Decimal("0.0")

    def test_very_large_amounts(self):
        """Handle very large amounts correctly."""
        price = Decimal("0.99")  # Maximum price
        size = Decimal("1000000.0")  # $1M order

        net_cost, fee = calculate_net_cost(Side.BUY, price, size, 0)
        assert net_cost == Decimal("1000000.0")
        assert isinstance(net_cost, Decimal)

    def test_zero_values(self):
        """Handle zero values correctly."""
        # Zero size
        net_cost, fee = calculate_net_cost(
            Side.BUY, Decimal("0.50"), Decimal("0.0"), 0
        )
        assert net_cost == Decimal("0.0")
        assert fee == Decimal("0.0")

    def test_division_by_zero_protection(self):
        """Ensure division by zero is caught."""
        # Zero entry price in profit calculation
        with pytest.raises(ZeroDivisionError):
            calculate_profit_after_fees(
                Side.BUY,
                Decimal("0.0"),  # Zero entry price
                Decimal("0.70"),
                Decimal("100.0"),
                0, 0
            )

        # Zero entry price in profitability check
        with pytest.raises(ZeroDivisionError):
            check_order_profitability(
                Decimal("0.0"),  # Zero entry
                Decimal("0.70"),
                Decimal("100.0"),
                0,
                Decimal("1.0")
            )

    def test_negative_values_rejected(self):
        """Ensure negative values are rejected."""
        # Negative price
        valid, error = validate_balance(
            Side.BUY,
            Decimal("-0.50"),  # Negative price
            Decimal("100.0"),
            Decimal("100.0"),
            Decimal("0.0"),
            0
        )
        assert valid is False
        assert "outside valid range" in error

        # Negative size
        valid, error = validate_balance(
            Side.BUY,
            Decimal("0.50"),
            Decimal("-100.0"),  # Negative size
            Decimal("100.0"),
            Decimal("0.0"),
            0
        )
        assert valid is False
        assert "below minimum" in error

        # Negative available USDC
        valid, error = validate_balance(
            Side.BUY,
            Decimal("0.50"),
            Decimal("100.0"),
            Decimal("-50.0"),  # Negative balance
            Decimal("0.0"),
            0
        )
        assert valid is False
        assert "cannot be negative" in error

        # Negative available tokens
        valid, error = validate_balance(
            Side.SELL,
            Decimal("0.50"),
            Decimal("100.0"),
            Decimal("100.0"),
            Decimal("-10.0"),  # Negative tokens
            0
        )
        assert valid is False
        assert "cannot be negative" in error

    def test_roi_with_zero_entry_cost(self):
        """ROI calculation handles zero entry cost safely."""
        # SELL with zero exit price → zero entry cost
        result = calculate_profit_after_fees(
            Side.SELL,
            Decimal("0.50"),
            Decimal("0.0"),  # Exit at 0
            Decimal("100.0"),
            0, 0
        )
        # Should return ROI = 0 when entry cost is 0 (not divide by zero)
        assert result['entry_cost'] == Decimal("0.0")
        assert result['roi_pct'] == Decimal("0.0")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
