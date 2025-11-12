"""
Example 11: CTF Exchange & Neg-Risk Features

Demonstrates the new CTF (Conditional Token Framework) infrastructure:
- Fee calculation utilities
- Neg-risk market detection
- NegRiskAdapter for NO→YES conversions
- Position splitting/merging
- Complete validation layer

These features enable capital-efficient trading on multi-outcome markets.

References:
- shared/polymarket/Documentation/NEG_RISK_CTF.md
- https://github.com/Polymarket/neg-risk-ctf-adapter
- https://github.com/Polymarket/ctf-exchange
"""

import os
from decimal import Decimal
from shared.polymarket import (
    PolymarketClient,
    Side,
    # Fee calculation utilities
    calculate_order_fee,
    calculate_net_cost,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    calculate_profit_after_fees,
    get_effective_spread,
    # Validation utilities
    validate_order,
    validate_balance,
    validate_price_bounds,
    validate_neg_risk_market,
    check_order_profitability,
    # CTF utilities
    NegRiskAdapter,
    ConversionCalculator,
    is_safe_to_trade,
    NEG_RISK_ADAPTER,
    CTF_ADDRESS,
)

def demonstrate_fee_calculations():
    """Show fee calculation utilities."""
    print("=" * 70)
    print("FEATURE 1: FEE CALCULATIONS")
    print("=" * 70)

    # Example order parameters (using Decimal for precision)
    price = Decimal("0.60")
    size = Decimal("100.0")  # $100 USDC
    fee_rate_bps = 0  # Polymarket charges 0% fees (officially confirmed)

    print(f"\nOrder: {size} USDC at ${price} (0% fee)")
    print("-" * 70)

    # 1. Calculate fee
    buy_fee = calculate_order_fee(Side.BUY, price, size, fee_rate_bps)
    sell_fee = calculate_order_fee(Side.SELL, price, size, fee_rate_bps)

    print(f"BUY fee:  ${buy_fee:.2f}")
    print(f"SELL fee: ${sell_fee:.2f}")
    print(f"Fee difference: ${abs(buy_fee - sell_fee):.2f}")

    # 2. Calculate net cost/proceeds
    buy_cost, _ = calculate_net_cost(Side.BUY, price, size, fee_rate_bps)
    sell_proceeds, _ = calculate_net_cost(Side.SELL, price, size, fee_rate_bps)

    token_count = size / price
    print(f"\nBUY ${size:.2f} USD worth at ${price} ({token_count:.2f} tokens):")
    print(f"  Base cost: ${size:.2f}")
    print(f"  + Fee: ${buy_fee:.2f}")
    print(f"  = Total: ${buy_cost:.2f}")

    print(f"\nSELL ${size:.2f} USD worth at ${price} ({token_count:.2f} tokens):")
    print(f"  Base proceeds: ${size:.2f}")
    print(f"  - Fee: ${sell_fee:.2f}")
    print(f"  = Net: ${sell_proceeds:.2f}")

    # 3. Compare fees
    comparison = compare_fees_buy_vs_sell(price, size, fee_rate_bps)
    print(f"\nFee Comparison:")
    print(f"  BUY fee % of cost: {comparison['buy_fee_pct_of_cost']:.2f}%")
    print(f"  SELL fee % of proceeds: {comparison['sell_fee_pct_of_proceeds']:.2f}%")

    # 4. Breakeven calculation
    entry_price = Decimal("0.60")
    breakeven, total_fees = estimate_breakeven_exit(
        Side.BUY, entry_price, size, fee_rate_bps, fee_rate_bps
    )
    print(f"\nBreakeven Analysis:")
    print(f"  Entry: ${entry_price:.4f}")
    print(f"  Breakeven exit: ${breakeven:.4f}")
    print(f"  Total fees: ${total_fees:.2f}")

    # 5. Profit calculation
    entry_price = Decimal("0.60")
    exit_price = Decimal("0.70")
    pnl = calculate_profit_after_fees(
        Side.BUY, entry_price, exit_price, size, fee_rate_bps, fee_rate_bps
    )

    print(f"\nP&L ($0.60 → $0.70):")
    print(f"  Entry cost: ${pnl['entry_cost']:.2f}")
    print(f"  Exit proceeds: ${pnl['exit_proceeds']:.2f}")
    print(f"  Gross profit: ${pnl['gross_profit']:.2f}")
    print(f"  Total fees: ${pnl['total_fees']:.2f}")
    print(f"  Net profit: ${pnl['net_profit']:.2f}")
    print(f"  ROI: {pnl['roi_pct']:.2f}%")

    # 6. Effective spread
    bid = Decimal("0.59")
    ask = Decimal("0.61")
    spread = get_effective_spread(bid, ask, size, fee_rate_bps)

    print(f"\nEffective Spread (bid={bid}, ask={ask}):")
    print(f"  Raw spread: {spread['raw_spread']:.4f} ({spread['raw_spread_bps']} bps)")
    print(f"  Buy cost: ${spread['buy_cost']:.2f}")
    print(f"  Sell proceeds: ${spread['sell_proceeds']:.2f}")
    print(f"  Effective spread: ${spread['effective_spread']:.2f} ({spread['effective_spread_bps']} bps)")

    print("\n✅ Fee calculations complete\n")


