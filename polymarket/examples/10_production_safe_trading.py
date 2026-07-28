"""Preview an order and optionally submit it through an EOA wallet.

Preview mode is the default and needs only ``POLYMARKET_TOKEN_ID``. A real
submission additionally requires ``POLYMARKET_PRIVATE_KEY`` and the exact
opt-in ``POLYMARKET_SUBMIT=1``.

This is infrastructure usage, not a trading recommendation. Proxy, Safe, and
POLY_1271 wallets need their matching signature type and funder configuration;
see ``polymarket/QUICKSTART.md`` before adapting this EOA example.
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
    calculate_net_cost,
)


async def main() -> None:
    token_id = os.environ.get("POLYMARKET_TOKEN_ID")
    if not token_id:
        raise RuntimeError("Set POLYMARKET_TOKEN_ID to the exact token to inspect")

    token_size = Decimal(os.environ.get("POLYMARKET_ORDER_SIZE", "5"))

    async with PolymarketClient() as client:
        orderbook = await client.get_orderbook(token_id)
        fee_info = await client.get_fee_info(token_id)
        configured_price = os.environ.get("POLYMARKET_ORDER_PRICE")
        price = (
            Decimal(configured_price)
            if configured_price is not None
            else orderbook.best_ask
        )
        if price is None:
            raise RuntimeError("The token has no best ask; set POLYMARKET_ORDER_PRICE")

        order = OrderRequest(
            token_id=token_id,
            price=price,
            size=token_size,
            side=Side.BUY,
            order_type=OrderType.GTC,
        )
        notional = order.price * order.size
        total_cost, fee = calculate_net_cost(
            side=order.side,
            price=order.price,
            size=notional,
            fee_rate_bps=fee_info.rate_bps,
            fee_exponent=fee_info.exponent,
        )

        print("Order preview")
        print(f"token: {order.token_id}")
        print(f"price: {order.price}")
        print(f"size: {order.size} tokens")
        print(f"notional: {notional}")
        print(f"estimated taker fee: {fee}")
        print(f"estimated collateral: {total_cost}")

        if os.environ.get("POLYMARKET_SUBMIT") != "1":
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
            wallet_id="example-eoa",
            set_default=True,
        )
        balance = await client.get_balances(wallet_id=wallet_id)
        if balance.collateral < total_cost:
            raise RuntimeError(
                f"Insufficient collateral: need {total_cost}, "
                f"have {balance.collateral}"
            )

        response = await client.place_order(order, wallet_id=wallet_id)
        print(
            {
                "success": response.success,
                "order_id": response.order_id,
                "status": response.status,
                "error": response.error_msg,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
