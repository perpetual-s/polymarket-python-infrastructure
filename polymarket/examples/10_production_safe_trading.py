"""
Example 10: Production-Safe Trading with Security Features

Demonstrates BEST PRACTICES for order placement including:
- Fee calculation before ordering
- Order validation
- Balance validation (including fees)
- Profitability checks
- Proper error handling

This is the RECOMMENDED pattern for all production trading.
Copy this for your Strategy-1 and Strategy-3 bots.
"""

import os
from decimal import Decimal
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    OrderType,
    # NEW: Import validation and fee utilities
    validate_order,
    validate_balance,
    calculate_net_cost,
    calculate_profit_after_fees,
    check_order_profitability,
)
from shared.polymarket.exceptions import (
    ValidationError,
    InsufficientBalanceError,
    OrderRejectedError,
)

def main():
    """Production-safe trading example with all validations."""

    # 1. Initialize client
    print("=" * 70)
    print("PRODUCTION-SAFE TRADING EXAMPLE")
    print("=" * 70)

    client = PolymarketClient()

    # 2. Add wallet
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        raise ValueError("Set POLYMARKET_PRIVATE_KEY environment variable")

    client.add_wallet(
        WalletConfig(private_key=private_key),
        wallet_id="strategy1",
        set_default=True
    )
    print("✓ Wallet added\n")

    # 3. Get balance
    balance = client.get_balance("strategy1")
    print(f"Current Balance: ${balance.collateral:.2f} USDC\n")

    # 4. Get market and orderbook
    markets = client.get_markets(limit=1, active=True)
    if not markets:
        print("No markets found")
        return

    market = markets[0]
    token_id = market.tokens[0] if market.tokens else None
    if not token_id:
        print("No tokens found")
        return

    print(f"Market: {market.question}")

    orderbook = client.get_orderbook(token_id)
    print(f"Best Ask: {orderbook.best_ask}")
    print(f"Best Bid: {orderbook.best_bid}")
    print(f"Spread: {orderbook.spread}\n")

    # ========================================================================
    # PRODUCTION-SAFE ORDER PLACEMENT WITH ALL VALIDATIONS
    # ========================================================================

    # Define order parameters
    entry_price = orderbook.best_ask  # Buy at best ask (already Decimal from orderbook)
    exit_price = entry_price + Decimal("0.05")   # Target 5 cent profit
    size = Decimal("50.0")  # $50 USDC order
    fee_rate_bps = 0  # Polymarket charges 0% fees (officially confirmed)

    print("=" * 70)
    print("STEP 1: PRE-FLIGHT VALIDATION")
    print("=" * 70)

    # 1.1: Create order request
    order = OrderRequest(
        token_id=token_id,
        price=entry_price,
        size=size,
        side=Side.BUY,
        order_type=OrderType.GTC
    )

    # 1.2: Validate order parameters
    print("\n[1/4] Validating order parameters...")
    valid, error = validate_order(order)
    if not valid:
        print(f"❌ Invalid order: {error}")
        return
    print(f"✓ Order parameters valid")

    # 1.3: Calculate fees BEFORE placing order
    print("\n[2/4] Calculating fees...")
    net_cost, fee = calculate_net_cost(
        side=Side.BUY,
        price=entry_price,
        size=size,
        fee_rate_bps=fee_rate_bps
    )

    token_count = size / entry_price
    print(f"  Order size: ${size:.2f} USD ({token_count:.2f} tokens)")
    print(f"  Fee:        ${fee:.2f}")
    print(f"  Total cost: ${net_cost:.2f} (${size:.2f} + ${fee:.2f} fee)")

    # 1.4: Validate balance (including fees!)
    print("\n[3/4] Validating balance...")
    valid, error = validate_balance(
        side=Side.BUY,
        price=entry_price,
        size=size,
        available_usdc=balance.collateral,
        fee_rate_bps=fee_rate_bps
    )

    if not valid:
        print(f"❌ Insufficient balance: {error}")
        print(f"   Have: ${balance.collateral:.2f}")
        print(f"   Need: ${net_cost:.2f}")
        return
    print(f"✓ Balance sufficient (${balance.collateral:.2f} available)")

    # 1.5: Check profitability
    print("\n[4/4] Checking profitability...")
    profitable, net_profit = check_order_profitability(
        entry_price=entry_price,
        exit_price=exit_price,
        size=size,
        fee_rate_bps=fee_rate_bps,
        min_profit_usdc=Decimal("1.0")  # Require at least $1 profit
    )

    if not profitable:
        print(f"❌ Trade not profitable: ${net_profit:.2f} profit")
        print(f"   Entry: ${entry_price:.4f}")
        print(f"   Target exit: ${exit_price:.4f}")
        print(f"   Spread too small after fees")
        return

    print(f"✓ Trade profitable: ${net_profit:.2f} net profit after fees")

    # Calculate detailed P&L breakdown
    pnl = calculate_profit_after_fees(
        entry_side=Side.BUY,
        entry_price=entry_price,
        exit_price=exit_price,
        size=size,
        entry_fee_rate_bps=fee_rate_bps,
        exit_fee_rate_bps=fee_rate_bps
    )

    print(f"\n  P&L Breakdown:")
    print(f"    Entry cost:    ${pnl['entry_cost']:.2f}")
    print(f"    Exit proceeds: ${pnl['exit_proceeds']:.2f}")
    print(f"    Gross profit:  ${pnl['gross_profit']:.2f}")
    print(f"    Total fees:    ${pnl['total_fees']:.2f}")
    print(f"    Net profit:    ${pnl['net_profit']:.2f}")
    print(f"    ROI:           {pnl['roi_pct']:.2f}%")

    # ========================================================================
    # STEP 2: PLACE ORDER (after all validations passed)
    # ========================================================================

    print("\n" + "=" * 70)
    print("STEP 2: PLACE ORDER")
    print("=" * 70)

    try:
        response = client.place_order(order, wallet_id="strategy1")

        if response.success:
            print(f"\n✅ Order placed successfully!")
            print(f"   Order ID: {response.order_id}")
            print(f"   Status: {response.status}")
            print(f"\n   Cost: ${net_cost:.2f}")
            print(f"   Expected profit: ${net_profit:.2f} at ${exit_price:.4f}")

        else:
            print(f"\n❌ Order failed: {response.error_msg}")

    except ValidationError as e:
        print(f"❌ Validation failed: {e}")
    except InsufficientBalanceError as e:
        print(f"❌ Insufficient balance: {e}")
    except OrderRejectedError as e:
        print(f"❌ Order rejected: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

    # ========================================================================
    # STEP 3: MONITOR POSITIONS
    # ========================================================================

    print("\n" + "=" * 70)
    print("STEP 3: POSITION MONITORING")
    print("=" * 70)

    positions = client.get_positions(wallet_id="strategy1")

    if positions:
        print(f"\nOpen Positions: {len(positions)}")
        for pos in positions[:3]:  # Show first 3
            print(f"\n  {pos.title}")
            print(f"    Size: {pos.size:.2f} shares @ avg ${pos.avg_price:.4f}")
            print(f"    Current value: ${pos.current_value:.2f}")
            print(f"    P&L: ${pos.cash_pnl:.2f} ({pos.percent_pnl:+.1f}%)")
    else:
        print("\nNo open positions")

    print("\n" + "=" * 70)
    print("KEY TAKEAWAYS:")
    print("=" * 70)
    print("✓ Always calculate fees BEFORE placing orders")
    print("✓ Always validate balance including fees")
    print("✓ Always check profitability before trading")
    print("✓ Use validate_order() to catch parameter errors early")
    print("✓ Handle all exceptions gracefully")
    print("=" * 70)


if __name__ == "__main__":
    main()