def demonstrate_market_validation():
    """Show market safety validation."""
    print("=" * 70)
    print("FEATURE 2: NEG-RISK MARKET VALIDATION")
    print("=" * 70)

    # Get markets
    client = PolymarketClient()
    markets = client.get_markets(limit=10)

    print(f"\nChecking {len(markets)} markets for neg-risk safety...\n")

    neg_risk_markets = []
    safe_count = 0

    for market in markets:
        # Check if it's a neg-risk market
        if market.neg_risk or market.enable_neg_risk:
            neg_risk_markets.append(market)

            # Validate safety
            try:
                if is_safe_to_trade(market):
                    safe_count += 1
                    print(f"✓ SAFE: {market.slug}")
                    print(f"  Outcomes: {market.outcomes}")
                else:
                    print(f"⚠ UNSAFE: {market.slug}")
                    if market.neg_risk_augmented:
                        print(f"  Reason: Augmented market (incomplete outcome universe)")
            except Exception as e:
                print(f"❌ INVALID: {market.slug}")
                print(f"  Reason: {e}")

    print(f"\nSummary:")
    print(f"  Total markets: {len(markets)}")
    print(f"  Neg-risk markets: {len(neg_risk_markets)}")
    print(f"  Safe for trading: {safe_count}")

    print("\n✅ Market validation complete\n")


def demonstrate_validation_utilities():
    """Show order validation utilities."""
    print("=" * 70)
    print("FEATURE 3: ORDER VALIDATION")
    print("=" * 70)

    print("\n[Test 1] Price bounds validation")
    print("-" * 70)

    try:
        validate_price_bounds(Decimal("0.55"))
        print("✓ Price 0.55: Valid")
    except Exception as e:
        print(f"❌ Price 0.55: {e}")

    try:
        validate_price_bounds(Decimal("1.50"))
        print("✓ Price 1.50: Valid")
    except Exception as e:
        print(f"❌ Price 1.50: {e}")

    print("\n[Test 2] Profitability check")
    print("-" * 70)

    # Profitable trade
    profitable, profit = check_order_profitability(
        entry_price=Decimal("0.60"),
        exit_price=Decimal("0.70"),
        size=Decimal("100.0"),
        fee_rate_bps=0,  # Polymarket charges 0% fees
        min_profit_usdc=Decimal("1.0")
    )

    print(f"Trade 1 ($0.60 → $0.70, $100):")
    print(f"  Profitable: {profitable}")
    print(f"  Net profit: ${profit:.2f}")

    # Unprofitable trade (spread too small)
    profitable, profit = check_order_profitability(
        entry_price=Decimal("0.60"),
        exit_price=Decimal("0.61"),
        size=Decimal("100.0"),
        fee_rate_bps=0,  # Polymarket charges 0% fees
        min_profit_usdc=Decimal("1.0")
    )

    print(f"\nTrade 2 ($0.60 → $0.61, $100):")
    print(f"  Profitable: {profitable}")
    print(f"  Net profit: ${profit:.2f}")

    print("\n[Test 3] Balance validation")
    print("-" * 70)

    # Sufficient balance
    valid, error = validate_balance(
        side=Side.BUY,
        price=Decimal("0.60"),
        size=Decimal("100.0"),
        available_usdc=Decimal("100.0"),
        fee_rate_bps=0  # Polymarket charges 0% fees
    )

    print(f"Balance check ($100 USDC, buy $100 at 0.60):")
    print(f"  Valid: {valid}")
    if error:
        print(f"  Error: {error}")

    # Insufficient balance
    valid, error = validate_balance(
        side=Side.BUY,
        price=Decimal("0.60"),
        size=Decimal("100.0"),
        available_usdc=Decimal("50.0"),
        fee_rate_bps=0  # Polymarket charges 0% fees
    )

    print(f"\nBalance check ($50 USDC, buy $100 at 0.60):")
    print(f"  Valid: {valid}")
    if error:
        print(f"  Error: {error}")

    print("\n✅ Validation utilities complete\n")


