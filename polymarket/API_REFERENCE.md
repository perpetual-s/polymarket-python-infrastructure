# Polymarket facade index

This is a maintained index of `PolymarketClient`. Exact arguments, types, and
response models live in `client.py` and `models.py`; those files are the
signature source of truth.

Every model, exception, and helper named in this file is importable directly
from `polymarket`; submodule paths are an implementation detail.

## Construction

```python
PolymarketClient(
    settings: PolymarketSettings | None = None,
    enable_rate_limiting: bool | None = None,
    db: Any | None = None,
    **settings_overrides,
)
```

Settings overrides must be real `PolymarketSettings` fields. The client
supports `async with`; call `await close()` when not using the context manager.

Requests are rate-limited, timed out, and retried independently. There is no
persistent circuit-breaker state or reset method.

## Wallets

```python
await client.add_wallet(
    wallet_config: WalletConfig,
    wallet_id: str | None = None,
    set_default: bool = False,
) -> str

client.remove_wallet(wallet_id: str) -> None
client.list_wallets() -> list[str]
client.get_default_wallet() -> str | None
```

`WalletConfig`:

```python
WalletConfig(
    private_key: SecretStr,
    address: str | None = None,
    signature_type: SignatureType = SignatureType.EOA,
    funder: str | None = None,
)
```

Use `wallet_identity.py` for env-based signer/funder resolution.

## Gamma discovery

| Method | Purpose |
|---|---|
| `get_markets(...)` | paginated market query |
| `get_markets_keyset(...)` | keyset universe page |
| `get_market_by_slug(slug)` | one market by slug |
| `get_market_by_id(market_id)` | one market by Gamma ID |
| `search_markets(query, limit=20)` | text search |
| `get_all_current_markets(limit=100)` | current market list |
| `get_clob_tradable_markets(limit=100)` | accepting CLOB markets |
| `get_events(...)` | event query |
| `filter_events_for_trading(events)` | local event filter |
| `get_all_tradeable_events(limit=100)` | tradable events |

## Public CLOB market data

| Method | Result |
|---|---|
| `get_orderbook(token_id)` | `OrderBook` |
| `get_orderbooks_batch(token_ids)` | token → `OrderBook` |
| `get_midpoint(token_id)` | midpoint or `None` |
| `get_midpoints(token_ids)` | token → `Decimal | None` |
| `get_price(token_id, side)` | side price or `None` |
| `get_prices(params)` | request key → `Decimal | None` |
| `get_last_trade_price(token_id)` | last price or `None` |
| `get_last_trades_prices(token_ids)` | token → price |
| `get_spread(token_id)` | spread or `None` |
| `get_spreads(token_ids)` | token → spread |
| `get_best_bid_ask(token_id)` | `(bid, ask)` or `None` |
| `get_liquidity_depth(...)` | depth around a price |
| `get_server_time()` | CLOB epoch milliseconds |
| `get_ok()` | public CLOB health boolean |
| `get_simplified_markets(next_cursor="MA==")` | raw page |
| `get_markets_full(next_cursor="MA==")` | full CLOB page |
| `get_market_by_condition(condition_id)` | market mapping |
| `get_resolution_payouts(condition_id)` | `ResolutionPayouts \| None` (raises `UnsupportedResolution`) |
| `get_tick_size(token_id)` | `Decimal` |
| `get_min_order_size(token_id)` | `Decimal` shares (CLOB `mos`) |
| `get_fee_rate_bps(token_id)` | integer basis points |
| `get_fee_info(token_id)` | `FeeInfo` |
| `is_order_scoring(order_id)` | scoring boolean |
| `are_orders_scoring(order_ids)` | order → boolean |

`get_resolution_payouts` wraps `get_market` and adds no new HTTP surface. It
validates the raw payload for an exact condition-id match, `closed is True`,
unique token ids, and boolean winner flags. An ordinary binary market pays the
winner 1.0 and the loser 0.0; `is_50_50_outcome` pays every token 0.5
(`kind="fifty_fifty"`); a normal negRisk market resolves the same way as an
ordinary binary one. Anything open, disputed, or malformed returns `None`
rather than an inferred payout. A token id absent from the returned `.payouts`
mapping means SKIP that token, never a 0.0 payout.

Caveat on `UnsupportedResolution`: the raw CLOB per-condition payload has no
confirmed field distinguishing an augmented neg-risk market (incomplete
outcome universe) from a normal one, and no sampled live payload carried one,
including a market added to an already-live negRisk group. The exception is
raised defensively on this client's own `neg_risk_augmented` key (see
`models.Market`, `ctf/utils.is_safe_to_trade`) in case a payload ever sets it,
but that is an unconfirmed heuristic rather than verified detection of
Polymarket's on-chain augmented-adapter flag.

