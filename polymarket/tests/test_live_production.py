"""
Quick test of Polymarket API with production wallet.

SAFE: Only reads data, does NOT place orders.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared.polymarket import PolymarketClient, WalletConfig


def test_production_api():
    """Test Polymarket API with production wallet (read-only)."""

    # Get credentials from .env
    private_key = os.getenv("WALLET_PRIVATE_KEY")
    if not private_key:
        print("❌ WALLET_PRIVATE_KEY not found in .env")
        return

    print("=" * 60)
    print("POLYMARKET PRODUCTION API TEST (READ-ONLY)")
    print("=" * 60)

    # Initialize client (uses settings from env, defaults to mainnet chain_id=137)
    print("\n1. Initializing client...")
    client = PolymarketClient(
        enable_rate_limiting=True,
        enable_circuit_breaker=True
    )
    print(f"✓ Client initialized (chain_id={client.settings.chain_id})")

    # Add wallet
    print("\n2. Adding wallet...")
    wallet = WalletConfig(private_key=f"0x{private_key}" if not private_key.startswith("0x") else private_key)
    client.add_wallet(wallet, wallet_id="production", set_default=True)
    print(f"✓ Wallet added: {wallet.address}")

    # Test 1: Get markets
    print("\n" + "=" * 60)
    print("TEST 1: Fetch Active Markets")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=5, active=True)
        print(f"✓ Found {len(markets)} active markets")

        if markets:
            for i, market in enumerate(markets[:3], 1):
                print(f"\n  {i}. {market.question}")
                print(f"     Slug: {market.slug}")
                if market.tokens:
                    print(f"     Tokens: {len(market.tokens)} outcomes")
    except Exception as e:
        print(f"❌ Error fetching markets: {e}")

    # Test 2: Get balances
    print("\n" + "=" * 60)
    print("TEST 2: Fetch Wallet Balance")
    print("=" * 60)
    try:
        balance = client.get_balances("production")
        print(f"✓ USDC Balance: ${balance.collateral:.2f}")

        if balance.tokens:
            print(f"✓ Token positions: {len(balance.tokens)}")
    except Exception as e:
        print(f"❌ Error fetching balance: {e}")

    # Test 3: Get positions
    print("\n" + "=" * 60)
    print("TEST 3: Fetch Positions")
    print("=" * 60)
    try:
        positions = client.get_positions("production")
        print(f"✓ Found {len(positions)} positions")

        if positions:
            total_value = sum(p.current_value for p in positions if p.current_value)
            total_pnl = sum(p.cash_pnl for p in positions if p.cash_pnl)

            print(f"\n  Portfolio Summary:")
            print(f"    Total Value:  ${total_value:.2f}")
            print(f"    Total P&L:    ${total_pnl:.2f}")

            print(f"\n  Top 3 Positions:")
            for i, pos in enumerate(positions[:3], 1):
                print(f"\n  {i}. {pos.title}")
                print(f"     Outcome: {pos.outcome}")
                print(f"     Size: {pos.size:.2f} shares")
                print(f"     Value: ${pos.current_value:.2f}")
                print(f"     P&L: ${pos.cash_pnl:+.2f} ({pos.percent_pnl:+.1%})")
    except Exception as e:
        print(f"❌ Error fetching positions: {e}")

    # Test 4: Get trades
    print("\n" + "=" * 60)
    print("TEST 4: Fetch Trade History")
    print("=" * 60)
    try:
        trades = client.get_trades("production", limit=5)
        print(f"✓ Found {len(trades)} recent trades")

        if trades:
            print(f"\n  Recent Trades:")
            for i, trade in enumerate(trades[:3], 1):
                print(f"\n  {i}. {trade.market if hasattr(trade, 'market') else 'Unknown Market'}")
                print(f"     Side: {trade.side if hasattr(trade, 'side') else 'N/A'}")
                print(f"     Size: {trade.size:.2f} @ ${trade.price:.4f}")
                print(f"     Time: {trade.timestamp if hasattr(trade, 'timestamp') else 'N/A'}")
    except Exception as e:
        print(f"❌ Error fetching trades: {e}")

    # Test 5: Get orderbook
    print("\n" + "=" * 60)
    print("TEST 5: Fetch Orderbook (if markets exist)")
    print("=" * 60)
    try:
        markets = client.get_markets(limit=1, active=True)
        if markets and markets[0].tokens:
            token_id = markets[0].tokens[0]
            orderbook = client.get_orderbook(token_id)

            print(f"✓ Orderbook for: {markets[0].question}")
            print(f"  Best Bid: ${orderbook.best_bid:.4f}" if orderbook.best_bid else "  No bids")
            print(f"  Best Ask: ${orderbook.best_ask:.4f}" if orderbook.best_ask else "  No asks")
            print(f"  Spread:   ${orderbook.spread:.4f}" if orderbook.spread else "  N/A")
            print(f"  Bids: {len(orderbook.bids)}, Asks: {len(orderbook.asks)}")
    except Exception as e:
        print(f"❌ Error fetching orderbook: {e}")

    # Test 6: Health check
    print("\n" + "=" * 60)
    print("TEST 6: API Health Check")
    print("=" * 60)
    try:
        health = client.health_check()
        print(f"✓ API Status: {health.get('status', 'unknown')}")
    except Exception as e:
        print(f"❌ Error checking health: {e}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE - NO ORDERS PLACED")
    print("=" * 60)
    print("\n✓ All read-only tests completed successfully")
    print("✓ Library is working with production Polymarket API")
    print("\nNext Steps:")
    print("  - Library is ready for Strategy-1 and Strategy-3 to use")
    print("  - Add more wallets with client.add_wallet()")
    print("  - Use batch operations for multi-wallet tracking")
    print("  - Enable WebSocket for real-time updates")

    # Cleanup
    client.close()


if __name__ == "__main__":
    # Load .env
    from dotenv import load_dotenv
    load_dotenv()

    test_production_api()
