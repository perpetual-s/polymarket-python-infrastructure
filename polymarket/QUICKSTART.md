# Quick Start

`polymarket` is an async-first client library. Use `await` for networked client operations and prefer `async with PolymarketClient()` so sessions close cleanly.

## Install

```bash
python -m pip install -e .
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
import os

from polymarket import PolymarketClient, SignatureType, WalletConfig


async def main():
    async with PolymarketClient() as client:
        await client.add_wallet(
            WalletConfig(
                private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
                signature_type=SignatureType.EOA,
            ),
            wallet_id="main",
            set_default=True,
        )


asyncio.run(main())
```

## 3. Place an Order

```python
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


async def main():
    async with PolymarketClient() as client:
        await client.add_wallet(
            WalletConfig(
                private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
                signature_type=SignatureType.EOA,
            ),
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
- BUY preflight reserves `size * price` collateral plus the current taker fee.
- SELL preflight checks require enough token balance from the positions API.
- `skip_balance_check=True` skips the exchange-balance preflight. It does not
  skip tick normalization, fee metadata, signing checks, or local BUY
  reservation accounting.
- Running this example can place a real order. Use a dedicated low-value wallet
  and replace the token ID only after inspecting the market.

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

## 6. Handle Errors

Every failure raised by the client derives from `PolymarketError`, so you can
catch the specific case you can act on and let the base class cover the rest.
All of these import directly from `polymarket`.

```python
import asyncio

from polymarket import (
    APIError,
    MarketNotFoundError,
    PolymarketError,
    PolymarketClient,
    PriceUnavailableError,
    RateLimitError,
    TimeoutError,
)


async def main():
    async with PolymarketClient() as client:
        try:
            midpoint = await client.get_midpoint("TOKEN_ID")
        except PriceUnavailableError as e:
            print("no price for", e.token_id)
        except MarketNotFoundError as e:
            print("unknown market", e.market_id)
        except RateLimitError as e:
            print("throttled on", e.endpoint, "retry after", e.retry_after)
        except TimeoutError:
            print("request timed out; back off and retry")
        except APIError as e:
            print("upstream returned", e.status_code)
        except PolymarketError as e:
            print("client error:", e.message)
        else:
            print("midpoint:", midpoint)


asyncio.run(main())
```

Messages and `details` are credential-redacted before the exception is raised.
For order submission specifically, use `is_definitive_order_rejection(error)`
to tell a proven non-submission from an ambiguous outcome that still needs
reconciliation; see [API_REFERENCE.md](./API_REFERENCE.md).

## Recommended Reading

- [Project overview](../README.md)
- [Package overview](./README.md)
- [API_REFERENCE.md](./API_REFERENCE.md)
- [`examples/`](./examples)
