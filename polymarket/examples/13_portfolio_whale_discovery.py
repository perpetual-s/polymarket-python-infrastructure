"""
Example 13: Portfolio Value Breakdown & Whale Discovery

Demonstrates newly discovered API capabilities:
- Portfolio value with detailed breakdown (bets, cash, equity_total)
- Whale discovery using holder filtering
- Activity tracking for position monitoring

Web research findings: https://docs.polymarket.com/
- /value endpoint returns detailed portfolio metrics
- /holders endpoint supports minBalance for whale filtering
- /activity endpoint tracks all onchain operations
"""

import os
import asyncio
from shared.polymarket import PolymarketClient, WalletConfig


async def main():
    """Demonstrate portfolio analytics and whale discovery."""

    # Initialize client (no wallet needed for public endpoints)
    client = PolymarketClient()

    print("=" * 80)
    print("Portfolio Value Breakdown & Whale Discovery Demo")
    print("=" * 80)

    # ========== Portfolio Value Breakdown ==========

    print("\n" + "=" * 80)
    print("1. Portfolio Value with Detailed Breakdown")
    print("=" * 80)

    # Add wallet for portfolio queries
    private_key = os.getenv("WALLET_0_PRIVATE_KEY")
    if private_key and not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    if private_key:
        client.add_wallet(
            wallet=WalletConfig(private_key=private_key),
            wallet_id="demo"
        )

        # Get portfolio value with breakdown
        portfolio = client.get_portfolio_value(wallet_id="demo")

        print(f"\nPortfolio Analysis:")
        print(f"  Total Value:    ${portfolio.equity_total or portfolio.value:.2f}")
        print(f"  Active Bets:    ${portfolio.bets or 0:.2f}")
        print(f"  Available Cash: ${portfolio.cash or 0:.2f}")

        if portfolio.bets and portfolio.equity_total:
            allocation_pct = (portfolio.bets / portfolio.equity_total) * 100
            print(f"  Allocation:     {allocation_pct:.1f}% deployed")
    else:
        print("\nSkipping portfolio demo (WALLET_0_PRIVATE_KEY not set)")

    # ========== Whale Discovery ==========

    print("\n" + "=" * 80)
    print("2. Whale Discovery in Active Markets")
    print("=" * 80)

    # Get a popular market from gamma API
    markets = client.gamma.get_markets(limit=5, active=True)

    if markets:
        market = markets[0]
        print(f"\nAnalyzing market: {market.question}")
        print(f"Condition ID: {market.condition_id}")

        # Find whales with significant positions (>$1000)
        print("\n--- Top Whales (>$1000 positions) ---")
        whales = client.get_market_holders(
            market=market.condition_id,
            limit=10,
            min_balance=1000
        )

        if whales:
            print(f"\nFound {len(whales)} whales:")
            for i, whale in enumerate(whales[:10], 1):
                # Safely access attributes
                pseudonym = getattr(whale, 'pseudonym', 'Unknown')
                amount = getattr(whale, 'amount', 0)
                token_id = getattr(whale, 'token_id', 'Unknown')
                proxy_wallet = getattr(whale, 'proxy_wallet', 'Unknown')

                print(
                    f"  {i:2d}. {pseudonym:20s} "
                    f"${amount:>10,.2f} "
                    f"on token {token_id[:10]}... "
                    f"({proxy_wallet[:8]}...)"
                )
        else:
            print("No whales found with >$1000 positions")

        # Compare with smaller holders
        print("\n--- All Holders (>$10 positions) ---")
        all_holders = client.get_market_holders(
            market=market.condition_id,
            limit=20,
            min_balance=10
        )
        print(f"Total holders with >$10: {len(all_holders)}")

        # Calculate whale concentration
        if whales and all_holders:
            whale_value = sum(getattr(w, 'amount', 0) for w in whales)
            total_value = sum(getattr(h, 'amount', 0) for h in all_holders)
            if total_value > 0:
                concentration = (whale_value / total_value) * 100
                print(f"Whale concentration: {concentration:.1f}% of market")

    # ========== Activity Monitoring ==========

    print("\n" + "=" * 80)
    print("3. Activity Tracking for Position Monitoring")
    print("=" * 80)

    if private_key:
        # Get recent activity for our wallet
        activities = client.get_activity(
            wallet_id="demo",
            limit=10
        )

        if activities:
            print(f"\nRecent Activity ({len(activities)} records):")
            for activity in activities[:5]:
                # Safely access attributes
                activity_type = getattr(activity, 'type', 'UNKNOWN')
                market_title = getattr(activity, 'market_title', 'Unknown Market')
                outcome = getattr(activity, 'outcome', 'Unknown')
                tokens = getattr(activity, 'tokens', 0)
                timestamp = getattr(activity, 'timestamp', 0)

                print(
                    f"  [{activity_type:10s}] "
                    f"{market_title[:40]:40s} "
                    f"| {outcome:3s} "
                    f"| {tokens:>8,.2f} tokens "
                    f"| {timestamp}"
                )
        else:
            print("\nNo recent activity found")

        # Get trade-only activity
        trade_activities = client.get_activity(
            wallet_id="demo",
            activity_type="TRADE",
            limit=5
        )
        print(f"\nRecent Trades: {len(trade_activities)} trades")

    # ========== Use Case: Track Specific Whale ==========

    print("\n" + "=" * 80)
    print("4. Use Case: Track Specific Whale's Activity")
    print("=" * 80)

    if whales:
        # Pick top whale
        top_whale = whales[0]
        whale_address = getattr(top_whale, 'address', None)
        pseudonym = getattr(top_whale, 'pseudonym', 'Unknown')

        if whale_address:
            print(f"\nTracking whale: {pseudonym}")
            print(f"Address: {whale_address}")

            # Get whale's portfolio value
            try:
                whale_portfolio = client.data.get_portfolio_value(user=whale_address)
                print(f"Total portfolio: ${whale_portfolio.equity_total or whale_portfolio.value:.2f}")

                # Get whale's recent activity
                whale_activities = client.data.get_activity(
                    user=whale_address,
                    limit=5
                )
                print(f"Recent activity: {len(whale_activities)} records")

                # This is how Strategy-3 tracks wallets!
                print("\n✅ This demonstrates Strategy-3's wallet tracking capability")
            except Exception as e:
                print(f"Could not fetch whale data: {e}")

    print("\n" + "=" * 80)
    print("Summary: Portfolio Analytics & Whale Discovery")
    print("=" * 80)
    print("""
Key Takeaways:
1. Portfolio Value: Get detailed breakdown (bets, cash, equity_total)
2. Whale Discovery: Filter holders by minimum balance
3. Activity Tracking: Monitor all onchain operations
4. Strategy-3 Ready: Full infrastructure for copy trading

Use Cases:
- Portfolio Management: Track allocation and deployment
- Whale Monitoring: Discover and follow large traders
- Risk Management: Monitor position concentrations
- Copy Trading: Track trader activity in real-time
    """)


if __name__ == "__main__":
    asyncio.run(main())
