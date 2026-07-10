# Polymarket API Reference

## 1. Purpose and scope

`shared/polymarket/` is Pelion's Polymarket boundary: typed market data, authenticated CLOB trading, Data API reads, wallet auth, CLOB credentials, WebSockets, rate limiting, and errors. This file is the reference surface for Claude and GPT agents; operational rules, orientation, and gotchas live in [README.md](README.md) and `CLAUDE.md`.

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

Canonical construction:

```python
from shared.polymarket import PolymarketClient

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
| `rpc_url` | `Optional[str]` | `None` | Polygon RPC URL |
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
| `get_circuit_breaker_state() -> Optional[str]` | no | state or `None` | none |
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
    "rate_limiter": {...},
    "inflight_orders": int,
    "timestamp": float,
}
```

## 4. Authentication and wallet management

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
| `SignatureType.MAGIC` | `1` | Magic/email wallet |
| `SignatureType.PROXY` | `2` | Polymarket proxy wallet; EOA signs, proxy/funder holds funds |

PROXY construction:

```python
from shared.polymarket import PolymarketClient, WalletConfig, SignatureType

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

### EOA token approvals

EOA wallets need six on-chain token approvals (USDC spender plus CTF operators) before the first trade. Helper: `shared.polymarket.utils.allowances`. Budget roughly `$3-5` gas on Polygon. PROXY wallets do not need this step — Polymarket's proxy contract holds the approvals.

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

Example:

```python
async with PolymarketClient() as client:
    markets = await client.get_markets(active=True, closed=False, limit=100)
