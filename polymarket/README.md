# Polymarket client

The `polymarket` package is the standalone public version of downstream project's shared
Polymarket client.

It provides async public market-data access, multi-wallet authentication,
CLOB v2 order signing, complete position/activity reads, typed public-flow
results, exact order lookup, fee-aware reservation accounting, WebSocket/RTDS
subscriptions, rate limiting, and credential-redacting errors.

## Start here

- Project overview and examples: [../README.md](../README.md)
- Complete API surface: [API_REFERENCE.md](API_REFERENCE.md)
- Short setup guide: [QUICKSTART.md](QUICKSTART.md)
- Examples: [examples/README.md](examples/README.md)

## Core contract

```python
from polymarket import PolymarketClient

async with PolymarketClient() as client:
    midpoint = await client.get_midpoint(token_id)
```

All network and wallet operations are async. `add_wallet()` must be awaited.
Public reads need no wallet. Authenticated trading starts with
`await client.add_wallet(WalletConfig(...))`.

Prefer completeness-bearing methods when empty and unavailable must remain
distinct:

- `client.data.get_positions_complete(...)`
- `client.data.get_activity_since(...)`
- `client.get_market_trades_result_v1(...)`
- `client.get_prices_history_result_v1(...)`
- `client.get_market_trades_events_result_v1(...)`

Order requests accept any price strictly between zero and one. The client
resolves the market tick, normalizes the signed price, retrieves current fee
metadata, and keeps ambiguous BUY submissions reserved until exact
reconciliation.

Run the hermetic suite from the repository root:

```bash
PYTHONPATH=. python -m pytest -q polymarket/tests
```
