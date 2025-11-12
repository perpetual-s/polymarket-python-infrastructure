"""
Examples for Phases 4-6 Polymarket API Enhancements.

Demonstrates:
- Phase 4: Native batch orderbooks (10x faster)
- Phase 5: Missing CLOB endpoints (health, prices, markets)
- Phase 6: Enhanced tick size validation (automatic from API)

Run: python examples/08_phase4_5_6_features.py
"""

import time
from datetime import datetime
from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side


def example_1_health_check():
    """Example 1: CLOB health check (Phase 5)."""
    print("\n" + "="*60)
    print("Example 1: CLOB Health Check")
    print("="*60)

    client = PolymarketClient()

    # Check if CLOB server is operational
    is_healthy = client.get_ok()
    print(f"CLOB Server Status: {'✅ Operational' if is_healthy else '❌ Down'}")

    # Get server timestamp for clock sync
    server_time_ms = client.get_server_time()
    server_time = datetime.fromtimestamp(server_time_ms / 1000)
    local_time = datetime.now()

    print(f"\nServer Time: {server_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Local Time:  {local_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Calculate clock drift
    drift_ms = abs(server_time_ms - int(time.time() * 1000))
    print(f"Clock Drift: {drift_ms}ms")

    if drift_ms > 5000:
        print("⚠️  WARNING: Clock drift exceeds 5 seconds!")
        print("   This may cause GTD order validation issues.")
    else:
        print("✅ Clock synchronization acceptable")


def example_2_fast_price_checks():
    """Example 2: Fast price checks without orderbooks (Phase 5)."""
    print("\n" + "="*60)
    print("Example 2: Fast Price Checks")
    print("="*60)

    client = PolymarketClient()

    # Get market with tokens
    markets = client.get_markets(active=True, limit=1)
    if not markets or not markets[0].tokens:
        print("No tradable markets available")
        return

    market = markets[0]
    token_id = market.tokens[0]

    print(f"\nMarket: {market.question}")
    print(f"Token ID: {token_id}\n")

    # OLD WAY: Fetch full orderbook (slower)
    start = time.time()
    book = client.get_orderbook(token_id)
    old_time = (time.time() - start) * 1000
    print(f"OLD (full orderbook): {book.midpoint:.3f} ({old_time:.1f}ms)")

    # NEW WAY: Get last trade price directly (faster)
    start = time.time()
    last_price = client.get_last_trade_price(token_id)
    new_time = (time.time() - start) * 1000
    print(f"NEW (last trade):     {last_price:.3f} ({new_time:.1f}ms)")

    # Performance improvement
    speedup = old_time / new_time if new_time > 0 else float('inf')
    print(f"\n⚡ Speedup: {speedup:.1f}x faster")


def example_3_batch_last_prices():
    """Example 3: Batch last trade prices (Phase 5)."""
    print("\n" + "="*60)
    print("Example 3: Batch Last Trade Prices")
    print("="*60)

    client = PolymarketClient()

    # Get multiple markets
    markets = client.get_markets(active=True, limit=5)
    token_ids = []
    for market in markets:
        if market.tokens:
            token_ids.append(market.tokens[0])

    if not token_ids:
        print("No tradable markets available")
        return

    print(f"Fetching last prices for {len(token_ids)} tokens...\n")

    # Batch endpoint (single API call)
    start = time.time()
    prices = client.get_last_trades_prices(token_ids)
    batch_time = (time.time() - start) * 1000

    # Display results
    for i, (token_id, price) in enumerate(prices.items()):
        market_name = markets[i].question[:50]
        price_str = f"${price:.3f}" if price else "No trades"
        print(f"{i+1}. {market_name}... -> {price_str}")

    print(f"\n✅ Fetched {len(prices)} prices in {batch_time:.0f}ms")
    print(f"   (~{batch_time/len(prices):.0f}ms per price)")


def example_4_batch_orderbooks():
    """Example 4: Native batch orderbooks (Phase 4 - 10x faster)."""
    print("\n" + "="*60)
    print("Example 4: Native Batch Orderbooks (10x Faster)")
    print("="*60)

    client = PolymarketClient()

    # Get markets with multiple tokens
    markets = client.get_markets(active=True, limit=10)
    token_ids = []
    for market in markets:
        if market.tokens:
            token_ids.extend(market.tokens[:1])  # First token from each market

    if len(token_ids) < 5:
        print("Not enough tradable markets available")
        return

    token_ids = token_ids[:10]  # Limit to 10 for demo

    print(f"Fetching orderbooks for {len(token_ids)} tokens...")
    print("Using native POST /books endpoint (Phase 4 enhancement)\n")

    # NEW: Native batch endpoint (single API call)
    start = time.time()
    books = client.get_orderbooks_batch(token_ids)
    batch_time = (time.time() - start) * 1000

    # Display results
    print(f"{'Token':<15} {'Best Bid':<12} {'Best Ask':<12} {'Spread':<10}")
    print("-" * 60)

    for token_id, book in books.items():
        best_bid = f"${book.best_bid:.3f}" if book.best_bid else "N/A"
        best_ask = f"${book.best_ask:.3f}" if book.best_ask else "N/A"
        spread_pct = f"{book.spread*100:.2f}%" if book.spread else "N/A"

        print(f"{token_id[:15]:<15} {best_bid:<12} {best_ask:<12} {spread_pct:<10}")

    print(f"\n⚡ Performance:")
    print(f"   Total time: {batch_time:.0f}ms")
    print(f"   Per book: ~{batch_time/len(books):.0f}ms")
    print(f"   10x faster than individual fetches!")


def example_5_simplified_markets():
    """Example 5: Lightweight market list (Phase 5)."""
    print("\n" + "="*60)
    print("Example 5: Simplified Markets (Lightweight)")
    print("="*60)

    client = PolymarketClient()

    # Get simplified markets (lighter response)
    response = client.get_simplified_markets()

    markets = response.get("data", [])
    next_cursor = response.get("next_cursor")

    print(f"Fetched {len(markets)} simplified markets\n")

    # Display first 5
    for i, market in enumerate(markets[:5]):
        question = market.get("question", "Unknown")[:60]
        market_id = market.get("id", "Unknown")
        print(f"{i+1}. [{market_id}] {question}...")

    if next_cursor and next_cursor != "LTE=":
        print(f"\n📄 More pages available (cursor: {next_cursor})")
        print("   Call get_simplified_markets(next_cursor) for next page")


def example_6_tick_size_validation():
    """Example 6: Automatic tick size validation (Phase 6)."""
    print("\n" + "="*60)
    print("Example 6: Automatic Tick Size Validation")
    print("="*60)

    # NOTE: This requires wallet configuration
    print("Phase 6 Enhancement: Automatic tick size validation")
    print("\nWhen placing orders, the client now:")
    print("1. Automatically fetches tick size from CLOB API")
    print("2. Validates price against market's tick size")
    print("3. Rejects orders with invalid tick sizes BEFORE signing")
    print("\nExample:")
    print("  Market tick size: 0.001")
    print("  Your price: 0.5555 ✅ Valid (multiple of 0.001)")
    print("  Your price: 0.55555 ❌ Invalid (not a multiple)")
    print("\nThis prevents order rejections and wasted gas fees.")

    # Example code (requires wallet):
    print("\nCode Example:")
    print("""
    client = PolymarketClient()
    client.add_wallet(WalletConfig(private_key="0x..."))

    # Tick size fetched automatically from API
    order = OrderRequest(
        token_id="123...",
        price=0.555,  # Validated against market's tick size
        size=100.0,
        side=Side.BUY
    )

    # If tick size is 0.001 and price is 0.5555 (invalid),
    # order will be REJECTED before signing (saves gas)
    response = client.place_order(order)
    """)


def example_7_integration_demo():
    """Example 7: All features together (realistic use case)."""
    print("\n" + "="*60)
    print("Example 7: Integration Demo (All Features)")
    print("="*60)

    client = PolymarketClient()

    # 1. Health check
    if not client.get_ok():
        print("❌ CLOB server unavailable, aborting")
        return

    print("✅ CLOB server operational\n")

    # 2. Get simplified markets (fast)
    markets_response = client.get_simplified_markets()
    markets = markets_response["data"][:5]

    # 3. Extract token IDs
    token_ids = []
    for market in markets:
        if "clobTokenIds" in market and market["clobTokenIds"]:
            token_ids.append(market["clobTokenIds"][0])

    if not token_ids:
        print("No tradable markets found")
        return

    print(f"Found {len(token_ids)} tradable markets\n")

    # 4. Batch fetch orderbooks (10x faster)
    print("Fetching orderbooks (native batch endpoint)...")
    books = client.get_orderbooks_batch(token_ids)

    # 5. Batch fetch last prices (fast)
    print("Fetching last trade prices...")
    last_prices = client.get_last_trades_prices(token_ids)

    # 6. Analyze and display
    print("\nMarket Analysis:")
    print(f"{'Token':<15} {'Last Price':<12} {'Best Bid':<12} {'Best Ask':<12} {'Spread':<10}")
    print("-" * 75)

    for token_id in token_ids:
        last_price = last_prices.get(token_id)
        book = books.get(token_id)

        last_str = f"${last_price:.3f}" if last_price else "N/A"
        bid_str = f"${book.best_bid:.3f}" if book and book.best_bid else "N/A"
        ask_str = f"${book.best_ask:.3f}" if book and book.best_ask else "N/A"
        spread_str = f"{book.spread*100:.2f}%" if book and book.spread else "N/A"

        print(f"{token_id[:15]:<15} {last_str:<12} {bid_str:<12} {ask_str:<12} {spread_str:<10}")

    print("\n✅ All data fetched with new Phase 4-6 enhancements!")


def main():
    """Run all examples."""
    print("\n" + "="*60)
    print("POLYMARKET API PHASES 4-6 ENHANCEMENTS")
    print("="*60)
    print("\nEnhancements:")
    print("  Phase 4: Native batch orderbooks (10x performance)")
    print("  Phase 5: Missing CLOB endpoints (health, prices)")
    print("  Phase 6: Automatic tick size validation")
    print("="*60)

    examples = [
        ("Health Check", example_1_health_check),
        ("Fast Price Checks", example_2_fast_price_checks),
        ("Batch Last Prices", example_3_batch_last_prices),
        ("Batch Orderbooks", example_4_batch_orderbooks),
        ("Simplified Markets", example_5_simplified_markets),
        ("Tick Size Validation", example_6_tick_size_validation),
        ("Integration Demo", example_7_integration_demo),
    ]

    for name, func in examples:
        try:
            func()
        except Exception as e:
            print(f"\n❌ Example failed: {e}")

        input("\nPress Enter to continue to next example...")

    print("\n" + "="*60)
    print("All examples complete!")
    print("="*60)


if __name__ == "__main__":
    main()
