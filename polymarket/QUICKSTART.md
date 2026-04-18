# Quick Start

`polymarket` is an async-first client library. Use `await` for networked client operations and prefer `async with PolymarketClient()` so sessions close cleanly.

## Install

```bash
pip install -r polymarket/requirements.txt
```

## 1. Public Market Data

No wallet is required for public CLOB queries.

```python
import asyncio

from polymarket import PolymarketClient


async def main():
    async with PolymarketClient() as client:
        token_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"

        midpoint = await client.get_midpoint(token_id)
        spread = await client.get_spread(token_id)
        bid_ask = await client.get_best_bid_ask(token_id)
        depth = await client.get_liquidity_depth(token_id, price_range=0.05)

        print("midpoint:", midpoint)
        print("spread:", spread)
        print("best bid/ask:", bid_ask)
        print("depth:", depth["total_depth"])


asyncio.run(main())
```

Useful public methods:

- `await client.get_midpoint(token_id)`
- `await client.get_spread(token_id)`
- `await client.get_best_bid_ask(token_id)`
- `await client.get_prices([{...}, {...}])`
- `await client.get_simplified_markets(next_cursor="MA==")`
- `await client.get_markets_full(next_cursor="MA==")`

## 2. Add a Wallet

```python
import asyncio

from polymarket import PolymarketClient, WalletConfig


async def main():
    async with PolymarketClient() as client:
        await client.add_wallet(
            WalletConfig(private_key="0x..."),
            wallet_id="main",
            set_default=True,
        )


asyncio.run(main())
```

## 3. Place an Order

```python
import asyncio
from decimal import Decimal

from polymarket import PolymarketClient, WalletConfig, OrderRequest, OrderType, Side


async def main():
    async with PolymarketClient() as client:
        await client.add_wallet(
            WalletConfig(private_key="0x..."),
            wallet_id="main",
            set_default=True,
        )

        order = OrderRequest(
            token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
            price=Decimal("0.55"),
            size=Decimal("10"),
            side=Side.BUY,
            order_type=OrderType.GTC,
        )

        response = await client.place_order(order, wallet_id="main")
        print(response)


asyncio.run(main())
```

Important semantics:

- `OrderRequest.size` is token quantity, not USD.
- BUY preflight checks require `size * price` in collateral.
- SELL preflight checks require enough token balance from the positions API.
- Use `skip_balance_check=True` only when you intentionally want fail-open behavior at the caller layer.

## 4. Batch Operations

```python
import asyncio

from polymarket import PolymarketClient


async def main():
    async with PolymarketClient(
        pool_connections=100,
        pool_maxsize=200,
        batch_max_workers=20,
    ) as client:
        wallets = ["0xabc...", "0xdef...", "0xghi..."]

        positions = await client.get_positions_batch(wallets)
        metrics = await client.aggregate_multi_wallet_metrics(wallets)
        signals = await client.detect_signals(wallets, min_wallets=2, min_agreement=0.6)

        print(positions.keys())
        print(metrics)
        print(signals)


asyncio.run(main())
```

## 5. Health and Shutdown

```python
import asyncio

from polymarket import PolymarketClient


async def main():
    async with PolymarketClient() as client:
        health = await client.health_check()
        print(health)


asyncio.run(main())
```

## Recommended Reading

- [README.md](./README.md)
- [API_REFERENCE.md](./API_REFERENCE.md)
- [`examples/`](./examples)