## Public trade and price history

| Method | Purpose |
|---|---|
| `get_market_trades_events(condition_id)` | market trade events |
| `get_market_trades_events_result_v1(...)` | bounded typed result |
| `get_prices_history(...)` | price points |
| `get_prices_history_result_v1(...)` | bounded typed result |
| `get_market_trades(...)` | market trades |
| `get_market_trades_result_v1(...)` | bounded typed result |
| `get_market_trades_window(...)` | explicit time window |

The `*_result_v1` methods expose parse/completeness status rather than
silently treating partial public data as complete.

## Result models

| Model | Produced by |
|---|---|
| `ResolutionPayouts` | `get_resolution_payouts` |
| `DataTradesResultV1` (`DataTradeV1`, `DataTradesQueryV1`, `DataTradesCoverageV1`) | `get_market_trades_result_v1` |
| `MarketTradeEventsResultV1` (`MarketTradeEventV1`) | `get_market_trades_events_result_v1` |
| `PriceHistoryResultV1` (`PriceHistoryPointV1`, `PriceHistoryQueryV1`, `PriceHistoryCoverageV1`) | `get_prices_history_result_v1` |
| `PublicRequestEvidenceV1`, `PublicDataStatus` | the `evidence`/`status` fields of every `*_result_v1` |

Compatibility surfaces return the plain models instead: `Market`, `Event`,
`OrderBook`, `PricePoint`, `Order`, `OrderResponse`, `Position`,
`ClosedPosition`, `Trade`, `ClobTrade`, `ClobMakerTrade`, `Activity`,
`Balance`, `FeeInfo`, `FeeSchedule`, `Holder`, `LeaderboardTrader`,
`PortfolioValue`, `WebSocketMessage`.

## Data API

Address-explicit public reads:

```python
await client.get_address_activity(address, **filters)
await client.get_address_positions(address, **filters)
```

Bulk history acquisition can pass `strict_parse=True` so malformed rows fail
the range instead of creating a false checkpoint, and `retry=True` for bounded
transient retries. Low-latency activity callers retain the no-retry default.
`get_closed_positions(..., strict_parse=True)` provides the same complete-page
parse behavior.

Wallet-aware reads resolve the configured funder address:

| Method | Purpose |
|---|---|
| `get_positions(wallet_id=None, **filters)` | open positions |
| `get_closed_positions(wallet_id=None, **filters)` | closed positions |
| `get_trades(wallet_id=None, **filters)` | public wallet trades |
| `get_activity(wallet_id=None, **filters)` | public activity |
| `get_portfolio_value(wallet_id=None, **filters)` | wallet value |
| `get_market_holders(...)` | holders |
| `get_leaderboard(...)` | `/v1/leaderboard` |
| `get_positions_batch(...)` | multi-address positions |
| `get_trades_batch(...)` | multi-address trades |
| `get_activity_batch(...)` | multi-address activity |
| `aggregate_multi_wallet_metrics(...)` | aggregate metrics |
| `detect_signals(...)` | multi-wallet signal helper |

## Authenticated trading

```python
await client.place_order(
    order: OrderRequest,
    wallet_id: str | None = None,
    skip_balance_check: bool = False,
    idempotency_key: str | None = None,
    pre_submit: Callable[[str, Decimal], Awaitable[None]] | None = None,
    timestamp_ms: int | None = None,
    tick_size: Decimal | None = None,
) -> OrderResponse
```

`pre_submit` receives the deterministic exchange order ID and reserved
collateral before the network submission so callers can persist a durable
intent. Reuse `idempotency_key`, timestamp, and payload only when rebuilding
the same order identity.

Other authenticated methods:

| Method | Purpose |
|---|---|
| `cancel_order(order_id, wallet_id=None)` | cancel one order |
| `get_orders(wallet_id=None, market=None)` | open orders |
| `get_order(order_id, wallet_id=None)` | exact order |
| `get_clob_trades(wallet_id=None, **filters)` | authenticated executions |
| `get_balances(wallet_id=None)` | collateral/allowance response |
| `get_token_balance(token_id, wallet_id=None)` | conditional-token balance |
| `get_position_balance(token_id, wallet_id=None)` | Data API position size |
| `update_balance_allowance(...)` | refresh CLOB balance/allowance |
| `get_reserved_balance(wallet_id=None)` | process-local BUY reservation |
| `release_reserved_balance(...)` | release exact reservation |
| `restore_reserved_balance(...)` | reconstruct reservation on restart |

