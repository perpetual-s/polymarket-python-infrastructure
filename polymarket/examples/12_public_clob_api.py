"""
Example: Public CLOB API Usage

Demonstrates how to use public (unauthenticated) CLOB endpoints for market data.

Benefits of public endpoints:
- No authentication required (no wallet/API keys needed)
- Doesn't consume trading rate limits
- Faster response (no signature overhead)
- Can be called from anywhere

Use cases:
- Market data dashboards
- Price monitoring
- Liquidity analysis
- Market research
- Strategy backtesting

Rate limits (per official docs):
- General CLOB: 5,000 req/10s (baseline)
- Single endpoints (/spread, /midpoint): 200 req/10s
- Batch endpoints (/spreads, /midpoints): 80 req/10s
- Markets: 250 req/10s (general), 100 req/10s (listing)
"""

import asyncio
from shared.polymarket import PolymarketClient
from shared.polymarket.config import PolymarketSettings


def example_basic_pricing():
    """Example 1: Basic pricing queries"""
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Pricing Queries")
    print("="*60)

    # Initialize client (no wallet needed for public endpoints!)
    settings = PolymarketSettings()
    client = PolymarketClient(settings=settings)

    # Example token ID (Trump 2024 YES token)
    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

    # Get midpoint price
    print("\n1. Midpoint price:")
    midpoint = client.get_midpoint(token_id)
    print(f"   Midpoint: ${midpoint:.4f}" if midpoint else "   No data")

    # Get best bid/ask
    print("\n2. Best bid/ask:")
    bid_ask = client.get_best_bid_ask(token_id)
    if bid_ask:
        bid, ask = bid_ask
        print(f"   Best Bid: ${bid:.4f}")
        print(f"   Best Ask: ${ask:.4f}")

    # Get spread
    print("\n3. Bid-ask spread:")
    spread = client.get_spread(token_id)
    if spread:
        print(f"   Spread: ${spread:.4f} ({spread*100:.2f}%)")

    # Get last trade price
    print("\n4. Last trade price:")
    last_price = client.get_last_trade_price(token_id)
    print(f"   Last: ${last_price:.4f}" if last_price else "   No trades yet")


