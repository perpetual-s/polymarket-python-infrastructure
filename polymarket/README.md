# Polymarket client

`polymarket` provides public Gamma, CLOB, and Data API reads; authenticated
orders and balances; wallet routing; CLOB and RTDS WebSockets; typed models;
endpoint rate limits; and bounded request retries.

Applications should not make direct Polymarket HTTP calls. Add a missing
operation to this facade.

## Import

```python
from decimal import Decimal

from polymarket import (
    OrderRequest,
    OrderType,
    PolymarketClient,
    Side,
    SignatureType,
    WalletConfig,
)
```

All HTTP operations and `add_wallet()` are async:

```python
async with PolymarketClient() as client:
    markets = await client.get_markets(active=True, closed=False, limit=100)
    midpoint = await client.get_midpoint(token_id)
```

There is no `polymarket.types` module.

## Public and authenticated clients

Public reads need no wallet:

```python
async with PolymarketClient() as client:
    positions = await client.get_address_positions(address)
    activity = await client.get_address_activity(address, limit=100)
```

Trading first adds a wallet:

```python
wallet = WalletConfig(
    private_key=private_key,
    address=signer_or_funder,
    signature_type=SignatureType.EOA,
)

async with PolymarketClient(db=db) as client:
    wallet_id = await client.add_wallet(
        wallet,
        wallet_id="WALLET_2",
        set_default=True,
    )
    response = await client.place_order(
        OrderRequest(
            token_id=token_id,
            price=Decimal("0.42"),
            size=Decimal("5"),
            side=Side.BUY,
            order_type=OrderType.GTC,
        ),
        wallet_id=wallet_id,
    )
```

The optional database client caches CLOB API credentials, never private keys.

## Wallet routing

`SignatureType` values:

| Value | Type |
|---|---|
| `0` | EOA |
| `1` | Polymarket proxy |
| `2` | Gnosis Safe / legacy website wallet |
| `3` | EIP-1271 deposit wallet |

For proxy-family wallets, the private key is the signer and `funder` is the
funds-holding contract address. Use
`polymarket.wallet_identity.resolve_wallet_config()` or the env
resolvers instead of duplicating address inference.

## Request behavior

Each request is independently rate-limited, bounded by timeout, and retried
according to `PolymarketSettings`. A failed upstream surface does not disable
the others and does not create persistent client state. The caller owns
long-running retry cadence and shutdown.

Exact endpoint limits live in `config.py`. Unknown settings passed to
`PolymarketClient` raise `TypeError`; unknown endpoint keys use the
conservative default limiter.

## Main facade groups

- Gamma: markets, keyset pagination, events, search, slugs, and IDs.
- public CLOB: books, midpoint, price, spread, last trade, server time, tick,
  fee, and scoring reads.
- Data API: positions, closed positions, trades, activity, portfolio value,
  holders, and leaderboard.
- authenticated CLOB: place/cancel orders, order/trade reads, balances, token
  balances, and allowance updates.
- batch/multi-wallet: books, prices, positions, trades, activity, aggregate
  metrics, and signal helpers.
- streams: CLOB orderbook/user orders and RTDS activity, markets, comments,
  prices, and RFQ topics.

`API_REFERENCE.md` indexes the concrete facade names. `client.py` remains the
signature source of truth.

## Orders and balances

- Financial values use `Decimal`.
- `OrderRequest.size` is share count. Market BUY amount semantics are handled
  by the signed-order builder and CLOB path; do not convert with floats.
- prices are normalized against live tick size before signing.
- BUY collateral is reserved under an async lock before submission.
- definitive rejection releases the reservation; ambiguous outcomes remain
  reserved until exact adjudication.
- callers that resolve a durable order reservation use
  `release_reserved_balance()` exactly once.
- cancel uses the authenticated wallet that owns the order.

The caller persists pre-submit intent and exact order state around these
calls; the client does not replace application database ownership.

## Streams

CLOB and RTDS subscription methods are synchronous registration wrappers;
callbacks run on transport threads. Connection and close operations remain
owned by the client.

```python
client.subscribe_user_orders(callback, wallet_id="WALLET_2")
connected = client.wait_until_websocket_connected(timeout=5)
client.unsubscribe_all()
await client.close()
```

Use the method-specific return contract in `API_REFERENCE.md`; do not `await`
subscription registration methods.

## Errors

All facade failures derive from `PolymarketError`. Common classes:

- `APIError`
- `AuthenticationError`
- `RateLimitError`
- `TimeoutError`
- `ValidationError`
- `TradingError`
- `InsufficientBalanceError`
- `OrderRejectedError`
- `MarketNotReadyError`
- `InvalidOrderError`
- `PriceUnavailableError`

`is_definitive_order_rejection()` distinguishes a proven non-submission from
an ambiguous exchange outcome. Do not turn an ambiguous order error into a
retrying duplicate placement.

## Configuration and files

`PolymarketSettings` uses the `POLYMARKET_` prefix. Important groups are API
URLs, Polygon chain/RPC, timeouts and retry, rate limiting, metrics, connection
pools, CLOB WebSocket, and RTDS.

| File | Responsibility |
|---|---|
| `client.py` | facade and wallet/transport ownership |
| `models.py` | request/response and wallet models |
| `config.py` | settings and endpoint rate limits |
| `exceptions.py` | typed errors and rejection classification |
| `api/` | Gamma, public/authenticated CLOB, Data API, streams |
| `auth/` | key manager, credentials, signed headers |
| `trading/` | EIP-712 order build/sign |
| `wallet_identity.py` | signer/funder resolution |
| `utils/` | fees, validation, allowance, retry, limiter |
