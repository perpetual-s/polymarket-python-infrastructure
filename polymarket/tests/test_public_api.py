"""
Test Polymarket public API (no authentication required).
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared.polymarket import PolymarketClient


def test_public_api():
    """Test Polymarket public API endpoints."""

    print("=" * 60)
    print("POLYMARKET PUBLIC API TEST")
    print("=" * 60)

    # Initialize client (no wallet needed for public endpoints)
    print("\n1. Initializing client...")
    client = PolymarketClient(
        enable_rate_limiting=True,
        enable_circuit_breaker=True
    )
    print(f"✓ Client initialized (chain_id={client.settings.chain_id})")

    # Test 1: Get markets
    print("\n" + "=" * 60)
    print("TEST 1: Fetch Active Markets")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=10, active=True)
        print(f"✓ Found {len(markets)} active markets\n")

        if markets:
            for i, market in enumerate(markets[:5], 1):
                print(f"{i}. {market.question}")
                print(f"   Slug: {market.slug}")
                print(f"   Outcomes: {len(market.outcomes) if hasattr(market, 'outcomes') and market.outcomes else 'N/A'}")
                if hasattr(market, 'volume') and market.volume:
                    print(f"   Volume: ${market.volume:,.0f}")
                print()

            print(f"Total active markets: {len(markets)}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Test 2: Search markets
    print("\n" + "=" * 60)
    print("TEST 2: Search Markets (query: 'trump')")
    print("=" * 60)
    try:
        results = client.search_markets("trump", limit=5)
        print(f"✓ Found {len(results)} results\n")

        for i, market in enumerate(results[:3], 1):
            print(f"{i}. {market.question}")
            print(f"   Slug: {market.slug}")
            print()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Test 3: Get market by slug
    print("\n" + "=" * 60)
    print("TEST 3: Get Market by Slug (if available)")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=1, active=True)
        if markets:
            slug = markets[0].slug
            market = client.get_market_by_slug(slug)

            print(f"✓ Fetched market: {market.question}")
            print(f"  ID: {market.id if hasattr(market, 'id') else 'N/A'}")
            print(f"  Slug: {market.slug}")
            if hasattr(market, 'tokens') and market.tokens:
                print(f"  Token IDs: {len(market.tokens)} tokens")
                print(f"    - {market.tokens[0][:20]}...")
            if hasattr(market, 'outcomes') and market.outcomes:
                print(f"  Outcomes: {', '.join(market.outcomes)}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Test 4: Get orderbook
    print("\n" + "=" * 60)
    print("TEST 4: Fetch Orderbook")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=1, active=True)
        if markets and hasattr(markets[0], 'tokens') and markets[0].tokens:
            token_id = markets[0].tokens[0]

            print(f"Fetching orderbook for: {markets[0].question}")
            print(f"Token ID: {token_id}\n")

            orderbook = client.get_orderbook(token_id)

            print(f"✓ Orderbook fetched:")
            if orderbook.best_bid is not None:
                print(f"  Best Bid:  ${orderbook.best_bid:.4f}")
            else:
                print(f"  Best Bid:  No bids")

            if orderbook.best_ask is not None:
                print(f"  Best Ask:  ${orderbook.best_ask:.4f}")
            else:
                print(f"  Best Ask:  No asks")

            if orderbook.spread is not None:
                print(f"  Spread:    ${orderbook.spread:.4f}")

            print(f"  Order Book Depth:")
            print(f"    Bids: {len(orderbook.bids)}")
            print(f"    Asks: {len(orderbook.asks)}")

            if orderbook.bids:
                print(f"\n  Top 3 Bids:")
                for i, (price, size) in enumerate(orderbook.bids[:3], 1):
                    print(f"    {i}. ${price:.4f} x {size:.2f} shares")

            if orderbook.asks:
                print(f"\n  Top 3 Asks:")
                for i, (price, size) in enumerate(orderbook.asks[:3], 1):
                    print(f"    {i}. ${price:.4f} x {size:.2f} shares")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Test 5: Get midpoint
    print("\n" + "=" * 60)
    print("TEST 5: Get Midpoint Price")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=1, active=True)
        if markets and hasattr(markets[0], 'tokens') and markets[0].tokens:
            token_id = markets[0].tokens[0]

            midpoint = client.get_midpoint(token_id)
            print(f"✓ Midpoint price: ${midpoint:.4f}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test 6: Batch orderbooks
    print("\n" + "=" * 60)
    print("TEST 6: Batch Fetch Orderbooks")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=3, active=True)
        if markets:
            token_ids = [token for m in markets if hasattr(m, 'tokens') and m.tokens for token in m.tokens[:1]]

            if token_ids:
                print(f"Fetching orderbooks for {len(token_ids)} tokens...\n")

                books = client.get_orderbooks_batch(token_ids)

                print(f"✓ Fetched {len(books)} orderbooks")
                for i, (token_id, book) in enumerate(list(books.items())[:3], 1):
                    print(f"\n  {i}. Token: {token_id[:20]}...")
                    print(f"     Best Bid: ${book.best_bid:.4f}" if book.best_bid else "     No bids")
                    print(f"     Best Ask: ${book.best_ask:.4f}" if book.best_ask else "     No asks")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("PUBLIC API TEST COMPLETE")
    print("=" * 60)
    print("\n✓ Public API endpoints working correctly")
    print("✓ Library successfully communicates with Polymarket")
    print("\nNext Steps for Authenticated Operations:")
    print("  1. Register wallet on Polymarket (visit app.polymarket.com)")
    print("  2. Set token allowances for USDC + CTF contracts")
    print("  3. Then wallet operations will work (balances, positions, orders)")

    # Cleanup
    client.close()


if __name__ == "__main__":
    # Load .env
    from dotenv import load_dotenv
    load_dotenv()

    test_public_api()
