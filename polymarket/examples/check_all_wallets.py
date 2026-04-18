#!/usr/bin/env python3
"""Check balances of all configured wallets."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

from polymarket import PolymarketClient, WalletConfig
from polymarket.models import SignatureType


async def main():
    """Check all wallet balances."""
    client = PolymarketClient()

    # Find all wallets in env
    wallet_ids = []
    for key in os.environ:
        if key.startswith("WALLET_") and key.endswith("_PRIVATE_KEY"):
            wallet_id = key.replace("_PRIVATE_KEY", "")
            wallet_ids.append(wallet_id)

    wallet_ids.sort()
    logger.info(f"Found {len(wallet_ids)} wallets")

    results = []

    for wallet_id in wallet_ids:
        private_key = os.getenv(f"{wallet_id}_PRIVATE_KEY")
        eoa_address = os.getenv(f"{wallet_id}_ADDRESS")
        proxy_address = os.getenv(f"{wallet_id}_PROXY_ADDRESS")

        if not private_key:
            continue

        try:
            wallet_config = WalletConfig(
                private_key=private_key,
                address=proxy_address if proxy_address else eoa_address,
                signature_type=SignatureType.PROXY if proxy_address else SignatureType.EOA
            )
            await client.add_wallet(wallet_config, wallet_id=wallet_id)

            balance = await client.get_balances(wallet_id=wallet_id)
            results.append({
                "wallet_id": wallet_id,
                "address": proxy_address or eoa_address,
                "balance": float(balance.collateral)
            })
            logger.info(f"{wallet_id}: ${balance.collateral:.2f} (proxy: {bool(proxy_address)})")

        except Exception as e:
            logger.error(f"{wallet_id}: Error - {e}")
            results.append({
                "wallet_id": wallet_id,
                "address": proxy_address or eoa_address,
                "balance": 0,
                "error": str(e)
            })

    await client.close()

    # Summary
    total = sum(r.get("balance", 0) for r in results)
    logger.info(f"\nTotal across all wallets: ${total:.2f}")

    # Find wallet with highest balance
    if results:
        best = max(results, key=lambda x: x.get("balance", 0))
        logger.info(f"Best wallet: {best['wallet_id']} with ${best['balance']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
