#!/usr/bin/env python3
"""
Real money test: Fed December 2025 rate decision market.

Tests:
1. Wallet balance check
2. Orderbook fetch
3. Small test trade ($1-2)

Market: Fed decreases interest rates by 25 bps after December 2025 meeting?
"""

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dotenv import load_dotenv
from loguru import logger

# Load environment
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

from polymarket import PolymarketClient, WalletConfig
from polymarket.models import SignatureType, OrderRequest

# Market details
MARKET_CONDITION_ID = "0xcb111226a8271fed0c71bb5ec1bd67b2a4fd72f1eb08466e2180b9efa99d3f32"
YES_TOKEN_ID = "87769991026114894163580777793845523168226980076553814689875238288185044414090"
NO_TOKEN_ID = "13411284055273560855537595688801764123705139415061660246624128667183605973730"


async def main():
    """Run real money test."""
    logger.info("=" * 60)
    logger.info("REAL MONEY TEST: Fed December 2025 Rate Decision")
    logger.info("=" * 60)

    # Get wallet config from env
    private_key = os.getenv("WALLET_1_PRIVATE_KEY")
    eoa_address = os.getenv("WALLET_1_ADDRESS")
    proxy_address = os.getenv("WALLET_1_PROXY_ADDRESS")

    if not private_key:
        logger.error("WALLET_1_PRIVATE_KEY not found in .env")
        return

    logger.info(f"EOA Address: {eoa_address}")
    logger.info(f"Proxy Address: {proxy_address}")

    # Initialize client
    client = PolymarketClient()

    # Add wallet with proxy configuration
    wallet_config = WalletConfig(
        private_key=private_key,
        address=proxy_address if proxy_address else eoa_address,
        signature_type=SignatureType.PROXY if proxy_address else SignatureType.EOA
    )
    await client.add_wallet(wallet_config, wallet_id="WALLET_1")

    try:
        # Step 1: Check balance
        logger.info("\n--- Step 1: Check Balance ---")
        balance = await client.get_balances(wallet_id="WALLET_1")
        logger.info(f"USDC Balance: ${balance.collateral:.2f}")

        if balance.collateral < Decimal("0.30"):
            logger.error("Insufficient balance for test trade (need at least $0.30)")
            return

        # Step 2: Get orderbook
        logger.info("\n--- Step 2: Get Orderbook ---")
        orderbook = await client.get_orderbook(YES_TOKEN_ID)

        if orderbook.best_bid:
            logger.info(f"Best Bid: ${orderbook.best_bid}")
        if orderbook.best_ask:
            logger.info(f"Best Ask: ${orderbook.best_ask}")
        if orderbook.best_bid and orderbook.best_ask:
            spread = orderbook.best_ask - orderbook.best_bid
            logger.info(f"Spread: ${spread:.4f} ({spread * 100:.2f} cents)")

        # Step 3: Get midpoint
        logger.info("\n--- Step 3: Get Midpoint ---")
        midpoint = await client.get_midpoint(YES_TOKEN_ID)
        logger.info(f"Midpoint: ${midpoint}")

        # Step 4: Place small test order (BUY YES at slightly below best ask)
        logger.info("\n--- Step 4: Place Test Order ---")

        # Calculate order price (1 tick below best ask for safety)
        tick_size = Decimal("0.01")  # Most markets use 0.01

        if not orderbook.best_ask:
            logger.error("No asks available, cannot place order")
            return

        # Place non-marketable limit order at best bid (to avoid $1 min for marketable orders)
        # Marketable = crosses spread (BUY at >= best_ask, SELL at <= best_bid)
        # Non-marketable = doesn't cross spread (BUY at < best_ask)
        order_price = orderbook.best_bid  # Place at best bid (non-marketable)
        if not order_price:
            logger.error("No bids available, cannot place order")
            return
        order_price = order_price.quantize(tick_size)

        # Calculate size for test order (minimum 0.5 tokens)
        # At current prices (~$0.90), 0.5 tokens costs ~$0.45
        min_size = Decimal("0.5")
        order_cost = min_size * order_price

        if order_cost > balance.collateral:
            logger.error(f"Insufficient balance: need ${order_cost:.2f} for minimum order, have ${balance.collateral:.2f}")
            return

        order_size = min_size

        logger.info(f"Order: BUY {order_size} YES @ ${order_price}")
        logger.info(f"Total cost: ~${order_size * order_price:.2f}")

        # Create order request
        order = OrderRequest(
            token_id=YES_TOKEN_ID,
            price=order_price,
            size=order_size,
            side="BUY"
        )

        # Place order
        logger.info("\nPlacing order...")
        response = await client.place_order(order, wallet_id="WALLET_1")

        if response.success:
            logger.success("✅ Order placed successfully!")
            logger.info(f"Order ID: {response.order_id}")
            logger.info(f"Status: {response.status}")
        else:
            logger.error(f"❌ Order failed: {response.error_msg}")

        # Step 5: Check open orders
        logger.info("\n--- Step 5: Check Open Orders ---")
        orders = await client.get_orders(wallet_id="WALLET_1")
        logger.info(f"Total open orders: {len(orders)}")
        for o in orders[:5]:
            logger.info(f"  - {o.id}: {o.side} {o.size} @ {o.price} ({o.status})")

        # Step 6: Cancel the test order (optional - leave it to potentially fill)
        if response.success and response.order_id:
            logger.info("\n--- Step 6: Cancel Test Order ---")
            cancel_result = await client.cancel_order(response.order_id, wallet_id="WALLET_1")
            if cancel_result:
                logger.success("✅ Order cancelled")
            else:
                logger.warning("Order may have already filled")

        logger.info("\n" + "=" * 60)
        logger.info("TEST COMPLETE")
        logger.info("=" * 60)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
