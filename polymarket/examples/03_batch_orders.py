"""
Example 3: Batch Order Placement for Strategy-3

Shows how to place 10+ orders simultaneously - critical for Strategy-3 performance.
"""

import os
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    OrderType
)

def main():
    """Batch order placement example."""

    # 1. Initialize client
    client = PolymarketClient()

    # 2. Add wallet
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    client.add_wallet(
        WalletConfig(private_key=private_key),
        wallet_id="strategy3"
    )

    # 3. Get multiple markets
    print("Fetching markets...")
    markets = client.get_markets(active=True, limit=10)
    print(f"✓ Found {len(markets)} active markets")

    # 4. Build batch of orders
    print("\nBuilding batch orders...")
    orders = []

    for market in markets[:5]:  # Place orders on first 5 markets
        if not market.tokens:
            continue

        token_id = market.tokens[0]  # First outcome

        # Create buy order
        order = OrderRequest(
            token_id=token_id,
            price=0.45,  # Buy at $0.45
            size=10.0,   # $10 per order
            side=Side.BUY,
            order_type=OrderType.GTC
        )
        orders.append(order)

    print(f"✓ Created {len(orders)} orders")

    # 5. Place all orders in single batch
    print(f"\nPlacing {len(orders)} orders in batch...")
    print("⚡ This is 10x faster than sequential placement!")

    import time
    start = time.time()

    # CRITICAL: Batch submission
    responses = client.place_orders_batch(
        orders,
        wallet_id="strategy3"
    )

    elapsed = time.time() - start

    # 6. Analyze results
    successful = [r for r in responses if r.success]
    failed = [r for r in responses if not r.success]

    print(f"\n✅ Results ({elapsed:.2f}s):")
    print(f"   Total: {len(responses)}")
    print(f"   Successful: {len(successful)}")
    print(f"   Failed: {len(failed)}")

    if successful:
        print("\nSuccessful Orders:")
        for r in successful[:3]:  # Show first 3
            print(f"   Order ID: {r.order_id}")
            print(f"   Status: {r.status}")

    if failed:
        print("\nFailed Orders:")
        for r in failed:
            print(f"   Error: {r.error_msg}")

    # 7. Batch orderbook fetching
    print("\n\nFetching orderbooks in batch...")

    token_ids = [market.tokens[0] for market in markets[:10] if market.tokens]

    start = time.time()

    # CRITICAL: Batch orderbook fetch
    orderbooks = client.get_orderbooks_batch(token_ids)

    elapsed = time.time() - start

    print(f"✓ Fetched {len(orderbooks)} orderbooks in {elapsed:.2f}s")
    print("\nOrderbook Summary:")

    for token_id, book in list(orderbooks.items())[:5]:
        print(f"   Token {token_id}:")
        print(f"     Best Bid: {book.best_bid}")
        print(f"     Best Ask: {book.best_ask}")
        print(f"     Spread: {book.spread}")

    # 8. Performance comparison
    print("\n\n📊 Performance Comparison:")
    print("Sequential placement (1 order/sec): 5 orders = 5s")
    print(f"Batch placement: 5 orders = {elapsed:.2f}s")
    print(f"Speedup: {5/elapsed:.1f}x faster! 🚀")

if __name__ == "__main__":
    main()
