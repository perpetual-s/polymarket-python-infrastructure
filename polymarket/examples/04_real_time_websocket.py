"""
Example 4: Real-Time WebSocket for Both Strategies

Shows how to use WebSocket for instant market updates and order fills.
Critical for HFT and real-time dashboards.

v3.2 Note: Typed message models for type-safe WebSocket handling.
- OrderbookMessage: Market orderbook updates
- TradeMessage: Trade executions (MATCHED, MINED, CONFIRMED, etc.)
- OrderMessage: Order events (PLACEMENT, UPDATE, CANCELLATION)
"""

import os
import time
import asyncio
from polymarket import PolymarketClient, WalletConfig
from polymarket.api.websocket_models import (
    TradeMessage,
    OrderMessage,
    TradeStatus,
    OrderEventType
)

async def main():
    """WebSocket real-time updates example with typed messages."""

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
    markets = await client.get_markets(active=True, limit=1)

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

        # Optional: Subscribe to order fills (TYPED MESSAGES)
        if private_key:
            print("\n📬 Also subscribing to order fill notifications...")
            print("  (Using typed messages: TradeMessage and OrderMessage)\n")

            def on_order_update(message):
                """
                Called when orders are filled or updated.

                Receives typed messages:
                - TradeMessage: Trade executions (status changes)
                - OrderMessage: Order events (placement, updates, cancellations)
                """
                if isinstance(message, TradeMessage):
                    # Trade execution update
                    status_emoji = {
                        TradeStatus.MATCHED: "🎯",
                        TradeStatus.MINED: "⛏️",
                        TradeStatus.CONFIRMED: "✅",
                        TradeStatus.RETRYING: "🔄",
                        TradeStatus.FAILED: "❌",
                    }.get(message.status, "📦")

                    print(f"\n{status_emoji} TRADE UPDATE: {message.status}")
                    print(f"   Trade ID: {message.id}")
                    print(f"   Side: {message.side}")
                    print(f"   Price: ${message.price}")
                    print(f"   Size: {message.size}")
                    print(f"   Outcome: {message.outcome}\n")

                elif isinstance(message, OrderMessage):
                    # Order event (placement, update, cancellation)
                    event_emoji = {
                        OrderEventType.PLACEMENT: "🆕",
                        OrderEventType.UPDATE: "📝",
                        OrderEventType.CANCELLATION: "🚫",
                    }.get(message.type, "📦")

                    print(f"\n{event_emoji} ORDER EVENT: {message.type}")
                    print(f"   Order ID: {message.id}")
                    print(f"   Side: {message.side}")
                    print(f"   Price: ${message.price}")
                    print(f"   Original Size: {message.original_size}")
                    print(f"   Matched: {message.size_matched}\n")

            client.subscribe_user_orders(on_order_update, wallet_id="strategy1")

        # Keep running
        print("\n✓ WebSocket connected - receiving real-time updates")
        print("  (Much faster than polling every 1s!)")
        print("  (All messages are type-safe with validation!)\n")

        # Show WebSocket health stats
        if hasattr(client._ws, 'stats'):
            stats = client._ws.stats()
            print("WebSocket Stats:")
            print(f"  Status: {stats['status']}")
            print(f"  Uptime: {stats.get('uptime_seconds', 0)}s")
            print(f"  Messages: {stats['messages_received']}")
            print(f"  Subscriptions: {stats['subscriptions']}\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopping...")
        client.unsubscribe_all()
        print(f"✓ Received {update_count} orderbook updates")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
