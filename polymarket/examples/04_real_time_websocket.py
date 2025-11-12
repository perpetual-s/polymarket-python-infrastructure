"""
Example 4: Real-Time WebSocket for Both Strategies

Shows how to use WebSocket for instant market updates and order fills.
Critical for HFT and real-time dashboards.

v2.8 Note: WebSocket cleanup in finally blocks ensures no socket leaks.
See Documentation/ROBUSTNESS_AUDIT.md for validation details.
"""

import os
import time
from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side

def main():
    """WebSocket real-time updates example."""

    # 1. Initialize client
    print("Initializing client...")
    client = PolymarketClient()

    # 2. Add wallet
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if private_key:
        client.add_wallet(
            WalletConfig(private_key=private_key),
            wallet_id="strategy1"
        )

    # 3. Get market to track
    print("\nFinding market...")
    markets = client.get_markets(active=True, limit=1)

    if not markets:
        print("No active markets found")
        return

    market = markets[0]
    token_id = market.tokens[0] if market.tokens else None

    if not token_id:
        print("No tokens found")
        return

    print(f"✓ Tracking: {market.question}")
    print(f"  Token ID: {token_id}")

    # 4. Subscribe to real-time orderbook updates
    print("\n🔴 LIVE: Subscribing to orderbook updates...")
    print("Press Ctrl+C to stop\n")

    update_count = 0

    def on_orderbook_update(book):
        """Called on each orderbook update (~100ms)."""
        nonlocal update_count
        update_count += 1

        # Show update
        print(f"[{update_count:04d}] Best Bid: {book.best_bid:.4f} | Best Ask: {book.best_ask:.4f} | Spread: {book.spread:.4f}")

        # Example: Trading logic
        if book.spread and book.spread < 0.01:
            print("  ⚡ Tight spread detected! (Good for market making)")

    try:
        # Subscribe to orderbook
        client.subscribe_orderbook(token_id, on_orderbook_update)

        # Optional: Subscribe to order fills
        if private_key:
            print("\n📬 Also subscribing to order fill notifications...")

            def on_order_update(order_data):
                """Called when orders are filled."""
                print(f"\n🔔 ORDER UPDATE: {order_data.get('status')}")
                print(f"   Order ID: {order_data.get('orderId')}")
                print(f"   Status: {order_data.get('status')}\n")

            client.subscribe_user_orders(on_order_update, wallet_id="strategy1")

        # Keep running
        print("\n✓ WebSocket connected - receiving real-time updates")
        print("  (Much faster than polling every 1s!)\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopping...")
        client.unsubscribe_all()
        print(f"✓ Received {update_count} updates")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    finally:
        client.close()

if __name__ == "__main__":
    main()
