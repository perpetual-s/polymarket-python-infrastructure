"""
Example 1: Simple Trading for Strategy-1

⚠️ WARNING: This example is SIMPLIFIED for learning purposes.
⚠️ For PRODUCTION bots, use example 10_production_safe_trading.py

Production bots MUST include:
- Fee calculation BEFORE placing orders
- Order validation (price, size, parameters)
- Balance validation (including fees!)
- Profitability checks

See examples/10_production_safe_trading.py for the complete pattern.

This example shows:
- Basic order placement
- Cancellation
- Balance checking
"""

import os
import time
from decimal import Decimal
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    OrderType
)

def main():
    """Simple trading bot example."""

    # 1. Initialize client
    print("Initializing Polymarket client...")
    client = PolymarketClient(
        # Optional: Configure for your needs
        pool_connections=20,  # For single wallet, 20 is plenty
        pool_maxsize=50
    )

    # 2. Add your wallet
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        raise ValueError("Set POLYMARKET_PRIVATE_KEY environment variable")

    client.add_wallet(
        WalletConfig(private_key=private_key),
        wallet_id="strategy1",
        set_default=True  # Makes this the default wallet
    )
    print("✓ Wallet added")

    # 3. Check balance
    balance = client.get_balance("strategy1")
    print(f"✓ Balance: ${balance.collateral:.2f} USDC")

    if balance.collateral < 10:
        print("⚠ Low balance! Need at least $10 USDC to trade")
        return

    # 4. Get market info
    print("\nFetching market...")
    markets = client.get_markets(slug="trump-vs-biden-2024", limit=1)

    if not markets:
        print("Market not found")
        return

    market = markets[0]
    print(f"✓ Market: {market.question}")
    print(f"  Volume: ${market.volume:,.2f}")
    print(f"  Active: {market.active}")

    # Get token ID for "Trump wins" outcome
    token_id = market.tokens[0] if market.tokens else None
    if not token_id:
        print("No tokens found")
        return

    # 5. Get current orderbook
    print(f"\nFetching orderbook for token {token_id}...")
    orderbook = client.get_orderbook(token_id)
    print(f"✓ Best Bid: {orderbook.best_bid}")
    print(f"✓ Best Ask: {orderbook.best_ask}")
    print(f"✓ Spread: {orderbook.spread}")

    # 6. Place limit order
    print("\nPlacing limit order...")
    order = OrderRequest(
        token_id=token_id,
        price=Decimal("0.50"),  # Buy at $0.50
        size=Decimal("10.0"),   # Spend $10 USDC
        side=Side.BUY,
        order_type=OrderType.GTC  # Good-til-cancelled
    )

    try:
        response = client.place_order(order, wallet_id="strategy1")

        if response.success:
            print(f"✅ Order placed successfully!")
            print(f"   Order ID: {response.order_id}")
            print(f"   Status: {response.status}")

            # 7. Wait a bit, then cancel
            print("\nWaiting 5 seconds before cancelling...")
            time.sleep(5)

            print("Cancelling order...")
            cancelled = client.cancel_order(response.order_id, wallet_id="strategy1")

            if cancelled:
                print("✅ Order cancelled successfully")
            else:
                print("❌ Cancel failed (order may already be filled)")

        else:
            print(f"❌ Order failed: {response.error_msg}")

    except Exception as e:
        print(f"❌ Error: {e}")
        # See example 05_error_handling.py for comprehensive error handling

    # 8. Check positions
    print("\nChecking positions...")
    positions = client.get_positions(wallet_id="strategy1")

    if positions:
        print(f"✓ You have {len(positions)} positions:")
        for pos in positions[:5]:  # Show first 5
            print(f"   {pos.title}: {pos.size:.2f} shares @ ${pos.avg_price:.2f}")
            print(f"   P&L: ${pos.cash_pnl:.2f} ({pos.percent_pnl:.1f}%)")
    else:
        print("  No open positions")

if __name__ == "__main__":
    main()