def example_batch_operations():
    """Example 2: Batch operations (more efficient)"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Batch Operations (10x More Efficient)")
    print("="*60)

    client = PolymarketClient()

    # Example: Multiple tokens
    token_ids = [
        "21742633143463906290569050155826241533067272736897614950488156847949938836455",  # Trump YES
        "48331043336612883890938759509493159234755048973500640148014422747788308965732",   # Biden YES
        "71321045679252212594626385532706912750332728571942532289631379312455583992563"    # Another market
    ]

    print("\n1. Batch midpoints (single API call for 3 tokens):")
    midpoints = client.get_midpoints(token_ids)
    for token_id, price in midpoints.items():
        short_id = token_id[:8] + "..."
        print(f"   {short_id}: ${price:.4f}" if price else f"   {short_id}: No data")

    print("\n2. Batch spreads:")
    spreads = client.get_spreads(token_ids)
    for token_id, spread in spreads.items():
        short_id = token_id[:8] + "..."
        print(f"   {short_id}: ${spread:.4f}" if spread else f"   {short_id}: No data")

    print("\n3. Batch prices (with sides):")
    # Query BUY and SELL prices for multiple tokens
    params = []
    for token_id in token_ids[:2]:  # Just first 2 for brevity
        params.append({"token_id": token_id, "side": "BUY"})
        params.append({"token_id": token_id, "side": "SELL"})

    prices = client.get_prices(params)
    for key, price in prices.items():
        print(f"   {key[:20]}...: ${price:.4f}" if price else f"   {key[:20]}...: No data")


def example_liquidity_analysis():
    """Example 3: Liquidity depth analysis"""
    print("\n" + "="*60)
    print("EXAMPLE 3: Liquidity Depth Analysis")
    print("="*60)

    client = PolymarketClient()

    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

    print("\n1. Liquidity depth within 5% of best price:")
    depth = client.get_liquidity_depth(token_id, price_range=0.05)

    print(f"   Bid depth: ${depth['bid_depth']:,.2f} across {depth['bid_levels']} levels")
    print(f"   Ask depth: ${depth['ask_depth']:,.2f} across {depth['ask_levels']} levels")
    print(f"   Total liquidity: ${depth['total_depth']:,.2f}")

    print("\n2. Tight liquidity (within 1%):")
    tight_depth = client.get_liquidity_depth(token_id, price_range=0.01)
    print(f"   Bid depth: ${tight_depth['bid_depth']:,.2f}")
    print(f"   Ask depth: ${tight_depth['ask_depth']:,.2f}")

    print("\n3. Wide liquidity (within 10%):")
    wide_depth = client.get_liquidity_depth(token_id, price_range=0.10)
    print(f"   Total liquidity: ${wide_depth['total_depth']:,.2f}")


def example_market_discovery():
    """Example 4: Market discovery and listing"""
    print("\n" + "="*60)
    print("EXAMPLE 4: Market Discovery")
    print("="*60)

    client = PolymarketClient()

    print("\n1. Simplified markets (lightweight, fast):")
    simplified = client.get_simplified_markets(next_cursor="MA==")
    print(f"   Retrieved {len(simplified.get('data', []))} markets")
    print(f"   Next cursor: {simplified.get('next_cursor', 'N/A')[:20]}...")

    if simplified.get('data'):
        first_market = simplified['data'][0]
        print(f"   Example market: {first_market.get('question', 'N/A')[:50]}...")

    print("\n2. Full markets (complete data, slower):")
    full_markets = client.get_markets_full(next_cursor="MA==")
    print(f"   Retrieved {len(full_markets.get('data', []))} markets with full data")


def example_market_details():
    """Example 5: Individual market details"""
    print("\n" + "="*60)
    print("EXAMPLE 5: Market Details by Condition ID")
    print("="*60)

    client = PolymarketClient()

    # Example condition ID (would need actual ID from market data)
    # This is just for demonstration
    print("\n1. Get market by condition ID:")
    print("   (Would use: client.get_market_by_condition(condition_id))")
    print("   Returns: Full market details including outcomes, volume, etc.")

    print("\n2. Get market trade events:")
    print("   (Would use: client.get_market_trades_events(condition_id))")
    print("   Returns: List of recent trades for the market")


def example_orderbook_analysis():
    """Example 6: Orderbook analysis"""
    print("\n" + "="*60)
    print("EXAMPLE 6: Orderbook Analysis")
    print("="*60)

    client = PolymarketClient()

    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

    print("\n1. Full orderbook:")
    orderbook = client.get_orderbook(token_id)
    print(f"   Market: {orderbook.market[:20]}...")
    print(f"   Bids: {len(orderbook.bids)} levels")
    print(f"   Asks: {len(orderbook.asks)} levels")

    if orderbook.bids and orderbook.asks:
        print(f"\n   Top 3 Bids:")
        for i, bid in enumerate(orderbook.bids[:3]):
            print(f"     {i+1}. ${float(bid['price']):.4f} x {float(bid['size']):,.0f}")

        print(f"\n   Top 3 Asks:")
        for i, ask in enumerate(orderbook.asks[:3]):
            print(f"     {i+1}. ${float(ask['price']):.4f} x {float(ask['size']):,.0f}")

    print("\n2. Batch orderbooks:")
    token_ids = [token_id]  # Could add more
    orderbooks = client.get_orderbooks_batch(token_ids)
    print(f"   Retrieved {len(orderbooks)} orderbooks in one call")


def example_health_and_metadata():
    """Example 7: Health checks and metadata"""
    print("\n" + "="*60)
    print("EXAMPLE 7: Health Checks & Metadata")
    print("="*60)

    client = PolymarketClient()

    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

    print("\n1. Server health:")
    is_healthy = client.get_ok()
    print(f"   CLOB server: {'✓ Operational' if is_healthy else '✗ Down'}")

    print("\n2. Server time:")
    server_time = client.get_server_time()
    print(f"   Server timestamp: {server_time}")

    print("\n3. Token metadata:")
    tick_size = client.clob.get_tick_size(token_id)
    neg_risk = client.clob.get_neg_risk(token_id)
    fee_rate = client.clob.get_fee_rate_bps(token_id)

    print(f"   Tick size: ${float(tick_size):.2f}")
    print(f"   Neg-risk: {neg_risk}")
    print(f"   Fee rate: {fee_rate} bps (Polymarket has 0 fees)")


def example_price_monitoring():
    """Example 8: Real-time price monitoring pattern"""
    print("\n" + "="*60)
    print("EXAMPLE 8: Price Monitoring Pattern")
    print("="*60)

    client = PolymarketClient()

    token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

    print("\n   Monitoring price changes (5 iterations, 2s interval):")
    print("   " + "-"*50)

    for i in range(5):
        # Get current price data
        midpoint = client.get_midpoint(token_id)
        spread = client.get_spread(token_id)
        depth = client.get_liquidity_depth(token_id, price_range=0.05)

        print(f"   [{i+1}] Price: ${midpoint:.4f} | Spread: ${spread:.4f} | "
              f"Liquidity: ${depth['total_depth']:,.0f}")

        if i < 4:  # Don't sleep on last iteration
            import time
            time.sleep(2)


def example_comparison_strategy():
    """Example 9: Comparing public vs authenticated endpoints"""
    print("\n" + "="*60)
    print("EXAMPLE 9: Public vs Authenticated Endpoints")
    print("="*60)

    print("\n   Public Endpoints (No Auth Required):")
    print("   ✓ get_midpoint() - Midpoint price")
    print("   ✓ get_spread() - Bid-ask spread")
    print("   ✓ get_orderbook() - Full orderbook")
    print("   ✓ get_best_bid_ask() - Top of book")
    print("   ✓ get_liquidity_depth() - Depth analysis")
    print("   ✓ get_markets*() - Market listing")
    print("   ✓ get_last_trade_price() - Recent trades")
    print("   → Rate limit: 200 req/10s (single), 80 req/10s (batch)")
    print("   → Doesn't consume trading rate limits!")

    print("\n   Authenticated Endpoints (Requires Wallet):")
    print("   ✓ place_order() - Submit orders")
    print("   ✓ cancel_order() - Cancel orders")
    print("   ✓ get_orders() - Query your orders")
    print("   ✓ get_balances() - Check balances")
    print("   → Rate limit: 2,400 req/10s (trading)")
    print("   → Consumes your wallet's rate limit quota")

    print("\n   Strategy Recommendation:")
    print("   → Use PUBLIC endpoints for market data")
    print("   → Use AUTHENTICATED endpoints only for trading")
    print("   → This maximizes your trading throughput!")


def main():
    """Run all examples"""
    print("\n" + "="*60)
    print("PUBLIC CLOB API - COMPREHENSIVE EXAMPLES")
    print("="*60)
    print("\nThese examples demonstrate Polymarket's public (unauthenticated)")
    print("CLOB endpoints. No wallet or API keys required!")

    try:
        # Basic examples
        example_basic_pricing()
        example_batch_operations()
        example_liquidity_analysis()

        # Market discovery
        example_market_discovery()
        example_market_details()

        # Advanced analysis
        example_orderbook_analysis()
        example_health_and_metadata()

        # Practical patterns
        example_price_monitoring()
        example_comparison_strategy()

        print("\n" + "="*60)
        print("✓ All examples completed successfully!")
        print("="*60)
        print("\nNext steps:")
        print("1. Try these methods with your own token IDs")
        print("2. Build a price monitoring dashboard")
        print("3. Analyze liquidity across multiple markets")
        print("4. Use batch operations for efficiency")
        print("\nSee shared/polymarket/API_REFERENCE.md for complete docs.")

    except Exception as e:
        print(f"\n✗ Error running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
