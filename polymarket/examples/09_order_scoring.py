"""Query the current scoring status of one or more existing orders.

Set ``POLYMARKET_ORDER_IDS`` to a comma-separated list of exchange order IDs.
Scoring status is exchange metadata for the current rewards program; it is not
proof of a fixed rebate, future reward, fill, or profitability.
"""

import asyncio
import os

from polymarket import PolymarketClient


async def main() -> None:
    order_ids = [
        value.strip()
        for value in os.environ.get("POLYMARKET_ORDER_IDS", "").split(",")
        if value.strip()
    ]
    if not order_ids:
        raise RuntimeError("Set POLYMARKET_ORDER_IDS to one or more order IDs")

    async with PolymarketClient() as client:
        if len(order_ids) == 1:
            result = {order_ids[0]: await client.is_order_scoring(order_ids[0])}
        else:
            result = await client.are_orders_scoring(order_ids)

    for order_id, scoring in result.items():
        print({"order_id": order_id, "scoring": scoring})


if __name__ == "__main__":
    asyncio.run(main())
