"""
Example 2: Multi-Wallet Tracking for Strategy-3

Shows how to track 100+ wallets simultaneously with batch operations.
Copy this pattern for your Strategy-3 dashboard backend.

v2.8 Note: Thread-safe request deduplication (2-10x fewer redundant API calls)
and background worker pattern prevent thread exhaustion under high load.
See Documentation/ROBUSTNESS_AUDIT.md for validation details.
"""

import os
from shared.polymarket import PolymarketClient
from typing import List, Dict

def main():
    """Multi-wallet tracking example."""

    # 1. Initialize client optimized for 100+ wallets
    print("Initializing client for multi-wallet...")
    client = PolymarketClient(
        # CRITICAL settings for Strategy-3
        pool_connections=100,      # Handle 100+ concurrent requests
        pool_maxsize=200,           # Max connections
        batch_max_workers=20        # Parallel batch operations
    )
    print("✓ Client initialized for 100+ wallets")

    # 2. Load wallet addresses (from your database)
    # In production: SELECT address FROM wallets WHERE strategy_id = 3
    wallet_addresses = [
        "0x1234567890abcdef1234567890abcdef12345678",
        "0xabcdef1234567890abcdef1234567890abcdef12",
        "0x7890abcdef1234567890abcdef1234567890abcd",
        # ... load 100+ addresses from database
    ]

    print(f"Loaded {len(wallet_addresses)} wallet addresses")

    # 3. Fetch positions for ALL wallets in parallel
    print(f"\nFetching positions for {len(wallet_addresses)} wallets...")
    print("(This takes ~10s for 100 wallets with batch operations)")

    import time
    start = time.time()

    # CRITICAL: Use batch operation - 10x faster than sequential
    positions_by_wallet = client.get_positions_batch(
        wallet_addresses,
        size_threshold=1.0,  # Only positions > $1
        limit=100            # Max 100 positions per wallet
    )

    elapsed = time.time() - start
    print(f"✓ Fetched positions in {elapsed:.2f}s")

    # 4. Aggregate metrics across all wallets
    print("\n📊 Aggregated Metrics:")

    total_wallets = len(wallet_addresses)
    active_wallets = len([w for w, p in positions_by_wallet.items() if p])
    total_positions = sum(len(p) for p in positions_by_wallet.values())
    total_value = sum(
        sum(pos.current_value for pos in positions)
        for positions in positions_by_wallet.values()
    )
    total_pnl = sum(
        sum(pos.cash_pnl for pos in positions)
        for positions in positions_by_wallet.values()
    )

    print(f"   Total Wallets: {total_wallets}")
    print(f"   Active Wallets: {active_wallets}")
    print(f"   Total Positions: {total_positions}")
    print(f"   Total Value: ${total_value:,.2f}")
    print(f"   Total P&L: ${total_pnl:,.2f}")

    # 5. Find top performers
    print("\n🏆 Top 5 Performing Wallets:")

    wallet_pnls: List[tuple[str, float]] = []
    for wallet, positions in positions_by_wallet.items():
        if positions:
            wallet_pnl = sum(p.cash_pnl for p in positions)
            wallet_pnls.append((wallet, wallet_pnl))

    wallet_pnls.sort(key=lambda x: x[1], reverse=True)

    for idx, (wallet, pnl) in enumerate(wallet_pnls[:5], 1):
        print(f"   {idx}. {wallet[:10]}... : ${pnl:,.2f}")

    # 6. Detect consensus signals
    print("\n🔍 Detecting Consensus Signals...")

    signals = client.detect_signals(
        wallet_addresses,
        min_wallets=5,           # At least 5 wallets in position
        min_agreement=0.7,       # 70% agree on direction
        size_threshold=100.0     # Position size > $100
    )

    if signals:
        print(f"✓ Found {len(signals)} consensus signals:")
        for signal in signals[:3]:  # Show top 3
            print(f"   {signal['market']}: {signal['consensus_side']}")
            print(f"     Wallets: {signal['wallet_count']}")
            print(f"     Agreement: {signal['agreement']:.1%}")
            print(f"     Total Size: ${signal['total_size']:,.2f}")
    else:
        print("  No strong consensus signals detected")

    # 7. Store in YOUR database (PostgreSQL)
    print("\n💾 Storing to database...")
    print("Example PostgreSQL integration:")
    print("""
    import psycopg2

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor()

    # Store positions
    for wallet, positions in positions_by_wallet.items():
        for pos in positions:
            cursor.execute(\"\"\"
                INSERT INTO strategy3_positions
                (wallet, market, size, pnl, timestamp)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (wallet, market) DO UPDATE
                SET size = EXCLUDED.size, pnl = EXCLUDED.pnl
            \"\"\", (wallet, pos.slug, pos.size, pos.cash_pnl))

    conn.commit()
    conn.close()
    """)

if __name__ == "__main__":
    main()