`OrderRequest` contains `token_id`, `price`, `size`, `side`, `order_type`, and
optional expiration. Values are normalized with `Decimal`.

## Health and local metrics

```python
client.get_rate_limiter_stats() -> dict
await client.health_check() -> dict
```

`health_check()` reports public CLOB connectivity, rate-limiter state,
in-flight order count, and timestamp. It is observation only.

## CLOB WebSocket

Registration methods are synchronous:

| Method | Purpose |
|---|---|
| `subscribe_orderbook(token_id, callback, wallet_id=None)` | market book |
| `subscribe_clob_market_last_trade_price(...)` | last trade |
| `subscribe_user_orders(callback, wallet_id=None)` | wallet orders/fills |
| `wait_until_websocket_connected(timeout=5.0)` | connection wait |
| `unsubscribe_all()` | detach CLOB subscriptions |
| `is_websocket_connected()` | connection state |
| `get_clob_websocket_telemetry_v1()` | immutable telemetry snapshot |

One CLOB transport owns one endpoint channel. USER transport is bound to one
wallet; MARKET and USER are not multiplexed through the same connection.

## RTDS

Registration methods are synchronous:

- `subscribe_activity_trades`
- `subscribe_activity_orders_matched`
- `subscribe_market_created`
- `subscribe_market_resolved`
- `subscribe_market_price_changes`
- `unsubscribe_market_price_changes`
- `subscribe_market_orderbook_rtds`
- `subscribe_market_last_trade_price`
- `subscribe_market_tick_size_change`
- `subscribe_comments`
- `subscribe_reactions`
- `subscribe_rfq_requests`
- `subscribe_rfq_quotes`
- `subscribe_crypto_prices`
- `subscribe_crypto_prices_chainlink`
- `unsubscribe_rtds_all`
- `get_rtds_stats`

Callbacks receive the typed RTDS `Message`. Registration validates filters and
reuses one lazily initialized RTDS client.

## Errors

All client errors derive from `PolymarketError`.

| Group | Common classes |
|---|---|
| request | `APIError`, `RateLimitError`, `TimeoutError` |
| auth | `AuthenticationError` |
| validation | `ValidationError`, `TickSizeError`, `OrderExpiredError` |
| trading | `TradingError`, `InsufficientBalanceError`, `InsufficientAllowanceError`, `OrderRejectedError`, `OrderNotFoundError`, `OrderDelayedError`, `MarketNotReadyError`, `InvalidOrderError`, `FOKNotFilledError`, `BalanceTrackingError` |
| market data | `MarketDataError`, `PriceUnavailableError`, `OrderBookError`, `MarketNotFoundError`, `UnsupportedResolution` |
| streams | `WebSocketError`, `WebSocketConnectionError`, `WebSocketDisconnectedError` |

`polymarket.TimeoutError` also derives from `builtins.TimeoutError`, so a
plain `except TimeoutError:` catches it. `polymarket.ValidationError` is this
package's own class and is unrelated to `pydantic.ValidationError`; import one
of them under an alias when both are in scope.

Use `is_definitive_order_rejection(error)` before releasing intent or retrying
an order. Duplicate or ambiguous exchange outcomes require exact order/trade
reconciliation.

```python
from polymarket import (
    APIError,
    InsufficientBalanceError,
    OrderRejectedError,
    PolymarketError,
    RateLimitError,
    TimeoutError,
    is_definitive_order_rejection,
)

try:
    response = await client.place_order(order, wallet_id="primary")
except InsufficientBalanceError:
    # Local preflight refused the order; nothing was submitted.
    raise
except (OrderRejectedError, APIError) as e:
    if is_definitive_order_rejection(e):
        # Proven non-submission: release the reservation and move on.
        raise
    # Ambiguous outcome: keep the reservation and reconcile with
    # get_order()/get_clob_trades() before retrying anything.
    raise
except (RateLimitError, TimeoutError):
    # Transient transport failure. Back off; do not create a new order identity.
    raise
except PolymarketError:
    # Every client error derives from PolymarketError.
    raise
```

## Rules

- No direct Polymarket HTTP outside this package.
- Do not pass an owner EOA as the funds holder for a proxy wallet.
- Do not release the same collateral reservation twice.
- Do not retry an ambiguous order by creating a new identity.
- Do not `await` synchronous subscription registration.
- Close the client from the application's shutdown path.
