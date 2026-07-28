#!/usr/bin/env python3
"""Read balances for wallets explicitly configured in the environment.

For each ``WALLET_<NAME>_PRIVATE_KEY``, optional companion values are:

- ``WALLET_<NAME>_SIGNATURE_TYPE``: integer 0-3, default 0 (EOA)
- ``WALLET_<NAME>_FUNDER_ADDRESS``: required by proxy/Safe/deposit wallets

The script reads account state but does not submit or cancel orders.
"""

import asyncio
import os

from polymarket import PolymarketClient, SignatureType, WalletConfig


async def main() -> None:
    prefixes = sorted(
        key.removesuffix("_PRIVATE_KEY")
        for key in os.environ
        if key.startswith("WALLET_") and key.endswith("_PRIVATE_KEY")
    )
    if not prefixes:
        raise RuntimeError("No WALLET_<NAME>_PRIVATE_KEY variables were found")

    async with PolymarketClient() as client:
        for prefix in prefixes:
            signature_type = SignatureType(
                int(os.environ.get(f"{prefix}_SIGNATURE_TYPE", "0"))
            )
            funder = os.environ.get(f"{prefix}_FUNDER_ADDRESS")
            wallet_id = await client.add_wallet(
                WalletConfig(
                    private_key=os.environ[f"{prefix}_PRIVATE_KEY"],
                    address=funder,
                    signature_type=signature_type,
                ),
                wallet_id=prefix.lower(),
            )
            balance = await client.get_balances(wallet_id=wallet_id)
            print(
                {
                    "wallet_id": wallet_id,
                    "signature_type": signature_type.value,
                    "funder": funder,
                    "collateral": str(balance.collateral),
                }
            )


if __name__ == "__main__":
    asyncio.run(main())
