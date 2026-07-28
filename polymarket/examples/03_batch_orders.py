"""Build a small batch of orders and submit only with an explicit opt-in.

Preview mode is read-only. A real submission requires both
``POLYMARKET_SUBMIT=1`` and ``POLYMARKET_PRIVATE_KEY``.
"""

import asyncio
import os
from decimal import Decimal

from polymarket import (
    OrderRequest,
    OrderType,
    PolymarketClient,
    Side,
    SignatureType,
    WalletConfig,
)


async def main() -> None:
    async with PolymarketClient() as client:
        markets = await client.get_markets(active=True, closed=False, limit=5)
        orders: list[OrderRequest] = []

        for market in markets:
            if not market.tokens:
                continue
            orders.append(
                OrderRequest(
                    token_id=market.tokens[0],
                    price=Decimal("0.45"),
                    size=Decimal("5"),
                    side=Side.BUY,
                    order_type=OrderType.GTC,
                )
            )

        print(f"Built {len(orders)} order previews")
        for order in orders:
            print(
                {
                    "token_id": order.token_id,
                    "price": str(order.price),
                    "size_tokens": str(order.size),
                    "notional_before_fees": str(order.price * order.size),
                }
            )

        if not orders or os.environ.get("POLYMARKET_SUBMIT") != "1":
            print("Preview only. Set POLYMARKET_SUBMIT=1 to enable submission.")
            return

        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            raise RuntimeError(
                "POLYMARKET_SUBMIT=1 requires POLYMARKET_PRIVATE_KEY"
            )

        wallet_id = await client.add_wallet(
            WalletConfig(
                private_key=private_key,
                signature_type=SignatureType.EOA,
            ),
            wallet_id="batch-example-eoa",
            set_default=True,
        )
        responses = await client.place_orders_batch(orders, wallet_id=wallet_id)
        print(
            [
                {
                    "success": response.success,
                    "order_id": response.order_id,
                    "status": response.status,
                    "error": response.error_msg,
                }
                for response in responses
            ]
        )


if __name__ == "__main__":
    asyncio.run(main())