```

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

Top-level market-data methods use either `client.clob` or `client.public_clob`.

| Method | Delegate | Return | Raises |
|---|---|---|---|
| `async get_orderbook(token_id: str) -> OrderBook` | `client.clob.get_orderbook` | order book | `TradingError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> Dict[str, OrderBook]` | `client.clob.get_orderbooks_batch` | token id to order book | `TradingError` |
| `async get_midpoint(token_id: str) -> Optional[float]` | `client.clob.get_midpoint` | annotated float, actual Decimal/None | `PriceUnavailableError` |
| `async get_midpoints(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_midpoints` | token id to midpoint | returns None values on batch error |
| `async get_price(token_id: str, side: Side) -> Optional[float]` | `client.clob.get_price` | annotated float, actual Decimal/None | `PriceUnavailableError` |
| `async get_prices(params: List[Dict[str, str]]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_prices` | composite key to price | returns `{}` on error |
| `async get_spread(token_id: str) -> Optional[float]` | `client.public_clob.get_spread` | annotated float, actual Decimal/None | returns `None` on error |
| `async get_spreads(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | `client.public_clob.get_spreads` | token id to spread | returns None values on error |
| `async get_best_bid_ask(token_id: str) -> Optional[tuple[Decimal, Decimal]]` | `client.public_clob.get_best_bid_ask` | `(best_bid, best_ask)` | returns `None` on error |
| `async get_liquidity_depth(token_id: str, price_range: Decimal | float = Decimal("0.05")) -> Dict[str, Any]` | `client.public_clob.get_liquidity_depth` | depth dict | zero-depth dict on error |
| `async get_last_trade_price(token_id: str) -> Optional[float]` | `client.clob.get_last_trade_price` | annotated float, actual Decimal/None | `PriceUnavailableError` |
| `async get_last_trades_prices(token_ids: List[str]) -> Dict[str, Optional[float]]` | `client.clob.get_last_trades_prices` | annotated float values, actual Decimal/None | `TradingError` |
| `async get_server_time() -> int` | `client.clob.get_server_time` | Unix ms | `TradingError` |
| `async get_ok() -> bool` | `client.clob.get_ok` | CLOB health | `TradingError` |
| `async get_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | `client.clob.get_simplified_markets` | CLOB page | `TradingError` |
| `async get_markets_full(next_cursor: str = "MA==") -> Dict[str, Any]` | `client.public_clob.get_markets` | full CLOB page | returns empty page on error |
| `async get_market_by_condition(condition_id: str) -> Dict[str, Any]` | `client.public_clob.get_market` | market dict | `MarketNotFoundError` |
| `async get_market_trades_events(condition_id: str) -> List[Dict[str, Any]]` | `client.public_clob.get_market_trades_events` | trade events | returns `[]` on error |
| `async get_prices_history(token_id: str, interval: Optional[str] = None, start_ts: Optional[int] = None, end_ts: Optional[int] = None, fidelity: Optional[int] = None) -> List[PricePoint]` | `client.public_clob.get_prices_history` | historical price points (keyless, `GET:/prices-history` 1,000 req/10s) | 404 → `[]`; `ValueError` if `interval` combined with `start_ts`/`end_ts` |
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
| `async get_ok() -> bool` | `True`/`False` | returns `False` |
| `async get_server_time() -> int` | Unix ms | propagates API errors |
| `async get_midpoint(token_id: str) -> Optional[Decimal]` | midpoint | `PriceUnavailableError` |
| `async get_midpoints(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to midpoint | None values |
| `async get_price(token_id: str, side: str) -> Optional[Decimal]` | price | `PriceUnavailableError` |
| `async get_prices(params: List[Dict[str, str]]) -> Dict[str, Optional[Decimal]]` | composite key to price | `{}` |
| `async get_spread(token_id: str) -> Optional[Decimal]` | spread | `None` |
| `async get_spreads(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to spread | None values |
| `async get_orderbook(token_id: str) -> OrderBook` | order book | `OrderBookError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> List[OrderBook]` | order books | `[]` |
| `async get_order_book_hash(orderbook: OrderBook) -> str` | SHA-256 hex | none |
| `async get_tick_size(token_id: str) -> Decimal` | tick size | default `Decimal("0.01")` |
| `async get_neg_risk(token_id: str) -> bool` | neg-risk flag | `False` |
| `async get_fee_rate_bps(token_id: str) -> int` | fee bps | `0` |
| `async get_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_sampling_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_sampling_simplified_markets(next_cursor: str = "MA==") -> Dict[str, Any]` | page | empty page |
| `async get_market(condition_id: str) -> Dict[str, Any]` | market dict | `MarketNotFoundError` |
| `async get_market_trades_events(condition_id: str) -> List[Dict[str, Any]]` | trade events | `[]` |
| `async get_prices_history(token_id: str, interval: Optional[str] = None, start_ts: Optional[int] = None, end_ts: Optional[int] = None, fidelity: Optional[int] = None) -> List[PricePoint]` | historical price points | `[]` on 404; malformed points skipped |
| `async get_last_trade_price(token_id: str) -> Optional[Decimal]` | last price | `None` |
| `async get_last_trades_prices(token_ids: List[str]) -> Dict[str, Optional[Decimal]]` | token id to last price | None values |
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
| `async get_orderbook(token_id: str) -> OrderBook` | order book | `TradingError` |
| `async get_orderbooks_batch(token_ids: List[str]) -> Dict[str, OrderBook]` | token id to order book | `TradingError` |
| `async get_tick_size(token_id: str) -> Decimal` | tick size | defaults to `0.01` |
| `async get_neg_risk(token_id: str) -> bool` | neg-risk flag | defaults to `False` |
| `async get_fee_rate_bps(token_id: str) -> int` | fee bps | always `0` in current code |
| `async is_order_scoring(order_id: str) -> bool` | scoring flag | `TradingError` |
| `async are_orders_scoring(order_ids: List[str]) -> Dict[str, bool]` | scoring flags | `TradingError` |

## 6. Trading

### Top-level trading methods

| Method | Return | Raises |
|---|---|---|
| `async place_order(order: OrderRequest, wallet_id: Optional[str] = None, skip_balance_check: bool = False, idempotency_key: Optional[str] = None) -> OrderResponse` | order response | `AuthenticationError`, `ValidationError`, `InsufficientBalanceError`, `OrderRejectedError`, `TradingError` |
| `async place_market_order(market_order: MarketOrderRequest, wallet_id: Optional[str] = None, skip_balance_check: bool = False, idempotency_key: Optional[str] = None) -> OrderResponse` | order response | `ValidationError`, `InsufficientBalanceError`, `TradingError` |
| `async place_orders_batch(orders: List[OrderRequest], wallet_id: Optional[str] = None, skip_balance_check: bool = False) -> List[OrderResponse]` | responses | `AuthenticationError`, `ValidationError`, `TradingError`, `InsufficientBalanceError` |
| `async cancel_order(order_id: str, wallet_id: Optional[str] = None) -> bool` | cancel success | `TradingError`, auth/key-manager errors |
| `async cancel_all_orders(wallet_id: Optional[str] = None, market_id: Optional[str] = None) -> int` | count | `TradingError`, auth/key-manager errors |
| `async cancel_market_orders(market_id: str, wallet_id: Optional[str] = None) -> int` | count | `TradingError`, auth/key-manager errors |
| `async get_orders(wallet_id: Optional[str] = None, market: Optional[str] = None) -> List[Order]` | open orders | `TradingError`, auth/key-manager errors |
| `async get_balances(wallet_id: Optional[str] = None) -> Balance` | balance | `TradingError`, auth/key-manager errors |
| `async get_token_balance(token_id: str, wallet_id: Optional[str] = None) -> Decimal` | CTF token balance | `TradingError`, auth/key-manager errors |
| `async get_position_balance(token_id: str, wallet_id: Optional[str] = None) -> Decimal` | Data API position size | returns `Decimal("0")` on lookup failure |
| `async update_balance_allowance(wallet_id: Optional[str] = None, asset_type: str = "COLLATERAL", token_id: Optional[str] = None) -> Dict[str, Any]` | update response | `TradingError`, auth/key-manager errors |
| `async release_reserved_balance(amount: Decimal, wallet_id: Optional[str] = None, order_id: Optional[str] = None) -> None` | `None` | `BalanceTrackingError` |
| `async get_reserved_balance(wallet_id: Optional[str] = None) -> Decimal` | reserved USD | none |

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
3. Validates token id, price, size, side, and minimum size.
4. `_check_and_reserve_buy_balance`: preflights balance AND atomically
   reserves `size * price` under `_balance_lock` (closes check-then-reserve
   TOCTOU).
5. Builds and signs EIP-712 order.
6. Fetches tick size, fee rate, and neg-risk metadata for signing.
7. Posts to CLOB `POST /order`.
8. On successful live response: reservation persists; caller releases later.
9. On failed response or exception: releases the pre-reservation before
   returning/re-raising so collateral does not leak.
10. Tracks metrics.

`place_order` flow when `skip_balance_check=True`:

- Skips steps 4 and the pre-reservation.
- After step 7, on successful BUY response, reserves `size * price` under
  `_balance_lock` (post-reserve path kept for callers that manage their own
  preflight).
- Same release-on-failure behavior as above.

`place_order` flow for SELL:

- Step 4 becomes `_check_balance` only (preflight, no reservation) unless
  `skip_balance_check=True`. SELL never reserves.

`idempotency_key`:

- Passed to signed-order construction.
- Used for deterministic order hash/salt behavior where supported by `OrderBuilder`.
- Batch placement does not accept idempotency keys.

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

- BUY collateral reserve is `order.size * order.price`.
- Reservation unit is USD collateral as `Decimal`.
- BUY default flow: `_check_and_reserve_buy_balance` preflights AND atomically reserves under `_balance_lock` BEFORE build/sign/submit (closes check-then-reserve TOCTOU).
- BUY with `skip_balance_check=True`: preflight and pre-reservation both skipped; a successful `post_order` response reserves `size * price` after the fact (legacy post-reserve path retained for callers with their own preflight).
- In either path, a failed `post_order` response or an exception after reservation releases the tentative reservation before the caller returns or re-raises.
- Successful live BUY orders keep reservation.
- Caller releases after fill, cancel, expiry, or no-longer-live state:

```python
await client.release_reserved_balance(
    order.size * order.price,
    wallet_id="WALLET_0",
    order_id=response.order_id,
)
```

- Over-release raises `BalanceTrackingError`.
- Balance lookup errors during preflight become `TradingError`; order is not submitted.
- SELL validation uses `get_position_balance()` from Data API positions.
- `get_token_balance()` reads conditional token balance from CLOB balance allowance.
- `place_orders_batch` preflights total batch balance.
- Batch successful BUY reservations happen after submit.
- Batch reservation failure for one response logs warning and continues.

### CLOBAPI trading direct methods

Available as `client.clob.<method>`; top-level wrappers fill auth fields from wallet credentials.

| Method | Return | Raises |
|---|---|---|
| `async post_order(signed_order: Dict[str, Any], address: str, api_key: str, api_secret: str, api_passphrase: str, order_type: str = "GTC") -> OrderResponse` | response | `OrderRejectedError`, `InsufficientBalanceError`, `TickSizeError`, `InsufficientAllowanceError`, `OrderDelayedError`, `OrderExpiredError`, `FOKNotFilledError`, `InvalidOrderError`, `MarketNotReadyError`, `AuthenticationError`, `TradingError` |
| `async post_orders_batch(signed_orders: List[Dict[str, Any]], address: str, api_key: str, api_secret: str, api_passphrase: str) -> List[OrderResponse]` | responses | `TradingError` |
| `async cancel_order(order_id: str, address: str, api_key: str, api_secret: str, api_passphrase: str) -> bool` | `True` if canceled or already gone | `TradingError` |
| `async cancel_market_orders(market_id: str, address: str, api_key: str, api_secret: str, api_passphrase: str) -> int` | cancel count | `TradingError` |
| `async cancel_all_orders(address: str, api_key: str, api_secret: str, api_passphrase: str, market_id: Optional[str] = None) -> int` | cancel count | `TradingError` |
| `async get_orders(address: str, api_key: str, api_secret: str, api_passphrase: str, market: Optional[str] = None) -> List[Order]` | orders | `TradingError` |
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
| other unsuccessful error | `OrderRejectedError` |

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

`get_balances`:

- Uses `GET /balance-allowance`.
- `asset_type="COLLATERAL"` for USDC.
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
| `async get_leaderboard(limit: int = 100, min_pnl: Optional[float] = None) -> List[LeaderboardTrader]` | leaderboard | `APIError`, `TimeoutError`, parse errors |
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
| `async get_trades(user: Optional[str] = None, limit: int = 100, offset: int = 0, taker_only: bool = True, filter_type: Optional[str] = None, filter_amount: Optional[float] = None, market: Optional[str] = None, side: Optional[Side] = None) -> List[Trade]` | `GET /trades` | trades |
| `async get_activity(user: str, market: Optional[str] = None, activity_type: Optional[ActivityType] = None, limit: int = 100, offset: int = 0, start: Optional[int] = None, end: Optional[int] = None, side: Optional[Side] = None, sort_by: str = "TIMESTAMP") -> List[Activity]` | `GET /activity` | activity |
| `async get_portfolio_value(user: str, market: Optional[str] = None) -> PortfolioValue` | `GET /value` | value model |
| `async get_holders(market: str, limit: int = 100, min_balance: int = 1) -> List[Holder]` | `GET /holders` | flattened holders |
| `async get_leaderboard(limit: int = 100, min_pnl: Optional[float] = None) -> List[LeaderboardTrader]` | `GET /v1/leaderboard` | traders |

Validation:

- `get_positions(user=...)` requires an address beginning with `0x`.
- `get_activity(user=...)` requires an address beginning with `0x`.
- `get_portfolio_value(user=...)` requires an address beginning with `0x`.
- `get_holders(market=...)` requires a non-empty condition id.
- `limit` is capped to `500` for positions, trades, activity, and holders.
- Position offset is capped to `10000`.
- Position title filter is truncated to 100 chars.

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

These methods are synchronous subscription wrappers.

| Method | Callback receives | Raises |
|---|---|---|
| `subscribe_orderbook(token_id: str, callback: Callable[[OrderBook], None], wallet_id: Optional[str] = None) -> None` | `OrderBook` | callback errors are logged |
| `subscribe_user_orders(callback: Callable[[Any], None], wallet_id: Optional[str] = None) -> None` | typed CLOB WS message | `ValueError` if user channel lacks API key |
| `unsubscribe_all() -> None` | none | logs disconnect errors |
| `is_websocket_connected() -> bool` | none | none |

Orderbook callback conversion:

- Accepts `OrderbookMessage` only.
- Converts `message.buys` and `message.sells` into `OrderBook.bids` and `OrderBook.asks`.
- Uses `Decimal(level.price)` and `Decimal(level.size)`.

Example:

```python
def on_book(book: OrderBook) -> None:
    latest["bid"] = book.best_bid
    latest["ask"] = book.best_ask

client.subscribe_orderbook(token_id, on_book)
```

### WebSocketClient direct methods

Available from `shared.polymarket.api.websocket.WebSocketClient`.

| Method | Return | Notes |
|---|---|---|
| `__init__(ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws", api_key: Optional[str] = None, reconnect_delay: float = 5.0, max_reconnects: int = 10, enable_metrics: bool = True, enable_queue: bool = True, queue_maxsize: int = 10000, ping_interval: int = 20, ping_timeout: int = 10, queue_drop_threshold: int = 1000, enable_compression: bool = True, on_failure_callback: Optional[Callable[[str], None]] = None, enable_deduplication: bool = True, dedup_window_seconds: int = 300) -> None` | client | constructor |
| `connect(event_loop: Optional[asyncio.AbstractEventLoop] = None) -> None` | `None` | starts background thread |
| `disconnect() -> None` | `None` | closes socket and consumer |
| `subscribe_market(token_id: str, callback: Callable[[WebSocketMessage], None]) -> None` | `None` | market channel |
| `subscribe_user(callback: Callable[[WebSocketMessage], None]) -> None` | `None` | requires `api_key` |
| `subscribe_markets_multi(token_ids: list[str], callback: Callable[[WebSocketMessage], None]) -> None` | `None` | one subscription message |
| `subscribe_markets_batch(token_ids: list[str], callback: Callable[[WebSocketMessage], None]) -> Dict[str, Any]` | result dict | rollback on partial failure |
| `unsubscribe(channel: str) -> None` | `None` | channel key |
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
| `LastTradePriceMessage` | `event_type`, `asset_id`, `market`, `price`, `side`, `size`, `fee_rate_bps`, `timestamp` |

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
| `subscribe_market_last_trade_price(callback: Callable[[Message], None], token_ids: List[str]) -> None` | `clob_market` / `last_trade_price` | token ids JSON list | `ValueError`, `RuntimeError` |
| `subscribe_market_tick_size_change(callback: Callable[[Message], None], token_ids: List[str]) -> None` | `clob_market` / `tick_size_change` | token ids JSON list | `ValueError`, `RuntimeError` |
| `unsubscribe_rtds_all() -> None` | disconnect | none | logs errors |

RTDS facade validation:

- `_ensure_rtds()` raises `RuntimeError` if `settings.enable_rtds` is false.
- Activity trades reject both `market_slug` and `event_slug` together.
- Token-id subscriptions reject empty lists.
- Crypto symbols must be one of `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`.
- Callback exceptions are caught and logged.

### RealTimeDataClient direct methods

Available from `shared.polymarket.api.real_time_data.RealTimeDataClient`.

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
| `SignatureType` | `EOA=0`, `MAGIC=1`, `PROXY=2` |
| `ActivityType` | `TRADE`, `SPLIT`, `MERGE`, `REDEEM`, `REWARD`, `CONVERSION`, `MAKER_REBATE`, `YIELD` |

### Decimal behavior

- Financial fields generally use `Decimal`.
- Validators accept `Decimal`, `str`, `int`, and `float`.
- Floats are converted through `str(value)`.
- `OrderRequest.price` is quantized to `Decimal("0.01")`.
- `OrderRequest.size` is quantized to `Decimal("0.01")`.
- `OrderBook.midpoint` is quantized to `Decimal("0.01")`.
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

- `price >= Decimal("0.01")`
- `price <= Decimal("0.99")`
- `size > 0`
- `expiration` is Unix timestamp for GTD orders.
- Enums remain enum objects; no `use_enum_values`.

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
    order_hashes: Optional[list[str]] = None
```

Notes:

- `model_config = ConfigDict(use_enum_values=True)`.
- `order_id` maps from CLOB `orderID` in single-order responses.
- `order_hashes` maps from CLOB `orderHashes`.

### Order

```python
class Order(BaseModel):
    id: str
    market: str
    asset_id: str
    token_id: str
    price: Decimal
    size: Decimal
    side: Side
    status: OrderStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    expiration: Optional[datetime] = None
```

Validators:

- `price` and `size` convert to `Decimal`.
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

### Trade

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
| `midpoint` | average best bid/ask quantized to `0.01`, or `None` |
| `spread` | best ask minus best bid quantized to `0.0001`, or `None` |

Validator:

- `tick_size` converts to `Decimal`.
- Unsupported `tick_size` becomes `None`.

## 10. Utility functions (validation, fees, CTF)

Public-exported helpers from `shared.polymarket.utils.*` and `shared.polymarket.ctf.*`. Every name listed here is exported via `shared.polymarket.__init__` and is part of the stable surface.

### Validation helpers

Source: `shared/polymarket/utils/validation.py`.

```python
from shared.polymarket import (
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
| `validate_price_bounds(price: Decimal) -> bool` | `True` or raises `ValidationError` | Price must be in `[0.01, 0.99]`. |
| `validate_size(size: Decimal, min_size: Decimal = MIN_SIZE) -> bool` | `True` or raises | Size must be > `min_size`. |
| `validate_fee_rate(fee_rate_bps: int) -> bool` | `True` or raises | Polymarket has zero fees; pass `0`. |
| `validate_token_complementarity(token_id_1: str, token_id_2: str, market: Optional[Market] = None) -> bool` | `True` or raises | Sanity check for YES/NO pairs. Full on-chain check still needs CTF. |
| `validate_neg_risk_market(market: Market) -> bool` | `True` or raises | Ensures neg-risk markets meet structural constraints. |
| `validate_balance(side: Side, price: Decimal, size: Decimal, available_usdc: Decimal, available_tokens: Decimal = Decimal("0"), fee_rate_bps: int = 0) -> tuple[bool, Optional[str]]` | Pair | Off-chain balance check for BUY (USDC) or SELL (tokens); reuses `validate_price_bounds` and `validate_size` internally. |
| `validate_order_amounts(maker_amount: Decimal, taker_amount: Decimal, min_amount: Decimal = Decimal("0.01")) -> bool` | `True` or raises | Signed-order sanity check. |
| `check_order_profitability(entry_price: Decimal, exit_price: Decimal, size: Decimal, fee_rate_bps: int, min_profit_usdc: Decimal = Decimal("0.10")) -> tuple[bool, Decimal]` | `(profitable, net_profit)` | Round-trip profitability for strategy pre-checks. |

All raise-on-invalid helpers raise `shared.polymarket.exceptions.ValidationError`.

### Fee helpers

Source: `shared/polymarket/utils/fees.py`.

```python
from shared.polymarket import (
    calculate_order_fee,
    calculate_net_cost,
    compare_fees_buy_vs_sell,
    estimate_breakeven_exit,
    calculate_profit_after_fees,
    get_effective_spread,
)
```

Polymarket has zero trading fees; these helpers exist for protocol compatibility and are always exercised with `fee_rate_bps=0`. Use them only when an external model expects a fee-aware calculation.

| Function | Returns |
|---|---|
| `calculate_order_fee(side: Side, price: Decimal, size: Decimal, fee_rate_bps: int = 0) -> Decimal` | `Decimal("0.0")` on Polymarket. |
| `calculate_net_cost(side: Side, price: Decimal, size: Decimal, fee_rate_bps: int = 0) -> tuple[Decimal, Decimal]` | `(gross_cost, fee)` pair. |
| `compare_fees_buy_vs_sell(...) -> dict` | Summary of fee impact on both sides. |
| `estimate_breakeven_exit(...) -> Decimal` | Exit price needed to cover entry + fees. |
| `calculate_profit_after_fees(...) -> Decimal` | Net profit for given entry/exit/size/fee. |
| `get_effective_spread(...) -> Decimal` | Spread minus fees on both sides. |

See the docstrings for the full argument lists; names and defaults are stable.

### CTF and Neg-Risk

Source: `shared/polymarket/ctf/`.

```python
from shared.polymarket import (
    NegRiskAdapter,
    ConversionCalculator,
    is_safe_to_trade,
    NEG_RISK_ADAPTER,
    NEG_RISK_EXCHANGE,
    CTF_ADDRESS,
)
```

Constants point at mainnet Polygon contracts and are for read-only reference.

`NegRiskAdapter(web3_provider: str = "https://polygon-rpc.com")` — on-chain operations for neg-risk positions. Every method that sends a transaction needs a private key; the constructor only holds a Web3 provider.

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

Configured values below are pre-margin. Runtime limiter applies `settings.rate_limit_margin` (default `0.8`) when a method passes a `rate_limit_key`. Source: <https://docs.polymarket.com/quickstart/introduction/rate-limits>. Last audited 2026-04-23 — every `rate_limit_key` passed by `shared/polymarket/api/*.py` now has an explicit config entry.

### CLOB API — Trading (burst / sustained)

| Endpoint key | Pre-margin cap |
|---|---:|
| `POST:/order` | `3,500 req / 10s`, sustained `36,000 req / 10min` |
| `DELETE:/order` | `3,000 req / 10s`, sustained `30,000 req / 10min` |
| `POST:/orders` | `1,000 req / 10s`, sustained `15,000 req / 10min` |
| `DELETE:/orders` | `1,000 req / 10s`, sustained `15,000 req / 10min` |
| `DELETE:/cancel-all` | `250 req / 10s`, sustained `6,000 req / 10min` |
| `DELETE:/cancel-market-orders` | `1,000 req / 10s`, sustained `1,500 req / 10min` |

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

`PublicCLOBAPI` methods generally call `BaseAPIClient.get/post` without `rate_limit_key`, so the local limiter is not applied to those direct public methods. Top-level methods that delegate to `client.public_clob` inherit that behavior. `get_prices_history` is the exception — it passes `rate_limit_key="GET:/prices-history"`, so the local limiter is applied to it (and to the top-level facade that delegates to it).

## 13. Verification

Run external static checks from repo root for line count, stale filesystem paths, removed changelog/status labels, and required contract strings. This file should remain between 1200 and 1700 lines.

Public CLOB probe:

```python
from shared.polymarket import PolymarketClient, Side

async with PolymarketClient() as client:
    ok = await client.get_ok()
    server_time_ms = await client.get_server_time()
    midpoint = await client.get_midpoint(token_id)
    price = await client.get_price(token_id, Side.BUY)
    book = await client.get_orderbook(token_id)
    depth = await client.get_liquidity_depth(token_id)
```

Gamma probe:

```python
async with PolymarketClient() as client:
    markets = await client.get_markets(active=True, closed=False, limit=10)
    events = await client.get_events(active=True, closed=False, limit=10)
    profile = await client.gamma.get_public_profile(proxy_address)
```

Data API probe:

```python
async with PolymarketClient() as client:
    wallet_id = await client.add_wallet(wallet_config, wallet_id="WALLET_0")
    positions = await client.get_positions(wallet_id)
    trades = await client.get_trades(wallet_id, limit=10)
    activity = await client.get_activity(wallet_id, limit=10)
    value = await client.get_portfolio_value(wallet_id)
    leaderboard = await client.get_leaderboard(limit=10)
```

Authenticated account probe:

```python
async with PolymarketClient() as client:
    wallet_id = await client.add_wallet(wallet_config, wallet_id="WALLET_0")
    balance = await client.get_balances(wallet_id)
    reserved = await client.get_reserved_balance(wallet_id)
    open_orders = await client.get_orders(wallet_id)
```

Controlled order lifecycle probe:

```python
async with PolymarketClient() as client:
    wallet_id = await client.add_wallet(wallet_config, wallet_id="WALLET_0")
    order = OrderRequest(
        token_id=token_id,
        price=Decimal("0.01"),
        size=Decimal("5.00"),
        side=Side.BUY,
        order_type=OrderType.GTC,
    )
    response = await client.place_order(order, wallet_id=wallet_id)
    if response.success and response.order_id:
        cancelled = await client.cancel_order(response.order_id, wallet_id=wallet_id)
        await client.release_reserved_balance(
            order.size * order.price,
            wallet_id=wallet_id,
            order_id=response.order_id,
        )
```

CLOB WebSocket probe:

```python
from shared.polymarket import OrderBook

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
from shared.polymarket.api.real_time_data import Message

def on_message(message: Message) -> None:
    latest["topic"] = message.topic
    latest["payload"] = message.payload

client = PolymarketClient(enable_rtds=True)
client.subscribe_market_price_changes(on_message, token_ids=[token_id])
client.unsubscribe_rtds_all()
```

Test suite probes:

```bash
pytest shared/polymarket/tests/unit -q
pytest shared/polymarket/tests/integration -q
pytest shared/polymarket/tests/test_api_regressions.py -q
pytest shared/polymarket/tests/test_reserved_balance.py -q
pytest shared/polymarket/tests/test_decimal_precision.py -q
```
