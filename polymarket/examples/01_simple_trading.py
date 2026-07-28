"""Build and inspect a limit-order request without submitting it.

Set ``POLYMARKET_TOKEN_ID`` to a current CLOB token ID. This example makes
public, read-only requests and never adds a wallet or places an order.
"""

import asyncio
import os
from decimal import Decimal

from polymarket import OrderRequest, OrderType, PolymarketClient, Side


async def main() -> None:
    token_id = os.environ.get("POLYMARKET_TOKEN_ID")
    if not token_id:
        raise RuntimeError("Set POLYMARKET_TOKEN_ID to a current CLOB token ID")

    async with PolymarketClient() as client:
        orderbook = await client.get_orderbook(token_id)
        fee_info = await client.get_fee_info(token_id)

        if orderbook.best_ask is None:
            raise RuntimeError("The token has no best ask to use for this preview")

        order = OrderRequest(
            token_id=token_id,
            price=orderbook.best_ask,
            size=Decimal("10"),
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        collateral = order.price * order.size

        print("Limit-order preview (not submitted)")
        print(f"token: {order.token_id}")
        print(f"price: {order.price}")
        print(f"size: {order.size} tokens")
        print(f"collateral before fees: {collateral}")
        print(
            "fee metadata:",
            {
                "base_fee_bps": fee_info.base_fee_bps,
                "rate_bps": fee_info.rate_bps,
                "exponent": str(fee_info.exponent),
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
