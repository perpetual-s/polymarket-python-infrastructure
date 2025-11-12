"""
Example: Order Scoring for Strategy-4 (Liquidity Mining).

Demonstrates how to check which orders earn 2% maker rebates on Polymarket.

Run: python examples/09_strategy4_order_scoring.py
"""

from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side


def example_1_check_single_order():
    """Example 1: Check if a single order earns maker rebates."""
    print("\n" + "="*60)
    print("Example 1: Check Single Order for Maker Rebates")
    print("="*60)

    client = PolymarketClient()

    # Example order ID (replace with real order ID)
    order_id = "0x123456789abcdef..."

    print(f"\nChecking if order {order_id[:20]}... earns maker rebates...")

    try:
        is_scoring = client.is_order_scoring(order_id)

        if is_scoring:
            print("✅ Order IS earning 2% maker rebates!")
            print("   This order contributes to Strategy-4 liquidity mining rewards.")
        else:
            print("❌ Order is NOT earning maker rebates")
            print("   This is likely a taker order or doesn't qualify for rewards.")

    except Exception as e:
        print(f"Error checking order scoring: {e}")


def example_2_batch_check_orders():
    """Example 2: Batch check multiple orders for rebates."""
    print("\n" + "="*60)
    print("Example 2: Batch Check Order Scoring")
    print("="*60)

    client = PolymarketClient()

    # Example: Check your active orders
    # In reality, you'd get these from client.get_orders()
    order_ids = [
        "0x123...",  # Replace with real order IDs
        "0x456...",
        "0x789...",
        "0xabc...",
        "0xdef..."
    ]

    print(f"\nChecking {len(order_ids)} orders for maker rebate eligibility...\n")

    try:
        scoring = client.are_orders_scoring(order_ids)

        # Count earning vs non-earning
        earning_count = sum(scoring.values())
        not_earning_count = len(scoring) - earning_count

        print(f"{'Order ID':<25} {'Status':<15} {'Rebate Eligible'}")
        print("-" * 60)

        for order_id, is_scoring in scoring.items():
            status = "✅ SCORING" if is_scoring else "❌ NOT SCORING"
            rebate = "2% rebate" if is_scoring else "No rebate"
            print(f"{order_id[:20]:<25} {status:<15} {rebate}")

        print("\n" + "="*60)
        print(f"Summary:")
        print(f"  Earning rebates: {earning_count} orders (2% maker rebate)")
        print(f"  Not earning:     {not_earning_count} orders")
        print(f"  Total checked:   {len(scoring)} orders")

    except Exception as e:
        print(f"Error checking batch order scoring: {e}")


def example_3_active_orders_scoring():
    """Example 3: Check all active orders for scoring status."""
    print("\n" + "="*60)
    print("Example 3: Analyze Active Orders for Rebate Earnings")
    print("="*60)

    # NOTE: Requires wallet configuration
    print("\nThis example requires wallet configuration.")
    print("Code demonstration:\n")

    print("""
from shared.polymarket import PolymarketClient, WalletConfig

# Setup
client = PolymarketClient()
client.add_wallet(WalletConfig(private_key="0x..."))

# Get all active orders
orders = client.get_orders(wallet_id="strategy4")
order_ids = [order.order_id for order in orders]

# Batch check scoring
scoring = client.are_orders_scoring(order_ids)

# Calculate potential rebate earnings
total_size = 0
scoring_size = 0

for order in orders:
    is_scoring = scoring.get(order.order_id, False)
    if is_scoring:
        scoring_size += order.size
    total_size += order.size

# 2% rebate on scoring orders
potential_rebate = scoring_size * 0.02

print(f"Total order size: ${total_size:.2f}")
print(f"Scoring order size: ${scoring_size:.2f}")
print(f"Potential rebate earnings: ${potential_rebate:.2f}")
    """)