def demonstrate_ctf_adapter():
    """Show NegRiskAdapter capabilities."""
    print("=" * 70)
    print("FEATURE 4: NEG-RISK CTF ADAPTER")
    print("=" * 70)

    print("\nNegRiskAdapter enables:")
    print("  • NO → YES position conversions")
    print("  • Split USDC → YES + NO tokens")
    print("  • Merge YES + NO → USDC")
    print("  • Redeem winning positions")

    print("\nContract Addresses (Polygon Mainnet):")
    print(f"  NEG_RISK_ADAPTER: {NEG_RISK_ADAPTER}")
    print(f"  CTF_ADDRESS: {CTF_ADDRESS}")

    print("\nConversion Calculator Example:")
    print("-" * 70)

    calc = ConversionCalculator()

    # Example: 3-candidate election
    result = calc.calculate_conversion(
        no_tokens=["token_a_no", "token_b_no"],
        amount=1.0,
        total_outcomes=3
    )

    print(f"Election with 3 candidates (A, B, C):")
    print(f"  Convert: 1 NO_A + 1 NO_B")
    print(f"  Receive:")
    print(f"    Collateral: ${result['collateral']:.2f} USDC")
    print(f"    YES tokens: {result['yes_token_count']} (YES_C)")

    print(f"\nFormula: collateral = amount × (no_token_count - 1)")
    print(f"  = 1.0 × (2 - 1) = 1.0 USDC")

    print("\n⚠ WARNING: NegRiskAdapter requires on-chain transactions")
    print("  • Requires MATIC for gas fees")
    print("  • Requires CTF token approvals")
    print("  • See adapter.approve_ctf_tokens() and adapter.convert_positions()")

    print("\n✅ CTF adapter overview complete\n")


def main():
    """Run all demonstrations."""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "CTF EXCHANGE & NEG-RISK FEATURES" + " " * 21 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    # Feature demonstrations
    demonstrate_fee_calculations()
    demonstrate_market_validation()
    demonstrate_validation_utilities()
    demonstrate_ctf_adapter()

    print("=" * 70)
    print("SUMMARY: CTF INFRASTRUCTURE")
    print("=" * 70)
    print("\n✅ Integrated 3 official Polymarket repositories:")
    print("   1. neg-risk-ctf-adapter (MIT)")
    print("   2. ctf-exchange (MIT)")
    print("   3. go-order-utils (MIT, Go reference)")

    print("\n✅ Available utilities:")
    print("   • 6 fee calculation functions")
    print("   • 9 order validation functions")
    print("   • 7 NegRiskAdapter methods")
    print("   • 5 conversion calculator utilities")

    print("\n✅ Production-ready features:")
    print("   • Gas price validation (max 500 gwei)")
    print("   • Private key sanitization")
    print("   • Thread-safe nonce management")
    print("   • Contract address verification")
    print("   • Comprehensive input validation")

    print("\n📚 Documentation:")
    print("   shared/polymarket/Documentation/NEG_RISK_CTF.md")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
