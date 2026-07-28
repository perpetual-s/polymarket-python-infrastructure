# Polymarket API Reference

## 1. Purpose and scope

`polymarket/` is a standalone boundary for typed market data, authenticated
CLOB trading, Data API reads, wallet auth, CLOB credentials, WebSockets, rate
limiting, and errors. Orientation and usage examples live in
[README.md](README.md).

## 2. Table of contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Table of contents](#2-table-of-contents)
3. [Client construction and settings](#3-client-construction-and-settings)
4. [Authentication and wallet management](#4-authentication-and-wallet-management)
5. [Market data](#5-market-data)
6. [Trading](#6-trading)
7. [Data API](#7-data-api)
8. [WebSocket](#8-websocket)
9. [Models and enums](#9-models-and-enums)
10. [Utility functions (validation, fees, CTF)](#10-utility-functions-validation-fees-ctf)
11. [Errors](#11-errors)
12. [Rate limits](#12-rate-limits)
13. [Verification](#13-verification)

## 3. Client construction and settings

### Constructor

```python
class PolymarketClient:
    def __init__(
        self,
        settings: Optional[PolymarketSettings] = None,
        enable_rate_limiting: Optional[bool] = None,
        enable_circuit_breaker: Optional[bool] = None,
        db: Optional[Any] = None,
        **settings_overrides: Any,
    ) -> None: ...
```

Semantics:

- `settings=None` loads `PolymarketSettings()` from `.env` and environment.
- `settings` is deep-copied before mutation.
- `enable_rate_limiting` overrides `settings.enable_rate_limiting`.
- `enable_circuit_breaker=False` disables circuit breaker construction.
- `db` is optional credential cache storage; expected methods are `get_wallet_credentials()` and `set_wallet_credentials()`.
- `**settings_overrides` may contain only real `PolymarketSettings` fields.
- Unknown override names raise `TypeError`.
- HTTP sessions are created during API client construction; use `await client.close()` or `async with`.
- Construction never installs or replaces process SIGINT/SIGTERM handlers. The
  application owns signal policy and must await `close()` from its shutdown path.
- `close()` releases client transports/resources but does not cancel open orders.
  The application owns order-cancellation policy; `atexit` cleanup is best-effort,
  not a controlled shutdown path.

Canonical construction:

```python
from polymarket import PolymarketClient

async with PolymarketClient(pool_connections=100, batch_max_workers=20) as client:
    ok = await client.get_ok()
```

### PolymarketSettings

`PolymarketSettings` is a Pydantic `BaseSettings` model.

Environment:

- Prefix: `POLYMARKET_`
- Env file: `.env`
- Case sensitive: `False`
- Extra env keys: ignored
- Assignment validation: enabled

Fields:

| Field | Type | Default | Notes |
|---|---:|---:|---|
| `clob_url` | `str` | `https://clob.polymarket.com` | CLOB API base URL |
| `gamma_url` | `str` | `https://gamma-api.polymarket.com` | Gamma API base URL |
| `chain_id` | `int` | `137` | Polygon |
| `rpc_url` | `str` | `https://polygon.drpc.org` | Polygon RPC URL (current documented public mainnet endpoint) |
| `request_timeout` | `float` | `30.0` | Socket read timeout, seconds |
| `connect_timeout` | `float` | `10.0` | Connect timeout, seconds |
| `max_retries` | `int` | `3` | `0..10` |
| `retry_backoff_base` | `float` | `2.0` | Exponential base |
| `retry_backoff_max` | `float` | `60.0` | Max retry delay |
| `enable_rate_limiting` | `bool` | `True` | Constructs `RateLimiter` |
| `rate_limit_margin` | `float` | `0.8` | Multiplies configured caps |
| `circuit_breaker_threshold` | `int` | `5` | Failures before open |
| `circuit_breaker_timeout` | `float` | `60.0` | Reset timeout |
| `log_level` | `str` | `INFO` | Logging level |
| `log_requests` | `bool` | `False` | HTTP request logging |
| `enable_metrics` | `bool` | `True` | Prometheus metrics |
| `metrics_port` | `int` | `9090` | Metrics port |
| `ws_url` | `str` | `wss://ws-subscriptions-clob.polymarket.com/ws` | CLOB WebSocket base |
| `ws_reconnect_delay` | `float` | `5.0` | Seconds |
| `ws_max_reconnects` | `int` | `10` | `0` disables retries |
| `rtds_url` | `str` | `wss://ws-live-data.polymarket.com` | RTDS URL |
| `rtds_auto_reconnect` | `bool` | `True` | RTDS reconnect |
| `rtds_ping_interval` | `float` | `5.0` | RTDS ping seconds |
| `rtds_connection_timeout` | `float` | `30.0` | RTDS connect timeout |
| `rtds_max_message_size` | `int` | `1048576` | Bytes |
| `enable_rtds` | `bool` | `True` | RTDS facade methods require this |
| `pool_connections` | `int` | `50` | Per-host pool limit |
| `pool_maxsize` | `int` | `100` | Total pool limit |
| `batch_max_workers` | `int` | `20` | Concurrent batch reads |
| `validate_orders` | `bool` | `True` | Reserved for validation toggles |
| `min_order_size` | `float` | `0.01` | Used by order validation |

### Lifecycle and local state methods

| Method | Async | Return | Raises |
|---|---:|---|---|
| `async close() -> None` | yes | `None` | logs cleanup errors |
| `async health_check() -> Dict[str, Any]` | yes | health dict | returns unhealthy dict on error |
| `get_rate_limiter_stats() -> dict` | no | stats or `{}` | none |
| `get_circuit_breaker_state() -> Optional[str]` | no | trading-breaker state or `None` | none |
| `get_data_circuit_breaker_state() -> Optional[str]` | no | worst-of data-plane state or `None` | none |
| `get_data_circuit_breaker_states() -> Dict[str, Optional[str]]` | no | per-surface data-plane states | none |
| `reset_circuit_breaker() -> None` | no | `None` | none |
| `__enter__() -> PolymarketClient` | no | self | cleanup via sync wrapper |
| `__exit__(exc_type, exc_val, exc_tb) -> None` | no | `None` | logs cleanup errors |
| `async __aenter__() -> PolymarketClient` | yes | self | none |
| `async __aexit__(exc_type, exc_val, exc_tb) -> None` | yes | `None` | logs cleanup errors |

Health shape:

```python
{
    "status": "healthy" | "degraded" | "unhealthy",
    "clob": {...},
    "circuit_breaker": "closed" | "open" | "half_open" | "disabled",
    "data_circuit_breakers": {  # per-surface data-plane breakers; {} if disabled
        "polymarket-gamma": "CLOSED" | "OPEN" | "HALF_OPEN",
        "polymarket-data": "CLOSED" | "OPEN" | "HALF_OPEN",
        "polymarket-clob-public": "CLOSED" | "OPEN" | "HALF_OPEN",
    },
    "rate_limiter": {...},
    "inflight_orders": int,
    "timestamp": float,
}
```

Circuit breakers are split per upstream surface — four named breakers, each guarding one plane so an outage on one cannot block the others:

- `polymarket-trading` — authenticated CLOB calls that move money (orders, cancels, balances). Exposed via `get_circuit_breaker_state()` and the `circuit_breaker` health key. **Only this breaker gates bot health**: `shared/bot` keys shutdown/health off the trading breaker alone, so a data-plane outage never shuts the bot down.
- `polymarket-gamma` — Gamma API (market/event metadata).
- `polymarket-data` — Data API (positions, trades, activity).
- `polymarket-clob-public` — public CLOB reads (books, prices, spreads).

The three data-plane breakers are independent (a Data API outage cannot open the Gamma breaker). `get_data_circuit_breaker_states()` returns each by name; `get_data_circuit_breaker_state()` returns the worst state across them (`OPEN` > `HALF_OPEN` > `CLOSED`) for back-compat. `reset_circuit_breaker()` resets all four. All are `None` (and the states map is `{}`) when `enable_circuit_breaker=False`.

Every successful request closes its breaker and resets the consecutive-failure
count. Identical public GETs without per-request headers may be coalesced.
Authenticated or otherwise custom-header GETs execute independently so one
wallet's response cannot satisfy another wallet's request; auth material is
never retained in a coalescing key.

## 4. Authentication and wallet management

> Note (2026): Polymarket now onboards new API users via deposit wallets with
> signature type 3 (`POLY_1271`). The facade supports the current numeric
> families 0–3. For type 3, both signed `maker` and `signer` are the deposit
> wallet while its owner/session EOA produces the ERC-7739-wrapped EIP-712
> signature checked through ERC-1271. Types 1–3 require the funds-holding
> address in `funder` (or the compatibility `address` field).

### Wallet management methods

```python
async def add_wallet(
    self,
    wallet_config: WalletConfig,
    wallet_id: Optional[str] = None,
    set_default: bool = False,
) -> str: ...

def remove_wallet(self, wallet_id: str) -> None: ...
def list_wallets(self) -> List[str]: ...
def get_default_wallet(self) -> Optional[str]: ...
```

Raises:

- `ValidationError`: invalid wallet config from key manager validation.
- `AuthenticationError`: duplicate wallet id or failed credential setup.
- `APIError`, `RateLimitError`, `TimeoutError`: propagated from credential bootstrap endpoints.

### WalletConfig

```python
class WalletConfig(BaseModel):
    private_key: SecretStr
    address: Optional[str] = None
    signature_type: SignatureType = SignatureType.EOA
    funder: Optional[str] = None
```

`SignatureType`:

| Name | Value | Meaning |
|---|---:|---|
| `SignatureType.EOA` | `0` | EOA signer and balance holder |
| `SignatureType.POLY_PROXY` (`MAGIC`) | `1` | Polymarket proxy family; EOA signs, funder holds funds |
| `SignatureType.GNOSIS_SAFE` (`PROXY`) | `2` | Historical Polymarket website wallet |
| `SignatureType.POLY_1271` | `3` | Current deposit-wallet contract |

Type 3 is pinned to the official `py-clob-client-v2` 1.1 known-answer vector
in `tests/test_clob_v2_signing.py`; it is not the raw type-0/1 ECDSA wire shape.

PROXY construction:

```python
from polymarket import PolymarketClient, WalletConfig, SignatureType

async with PolymarketClient() as client:
    wallet_id = await client.add_wallet(
        WalletConfig(
            private_key=eoa_private_key,
            address=proxy_address,
            signature_type=SignatureType.PROXY,
        ),
        wallet_id="WALLET_0",
        set_default=True,
    )
```

PROXY mapping:

- `WalletConfig.private_key`: EOA private key.
- `WalletConfig.address`: proxy address for PROXY wallets.
- Key manager derives signer EOA from private key.
- Auth headers use signer EOA.
- `credentials.funder` stores proxy address for PROXY/MAGIC.
- Balance and Data API reads use `funder` when present.

### Wallet identity resolution

`polymarket.wallet_identity` is the single place a signing identity is
resolved. It performs no I/O; `add_wallet` and environment/registry consumers
can go through it so they cannot disagree about who a wallet is.

```python
from polymarket import (
    ResolvedWalletIdentity,
    ResolvedWalletRouting,
    abbreviate_address,
    resolve_wallet_config,
    resolve_wallet_identity_from_env,
    resolve_wallet_routing_from_env,
)

def resolve_wallet_config(
    wallet_config: WalletConfig,
    *,
    expected_signer_address: Optional[str] = None,
    private_key_name: str = "private_key",
    address_name: str = "address",
) -> ResolvedWalletIdentity: ...

def resolve_wallet_identity_from_env(
    wallet_id: str,
    private_key: SecretStr | str,
    environ: Mapping[str, str],
) -> ResolvedWalletIdentity: ...

def resolve_wallet_routing_from_env(
    wallet_id: str,
    environ: Mapping[str, str],
) -> ResolvedWalletRouting: ...

def abbreviate_address(address: Optional[str]) -> str: ...
```

Rules:

- The signer is always derived from the private key. A configured address is a
  claim to check, never a source of truth: a mismatch raises `ValidationError`.
- `ResolvedWalletIdentity` carries `signer_address`, `funder_address`,
  `signature_type`, `wallet_type` (`eoa`/`smart_contract`), `funds_address`,
  and a normalized `wallet_config` to hand to `add_wallet`.
- Types 1–3 require a funder; an EOA's collateral lives at its signer, so a
  stray funder on an explicit type 0 is ignored rather than routed to.
- Environment convention per logical wallet `X`: `X_PRIVATE_KEY`, optional
  `X_ADDRESS` (checked against the derived signer), `X_FUNDER_ADDRESS` or the
  legacy `X_PROXY_ADDRESS`, and `X_SIGNATURE_TYPE` (falling back to
  `COPY_WALLET_SIGNATURE_TYPE`). A legacy proxy with no explicit type resolves
  to `GNOSIS_SAFE` (2).
- `abbreviate_address` is what belongs in logs. Never log a private key, and
  prefer logical wallet IDs plus abbreviated public addresses.

### EOA token approvals

EOA wallets need six on-chain token approvals (USDC spender plus CTF operators) before the first trade. Helper: `polymarket.utils.allowances`. Budget roughly `$3-5` gas on Polygon. PROXY wallets do not need this step — Polymarket's proxy contract holds the approvals.

### CLOB credential bootstrap

`await client.add_wallet(...)` initializes CLOB credentials in this order:

1. Wallet-specific env:
   - `{wallet_id}_CLOB_API_KEY`
   - `{wallet_id}_CLOB_SECRET`
   - `{wallet_id}_CLOB_PASSPHRASE`
2. Global env:
   - `CLOB_API_KEY`
   - `CLOB_SECRET`
   - `CLOB_PASS_PHRASE`
3. Database cache when `db` is provided:
   - `await db.get_wallet_credentials(wallet_id)`
4. Existing API key derivation:
   - `GET /auth/derive-api-key`
   - L1 headers from signer EOA
5. New API key creation:
   - `POST /auth/api-key`
   - L1 headers from signer EOA
6. Database cache write when `db` is provided:
   - `await db.set_wallet_credentials(...)`

Failure to obtain all of `apiKey`, `secret`, and `passphrase` raises `AuthenticationError("Failed to get API credentials")`.

### Address selection by surface

| Surface | EOA wallet | PROXY wallet |
|---|---|---|
| L1 credential bootstrap | EOA signer | EOA signer |
| L2 trading headers | EOA signer | EOA signer |
| Signed order `funder` | none | proxy/funder |
| CLOB balance `address` | EOA signer | EOA signer |
| CLOB balance `funder` | none | proxy/funder |
| Data API `user` | EOA signer | proxy/funder |

## 5. Market data

### Top-level Gamma methods

| Method | Return | Raises |
|---|---|---|
| `async get_markets(limit: int = 100, offset: int = 0, active: Optional[bool] = None, closed: Optional[bool] = None, **kwargs) -> List[Market]` | markets | `MarketDataError` |
| `async get_markets_keyset(limit: int = 100, after_cursor: Optional[str] = None, active: Optional[bool] = None, closed: Optional[bool] = None, archived: Optional[bool] = None, **kwargs) -> Dict[str, Any]` | `{"markets": List[Market], "next_cursor": Optional[str], "raw_count": int}` | `MarketDataError` |
| `async get_market_by_slug(slug: str) -> Optional[Market]` | market or `None` | `MarketDataError` |
| `async get_market_by_id(market_id: str) -> Optional[Market]` | market or `None` | `MarketDataError` |
| `async search_markets(query: str, limit: int = 20) -> List[Market]` | markets | `MarketDataError` |
| `async get_all_current_markets(limit: int = 100) -> List[Market]` | active, open, unarchived markets | `MarketDataError` |
| `async get_clob_tradable_markets(limit: int = 100) -> List[Market]` | markets with token ids | `MarketDataError` |
| `async get_events(limit: int = 100, offset: int = 0, active: Optional[bool] = None, closed: Optional[bool] = None, archived: Optional[bool] = None) -> List[Event]` | events | `MarketDataError` |
| `filter_events_for_trading(events: List[Event]) -> List[Event]` | active, unrestricted, unarchived, open events | none |
| `async get_all_tradeable_events(limit: int = 100) -> List[Event]` | filtered events | `MarketDataError` |

`get_markets(..., **kwargs)` forwards extra filters to Gamma `/markets`.
Use `get_markets_keyset` for full/deep market cache refreshes; Gamma rejects
large offset pagination and returns `next_cursor` for the next page. The return
dict also carries `raw_count` (markets in the raw page before parse drops), so
auto-pagination continues on full pages even when some rows fail to parse.

Common extra filters accepted by `GammaAPI.get_markets`:

- `archived: Optional[bool]`
- `tag_id: Optional[int]`
- `slug: Optional[str]`
- Other query params accepted by Gamma.

### Direct GammaAPI methods

Available as `client.gamma.<method>`.

| Method | Return | Raises |
|---|---|---|
| `async get_markets(limit: int = 100, offset: int = 0, active: Optional[bool] = None, closed: Optional[bool] = None, archived: Optional[bool] = None, tag_id: Optional[int] = None, slug: Optional[str] = None, **kwargs) -> List[Market]` | markets | `MarketDataError` |
| `async get_markets_keyset(limit: int = 100, after_cursor: Optional[str] = None, active: Optional[bool] = None, closed: Optional[bool] = None, archived: Optional[bool] = None, tag_id: Optional[int] = None, slug: Optional[str] = None, **kwargs) -> Dict[str, Any]` | cursor page (`markets`, `next_cursor`, `raw_count`) | `MarketDataError` |
| `async get_market_by_slug(slug: str) -> Optional[Market]` | market or `None` | `MarketDataError` |
| `async get_market_by_id(market_id: str) -> Optional[Market]` | market or `None` | `MarketDataError` |
| `async get_events(limit: int = 100, offset: int = 0, active: Optional[bool] = None, closed: Optional[bool] = None, archived: Optional[bool] = None, **kwargs) -> List[Event]` | events with nested markets | `MarketDataError` |
| `async get_events_paginated(tag_slug: Optional[str] = None, limit: int = 20, order: str = "volume24hr", ascending: bool = False, cursor: Optional[str] = None) -> dict` | cursor page | `MarketDataError` |
| `async get_high_volume_events(min_volume_24h: float = 10000, tag_slugs: Optional[List[str]] = None, limit: int = 100) -> List[Event]` | events with bid/ask nested markets | `MarketDataError` |
| `extract_tradeable_markets(events: List[Event], min_spread: float = 0.0, max_spread: float = 0.15, min_price: float = 0.10, max_price: float = 0.90, min_days_to_resolution: int = 3) -> List[Market]` | filtered markets | none |
| `async get_tags() -> List[dict]` | tags | `MarketDataError` |
| `async search_markets(query: str, limit: int = 20) -> List[Market]` | markets | `MarketDataError` |
| `async get_all_current_markets(limit: int = 100) -> List[Market]` | auto-paginated markets | `MarketDataError` |
| `async get_clob_tradable_markets(limit: int = 100) -> List[Market]` | markets with token ids | `MarketDataError` |
| `filter_events_for_trading(events: List[Event]) -> List[Event]` | filtered events | none |
| `async get_all_tradeable_events(limit: int = 100) -> List[Event]` | filtered events | `MarketDataError` |
| `async get_15min_crypto_markets(assets: Optional[List[str]] = None, slots_ahead: int = 8, slots_behind: int = 1) -> List[Event]` | BTC/ETH/SOL/XRP 15-minute events | logs missing slots |
| `async get_15min_markets_expiring_soon(within_seconds: int = 120, assets: Optional[List[str]] = None) -> List[Event]` | soon-expiring 15-minute events | logs parse skips |
| `async get_public_profile(address: str) -> Optional[Dict[str, Any]]` | profile or `None` | `MarketDataError` except 404 |

`get_events_paginated` return shape:

```python
{
    "data": [...],
    "cursor": "NEXT_CURSOR_OR_NONE",
}
```

`get_public_profile` current behavior:

- Calls Gamma `GET /public-profile`.
- Sends `address=<lowercase address>`.
- Normalizes a dict response to that dict.
- Normalizes a list response to the first dict item.
- Returns `None` for empty address.
- Returns `None` on `APIError(status_code=404)`.
- Raises `MarketDataError` for non-404 API failures.
- Old `/v1/public-profile` references are stale for current code.

### Top-level CLOB and Public CLOB market data

Top-level robust single reads and order-book batches use `client.market_clob`,
a keyless `CLOBAPI` instance on the public-data breaker. Authenticated order,
cancel, balance, and ledger methods alone use `client.clob`. The older
fail-soft batch/convenience reads use `client.public_clob`.

| Method | Delegate | Return | Raises |
|---|---|---|---|
| `async get_orderbook(token_id: str) -> OrderBook` | `client.market_clob.get_orderbook` | complete parsed order book whose required `asset_id` equals the requested token | `TradingError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> Dict[str, OrderBook]` | `client.market_clob.get_orderbooks_batch` | every requested token id to a complete parsed order book | `TradingError` on malformed, duplicate, missing, partial, or unrequested rows |
| `async get_midpoint(token_id: str) -> Optional[float]` | `client.market_clob.get_midpoint` | annotated float, actual Decimal/None | `PriceUnavailableError` |
| `async get_midpoints(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_midpoints` | token id to midpoint | returns None values on batch error |
| `async get_price(token_id: str, side: Side) -> Optional[float]` | `client.market_clob.get_price` | annotated float, actual Decimal/None | `PriceUnavailableError` |
| `async get_prices(params: List[Dict[str, str]]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_prices` | composite key to price | returns `{}` on error |
| `async get_spread(token_id: str) -> Optional[float]` | `client.public_clob.get_spread` | annotated float, actual Decimal/None | returns `None` on error |
| `async get_spreads(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_spreads` | token id to spread | returns None values on error |
| `async get_best_bid_ask(token_id: str) -> Optional[tuple[Decimal, Decimal]]` | `client.public_clob.get_best_bid_ask` | `(best_bid, best_ask)` | returns `None` on error |
| `async get_liquidity_depth(token_id: str, price_range: Decimal | float = Decimal("0.05")) -> Dict[str, Any]` | `client.public_clob.get_liquidity_depth` | depth dict | zero-depth dict on error |
| `async get_last_trade_price(token_id: str) -> Optional[float]` | `client.market_clob.get_last_trade_price` | annotated float, actual Decimal/None; `GET /last-trade-price` | `PriceUnavailableError` |
| `async get_last_trades_prices(token_ids: List[str]) -> Dict[str, Optional[float]]` | `client.market_clob.get_last_trades_prices` | annotated float values, actual Decimal/None; `POST /last-trades-prices` | `TradingError` |
| `async get_server_time() -> int` | `client.market_clob.get_server_time` | Unix ms | `TradingError` |
| `async get_ok() -> bool` | `client.market_clob.get_ok` | CLOB health | `TradingError` for transport or malformed health data |
| `async get_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | `client.market_clob.get_simplified_markets` | CLOB page | `TradingError` |
| `async get_markets_full(next_cursor: str = "MA==") -> Dict[str, Any]` | `client.public_clob.get_markets` | full CLOB page | returns empty page on error |
| `async get_market_by_condition(condition_id: str) -> Dict[str, Any]` | `client.public_clob.get_market` | market dict | `MarketNotFoundError` |
| `async get_market_trades_events(condition_id: str) -> List[Dict[str, Any]]` (compatibility only)<br>`async get_market_trades_events_result_v1(condition_id: str) -> MarketTradeEventsResultV1` | `client.public_clob` | `GET /live-activity/events/{condition_id}`; legacy list or strict typed v1 events with exact request evidence and fixed 1,000-row/1 MiB decoded acceptance ceilings (`CLOB:default`) | legacy returns `[]` on error; v1 projects official nested `market`/`user` fields, emits closed `auth`/`request`/`non_list`/`serialization`/`bounds` error categories, distinguishes success/not-found/error/empty/parse loss, and never claims undocumented retention completeness or maker/taker role |
| `async get_prices_history(...) -> List[PricePoint]` (compatibility only)<br>`async get_prices_history_result_v1(token_id: str, interval: Optional[str] = None, start_ts: Optional[int] = None, end_ts: Optional[int] = None, fidelity: Optional[int] = None) -> PriceHistoryResultV1` | `client.public_clob` | legacy points or v1 query/request/counts plus strict timestamp-grid coverage (`GET:/prices-history` 1,000 req/10s) | `ValueError` if `interval` is combined with `start_ts`/`end_ts`; v1 separates 404/error/empty and proves range completeness only for a clean explicit fidelity grid |
| `async get_market_trades(...) -> List[Trade]` (compatibility only)<br>`async get_market_trades_result_v1(market: Optional[str] = None, *, user: Optional[str] = None, event_id: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, limit: int = 100, offset: int = 0, taker_only: bool = True, filter_type: Optional[str] = None, filter_amount: Optional[float] = None, side: Optional[Side] = None) -> DataTradesResultV1` | `client.data` | legacy rows or documented-shape v1 rows with effective query, exact request evidence, counts, and bounded-page coverage (`GET:/trades` 200 req/10s) | v1 validates query, separates not-found/error/empty, and marks complete only for a clean in-bounds short first page with explicit `start`/`end` |
| `async get_address_activity(address: str, **kwargs) -> List[Activity]` | `client.data.get_activity` | activity history for a raw wallet address (keyless, no key_manager; `GET:/activity` 1,000 req/10s) | `ValueError` if `address` not `0x…`; `APIError`, `TimeoutError`, parse errors |
| `async get_market_trades_window(market: str, start_ts: int, *, taker_only: bool = True, filter_type: Optional[str] = None, filter_amount: Optional[float] = None, max_pages: int = 4) -> Dict[str, Any]` | pages `client.data.get_trades` | `{"trades": List[Trade], "complete": bool}`; pages newest-first back to `start_ts` (keyless; `GET:/trades` 200 req/10s); offset paging over an actively-trading market can double-count or skip trades across page boundaries — `complete` attests window coverage, not per-trade exactness | `complete=False` when the `max_pages` budget is exhausted before reaching `start_ts` (window truncated); `APIError`, `TimeoutError`, parse errors |
| `async is_order_scoring(order_id: str) -> bool` | `client.clob.is_order_scoring` | scoring flag | `TradingError` |
| `async are_orders_scoring(order_ids: List[str]) -> Dict[str, bool]` | `client.clob.are_orders_scoring` | order id to scoring flag | `TradingError` |

`get_prices` param shape:

```python
params = [
    {"token_id": token_id, "side": "BUY"},
    {"token_id": token_id, "side": "SELL"},
]
prices = await client.get_prices(params)
```

`get_prices` return shape:

```python
{
    f"{token_id}_BUY": Decimal("0.52"),
    f"{token_id}_SELL": Decimal("0.53"),
}
```

`get_liquidity_depth` return shape:

```python
{
    "bid_depth": Decimal("123.45"),
    "ask_depth": Decimal("98.76"),
    "bid_levels": 4,
    "ask_levels": 3,
    "total_depth": Decimal("222.21"),
}
```

Market listing page shape:

```python
{
    "data": [...],
    "next_cursor": "MA==" | "LTE=" | "...",
}
```

### PublicCLOBAPI direct methods

Available as `client.public_clob.<method>`.

| Method | Return | Failure behavior |
|---|---|---|
| `async get_ok() -> bool` | `True`/`False` for an explicit healthy/unhealthy response | `TradingError` on transport or malformed health data |
| `async get_server_time() -> int` | Unix ms | propagates API errors |
| `async get_midpoint(token_id: str) -> Optional[Decimal]` | midpoint | `PriceUnavailableError` |
| `async get_midpoints(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to midpoint | None values |
| `async get_price(token_id: str, side: str) -> Optional[Decimal]` | price | `PriceUnavailableError` |
| `async get_prices(params: List[Dict[str, str]]) -> Dict[str, Optional[Decimal]]` | composite key to price | `{}` |
| `async get_spread(token_id: str) -> Optional[Decimal]` | spread | `None` |
| `async get_spreads(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to spread | None values |
| `async get_orderbook(token_id: str) -> OrderBook` | complete book with matching required `asset_id` | `OrderBookError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> Dict[str, OrderBook]` | every requested token id to a complete parsed book | `TradingError` on malformed, duplicate, missing, partial, or unrequested rows |
| `async get_order_book_hash(orderbook: OrderBook) -> str` | SHA-256 hex | none |
| `async get_tick_size(token_id: str) -> Decimal` | authoritative `minimum_tick_size` | propagates failure; no default |
| `async get_neg_risk(token_id: str) -> bool` | authoritative boolean flag | propagates failure; no default |
| `async get_fee_rate_bps(token_id: str) -> int` | protocol `base_fee` metadata | propagates failure; no zero default |
| `async get_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_sampling_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_sampling_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_market(condition_id: str) -> Dict[str, Any]` | market dict | `MarketNotFoundError` |
| `async get_market_trades_events(...) -> List[Dict[str, Any]]` (compatibility only)<br>`async get_market_trades_events_result_v1(condition_id: str) -> MarketTradeEventsResultV1` | `GET /live-activity/events/{condition_id}`; legacy rows or typed/bounded v1 result | both use `CLOB:default`; v1 projects `market.condition_id`, `market.asset_id`, and observed actor `user.address`, classifies failures without retaining response bodies, rejects oversized/malformed data, and leaves maker/taker role plus clean source completeness unknown |
| `async get_prices_history(...) -> List[PricePoint]` (compatibility only)<br>`async get_prices_history_result_v1(...) -> PriceHistoryResultV1` | legacy points or coverage-bearing v1 result | legacy `[]` on 404/skips malformed; v1 preserves exact request evidence and strict fidelity-grid diagnostics |
| `async get_last_trade_price(token_id: str) -> Optional[Decimal]` | last price via `GET /last-trade-price` | `PriceUnavailableError`; `None` only when the response explicitly has no price |
| `async get_last_trades_prices(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to last price via `POST /last-trades-prices` | `TradingError` |
| `async get_best_bid_ask(token_id: str) -> Optional[Tuple[Decimal, Decimal]]` | bid/ask | `None` |
| `async get_liquidity_depth(token_id: str, price_range: Decimal = Decimal("0.05")) -> Dict[str, Any]` | depth dict | zero-depth dict |

### CLOBAPI read-only direct methods

Available as `client.clob.<method>`.

| Method | Return | Raises |
|---|---|---|
| `async get_ok() -> bool` | CLOB health | `TradingError` |
| `async get_server_time() -> int` | Unix ms | `TradingError` |
| `async get_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | `TradingError` |
| `async get_midpoint(token_id: str) -> Optional[Decimal]` | midpoint | `PriceUnavailableError` |
| `async get_price(token_id: str, side: str) -> Optional[Decimal]` | price | `PriceUnavailableError` |
| `async get_last_trade_price(token_id: str) -> Optional[Decimal]` | last price | `PriceUnavailableError` |
| `async get_last_trades_prices(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to price | `TradingError` |
| `async get_orderbook(token_id: str) -> OrderBook` | complete book with matching required `asset_id` | `TradingError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> Dict[str, OrderBook]` | token id to order book | `TradingError` |
| `async get_tick_size(token_id: str) -> Decimal` | authoritative `minimum_tick_size` | `TradingError`; no default |
| `async get_neg_risk(token_id: str) -> bool` | authoritative neg-risk flag | `TradingError`; no default |
| `async get_fee_rate_bps(token_id: str) -> int` | protocol `base_fee` metadata, not economic rate | `TradingError`; no default |
| `async get_fee_schedule(token_id: str) -> Optional[FeeSchedule]` | token → condition → compact CLOB `fd` economic taker curve; `None` only for an explicitly fee-free shape | `TradingError` |
| `async is_order_scoring(order_id: str) -> bool` | scoring flag | `TradingError` |
| `async are_orders_scoring(order_ids: List[str]) -> Dict[str, bool]` | scoring flags | `TradingError` |

## 6. Trading

> **CLOB V2 order path — LANDED 2026-07-18 (core-loop Task B2); live proof
> pending Task C2.** The facade signs V2 orders in-repo
> (`trading/order_builder.py`; known-answer vectors against the official
> py-clob-client-v2 in `tests/test_clob_v2_signing.py`). The signed struct
> is `salt, maker, signer, tokenId, makerAmount, takerAmount, side,
> signatureType, timestamp(ms), metadata, builder` — `taker`, `expiration`,
> `nonce`, `feeRateBps` are gone from the SIGNATURE (`expiration` still
> rides the wire body for GTD); Exchange domain version `"2"` (ClobAuth
> stays `"1"`); collateral is **pUSD**
> `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`; exchanges: CTF V2
> `0xE111180000d2663C0091e4f400237545B87B996B`, Neg Risk V2
> `0xe2222d279d744050d28e00520010520000310F59`, adapter
> `0xadA2005600Dec949baf300f4C6120000bDB6eAab`. Successful FAK/FOK
> responses return `tradeIDs` (parsed into `OrderResponse.trade_ids`;
> legacy `transactionHashes` still accepted). The V2 EIP-712 order hash IS
> the exchange `orderID` and is computable pre-submit — `place_order`'s
> `pre_submit` hook receives the hash and exact BUY reservation so the caller
> can persist both for crash-safe identity and cap accounting (there is no
> COID field in the official V2 client; order-hash identity replaces the
> changelog's "COID" mention). Taker fees are real on several categories.
> `/fee-rate` supplies protocol `base_fee`; token → condition →
> `/clob-markets/{condition_id}` supplies the economic compact `fd` schedule.
> `get_fee_info()` combines those distinct values for sizing and P&L.

### Top-level trading methods

| Method | Return | Raises |
|---|---|---|
| `async place_order(order: OrderRequest, wallet_id: Optional[str] = None, skip_balance_check: bool = False, idempotency_key: Optional[str] = None, pre_submit: Optional[Callable[[str, Decimal], Awaitable[None]]] = None, timestamp_ms: Optional[int] = None, tick_size: Optional[Decimal] = None) -> OrderResponse` | order response; the hook receives `(order_hash, buy_reservation)`; optional caller-resolved tick is reused for normalization and signing | `AuthenticationError`, `ValidationError`, `InsufficientBalanceError`, `OrderRejectedError`, `TradingError` |
| `async place_market_order(market_order: MarketOrderRequest, wallet_id: Optional[str] = None, skip_balance_check: bool = False, idempotency_key: Optional[str] = None) -> OrderResponse` | order response; recomputes from the current book and has no durable `pre_submit` hook | `ValidationError`, `InsufficientBalanceError`, `TradingError` |
| `async place_orders_batch(orders: List[OrderRequest], wallet_id: Optional[str] = None, skip_balance_check: bool = False, pre_submit: Optional[Callable[[List[tuple[str, Decimal]]], Awaitable[None]]] = None) -> List[OrderResponse]` | one response per input; at most 15 orders; the atomic hook receives all `(order_hash, buy_reservation)` intents | `AuthenticationError`, `ValidationError`, `TradingError`, `InsufficientBalanceError` |
| `async cancel_order(order_id: str, wallet_id: Optional[str] = None) -> bool` | transport acknowledgement, including `NOT_FOUND`/empty 200; never terminal-order proof | `TradingError`, auth/key-manager errors |
| `async cancel_all_orders(wallet_id: Optional[str] = None, market_id: Optional[str] = None) -> int` | compatibility request helper; not terminal proof | `TradingError`, auth/key-manager errors |
| `async cancel_market_orders(market_id: str, wallet_id: Optional[str] = None) -> int` | compatibility request helper; not terminal proof | `TradingError`, auth/key-manager errors |
| `async get_orders(wallet_id: Optional[str] = None, market: Optional[str] = None) -> List[Order]` | open orders | `TradingError`, auth/key-manager errors |
| `async get_order(order_id: str, wallet_id: Optional[str] = None) -> Optional[Order]` | exact order including terminal state; `None` on 404 | `TradingError`, auth/key-manager errors |
| `async get_clob_trades(wallet_id: Optional[str] = None, **filters) -> List[ClobTrade]` | authenticated execution rows and maker contributions | `TradingError`, auth/key-manager errors |
| `async get_tick_size(token_id: str) -> Decimal` | authoritative current tick | `TradingError`; no fallback |
| `async get_fee_rate_bps(token_id: str) -> int` | protocol `base_fee`, not the economic rate | `TradingError`; no fallback |
| `async get_fee_info(token_id: str) -> FeeInfo` | complete economic rate/exponent/taker-only metadata plus protocol base fee | `TradingError`; a positive base fee without `fd` is incomplete |
| `async get_balances(wallet_id: Optional[str] = None) -> Balance` | balance | `TradingError`, auth/key-manager errors |
| `async get_token_balance(token_id: str, wallet_id: Optional[str] = None) -> Decimal` | CTF token balance | `TradingError`, auth/key-manager errors |
| `async get_position_balance(token_id: str, wallet_id: Optional[str] = None) -> Decimal` | size from one complete Data API observation | incomplete/failed reads propagate; returns `Decimal("0")` only after complete absence |
| `async update_balance_allowance(wallet_id: Optional[str] = None, asset_type: str = "COLLATERAL", token_id: Optional[str] = None) -> Dict[str, Any]` | update response | `TradingError`, auth/key-manager errors |
| `async release_reserved_balance(amount: Decimal, wallet_id: Optional[str] = None, order_id: Optional[str] = None) -> None` | `None` | `BalanceTrackingError` |
| `async get_reserved_balance(wallet_id: Optional[str] = None) -> Decimal` | reserved USD | none |
| `async restore_reserved_balance(amount: Decimal, wallet_id: Optional[str] = None) -> None` | replaces process-local reserved USD with the durable restart-reservation ledger total (active/unresolved plus terminal release debt) | `BalanceTrackingError` for negative/non-finite input |

### Order placement

`OrderRequest`:

```python
order = OrderRequest(
    token_id=token_id,
    price=Decimal("0.55"),
    size=Decimal("10.00"),
    side=Side.BUY,
    order_type=OrderType.GTC,
)
response = await client.place_order(order, wallet_id="WALLET_0")
```

`place_order` flow (BUY default, `skip_balance_check=False`):

1. Resolves wallet credentials.
2. Requires initialized CLOB API credentials.
3. Validates token id, open-unit price, size, side, and minimum size.
4. Resolves the current tick (or accepts one already resolved by the caller)
   and normalizes once: BUY floors and SELL ceilings so rounding never crosses
   the caller's adverse-price bound. The same exact price/tick is used for
   intent persistence, balance math, and signing.
5. Resolves complete `FeeInfo`. Metadata failure suppresses both BUY and SELL;
   `base_fee` alone is not substituted for the economic rate.
6. `_check_and_reserve_buy_balance`: preflights balance and atomically
   reserves normalized notional plus the current taker-curve fee under
   `_balance_lock`.
7. Builds and signs the EIP-712 order, resolving current neg-risk metadata.
8. Runs `pre_submit(order_hash, buy_reservation)` when provided, then posts to
   CLOB
   `POST /order`.
9. Requires a successful response's `orderID` to equal the locally computed
   V2 hash, even when no hook was provided.
10. On success the fee-inclusive reservation persists until exact terminal
    reconciliation. A proven pre-transport failure or definitive exchange
    rejection releases it; cancellation, timeout, malformed responses,
    duplicate/delayed responses, ID mismatch, and other ambiguous outcomes
    retain it.
11. Tracks metrics.

`place_order` flow when `skip_balance_check=True`:

- Skips the exchange-balance preflight, but still reserves the same normalized,
  fee-inclusive amount under `_balance_lock` before signing and transport.
- It still resolves current tick and complete fee metadata and follows the
  same exact-response and ambiguity rules.

`place_order` flow for SELL:

- Step 4 becomes `_check_balance` only (preflight, no reservation) unless
  `skip_balance_check=True`. SELL never reserves.

`idempotency_key`:

- Passed to signed-order construction as deterministic salt input.
- V2 full retry identity additionally requires the same explicit
  `timestamp_ms` and identical normalized order payload. An idempotency key
  alone does not freeze the creation timestamp or a recomputed market order.
- Batch placement does not accept idempotency keys.

`pre_submit` is optional at the reusable facade boundary, but omitting it
means the caller has no durable restart record for an order that reached
transport. A restart-safe runtime should supply the hook. Batch callers must
likewise persist the entire batch atomically before transport.

### Batch orders

- Accepts 1–15 orders and sends the official `POST /orders` array of complete
  per-order wrappers (`order`, API-key `owner`, `orderType`, `deferExec`,
  `postOnly`).
- Resolves tick, fee metadata, SELL token balance, and fee-inclusive BUY
  reservation for every item before signing or submitting.
- The optional hook is one atomic call containing every deterministic hash and
  reservation. A normal hook exception aborts before transport; cancellation
  during the durability boundary retains reservations because persistence may
  already have completed.
- Response cardinality and item types are exact. Every successful `orderID`
  must equal its local V2 hash.
- Only per-item failures classified as definitive rejections release their BUY
  reservation. Duplicate, delayed, unknown, malformed, incomplete, and
  transport-level outcomes remain ambiguous and retain cap until exact
  reconciliation.
- Production batch callers need the durability hook before transport.

### Market orders

Signature:

```python
async def place_market_order(
    self,
    market_order: MarketOrderRequest,
    wallet_id: Optional[str] = None,
    skip_balance_check: bool = False,
    idempotency_key: Optional[str] = None,
) -> OrderResponse: ...
```

Amount semantics:

| Side | `MarketOrderRequest.amount` means | Orderbook side traversed |
|---|---|---|
| `Side.BUY` | USD to spend | asks, low to high |
| `Side.SELL` | tokens/shares to sell | bids, high to low |

Execution:

- Computes a marketable limit price from the current orderbook.
- Converts BUY USD amount into token size using the computed market price.
- Leaves SELL amount as token size.
- Uses the same `place_order` path after conversion.
- `OrderType.FOK` raises `TradingError` when available liquidity cannot fill the requested amount.
- `OrderType.FAK` proceeds with available liquidity where the code path allows it.
- This generic helper has no `pre_submit` parameter and therefore no
  crash-durable restart identity. Restart-safe runtimes should build the exact
  executable `OrderRequest` and call `place_order` with a durability hook.

Examples:

```python
buy = MarketOrderRequest(
    token_id=token_id,
    amount=Decimal("10.00"),
    side=Side.BUY,
    order_type=OrderType.FOK,
)
buy_response = await client.place_market_order(buy, wallet_id="WALLET_0")

sell = MarketOrderRequest(
    token_id=token_id,
    amount=Decimal("25.00"),
    side=Side.SELL,
    order_type=OrderType.FOK,
)
sell_response = await client.place_market_order(sell, wallet_id="WALLET_0")
```

### Reservation accounting

Reservation behavior is part of the live trading contract; see [README.md](README.md#reservation-accounting) for the operating rule. API reference:

- BUY collateral reserve is normalized notional plus the current taker-curve
  fee from `FeeInfo.rate_bps` and `FeeInfo.exponent`.
- Reservation unit is USD collateral as `Decimal`.
- BUY default flow: `_check_and_reserve_buy_balance` preflights AND atomically reserves under `_balance_lock` BEFORE build/sign/submit (closes check-then-reserve TOCTOU).
- BUY with `skip_balance_check=True`: exchange-balance preflight is skipped,
  but the same fee-inclusive amount is still reserved before transport.
- A local failure before durability/transport or a definitive rejection
  releases the tentative reservation. Any outcome that may have landed
  retains it for exact reconciliation.
- Successful live BUY orders keep the fee-inclusive reservation.
- `restore_reserved_balance()` replaces a wallet's process-local total with
  the durable restart-reservation ledger sum (active/unresolved plus terminal
  release debt) during startup; it does not add to the current total.
- Caller releases the exact stored amount after exact fill, cancel, expiry, or
  no-longer-live adjudication:

```python
await client.release_reserved_balance(
    reserved_amount,
    wallet_id="WALLET_0",
    order_id=response.order_id,
)
```

- The production `pre_submit` hook durably associates hash and reservation;
  startup restores unresolved reservations and exact terminal adjudication
  releases them transactionally.
- Over-release raises `BalanceTrackingError`.
- Balance lookup errors during preflight become `TradingError`; order is not submitted.
- SELL validation uses `get_token_balance()`, the authenticated CONDITIONAL
  balance from CLOB balance allowance. Failed or malformed reads propagate;
  they are never treated as zero.
- `place_orders_batch` resolves complete fee metadata for every token before
  submission, preflights total fee-inclusive BUY collateral, and validates
  SELL balances.
- Batch BUY reservations are established before signing/submission and retained
  or released under the same definitive-versus-ambiguous rules above.

### CLOBAPI trading direct methods

Available as `client.clob.<method>`; top-level wrappers fill auth fields from wallet credentials.

| Method | Return | Raises |
|---|---|---|
| `async post_order(signed_order: Dict[str, Any], address: str, api_key: str, api_secret: str, api_passphrase: str, order_type: str = "GTC") -> OrderResponse` | response | `OrderRejectedError`, `InsufficientBalanceError`, `TickSizeError`, `InsufficientAllowanceError`, `OrderDelayedError`, `OrderExpiredError`, `FOKNotFilledError`, `InvalidOrderError`, `MarketNotReadyError`, `AuthenticationError`, `TradingError` |
| `async post_orders_batch(signed_orders: List[Dict[str, Any]], address: str, api_key: str, api_secret: str, api_passphrase: str, order_types: Optional[List[str]] = None) -> List[OrderResponse]` | responses; order types default to GTC and must match input cardinality | `TradingError` |
| `async cancel_order(order_id: str, address: str, api_key: str, api_secret: str, api_passphrase: str) -> bool` | `True` if canceled or already gone | `TradingError` |
| `async cancel_market_orders(market_id: str, address: str, api_key: str, api_secret: str, api_passphrase: str) -> int` | compatibility request helper; not terminal proof | `TradingError` |
| `async cancel_all_orders(address: str, api_key: str, api_secret: str, api_passphrase: str, market_id: Optional[str] = None) -> int` | compatibility request helper; not terminal proof | `TradingError` |
| `async get_orders(address: str, api_key: str, api_secret: str, api_passphrase: str, market: Optional[str] = None) -> List[Order]` | orders | `TradingError` |
| `async get_order(order_id: str, address: str, api_key: str, api_secret: str, api_passphrase: str) -> Optional[Order]` | exact order, including terminal state | `TradingError` |
| `async get_trades(address: str, api_key: str, api_secret: str, api_passphrase: str, *, trade_id: Optional[str] = None, maker_address: Optional[str] = None, market: Optional[str] = None, asset_id: Optional[str] = None, before: Optional[int] = None, after: Optional[int] = None) -> List[ClobTrade]` | paginated authenticated executions | `TradingError` |
| `async get_balances(address: str, api_key: str, api_secret: str, api_passphrase: str, signature_type: int = 0, funder: Optional[str] = None, asset_type: str = "COLLATERAL", token_id: Optional[str] = None) -> Balance` | balance | `TradingError` |
| `async update_balance_allowance(address: str, api_key: str, api_secret: str, api_passphrase: str, signature_type: int = 0, asset_type: str = "COLLATERAL", token_id: Optional[str] = None) -> Dict[str, Any]` | update response | `TradingError` |

### `POST /order` request and response

`CLOBAPI.post_order` body:

```python
{
    "order": signed_order,
    "owner": api_key,
    "orderType": "GTC" | "GTD" | "FOK" | "FAK",
}
```

Notes:

- Uses stdlib `json.dumps()` for the signed order body.
- Uses the same raw JSON string for HMAC and request body.
- Adds `Content-Type: application/json`.
- `retry=False` for order submission.

Order response parse:

```python
{
    "success": bool,
    "orderID": "ORDER_ID",
    "status": "live",
    "errorMsg": None,
    "orderHashes": ["..."],
}
```

Mapped model:

```python
OrderResponse(
    success=success,
    order_id=response.get("orderID"),
    status=OrderStatus(status) if status else None,
    error_msg=response.get("errorMsg"),
    order_hashes=response.get("orderHashes"),
)
```

`success` must be a real boolean. `errorMsg` must be a string or null, success
requires a non-empty `orderID` and no nonblank error, and failure requires a
nonblank error. Malformed shapes raise `TradingError` and remain ambiguous to
the facade. Unknown unsuccessful error text is also ambiguous rather than
being coerced into a definitive rejection.

Error mapping from `errorMsg`:

| Error text contains | Exception |
|---|---|
| `MIN_TICK_SIZE`, `TICK_SIZE` | `TickSizeError` |
| `NOT_ENOUGH_BALANCE`, `INSUFFICIENT` | `InsufficientBalanceError` |
| `ALLOWANCE` | `InsufficientAllowanceError` |
| `EXPIRATION`, `EXPIRED` | `OrderExpiredError` |
| `FOK` and `NOT_FILLED` | `FOKNotFilledError` |
| `ORDER_DELAYED`, `DELAYED` | `OrderDelayedError` |
| `SIZE_TOO_SMALL`, `MINIMUM_SIZE` | `InvalidOrderError` |
| `PRICE_OUT_OF_RANGE`, `INVALID_PRICE` | `InvalidOrderError` |
| `MARKET_CLOSED`, `MARKET_NOT_ACTIVE` | `MarketNotReadyError` |
| `INVALID_SIGNATURE`, `SIGNATURE_FAILED` | `AuthenticationError` |
| `NONCE_TOO_LOW`, `INVALID_NONCE` | `OrderRejectedError(reason="NONCE_CONFLICT")` |
| `ORDER_ALREADY_EXISTS`, `DUPLICATE_ORDER` | `OrderRejectedError(reason="DUPLICATE")` |
| other unsuccessful error | `TradingError` (ambiguous; reconcile exact hash) |

### Cancel order contract

Top-level:

```python
cancelled = await client.cancel_order(order_id, wallet_id="WALLET_0")
```

Underlying CLOB request:

```http
DELETE /order
Content-Type: application/json
```

Body:

```json
{"orderID": "ORDER_ID"}
```

Response shape:

```python
{
    "canceled": ["ORDER_ID"],
    "not_canceled": {
        "OTHER_ORDER_ID": "REASON"
    },
}
```

Return behavior:

- Returns `True` if `order_id` appears in `canceled`.
- Returns `True` if `order_id` appears in `not_canceled` with `NOT_FOUND`.
- Returns `True` for legacy `{"success": true}`.
- Returns `True` for empty 200 response with no canceled/not_canceled data.
- Raises `TradingError` for other not-canceled reasons.

Every `True` value is a request acknowledgement only. It does not prove the
order was cancelled or unfilled; exact `get_order` plus complete authenticated
trade history decides terminal state.
- Raises `TradingError` for unexpected response shape.

### Orders and balances

`get_orders`:

- Uses `GET /data/orders`.
- Starts cursor at `MA==`.
- Stops at `LTE=`.
- Handles paginated dict response: `{"data": [...], "next_cursor": "..."}`.
- Handles legacy list response.
- Normalizes uppercase API statuses to lowercase model values.
- Parses `created_at` as seconds, milliseconds, or ISO string.

`get_order` and `get_trades`:

- `GET /data/order/{id}` is the reconciliation source for both open and terminal orders; a missing order remains unresolved rather than being guessed filled/cancelled.
- `GET /data/trades` is L2-authenticated and cursor-paginated. Fill accounting
  rejects duplicate requested IDs and requires every lookup to return exactly
  one row with that requested trade ID and exactly one local contribution.
  The contributions must total the exchange matched size; incomplete,
  mismatched, or partial associated-trade sets remain retriable. Taker
  platform fees use current `FeeInfo` curve metadata, makers incur zero
  platform fee, and wire/event `fee_rate_bps` remains metadata rather than
  the authoritative economic rate.

`get_balances`:

- Uses `GET /balance-allowance`.
- `asset_type="COLLATERAL"` for pUSD (CLOB V2 collateral; not native USDC or USDC.e).
- `asset_type="CONDITIONAL"` plus `token_id` for CTF tokens.
- Sends `signature_type`.
- Sends `funder` for PROXY wallets.
- Converts integer-style six-decimal CLOB balance strings to USDC `Decimal`.

`update_balance_allowance`:

- Uses `GET /balance-allowance/update`.
- Required after some deposit/allowance changes.
- `token_id` is required by Polymarket when updating a conditional asset.

## 7. Data API

### Address contract

Top-level Data API facade methods take `wallet_id`, resolve credentials, then query:

- EOA wallet: signer EOA address.
- PROXY/MAGIC wallet: `credentials.funder`.

Batch facade methods take wallet addresses directly, not wallet ids.

### Top-level Data API methods

| Method | Return | Raises |
|---|---|---|
| `async get_positions(wallet_id: Optional[str] = None, **kwargs) -> List[Position]` | positions | `ValidationError`, `APIError`, `TimeoutError`, parse errors |
| `async get_trades(wallet_id: Optional[str] = None, **kwargs) -> List[Trade]` | trades | `APIError`, `TimeoutError`, parse errors |
| `async get_activity(wallet_id: Optional[str] = None, **kwargs) -> List[Activity]` | activity | `ValidationError`, `APIError`, `TimeoutError`, parse errors |
| `async get_portfolio_value(wallet_id: Optional[str] = None, market: Optional[str] = None) -> PortfolioValue` | value model | `ValidationError`, `APIError`, `TimeoutError`, parse errors |
| `async get_market_holders(market: str, limit: int = 100, min_balance: int = 1) -> List[Holder]` | holders | `ValidationError`, `APIError`, `TimeoutError`, parse errors |
| `async get_leaderboard(category: str = "OVERALL", time_period: str = "MONTH", order_by: str = "PNL", limit: int = 50, offset: int = 0) -> List[LeaderboardTrader]` | leaderboard (category/timePeriod/orderBy passthrough; server caps: limit ≤50, offset ≤1000) | `APIError`, `TimeoutError`, parse errors |
| `async get_closed_positions(user: str, limit: int = 100, offset: int = 0) -> List[ClosedPosition]` | closed positions with realized PnL (`realized_pnl`, `total_bought`, `avg_price` — live-verified 2026-07-17) | `APIError`, `TimeoutError`, parse errors |
| `async get_positions_batch(wallet_addresses: List[str], **kwargs) -> Dict[str, List[Position]]` | address to positions | fail-soft per address |
| `async get_trades_batch(wallet_addresses: List[str], **kwargs) -> Dict[str, List[Trade]]` | address to trades | fail-soft per address |
| `async get_activity_batch(wallet_addresses: List[str], **kwargs) -> Dict[str, List[Activity]]` | address to activity | fail-soft per address |
| `async aggregate_multi_wallet_metrics(wallet_addresses: List[str], **kwargs) -> Dict[str, Any]` | aggregate metrics | fail-soft via position batch |
| `async detect_signals(wallet_addresses: List[str], min_wallets: int = 5, min_agreement: float = 0.6, **kwargs) -> List[Dict[str, Any]]` | consensus signals | fail-soft via position batch |

### Direct DataAPI methods

Available as `client.data.<method>`.

| Method | Endpoint | Return |
|---|---|---|
| `async get_positions(user: str, market: Optional[str] = None, event_id: Optional[str] = None, size_threshold: float = 1.0, redeemable: Optional[bool] = None, mergeable: Optional[bool] = None, limit: int = 100, offset: int = 0, sort_by: str = "TOKENS", sort_direction: str = "DESC", title: Optional[str] = None) -> List[Position]` | `GET /positions` | positions |
| `async get_positions_complete(user: str, market: Optional[str] = None, event_id: Optional[str] = None, size_threshold: float = 1.0, redeemable: Optional[bool] = None, mergeable: Optional[bool] = None, sort_by: str = "TOKENS", sort_direction: str = "DESC", title: Optional[str] = None) -> List[Position]` | repeated `GET /positions` | authoritative current observation only |
| `async get_trades(...) -> List[Trade]` (compatibility only)<br>`async get_trades_result_v1(user: Optional[str] = None, limit: int = 100, offset: int = 0, taker_only: bool = True, filter_type: Optional[str] = None, filter_amount: Optional[float] = None, market: Optional[str] = None, event_id: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, side: Optional[Side] = None) -> DataTradesResultV1` | `GET /trades` | legacy rows or documented-shape v1 rows with effective query and truthful result metadata |
| `async get_activity(user: str, market: Optional[str] = None, activity_type: Optional[ActivityType] = None, limit: int = 100, offset: int = 0, start: Optional[int] = None, end: Optional[int] = None, side: Optional[Side] = None, sort_by: str = "TIMESTAMP") -> List[Activity]` | `GET /activity` | activity |
| `async get_portfolio_value(user: str, market: Optional[str] = None) -> PortfolioValue` | `GET /value` | value model |
| `async get_holders(market: str, limit: int = 100, min_balance: int = 1) -> List[Holder]` | `GET /holders` | flattened holders |
| `async get_leaderboard(category: str = "OVERALL", time_period: str = "MONTH", order_by: str = "PNL", limit: int = 50, offset: int = 0) -> List[LeaderboardTrader]` | `GET /v1/leaderboard` | traders |
| `async get_closed_positions(user: str, limit: int = 100, offset: int = 0, sort_by: Optional[str] = None, sort_direction: str = "DESC") -> List[ClosedPosition]` | `GET /closed-positions` | closed positions (realized economics; no open-position fields) |

`/closed-positions` ordering (live-verified 2026-07-27): the endpoint's own
default is `sortBy=REALIZEDPNL`, `sortDirection=DESC` — largest realized win
first. Any caller that reads fewer rows than the wallet has therefore gets a
winner-biased sample, not a history: win rates computed from it come back at
1.00 and realized P&L contains no losses. Pass `sort_by="TIMESTAMP"` for a
chronological read. Accepted `sortBy` values are `REALIZEDPNL`, `AVGPRICE`,
`PRICE`, `TITLE`, `TIMESTAMP`; the facade rejects anything else before
transport, and the endpoint itself answers `400` with the same list, so an
unsupported value can never silently fall back to the default ordering.

Validation:

- `get_positions(user=...)` requires an address beginning with `0x`.
- `get_activity(user=...)` requires an address beginning with `0x`.
- `get_portfolio_value(user=...)` requires an address beginning with `0x`.
- `get_holders(market=...)` requires a non-empty condition id.
- Legacy limits are capped to `500` for positions, trades, activity, and holders; `get_trades_result_v1` uses the documented `10,000` cap.
- Position offset is capped to `10000`.
- Position title filter is truncated to 100 chars.

`get_positions_complete()` uses fixed 500-row pages, probes past every exact
page boundary, and requires two complete passes to agree on canonical
`(condition_id, asset, outcome, size)` custody state. Strict parsing, wallet
identity, duplicate identity, pass mutation, transport errors, and a full page
at the offset ceiling all raise. It never returns a partial collection as an
empty or authoritative portfolio.

`get_portfolio_value` normalization:

- `/value` may return a list with one item.
- `/value` may return a dict.
- `/value` may return a number.
- Missing `user` is filled from the request address.
- Missing `value` is filled from `equityTotal` or `equity_total`, else `0`.

Portfolio shape:

```python
PortfolioValue(
    user="0x...",
    value=Decimal("123.45"),
    bets=Decimal("100.00"),
    cash=Decimal("23.45"),
    equity_total=Decimal("123.45"),
)
```

`get_holders` normalization:

Raw API shape:

```python
[
    {
        "token": "TOKEN_ID",
        "holders": [
            {"proxyWallet": "0x...", "amount": "10.5", "outcomeIndex": 0}
        ],
    }
]
```

Returned shape:

```python
[
    Holder(
        proxy_wallet="0x...",
        amount=Decimal("10.5"),
        outcome_index=0,
        token_id="TOKEN_ID",
    )
]
```

Leaderboard:

- Endpoint is `GET /v1/leaderboard`.
- Not `GET /leaderboard`.
- Code applies `min_pnl` client-side.
- Code stops after `limit` accepted traders.

Multi-wallet aggregate shape:

```python
{
    "total_wallets": int,
    "total_positions": int,
    "total_pnl": Decimal | float,
    "total_value": Decimal | float,
    "avg_pnl_per_wallet": Decimal | float,
    "top_performers": [
        {"wallet": "0x...", "pnl": ..., "value": ...}
    ],
    "wallet_summaries": {
        "0x...": {
            "total_pnl": ...,
            "unrealized_pnl": ...,
            "realized_pnl": ...,
            "total_value": ...,
            "position_count": int,
        }
    },
}
```

Consensus signal shape:

```python
{
    "market": "market-slug",
    "title": "Market title",
    "outcome": "Yes",
    "wallet_count": int,
    "agreement_ratio": float,
    "total_value": Decimal | float,
    "wallets": ["0x..."],
}
```

## 8. WebSocket

### Top-level CLOB WebSocket facade

These methods are synchronous subscription and telemetry wrappers.

| Method | Return or callback | Raises |
|---|---|---|
| `subscribe_orderbook(token_id: str, callback: Callable[[OrderBook], None], wallet_id: Optional[str] = None) -> None`<br>`subscribe_clob_market_last_trade_price(token_id: str, callback: Callable[[LastTradePriceMessage], None]) -> None` | official CLOB Market Channel `OrderBook` or typed last-trade message; same-token callbacks share one subscription | `RuntimeError` if the facade transport is already USER; callback errors are logged; request shutdown/unsubscribe from the owner thread, not from a synchronous callback |
| `subscribe_user_orders(callback: Callable[[Any], None], wallet_id: Optional[str] = None, on_failure_callback: Optional[Callable[[str], None]] = None) -> None` | typed CLOB WS message; optional permanent-transport-failure callback | `ValueError` if user credentials are incomplete; `RuntimeError` if the facade transport is already MARKET; `AuthenticationError` if it is bound to another USER wallet |
| `unsubscribe_all() -> None` | none | disconnect errors propagate after local handle/callback cleanup |
| `is_websocket_connected() -> bool` | actual transport-open state, not worker-thread state | none |
| `get_clob_websocket_telemetry_v1() -> WebSocketTelemetrySnapshotV1` | frozen identifier-free counters for transport, receipts/parsing, queue pressure/drops, callbacks, reconnects/silences, and local send failures; zero snapshot before first transport and retained final snapshot after disconnect | none; reconnect silence is locally observed downtime, not source-sequence completeness |

Official `last_trade_price` messages bypass content-hash dedupe because the documented payload has no stable event ID; API-B must establish replay and identity semantics before loss-bearing dedupe.

Public Market wire contract: initial `{"type":"market","assets_ids":[...],"custom_feature_enabled":false}`; dynamic subscribe/unsubscribe uses `operation` plus `assets_ids` (subscribe also carries `custom_feature_enabled=false`).
Incoming frames may be one object or an array: `messages_received` counts wire frames, while parsed/unknown/failure and delivery counters count decoded elements.
Official last-trade parsing requires `asset_id`, `market`, `price`, and `side`;
optional `size`, `fee_rate_bps`, `timestamp`, and `transaction_hash` fields
are preserved when present and represented as `None` when omitted.
`fee_rate_bps` is wire metadata only and is not authoritative economic fee
data for sizing, fill attribution, or P&L.

The endpoint and channel are fixed for one WebSocket transport: MARKET and
USER subscriptions cannot share it. A USER transport is also bound to one
concrete wallet identity. Use separate `PolymarketClient` instances (and
therefore separate transports) for MARKET versus USER, or for different USER
wallets.

Orderbook callback conversion:

- Accepts `OrderbookMessage` only.
- Converts `message.buys` and `message.sells` into `OrderBook.bids` and `OrderBook.asks`.
- Uses `Decimal(level.price)` and `Decimal(level.size)`.

### WebSocketClient direct methods

Available from `polymarket.api.websocket.WebSocketClient`.

| Method | Return | Notes |
|---|---|---|
| `__init__(..., api_key: Optional[str] = None, api_secret: Optional[str] = None, api_passphrase: Optional[str] = None, reconnect_silence_threshold_seconds: float = 0.0, reconnect_silence_history_size: int = 100) -> None` | client | USER auth requires all three credentials; one transport may carry MARKET or USER, never both |
| `connect(event_loop: Optional[asyncio.AbstractEventLoop] = None) -> None` | `None` | starts background thread |
| `wait_until_connected(timeout: float) -> bool` | transport-open result | waits on the actual socket-open event |
| `disconnect() -> None` | `None` | closes socket and consumer |
| `subscribe_market(token_id: str, callback: Callable[[WebSocketMessage], None]) -> None` | `None` | public market channel; one initial aggregate frame per open, then dynamic updates only for new assets |
| `subscribe_user(callback: Callable[[WebSocketMessage], None]) -> None` | `None` | sends `{"auth":{"apiKey","secret","passphrase"},"markets":[],"type":"user"}` and replays it after reconnect |
| `subscribe_markets_multi(token_ids: list[str], callback: Callable[[WebSocketMessage], None]) -> None` | `None` | one dynamic subscription update containing only newly registered assets |
| `subscribe_markets_batch(token_ids: list[str], callback: Callable[[WebSocketMessage], None]) -> Dict[str, Any]` | result dict | rollback on partial failure |
| `unsubscribe(channel: str) -> None` | `None` | channel key |
| `telemetry_snapshot_v1() -> WebSocketTelemetrySnapshotV1` | frozen aggregate snapshot | no identifiers; bounded silence history plus truncation count |
| `stats() -> Dict[str, Any]` | stats | queue and dedup fields included when enabled |
| `health_check() -> Dict[str, str]` | health | healthy/degraded/disconnected |
| `__enter__()` | self | connects |
| `__exit__(exc_type, exc_val, exc_tb)` | `None` | disconnects |

Batch subscription result shape:

```python
{
    "success": True,
    "succeeded": ["TOKEN_ID"],
    "failed": [],
    "error": None,
}
```

### CLOB WebSocket message types

Parser:

```python
def parse_websocket_message(data: dict) -> Optional[WebSocketMessage]: ...
```

Return union:

- `OrderbookMessage`
- `PriceChangeMessage`
- `TickSizeChangeMessage`
- `LastTradePriceMessage`
- `TradeMessage`
- `OrderMessage`
- `None` for missing or unknown `event_type`

Parser raises:

- `ValueError` when required fields are missing.
- `ValueError` for unsupported legacy `price_change` schema.
- `ValueError` for invalid enum values.

Market channel dataclasses:

| Dataclass | Key fields |
|---|---|
| `OrderLevel` | `price: str`, `size: str`, `to_decimal() -> tuple[Decimal, Decimal]` |
| `OrderbookMessage` | `event_type`, `asset_id`, `market`, `timestamp`, `hash`, `buys`, `sells`, `best_bid`, `best_ask`, `spread` |
| `PriceChange` | `asset_id`, `price`, `size`, `side`, `hash`, `best_bid`, `best_ask` |
| `PriceChangeMessage` | `event_type`, `market`, `timestamp`, `price_changes`, `schema_version="v2"` |
| `TickSizeChangeMessage` | `event_type`, `asset_id`, `market`, `old_tick_size`, `new_tick_size`, `side`, `timestamp` |
| `LastTradePriceMessage` | required `event_type`, `asset_id`, `market`, `price`, `side`; optional `size`, `fee_rate_bps`, `timestamp`, `transaction_hash` |

User channel dataclasses:

| Dataclass | Key fields |
|---|---|
| `MakerOrder` | `asset_id`, `matched_amount`, `order_id`, `outcome`, `owner`, `price` |
| `TradeMessage` | `event_type`, `type`, `id`, `asset_id`, `market`, `status`, `side`, `size`, `price`, `outcome`, `owner`, `trade_owner`, `taker_order_id`, `maker_orders`, `timestamp`, `last_update`, `matchtime` |
| `OrderMessage` | `event_type`, `type`, `id`, `asset_id`, `market`, `outcome`, `side`, `price`, `original_size`, `size_matched`, `owner`, `order_owner`, `associate_trades`, `timestamp` |

WebSocket enums:

| Enum | Values |
|---|---|
| `CLOBEventType` | `book`, `trade`, `order`, `price_change`, `tick_size_change`, `last_trade_price` |
| `TradeStatus` | `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`, `FAILED` |
| `OrderEventType` | `PLACEMENT`, `UPDATE`, `CANCELLATION` |

### Top-level RTDS facade

These methods are synchronous subscription wrappers.

| Method | Topic/type | Filters | Raises |
|---|---|---|---|
| `subscribe_activity_trades(callback: Callable[[Message], None], market_slug: Optional[str] = None, event_slug: Optional[str] = None) -> None` | `activity` / `trades` | market or event slug | `ValueError`, `RuntimeError` |
| `subscribe_activity_orders_matched(callback: Callable[[Message], None], market_slug: Optional[str] = None) -> None` | `activity` / `orders_matched` | market slug | `RuntimeError` |
| `subscribe_market_created(callback: Callable[[Message], None]) -> None` | `clob_market` / `market_created` | none | `RuntimeError` |
| `subscribe_market_resolved(callback: Callable[[Message], None]) -> None` | `clob_market` / `market_resolved` | none | `RuntimeError` |
| `subscribe_market_price_changes(callback: Callable[[Message], None], token_ids: List[str]) -> None` | `clob_market` / `price_change` | token ids JSON list | `ValueError`, `RuntimeError` |
| `unsubscribe_market_price_changes(token_ids: List[str]) -> None` | `clob_market` / `price_change` | token ids JSON list | `ValueError` |
| `subscribe_market_orderbook_rtds(callback: Callable[[Message], None], token_ids: List[str]) -> None` | `clob_market` / `agg_orderbook` | token ids JSON list | `ValueError`, `RuntimeError` |
| `subscribe_comments(callback: Callable[[Message], None], parent_entity_id: Optional[int] = None, parent_entity_type: str = "Event") -> None` | `comments` / `*` | parent entity | `RuntimeError` |
| `subscribe_reactions(callback: Callable[[Message], None], parent_entity_id: Optional[int] = None) -> None` | `comments` / `reaction_*` | parent entity | `RuntimeError` |
| `subscribe_rfq_requests(callback: Callable[[Message], None], market: Optional[str] = None) -> None` | `rfq` / `request_*` | market | `RuntimeError` |
| `subscribe_rfq_quotes(callback: Callable[[Message], None], request_id: Optional[str] = None) -> None` | `rfq` / `quote_*` | request id | `RuntimeError` |
| `subscribe_crypto_prices(callback: Callable[[Message], None], symbol: str = "btcusdt") -> None` | `crypto_prices` / `update` | symbol | `ValueError`, `RuntimeError` |
| `subscribe_crypto_prices_chainlink(callback: Callable[[Message], None], symbol: str = "btcusdt") -> None` | `crypto_prices_chainlink` / `update` | symbol | `ValueError`, `RuntimeError` |
| `subscribe_market_last_trade_price(callback: Callable[[Message], None], token_ids: List[str]) -> None` | legacy undocumented RTDS `clob_market` / `last_trade_price` (not the official CLOB Market Channel facade above) | token ids JSON list | `ValueError`, `RuntimeError` |
| `subscribe_market_tick_size_change(callback: Callable[[Message], None], token_ids: List[str]) -> None` | `clob_market` / `tick_size_change` | token ids JSON list | `ValueError`, `RuntimeError` |
| `unsubscribe_rtds_all() -> None` | disconnect | none | logs errors |

RTDS facade validation:

- `_ensure_rtds()` raises `RuntimeError` if `settings.enable_rtds` is false.
- Activity trades reject both `market_slug` and `event_slug` together.
- Token-id subscriptions reject empty lists.
- Crypto symbols must be one of `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`.
- Callback exceptions are caught and logged.

### RealTimeDataClient direct methods

Available from `polymarket.api.real_time_data.RealTimeDataClient`.

| Method | Return | Notes |
|---|---|---|
| `__init__(host: Optional[str] = None, on_connect: Optional[Callable[[RealTimeDataClient], None]] = None, on_message: Optional[Callable[[RealTimeDataClient, Message], None]] = None, on_status_change: Optional[Callable[[ConnectionStatus], None]] = None, auto_reconnect: bool = True, ping_interval: float = DEFAULT_PING_INTERVAL) -> None` | client | constructor |
| `connect() -> RealTimeDataClient` | self | starts background thread |
| `disconnect()` | `None` | disables reconnect and closes socket |
| `subscribe(topic: str, type: str = "*", filters: Optional[str] = None, clob_auth: Optional[ClobApiKeyCreds] = None)` | `None` | logs if disconnected |
| `unsubscribe(topic: str, type: str = "*", filters: Optional[str] = None)` | `None` | logs if disconnected |
| `get_status() -> ConnectionStatus` | status | local state |
| `stats() -> dict` | stats | connection metrics |

RTDS dataclasses:

| Dataclass | Fields |
|---|---|
| `ClobApiKeyCreds` | `key`, `secret`, `passphrase` |
| `Subscription` | `topic`, `type`, `filters`, `clob_auth` |
| `Message` | `topic`, `type`, `timestamp`, `payload`, `connection_id` |

RTDS status enum:

| Name | Value |
|---|---|
| `ConnectionStatus.CONNECTING` | `CONNECTING` |
| `ConnectionStatus.CONNECTED` | `CONNECTED` |
| `ConnectionStatus.DISCONNECTED` | `DISCONNECTED` |

## 9. Models and enums

### Core enums

| Enum | Values |
|---|---|
| `Side` | `BUY`, `SELL` |
| `OrderType` | `GTC`, `GTD`, `FOK`, `FAK` |
| `OrderStatus` | `live`, `pending`, `filled`, `matched`, `cancelled`, `expired`, `rejected`, `delayed`, `unmatched` |
| `SignatureType` | `EOA=0`, `POLY_PROXY=1` (`MAGIC` alias), `GNOSIS_SAFE=2` (`PROXY` alias), `POLY_1271=3` |
| `ActivityType` | `TRADE`, `SPLIT`, `MERGE`, `REDEEM`, `REWARD`, `CONVERSION`, `MAKER_REBATE`, `YIELD` |

### Decimal behavior

- Financial fields generally use `Decimal`.
- Validators accept `Decimal`, `str`, `int`, and `float`.
- Floats are converted through `str(value)`.
- `OrderRequest.price` preserves the exact finite `Decimal` and requires
  `0 < price < 1`; current per-token tick handling occurs in the facade.
- `OrderRequest.size` is quantized to `Decimal("0.01")`.
- `OrderBook.midpoint` is exact `(best_bid + best_ask) / 2` arithmetic.
- `OrderBook.spread` is quantized to `Decimal("0.0001")`.
- `Position` invalid/empty numeric strings become `Decimal("0.0")`.
- `Market.volume` and `Market.liquidity` bad/missing values become `Decimal("0.0")`.
- `Market` optional numeric invalid values become `None`.
- `Event.volume`, `Event.liquidity`, and `Event.volume_24h` are `float`.

### OrderRequest

```python
class OrderRequest(BaseModel):
    token_id: str
    price: Decimal
    size: Decimal
    side: Side
    order_type: OrderType = OrderType.GTC
    expiration: Optional[int] = None
```

Constraints:

- `Decimal("0") < price < Decimal("1")`
- `size > 0`
- `expiration` is Unix timestamp for GTD orders.
- Enums remain enum objects; no `use_enum_values`.

Before signing, the facade resolves the token's current tick and requires the
normalized price to satisfy `tick <= price <= 1 - tick`. BUY prices floor and
SELL prices ceiling to the tick so normalization does not cross the caller's
adverse-price bound. Direct `OrderBuilder` calls reject off-grid prices.

### MarketOrderRequest

```python
class MarketOrderRequest(BaseModel):
    token_id: str
    amount: Decimal
    side: Side
    order_type: OrderType = OrderType.FOK
```

Constraints and semantics:

- `amount > 0`
- BUY amount is USD to spend.
- SELL amount is tokens/shares to sell.
- `model_config = ConfigDict(use_enum_values=True)`.

### OrderResponse

```python
class OrderResponse(BaseModel):
    success: bool
    order_id: Optional[str] = None
    status: Optional[OrderStatus] = None
    error_msg: Optional[str] = None
    definitive_rejection: Optional[bool] = None
    order_hashes: Optional[list[str]] = None
    trade_ids: Optional[list[str]] = None
```

Notes:

- `model_config = ConfigDict(use_enum_values=True)`.
- `order_id` maps from CLOB `orderID` in single-order responses.
- `definitive_rejection` is populated for unsuccessful batch items: `True`
  proves the item did not land, while `False` retains it for exact
  reconciliation.
- `order_hashes` maps from CLOB `orderHashes`.
- `trade_ids` maps current successful FAK/FOK `tradeIDs` responses.

### Order

```python
class Order(BaseModel):
    id: str
    market: str
    token_id: str
    price: Decimal
    original_size: Decimal
    size_matched: Decimal = Decimal("0")
    side: Side
    status: OrderStatus
    created_at: datetime
    expiration: Optional[datetime] = None
    owner: Optional[str] = None
    maker_address: Optional[str] = None
    outcome: Optional[str] = None
    order_type: Optional[str] = None
    associate_trades: list[str] = Field(default_factory=list)
```

Validators:

- `asset_id`/`token_id` and `original_size`/`size` are accepted as wire/legacy aliases.
- `price`, `original_size`, and `size_matched` convert to `Decimal`; documented status prefixes and US/UK cancellation spellings normalize.
- `model_config = ConfigDict(use_enum_values=True)`.

### Position

```python
class Position(BaseModel):
    proxy_wallet: str = Field(..., alias="proxyWallet")
    asset: str
    condition_id: str = Field(..., alias="conditionId")
    size: Decimal
    avg_price: Decimal = Field(..., alias="avgPrice")
    current_value: Decimal = Field(..., alias="currentValue")
    initial_value: Decimal = Field(..., alias="initialValue")
    cur_price: Decimal = Field(..., alias="curPrice")
    cash_pnl: Decimal = Field(..., alias="cashPnl")
    percent_pnl: Decimal = Field(..., alias="percentPnl")
    realized_pnl: Decimal = Field(default=Decimal("0.0"), alias="realizedPnl")
    percent_realized_pnl: Decimal = Field(default=Decimal("0.0"), alias="percentRealizedPnl")
    title: str
    slug: str
    icon: Optional[str] = None
    outcome: str
    outcome_index: int = Field(..., alias="outcomeIndex")
    opposite_outcome: str = Field(..., alias="oppositeOutcome")
    end_date: Optional[str] = Field(None, alias="endDate")
    redeemable: bool = False
    mergeable: bool = False
    negative_risk: bool = Field(default=False, alias="negativeRisk")
```

Validators:

- CamelCase Data API fields populate snake_case attributes.
- `populate_by_name=True`.
- Numeric invalid strings, empty strings, `null`, `None`, and `NaN`-like values become `Decimal("0.0")`.

### Trade and public-flow v1 results

```python
class Trade(BaseModel):
    id: str
    market: str
    condition_id: str = Field(..., alias="conditionId")
    asset: str
    side: Side
    size: Decimal
    price: Decimal
    fee_rate_bps: int = Field(..., alias="feeRateBps")
    timestamp: int
    transaction_hash: Optional[str] = Field(None, alias="transactionHash")
    maker_address: Optional[str] = Field(None, alias="makerAddress")
    maker_pseudonym: Optional[str] = Field(None, alias="makerPseudonym")
    taker_address: Optional[str] = Field(None, alias="takerAddress")
    taker_pseudonym: Optional[str] = Field(None, alias="takerPseudonym")
```

Validators:

- `size` and `price` convert to `Decimal`.
- `populate_by_name=True`.
- `use_enum_values=True`.

`DataTradeV1` preserves documented Data `/trades` attribution and exact decimals. All three v1 results include `PublicRequestEvidenceV1` (wall time, attempt/retry and observed rate-limit-error counts, key, retry ceiling, and local-limiter state). Data completeness is true only for a clean, in-bounds, short offset-zero page with explicit time bounds. `MarketTradeEventV1` requires documented user/condition/asset/side/size/price/time/transaction semantics; event retention remains unknown and its 1,000-row/1 MiB ceilings are post-decode acceptance bounds, not streaming wire limits. Event error category `serialization` means local canonical decoded-response sizing; upstream JSON decode failures remain `request`. Price history reports explicit boundary/order/duplicate/out-of-range/gap diagnostics and is complete only on a clean fidelity grid. `PriceHistoryPointV1` retains finite `[0, 1]` and non-negative-time validation; a null `history` envelope is an error.

### Activity

```python
class Activity(BaseModel):
    timestamp: int
    type: ActivityType
    transaction_hash: str = Field(..., alias="transactionHash")
    size: Decimal
    usdc_size: Decimal = Field(..., alias="usdcSize")
    proxy_wallet: Optional[str] = Field(None, alias="proxyWallet")
    condition_id: Optional[str] = Field(None, alias="conditionId")
    asset: Optional[str] = None
    title: Optional[str] = None
    outcome: Optional[str] = None
    outcome_index: Optional[int] = Field(None, alias="outcomeIndex")
    slug: Optional[str] = None
    event_slug: Optional[str] = Field(None, alias="eventSlug")
    icon: Optional[str] = None
    side: Optional[Side] = None
    price: Optional[Decimal] = None
    name: Optional[str] = None
    pseudonym: Optional[str] = None
    bio: Optional[str] = None
    profile_image: Optional[str] = Field(None, alias="profileImage")
```

Validators:

- Empty `side` string coerces to `None`.
- `side=None` remains `None`.
- `size`, `usdc_size`, and `price` convert to `Decimal`.
- Numeric `None` remains `None`.
- `populate_by_name=True`.
- `use_enum_values=True`.

### PortfolioValue

```python
class PortfolioValue(BaseModel):
    user: str
    value: Decimal
    bets: Optional[Decimal] = None
    cash: Optional[Decimal] = None
    equity_total: Optional[Decimal] = Field(None, alias="equityTotal")
```

Validators:

- `value`, `bets`, `cash`, and `equity_total` convert to `Decimal`.
- `None` optional numerics remain `None`.
- `populate_by_name=True`.

### Holder

```python
class Holder(BaseModel):
    proxy_wallet: str = Field(..., alias="proxyWallet")
    amount: Decimal
    outcome_index: int = Field(..., alias="outcomeIndex")
    token_id: Optional[str] = None
    asset: Optional[str] = None
    pseudonym: Optional[str] = None
    name: Optional[str] = None
    bio: Optional[str] = None
    profile_image: Optional[str] = Field(None, alias="profileImage")
    profile_image_optimized: Optional[str] = Field(None, alias="profileImageOptimized")
    display_username_public: bool = Field(False, alias="displayUsernamePublic")
    verified: bool = False
```

Validators:

- `amount` converts to `Decimal`.
- `/holders` parser flattens token groups and adds `token_id`.
- `populate_by_name=True`.

### LeaderboardTrader

```python
class LeaderboardTrader(BaseModel):
    rank: str
    user_id: str = Field(..., validation_alias=AliasChoices("user_id", "proxyWallet"))
    user_name: str = Field(..., validation_alias=AliasChoices("user_name", "userName"))
    vol: Decimal
    pnl: Decimal
    profile_image: Optional[str] = Field(None, validation_alias=AliasChoices("profile_image", "profileImage"))
    x_username: Optional[str] = Field(None, alias="xUsername")
    verified_badge: Optional[bool] = Field(None, alias="verifiedBadge")
```

Validators:

- `vol` and `pnl` convert to `Decimal`.
- Accepts snake_case and Polymarket camelCase profile fields.
- `populate_by_name=True`.

### Balance

```python
class Balance(BaseModel):
    collateral: Decimal
    tokens: dict[str, Decimal] = Field(default_factory=dict)
```

Validators:

- `collateral` converts to `Decimal`.
- `tokens` converts each token balance to `Decimal`.
- Non-dict `tokens` becomes `{}`.
- Unsupported token balance values become `Decimal("0.0")`.

### Market

Core fields:

```python
class Market(BaseModel):
    id: str
    question: str
    slug: str
    condition_id: str
    category: str
    outcomes: list[str]
    outcome_prices: list[Decimal]
    volume: Decimal
    liquidity: Decimal
    active: bool
    closed: bool
    tokens: Optional[list[str]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
```

Key optional fields and aliases:

| Attribute | Alias | Notes |
|---|---|---|
| `rewards_min_size` | `rewardsMinSize` | reward min size |
| `rewards_max_spread` | `rewardsMaxSpread` | reward max spread |
| `ticker` | none | short code |
| `new` | none | newly created flag |
| `featured` | none | featured flag |
| `restricted` | none | access restriction flag |
| `archived` | none | archived flag |
| `neg_risk` | `negRisk` | neg-risk market |
| `enable_neg_risk` | `enableNegRisk` | neg-risk enabled |
| `neg_risk_augmented` | `negRiskAugmented` | incomplete outcome universe |
| `neg_risk_market_id` | `negRiskMarketID` | adapter market id |
| `neg_risk_request_id` | `negRiskRequestID` | adapter request id |
| `group_item_title` | `groupItemTitle` | grouped market resolution/date label |
| `group_item_threshold` | `groupItemThreshold` | grouped ordering threshold |
| `best_bid` | `bestBid` | current bid |
| `best_ask` | `bestAsk` | current ask |
| `spread` | none | current spread |
| `last_trade_price` | `lastTradePrice` | last price |
| `competitive` | none | competitiveness score |
| `order_min_size` | `orderMinSize` | min order size |
| `order_price_min_tick_size` | `orderPriceMinTickSize` | price tick |
| `accepting_orders` | `acceptingOrders` | accepts orders |
| `fees_enabled` | `feesEnabled` | whether Gamma marks fees enabled |
| `taker_base_fee` | `takerBaseFee` | protocol base-fee metadata |
| `fee_schedule` | `feeSchedule` | Gamma economic curve representation |
| `question_id` | `questionID` | UMA question id |
| `uma_bond` | `umaBond` | UMA bond |
| `uma_reward` | `umaReward` | UMA reward |
| `resolution_source` | `resolutionSource` | source URL/text |
| `volume_24h` | `volume24hr` | 24-hour volume |
| `volume_1wk` | `volume1wk` | 1-week volume |
| `volume_1mo` | `volume1mo` | 1-month volume |
| `one_hour_price_change` | `oneHourPriceChange` | 1-hour price change (Decimal; invalid -> None) |
| `one_day_price_change` | `oneDayPriceChange` | 1-day price change (Decimal; invalid -> None) |
| `submitted_by` | `submitted_by` | submitter address |
| `resolved_by` | `resolvedBy` | resolver address |
| `has_reviewed_dates` | `hasReviewedDates` | date review flag |

Validators:

- `outcomes` parses JSON strings.
- `outcome_prices` parses JSON strings and converts entries to `Decimal`.
- Unsupported outcome price entries become `Decimal("0.0")`.
- `volume` and `liquidity` convert to `Decimal`.
- Missing `volume` and `liquidity` become `Decimal("0.0")`.
- Optional numeric fields convert to `Decimal`.
- Invalid optional numeric strings become `None`.
- `tokens` parses JSON strings.
- `populate_by_name=True`.

Grouped market note:

- Use `group_item_title` for grouped market resolution/date labels.
- Do not use `end_date` as that label.

### FeeSchedule and FeeInfo

```python
class FeeSchedule(BaseModel):
    rate: Decimal
    exponent: Decimal
    taker_only: bool = True
    rebate_rate: Decimal = Decimal("0")


class FeeInfo(BaseModel):
    base_fee_bps: int
    rate_bps: int
    exponent: Decimal = Decimal("1")
    taker_only: bool = True
    rebate_rate: Decimal = Decimal("0")
```

`FeeSchedule` represents the economic curve. `FeeInfo` combines that curve
with the separate `/fee-rate` protocol value. Cost code consumes
`rate_bps`/`exponent`, never `base_fee_bps`.

### Event

```python
class Event(BaseModel):
    id: str
    slug: str
    title: str
    description: Optional[str] = None
    ticker: Optional[str] = None
    active: bool
    closed: bool
    archived: bool
    new: Optional[bool] = None
    featured: Optional[bool] = None
    restricted: Optional[bool] = None
    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")
    markets: list[Market] = Field(default_factory=list)
    neg_risk: Optional[bool] = Field(None, alias="negRisk")
    volume: float = 0.0
    liquidity: float = 0.0
    volume_24h: Optional[float] = Field(None, alias="volume24hr")
```

Validators:

- `markets` default to `[]`.
- Comma-separated string `markets` split into list items.
- `populate_by_name=True`.

### OrderBook

```python
class OrderBook(BaseModel):
    token_id: str
    bids: list[tuple[Decimal, Decimal]] = Field(default_factory=list)
    asks: list[tuple[Decimal, Decimal]] = Field(default_factory=list)
    market: Optional[str] = None
    tick_size: Optional[Decimal] = None
    neg_risk: Optional[bool] = None
    timestamp: Union[datetime, int] = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Properties:

| Property | Return |
|---|---|
| `best_bid` | first bid price or `None` |
| `best_ask` | first ask price or `None` |
| `midpoint` | exact average of best bid/ask, or `None` |
| `spread` | best ask minus best bid quantized to `0.0001`, or `None` |

Validator:

- `tick_size` converts to `Decimal`.
- Unsupported `tick_size` becomes `None`.

## 10. Utility functions (validation, fees, CTF)

Public-exported helpers from `polymarket.utils.*` and `polymarket.ctf.*`. Every name listed here is exported via `polymarket.__init__` and is part of the stable surface.

### Validation helpers

Source: `polymarket/utils/validation.py`.

```python
from polymarket import (
    validate_order,
    validate_price_bounds,
    validate_size,
    validate_fee_rate,
    validate_token_complementarity,
    validate_neg_risk_market,
    validate_balance,
    validate_order_amounts,
    check_order_profitability,
)
```

| Function | Returns | Semantics |
|---|---|---|
| `validate_order(order: OrderRequest) -> tuple[bool, Optional[str]]` | `(True, None)` or `(False, error)` | Composite: price bounds, size, fee rate, GTD expiration, token id. |
| `validate_price_bounds(price: Decimal) -> bool` | `True` or raises `ValidationError` | Price must be strictly inside `(0, 1)`; market tick bounds are applied later. |
| `validate_size(size: Decimal, min_size: Decimal = MIN_SIZE) -> bool` | `True` or raises | Size must be > `min_size`. |
| `validate_fee_rate(fee_rate_bps: int) -> bool` | `True` or raises | Validates the market-provided fee rate against supported bounds. |
| `validate_token_complementarity(token_id_1: str, token_id_2: str, market: Optional[Market] = None) -> bool` | `True` or raises | Sanity check for YES/NO pairs. Full on-chain check still needs CTF. |
| `validate_neg_risk_market(market: Market) -> bool` | `True` or raises | Ensures neg-risk markets meet structural constraints. |
| `validate_balance(side: Side, price: Decimal, size: Decimal, available_usdc: Decimal, available_tokens: Decimal = Decimal("0"), fee_rate_bps: int = 0) -> tuple[bool, Optional[str]]` | Pair | Off-chain balance check for BUY (USDC) or SELL (tokens); reuses `validate_price_bounds` and `validate_size` internally. |
| `validate_order_amounts(maker_amount: Decimal, taker_amount: Decimal, min_amount: Decimal = Decimal("0.01")) -> bool` | `True` or raises | Signed-order sanity check. |
| `check_order_profitability(entry_price: Decimal, exit_price: Decimal, size: Decimal, fee_rate_bps: int, min_profit_usdc: Decimal = Decimal("0.10")) -> tuple[bool, Decimal]` | `(profitable, net_profit)` | Round-trip profitability for strategy pre-checks. |

All raise-on-invalid helpers raise `polymarket.exceptions.ValidationError`.

### Fee helpers

Source: `polymarket/utils/fees.py`.

```python
from polymarket import (
    calculate_order_fee,
    calculate_net_cost,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    calculate_profit_after_fees,
    get_effective_spread,
)
```

Fee-free markets coexist with fee-enabled categories. `calculate_order_fee`
implements the current taker curve; its `size` argument is USD notional. Pass
`FeeInfo.rate_bps` and `FeeInfo.exponent`, never raw
`FeeInfo.base_fee_bps`/`get_fee_rate_bps()`. Makers incur zero platform fee.
Authenticated associated-trade economics take precedence over estimates, but
incomplete associated-trade sets remain retriable rather than being recorded
as partial truth.

| Function | Returns |
|---|---|
| `calculate_order_fee(side: Side, price: Decimal, size: Decimal, fee_rate_bps: int = 0, fee_exponent: Decimal = Decimal("1")) -> Decimal` | Current taker-curve fee for USD notional, rounded to five decimals; values below `0.00001` become zero. |
| `calculate_net_cost(side: Side, price: Decimal, size: Decimal, fee_rate_bps: int = 0, fee_exponent: Decimal = Decimal("1")) -> tuple[Decimal, Decimal]` | `(BUY total cost or SELL net proceeds, fee)` pair for USD notional. |
| `compare_fees_buy_vs_sell(...) -> dict` | Summary of fee impact on both sides. |
| `estimate_breakeven_exit(...) -> tuple[Decimal, Decimal]` | Exit price needed to cover entry + fees, plus total fees at that price. |
| `calculate_profit_after_fees(...) -> Dict[str, Any]` | Metrics dict containing gross/net profit, fees, ROI, costs/proceeds, and token count. |
| `get_effective_spread(...) -> Dict[str, Any]` | Raw/effective spread metrics including both fee legs. |

See the docstrings for the full argument lists; names and defaults are stable.

### CTF and Neg-Risk

Source: `polymarket/ctf/`.

```python
from polymarket import (
    NegRiskAdapter,
    ConversionCalculator,
    is_safe_to_trade,
    NEG_RISK_ADAPTER,
    NEG_RISK_EXCHANGE,
    CTF_ADDRESS,
)
```

Constants point at mainnet Polygon contracts and are for read-only reference.

`NegRiskAdapter(web3_provider: str = "https://polygon.drpc.org")` — on-chain operations for neg-risk positions. Every method that sends a transaction needs a private key; the constructor only holds a Web3 provider.

| Method | Signature | Behavior |
|---|---|---|
| `check_ctf_approval(wallet_address: str) -> Optional[bool]` | read-only | `None` when state cannot be read. |
| `approve_ctf_tokens(private_key: str, gas_price_gwei: int = 50) -> str` | sends tx, returns tx hash | Sets `setApprovalForAll(NegRiskAdapter, True)`. Required once before other ops. |
| `get_ctf_balance(wallet_address: str, position_id: int) -> int` | read-only | Raw on-chain balance for a position. |
| `convert_positions(private_key: str, condition_id: str, index_set: int, amount: int, gas_price_gwei: int = 50) -> str` | sends tx | Converts NO positions into complementary outcome token set. |
| `split_position(private_key, condition_id, partition, amount, gas_price_gwei=50) -> str` | sends tx | Split a collateral position into outcome tokens. |
| `merge_position(private_key, condition_id, partition, amount, gas_price_gwei=50) -> str` | sends tx | Merge outcome tokens back to collateral. |
| `redeem_position(private_key, condition_id, index_sets, gas_price_gwei=50) -> str` | sends tx | Redeem after market resolution. |
| `estimate_conversion_output(...) -> dict` | read-only | Dry-run before `convert_positions`. |
| `health_check() -> Dict[str, Any]` | read-only | RPC reachability + contract presence. |

Gas-price cap enforced by `_validate_gas_price`; exceeding the limit raises `ValueError`. Transaction failures raise `NegRiskAdapterError` (or subclasses `InsufficientBalanceError`, `InvalidParameterError`).

`ConversionCalculator` — pure math utilities (no RPC, no state):

| Method | Signature |
|---|---|
| `calculate_conversion(...) -> dict` | Expected output of a theoretical conversion. |
| `is_conversion_profitable(...) -> bool` | Threshold check using current prices. |

`is_safe_to_trade(market: Market) -> bool` — combines neg-risk structural checks and outcome-set sanity. Use before routing a signal to `CTF`-involved flows.

## 11. Errors

### Hierarchy

```text
PolymarketError
├── APIError
├── AuthenticationError
├── ValidationError
│   ├── TickSizeError
│   └── OrderExpiredError
├── RateLimitError
├── TimeoutError
├── CircuitBreakerError
├── TradingError
│   ├── InsufficientBalanceError
│   ├── BalanceTrackingError
│   ├── OrderRejectedError
│   ├── MarketNotReadyError
│   ├── InvalidOrderError
│   ├── OrderNotFoundError
│   ├── InsufficientAllowanceError
│   ├── OrderDelayedError
│   └── FOKNotFilledError
├── MarketDataError
│   ├── PriceUnavailableError
│   ├── OrderBookError
│   └── MarketNotFoundError
└── WebSocketError
    ├── WebSocketConnectionError
    └── WebSocketDisconnectedError
```

### Base and infrastructure errors

| Error | Constructor/details | Raised by |
|---|---|---|
| `PolymarketError` | `(message: str, details: Optional[dict[str, Any]] = None)` | base |
| `APIError` | `(message: str, status_code: Optional[int] = None, response: Optional[dict] = None)` | HTTP >= 400 except 401/403/429; invalid JSON; connection errors |
| `AuthenticationError` | message only | HTTP 401/403; missing API credentials; invalid order signature |
| `ValidationError` | message only | invalid user address; invalid holder market; invalid order params |
| `RateLimitError` | `(message: str, endpoint: str, retry_after: Optional[float] = None)` | HTTP 429 or local limiter timeout |
| `TimeoutError` | message only | `asyncio.TimeoutError` in HTTP path |
| `CircuitBreakerError` | message only | circuit breaker blocks request |

### Trading errors

| Error | Constructor/details | Raised by |
|---|---|---|
| `TradingError` | message only | generic trading failure wrapper |
| `InsufficientBalanceError` | message only | preflight balance; CLOB balance rejection |
| `BalanceTrackingError` | message only | reservation over-release |
| `OrderRejectedError` | `(message: str, order_id: Optional[str] = None, reason: Optional[str] = None)` | exchange rejection, duplicate, nonce conflict |
| `MarketNotReadyError` | message only | market closed or inactive |
| `InvalidOrderError` | message only | size too small, invalid price |
| `OrderNotFoundError` | message only | defined; no current raise site in inspected code |
| `TickSizeError` | `(message: str, price: Optional[float] = None, tick_size: Optional[float] = None)` | order price violates tick size |
| `InsufficientAllowanceError` | `(message: str, token: Optional[str] = None, required: Optional[int] = None, current: Optional[int] = None)` | allowance rejection |
| `OrderDelayedError` | `(message: str, order_id: Optional[str] = None)` | delayed order rejection |
| `OrderExpiredError` | `(message: str, expiration: Optional[int] = None)` | expiration rejection |
| `FOKNotFilledError` | `(message: str, token_id: Optional[str] = None, requested_size: Optional[float] = None)` | fill-or-kill not filled |

### Market data and WebSocket errors

| Error | Constructor/details | Raised by |
|---|---|---|
| `MarketDataError` | message only | Gamma parse/fetch wrappers |
| `PriceUnavailableError` | `(message: str, token_id: Optional[str] = None)` | CLOB/public price fetch failures |
| `OrderBookError` | `(message: str, token_id: Optional[str] = None)` | public orderbook fetch failures |
| `MarketNotFoundError` | `(message: str, market_id: Optional[str] = None)` | public market-by-condition failure |
| `WebSocketError` | message only | base WebSocket error |
| `WebSocketConnectionError` | message only | defined; no current raise site in inspected code |
| `WebSocketDisconnectedError` | message only | defined; no current raise site in inspected code |

HTTP error mapping:

- `401` or `403` -> `AuthenticationError`.
- `429` -> `RateLimitError`.
- Other `>=400` -> `APIError(status_code=..., response=...)`.
- Invalid JSON -> `APIError`.
- `aiohttp.ClientError` -> `APIError`.
- `asyncio.TimeoutError` -> `TimeoutError`.

## 12. Rate limits

Configured values below are pre-margin. Runtime limiter applies `settings.rate_limit_margin` (default `0.8`) when a method passes a `rate_limit_key`. Source: <https://docs.polymarket.com/api-reference/rate-limits>. Last audited 2026-07-17 — trading ceilings rose through 2026 (changelog 2026-04-08 and 2026-06-01); market-data/Gamma/Data values unchanged. Every `rate_limit_key` passed by `polymarket/api/*.py` has an explicit config entry. Enforcement is Cloudflare throttling (queued, not rejected) on sliding windows.

### CLOB API — Trading (burst / sustained)

| Endpoint key | Pre-margin cap |
|---|---:|
| `POST:/order` | `5,000 req / 10s`, sustained `120,000 req / 10min` |
| `DELETE:/order` | `5,000 req / 10s`, sustained `120,000 req / 10min` |
| `POST:/orders` | `2,000 req / 10s`, sustained `21,000 req / 10min` |
| `DELETE:/orders` | `2,000 req / 10s`, sustained `15,000 req / 10min` |
| `DELETE:/cancel-all` | `250 req / 10s`, sustained `6,000 req / 10min` |
| `DELETE:/cancel-market-orders` | `1,500 req / 10s`, sustained `21,000 req / 10min` |

### CLOB API — Market data

| Endpoint key | Pre-margin cap |
|---|---:|
| `GET:/book`, `GET:/midpoint`, `GET:/price`, `GET:/last-trade-price`, `GET:/spread` | `1,500 req / 10s` |
| `GET:/books`, `POST:/books`, `GET:/midpoints`, `GET:/prices`, `POST:/last-trades-prices`, `GET:/simplified-markets` | `500 req / 10s` |
| `GET:/prices-history` | `1,000 req / 10s` |
| `GET:/tick-size`, `GET:/neg-risk` | `200 req / 10s` |

### CLOB API — Ledger, balance, auth, general

| Endpoint key | Pre-margin cap |
|---|---:|
| `GET:/data/order`, `GET:/order-scoring`, `POST:/orders-scoring` | `900 req / 10s` |
| `GET:/data/orders`, `GET:/data/trades` | `500 req / 10s` |
| `GET:/notifications` | `125 req / 10s` |
| `GET:/balance-allowance` | `200 req / 10s` |
| `GET:/balance-allowance/update` | `50 req / 10s` |
| `POST:/auth/api-key`, `GET:/auth/derive-api-key`, `POST:/auth/nonce` | `100 req / 10s` |
| `GET:/ok`, `GET:/`, `GET:/time` | `100 req / 10s` |
| `CLOB:default` | `9,000 req / 10s` |

### Gamma API

| Endpoint key | Pre-margin cap |
|---|---:|
| `GET:/markets`, `GET:/markets/keyset` | `300 req / 10s` |
| `GET:/events`, `GET:/events/pagination` | `500 req / 10s` |
| `GET:/comments`, `GET:/tags` | `200 req / 10s` |
| `GET:/search` | `300 req / 10s` (docs also list `/public-search` at 350 req/10s) |
| `GET:/public-profile` | `100 req / 10s` |
| `GAMMA:default` | `4,000 req / 10s` |

### Data API

| Endpoint key | Pre-margin cap |
|---|---:|
| `GET:/positions`, `GET:/closed-positions` | `150 req / 10s` |
| `GET:/trades`, `GET:/v1/leaderboard` | `200 req / 10s` |
| `GET:/activity`, `GET:/holders`, `GET:/value` | `1,000 req / 10s` |
| `DATA:default` | `1,000 req / 10s` |

### Default fallback

`default`: `100 req / 10s`. Intentionally conservative; any key that falls through is either new or misnamed, so stay well under the platform limit until the key is registered above.

### Calls without a rate-limit key

`PublicCLOBAPI` methods generally omit `rate_limit_key`, so their direct and top-level delegates bypass the local limiter. Price-history methods use `GET:/prices-history`; market-trade-event compatibility and v1 methods use conservative `CLOB:default`.

## 13. Verification

Run external static checks from repo root for stale filesystem paths, removed
changelog/status labels, and required contract strings. Keep this reference
aligned with the exported facade; line count is not a contract.

Controlled order lifecycle probes should use a persisted path:

1. Persist `(order_hash, reservation)` atomically through `pre_submit` before
   transport.
2. Treat `cancel_order()` only as a request acknowledgement.
3. Decide fill/cancel/reject/not-submitted from exact `get_order()` plus
   complete authenticated trade history.
4. Release the stored reservation only in the same durable terminal
   reconciliation.

CLOB WebSocket probe:

```python
from polymarket import OrderBook

seen = {}

def on_book(book: OrderBook) -> None:
    seen["best_bid"] = book.best_bid
    seen["best_ask"] = book.best_ask

client = PolymarketClient()
client.subscribe_orderbook(token_id, on_book)
connected = client.is_websocket_connected()
client.unsubscribe_all()
```

RTDS probe:

```python
from polymarket.api.real_time_data import Message

def on_message(message: Message) -> None:
    latest["topic"] = message.topic
    latest["payload"] = message.payload

client = PolymarketClient(enable_rtds=True)
client.subscribe_market_price_changes(on_message, token_ids=[token_id])
client.unsubscribe_rtds_all()
```

Test suite probes:

```bash
pytest polymarket/tests/unit -q
pytest polymarket/tests/integration -q
pytest polymarket/tests/test_api_regressions.py -q
pytest polymarket/tests/test_reserved_balance.py -q
pytest polymarket/tests/test_decimal_precision.py -q
```