def example_4_strategy4_optimization():
    """Example 4: Strategy-4 optimization - maximize scoring orders."""
    print("\n" + "="*60)
    print("Example 4: Strategy-4 Optimization")
    print("="*60)

    print("\nOptimizing for 2% maker rebates:\n")

    print("""
Strategy-4 Goal: Maximize maker rebate earnings

To earn 2% maker rebates on Polymarket:

1. ✅ Place LIMIT orders (not market orders)
   - Market orders are takers (no rebate)
   - Limit orders can be makers (earn rebate)

2. ✅ Orders must be FILLED as maker
   - If your limit order crosses the spread → taker (no rebate)
   - If your limit order sits on book and gets filled → maker (2% rebate!)

3. ✅ Check scoring status regularly
   # Monitor which orders are earning
   scoring = client.are_orders_scoring(active_order_ids)
   earning_orders = [oid for oid, is_scoring in scoring.items() if is_scoring]

4. ✅ Optimize order placement
   - Place orders INSIDE the spread (more likely to be maker)
   - Avoid crossing the spread (becomes taker)
   - Let orders sit on book to get filled

Example:
  Current spread: $0.50 bid / $0.52 ask

  ❌ BAD: Place BUY at $0.52 (crosses spread → taker → no rebate)
  ✅ GOOD: Place BUY at $0.51 (sits on book → maker → 2% rebate!)

Strategy-4 monitors scoring status and adjusts orders to maximize rebates.
    """)


def example_5_rebate_analytics():
    """Example 5: Analytics - track rebate earnings over time."""
    print("\n" + "="*60)
    print("Example 5: Rebate Earnings Analytics")
    print("="*60)

    print("\nTracking maker rebate earnings:\n")

    print("""
# Track rebate earnings over time
from datetime import datetime, timedelta

client = PolymarketClient()

# Get trade history
trades = client.get_trades(wallet_id="strategy4")

# Filter for filled maker orders (earning rebates)
maker_trades = []
for trade in trades:
    # Check if this was a scoring order
    was_scoring = client.is_order_scoring(trade.order_id)
    if was_scoring:
        maker_trades.append(trade)

# Calculate total rebates earned
total_volume = sum(trade.size * trade.price for trade in maker_trades)
total_rebates = total_volume * 0.02  # 2% rebate

# Analytics
print(f"Maker Orders Filled: {len(maker_trades)}")
print(f"Total Maker Volume: ${total_volume:.2f}")
print(f"Rebates Earned: ${total_rebates:.2f}")

# Daily rebate rate
days_active = 30
daily_rebate = total_rebates / days_active
print(f"Average Daily Rebate: ${daily_rebate:.2f}")

# Annualized return (from rebates only)
annual_rebate = daily_rebate * 365
print(f"Annualized Rebate Income: ${annual_rebate:.2f}")
    """)


def main():
    """Run all examples."""
    print("\n" + "="*60)
    print("STRATEGY-4: ORDER SCORING FOR LIQUIDITY MINING")
    print("="*60)
    print("\nPolymarket offers 2% maker rebates for liquidity providers.")
    print("This example shows how to check which orders earn rebates.")
    print("="*60)

    examples = [
        ("Check Single Order", example_1_check_single_order),
        ("Batch Check Orders", example_2_batch_check_orders),
        ("Active Orders Analysis", example_3_active_orders_scoring),
        ("Strategy-4 Optimization", example_4_strategy4_optimization),
        ("Rebate Analytics", example_5_rebate_analytics),
    ]

    for name, func in examples:
        try:
            func()
        except Exception as e:
            print(f"\n❌ Example failed: {e}")

        input("\nPress Enter to continue to next example...")

    print("\n" + "="*60)
    print("Key Takeaways:")
    print("="*60)
    print("1. Use is_order_scoring() to check single orders")
    print("2. Use are_orders_scoring() for batch checking (more efficient)")
    print("3. Only MAKER orders earn 2% rebates (not takers)")
    print("4. Place limit orders inside spread to maximize maker fills")
    print("5. Monitor scoring status to optimize Strategy-4 performance")
    print("\n✅ Strategy-4 can earn consistent 2% on maker volume!")
    print("="*60)


if __name__ == "__main__":
    main()
