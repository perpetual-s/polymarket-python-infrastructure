# polymarket-python-infrastructure

An unofficial, async Python client for Polymarket market data, wallet-aware
trading, and real-time streams.

This repository provides a standalone Polymarket boundary for reusable Python
applications. It favors explicit result contracts, `Decimal` arithmetic,
typed errors, bounded retries, and fail-closed behavior when exchange state is
incomplete.

## What is current

The client includes:

- Public Gamma, Data API, and CLOB market-data access without a wallet.
- Multi-wallet CLOB authentication with EOA, Poly proxy, Gnosis Safe, and
  POLY_1271/deposit-wallet routing.
- In-repository CLOB v2 EIP-712 signing, including type-3 ERC-7739 wrapping.
- Tick-aware order normalization using the market's current tick, including
  `0.001` markets.
- Per-market minimum-order-size discovery from current CLOB `mos` metadata.
- Fee-schedule parsing that keeps protocol `base_fee` metadata separate from
  the economic `fd` taker curve.
- Exact order lookup, authenticated trade history, cancellation, and
  fee-inclusive BUY reservation accounting.
- Complete position pagination and activity-frontier reads that do not turn
  transport or parsing failures into an empty portfolio.
- Strict, retry-capable wallet-history reads for durable bulk acquisition.
- Typed public-flow result objects for trades, price history, and market trade
  events, with request evidence and explicit completeness limits.
- CLOB WebSocket and RTDS subscription lifecycle handling.
- Per-endpoint rate limiting, bounded retries, caching, Prometheus metrics,
  and credential-redacting logs and exceptions.

The package is async-first. Network and wallet methods must be awaited; local
subscription registration and state-inspection helpers are synchronous.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/perpetual-s/polymarket-python-infrastructure.git
cd polymarket-python-infrastructure
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Add `.[onchain]` for Web3-backed CTF operations, `.[test]` for the test suite,
or `.[dev]` for all development and example dependencies. The compatibility
file at `polymarket/requirements.txt` installs the full development set.

Web3-backed CTF support is loaded lazily. Public market-data use does not
require Web3 or a private key.

## Public market-data example

```python
import asyncio

from polymarket import PolymarketClient


async def main() -> None:
    async with PolymarketClient() as client:
        healthy = await client.get_ok()
        midpoint = await client.get_midpoint("TOKEN_ID")
        positions = await client.data.get_positions_complete("0xWALLET")

        print({"healthy": healthy, "midpoint": midpoint})
        print(f"positions: {len(positions)}")


asyncio.run(main())
```

`get_positions_complete()` returns only a complete, parsed collection. A
timeout, malformed page, page ceiling, or identity mismatch raises instead of
returning a false empty portfolio.

## Authenticated wallet example

Never commit a private key. Load it from a secret store or an ignored `.env`
file.

```python
import asyncio
import os
from decimal import Decimal

from polymarket import (
    OrderRequest,
    PolymarketClient,
    Side,
    SignatureType,
    WalletConfig,
)


async def main() -> None:
    wallet = WalletConfig(
        private_key=os.environ["POLY_PRIVATE_KEY"],
        signature_type=SignatureType.EOA,
    )

    async with PolymarketClient() as client:
        wallet_id = await client.add_wallet(
            wallet,
            wallet_id="primary",
            set_default=True,
        )

        response = await client.place_order(
            OrderRequest(
                token_id="TOKEN_ID",
                price=Decimal("0.55"),
                size=Decimal("10"),
                side=Side.BUY,
            ),
            wallet_id=wallet_id,
        )
        print(response.order_id)


asyncio.run(main())
```

For `SignatureType.EOA`, omit the funder address. Types 1-3 require the
funds-holding contract address. The signer is always derived from the private
key; a configured signer address is treated as a claim to verify, never as
identity truth.

`OrderRequest.size` is token quantity. The example reserves roughly
`10 × 0.55 = 5.50` collateral plus any current taker fee; it is not a
10-dollar market order.

## Important order semantics

- Request models accept `0 < price < 1`; the client resolves the token's
  current tick and applies `tick <= price <= 1 - tick` before signing.
- BUY reservations include the normalized notional and current taker fee.
- Ambiguous submission outcomes retain their reservation for exact
  reconciliation. Definitive local or exchange rejection releases it.
- Cancellation or disappearance from the open-orders list is not terminal
  proof by itself. Use exact order lookup plus authenticated trade history.
- Makers currently have zero platform fee; taker estimates use the market's
  economic fee schedule.

## Typed result surfaces

Compatibility helpers remain available, but code that needs to distinguish
empty, incomplete, not found, and failed observations should prefer:

- `get_market_trades_result_v1(...)`
- `get_prices_history_result_v1(...)`
- `get_market_trades_events_result_v1(...)`
- `client.data.get_positions_complete(...)`
- `client.data.get_activity_since(...)`

See [the full API reference](polymarket/API_REFERENCE.md) for exact return
models and coverage limits.

## Package map

| Path | Purpose |
|---|---|
| `polymarket/client.py` | Main facade, wallets, orders, reservations, subscriptions |
| `polymarket/api/clob.py` | Authenticated and robust CLOB operations |
| `polymarket/api/clob_public.py` | Wallet-free CLOB market data and typed public-flow results |
| `polymarket/api/data_api.py` | Positions, trades, activity, leaderboard, durable frontiers |
| `polymarket/api/gamma.py` | Markets, events, tags, profiles |
| `polymarket/trading/order_builder.py` | Tick-aware CLOB v2 build and signing |
| `polymarket/wallet_identity.py` | Signer/funder/signature-type resolution |
| `polymarket/models.py` | Pydantic request and response contracts |
| `polymarket/exceptions.py` | Typed, credential-redacting errors |
| `polymarket/API_REFERENCE.md` | Complete method and model reference |

## Validation

The default suite is hermetic. Live-network and testnet checks are opt-in.

```bash
PYTHONPATH=. python -m pytest -q polymarket/tests
```

Live or funded operations require explicit environment configuration. The
offline suite proves local contracts; it does not prove account entitlement,
funding, regional access, or exchange transport.

## Contributors and coding agents

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup and change validation.
Claude reads [CLAUDE.md](CLAUDE.md); Codex, GPT, and other compatible coding
agents discover the same guidance through `AGENTS.md`.

## Security

- Keep private keys and API credentials out of source control.
- Use the top-level client rather than bypassing it with raw HTTP.
- Treat log and exception redaction as a last boundary, not a substitute for
  correct secret handling.
- Understand the reservation and reconciliation contract before using real
  capital.
- Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
