# polymarket-python-infrastructure

Production-grade infrastructure for Polymarket prediction markets. Built for
reliability, performance, and scale.

An unofficial, async Python client for Polymarket market data, wallet-aware
trading, and real-time streams. It favors explicit result contracts, `Decimal`
arithmetic, typed errors, bounded retries, and fail-closed behavior when
exchange state is incomplete.

## Why This Exists

When I started, I used the official [py-clob-client](https://github.com/Polymarket/py-clob-client), but quickly ran into limitations when scaling to production. I needed to track 100+ wallets simultaneously, handle concurrent requests safely, and recover from failures automatically—things the official client wasn't built for.

So I built my own. After months of development and iteration, it became clear this infrastructure could help others avoid the same problems. I'm open-sourcing it hoping it helps other traders build more robust systems.

**This is what I actually use in production with real money.** Every feature exists because I needed it, every edge case handled because I hit it, every safety check added because something broke without it.

## Why "Infrastructure" not "Client"?

A client makes API requests. Infrastructure provides the foundation for production systems.

This includes:
- **Multi-wallet architecture** for tracking hundreds of wallets concurrently
- **Production safety** with rate limiting, bounded retries, and comprehensive error handling
- **Observability** through structured logging and Prometheus metrics
- **Performance** via batch operations and lock-protected shared state
- **Reliability** with auto-recovery, retry logic, and defensive programming

Think of it as the difference between a bicycle (client) and a highway system (infrastructure).

## The Problem with py-clob-client

The official client works well for simple use cases, but has fundamental limitations for production:

**Architecture Limitations:**
- **Single wallet only** - Can't track multiple wallets or build copy-trading strategies
- **No concurrency primitives** - Fully synchronous with no locking; safe concurrent use is left entirely to you
- **No rate limiting** - Easy to hit API limits and get blocked
- **Basic error handling** - Only 2 exception types, debugging production issues is difficult

**Implementation Issues:**
- **Float precision** - `OrderArgs.price` and `size` are floats, causing rounding errors in financial calculations
- **No streaming** - No WebSocket support at all; market data must be polled
- **No caching** - No cache layer, so repeated reads always hit the network
- **No observability** - Can't debug production issues, no metrics or structured logging

These aren't criticisms—py-clob-client is a reference implementation, not production infrastructure. But if you're building something serious, you need more.

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
- Per-endpoint rate limiting, bounded retries, caching, optional Prometheus
  metrics, and credential-redacting logs and exceptions.

The package is async-first. Network and wallet methods must be awaited; local
subscription registration and state-inspection helpers are synchronous.

## Scope

This is a client library, not a trading strategy. It transports, validates,
and types Polymarket data and orders; it does not decide what to trade, size a
position, or manage risk. Those belong to the application that uses it.

It is unofficial and independent: not affiliated with, endorsed by, or
supported by Polymarket. Upstream endpoints and semantics can change without
notice.

The default test suite is offline and hermetic. Passing it proves the local
contracts in this repository. It does not prove live exchange availability,
regional eligibility, account entitlement, allowances, funding, or that any
use of this library is profitable or lawful in your jurisdiction. See
[SECURITY.md](SECURITY.md).

## What This Offers

### Production Safety

**Concurrency Safety**
- Atomic nonce management with cryptographic randomization
- Lock-protected shared state for credentials, caches, and rate limits
- No race conditions in concurrent order placement
- Safe batch operations via `asyncio.gather`

**Failure Resilience**
- Exponential backoff retry logic for transient errors
- Graceful degradation when services are unavailable
- Resource cleanup in exception paths (no memory leaks)
- Fail-closed behavior when exchange state is incomplete

**Error Handling**
- 26 typed exceptions vs 2 in official client
- Each exception includes context (token_id, price, amounts, etc.)
- Know exactly what failed and how to fix it
- Actionable error messages for debugging at 3am

**Rate Limiting**
- Per-endpoint rate limits matching Polymarket's quotas
- Automatic throttling to prevent blocking
- Configurable safety margins (default: 80% of limits)
- Low-overhead implementation

**Observability**
- Structured JSON logging with correlation IDs
- Optional Prometheus metrics (latency, success/failure rates, balances)
- Request tracing across components
- Ready for Elasticsearch/Datadog/Grafana

### Multi-Wallet Architecture

The primary motivation for building this. Supports:

**Unlimited Wallets**
- Track 100+ profitable wallets in real-time
- Lock-protected credential management
- Per-wallet rate limiting (no quota contamination)
- Isolated failure domains

**Batch Operations**
- Fetch positions across many wallets in one call
- Batch orderbook, trade, and activity reads
- Concurrent operations via `asyncio.gather`
- Efficient resource utilization

**Analytics**
- Aggregate P&L across all tracked wallets
- Detect consensus signals (when N+ wallets agree on outcome)
- Calculate win rates, top performers, market exposure
- Portfolio-level metrics and dashboards

### Performance

Measured against py-clob-client 0.28.0 on 2026-08-07: 10 tokens with live
orderbooks, 7 interleaved repetitions, median reported. Both libraries call the
same public CLOB endpoints from the same machine.

| Operation | py-clob-client | This library | Ratio |
|-----------|---------------|--------------|-------|
| Batch orderbooks (10 tokens, one `POST /books`) | 258 ms | 175 ms | **1.5x** |
| Sequential orderbooks (10 × `GET /book`) | 2668 ms | 1775 ms | **1.5x** |

The gap comes from connection reuse and async I/O, not from a different
endpoint. These numbers are network-bound and will vary with your location.

Batching is worth roughly 10x over sequential reads—but that is a property of
the API, not of this library: py-clob-client exposes the same `/books` endpoint
and gains the same 10x. What differs is the handling. When `/books` returns
fewer books than requested, py-clob-client returns the short list silently;
this library raises rather than hand back a partial result that looks complete.

### Financial Precision

**Decimal Everywhere**
- No float rounding errors in prices or amounts
- Financial-grade precision for all calculations
- Exact arithmetic with quantize() for rounding
- Pydantic auto-converts floats for convenience

```python
# Why this matters:
0.1 + 0.2                          # 0.30000000000000004 (float)
Decimal("0.1") + Decimal("0.2")    # 0.3 (exact)

# Over 1000 trades, float errors accumulate to real money loss
```

### Advanced Features

**Trading**
- Tick-aware order normalization and CLOB v2 EIP-712 signing
- Token allowance automation for MetaMask/EOA wallets
- Fee calculations before trading
- Fee-inclusive BUY reservation accounting
- Orderbook depth analysis for slippage estimation

**Real-Time Data**
- WebSocket orderbook updates (~100ms vs 1s polling)
- Auto-reconnect with exponential backoff
- Order fill notifications
- RTDS integration (12+ stream types: trades, price changes, market lifecycle)

**Data API**
- Positions with real-time P&L tracking
- Complete trade history with fees and timestamps
- Onchain activity monitoring
- Portfolio value breakdown
- Whale discovery (market holders with filtering)

## Quick Comparison

<table>
<tr><th>Feature</th><th>py-clob-client</th><th>polymarket-python-infrastructure</th></tr>
<tr><td>Lines of code</td><td>~1,900</td><td>~24,000</td></tr>
<tr><td>Multi-wallet support</td><td>❌</td><td>✅ Unlimited</td></tr>
<tr><td>Concurrency model</td><td>⚠️ Synchronous, no locking</td><td>✅ Async, lock-protected state</td></tr>
<tr><td>Rate limiting</td><td>❌</td><td>✅ Per-endpoint</td></tr>
<tr><td>Exception types</td><td>2</td><td>26 typed</td></tr>
<tr><td>Numeric precision</td><td>float</td><td>Decimal</td></tr>
<tr><td>Structured logging</td><td>❌</td><td>✅ JSON + correlation IDs</td></tr>
<tr><td>Prometheus metrics</td><td>❌</td><td>✅ Optional extra</td></tr>
<tr><td>WebSocket reconnect</td><td>❌</td><td>✅ Exponential backoff</td></tr>
<tr><td>Consensus detection</td><td>❌</td><td>✅ Multi-wallet analytics</td></tr>
</table>

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/perpetual-s/polymarket-python-infrastructure.git
cd polymarket-python-infrastructure
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Add `.[metrics]` for Prometheus metrics, `.[onchain]` for Web3-backed CTF
operations, `.[test]` for the test suite, or `.[dev]` for all development and
example dependencies. The compatibility file at `polymarket/requirements.txt`
installs the full development set.

Web3-backed CTF support is loaded lazily and metrics degrade to no-ops when
`prometheus-client` is absent. Public market-data use does not require Web3, a
metrics backend, or a private key.

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

## Multi-Wallet Tracking

```python
# Track 100+ wallets (e.g., profitable traders for copy trading)
tracked_wallets = [
    "0xabc...",  # Wallet with 85% win rate
    "0xdef...",  # Whale with $500k positions
    # ... 100+ more
]

async with PolymarketClient() as client:
    # Batch fetch all positions in one call
    wallet_positions = await client.get_positions_batch(tracked_wallets)

    # Aggregate P&L across all wallets
    metrics = await client.aggregate_multi_wallet_metrics(tracked_wallets)
    print(f"Total P&L: ${metrics['total_pnl']:,.2f}")
    print(f"Best performer: {metrics['top_performers'][0]}")

    # Detect consensus signals (5+ wallets betting same outcome)
    signals = await client.detect_signals(
        tracked_wallets,
        min_wallets=5,
        min_agreement=0.6,  # 60% agreement threshold
    )

    for signal in signals[:3]:
        print(f"Signal: {signal['wallet_count']} wallets → {signal['outcome']}")
        print(f"Market: {signal['title']}")
        print(f"Agreement: {signal['agreement_ratio']:.0%}")
        print(f"Total stake: ${signal['total_value']:,.0f}")
```

## Real-Time Data Streams

Subscription registration is synchronous; the client must already be running
inside an event loop.

```python
# WebSocket: Live orderbook updates (~100ms vs 1s polling)
def on_book_update(book):
    print(f"Best bid: {book.best_bid}")
    print(f"Best ask: {book.best_ask}")


client.subscribe_orderbook("TOKEN_ID", callback=on_book_update)


# RTDS: Price changes across multiple tokens
def on_price_change(message):
    print(f"Price update: {message.data['token_id']} → ${message.data['price']}")


client.subscribe_market_price_changes(
    callback=on_price_change,
    token_ids=["token1", "token2", "token3"],
)
```

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

## Architecture Decisions

### Why Decimal instead of float?

Financial-grade precision. Floats accumulate rounding errors:

```python
# With floats (py-clob-client)
price = 0.1 + 0.2  # 0.30000000000000004 (wrong)

# With Decimal (this library)
price = Decimal("0.1") + Decimal("0.2")  # 0.3 (exact)
```

In trading, `0.30000000000000004` != `0.3` costs real money.

### Why guarded nonce management?

Without atomic operations, race conditions cause duplicate nonces:

1. Task A reads nonce = 5
2. Task B reads nonce = 5
3. Both increment to 6
4. Both sign orders with nonce = 6
5. Second order rejected (duplicate)
6. Trade opportunity lost

This library uses lock-protected atomic operations with cryptographic randomization. No race conditions, ever.

### Why 26 exception types?

Because "API Error" tells you nothing at 3am when production breaks.

Specific exceptions enable specific fixes:
- `TickSizeError` → Adjust price rounding
- `InsufficientAllowanceError` → Run token approval
- `RateLimitError` → Back off for N seconds
- `UnsupportedResolution` → Skip the market, don't infer a payout
- `OrderDelayedError` → Don't panic, order is queued

Each exception includes context (token_id, price, amounts) for debugging.

## Design Philosophy

Built from production failures:

1. **Defensive programming** - Validate inputs before expensive API calls
2. **Fail fast** - Detect problems early with comprehensive validation
3. **Observability first** - Can't fix what you can't measure
4. **Type safety** - Full type hints, catch errors before runtime
5. **Resource cleanup** - No memory leaks, proper WebSocket cleanup
6. **Security** - Credential redaction in logs, cryptographic nonces

Every safety feature exists because something broke without it.

## When to Use This vs Official Client

### Use py-clob-client when:
- Learning Polymarket or experimenting
- Single wallet, low volume (<10 req/min)
- Simple scripts or one-off tasks
- Want minimal dependencies
- Don't need production safety

### Use this library when:
- Trading with real capital
- Need multi-wallet support (copy trading, tracking)
- High volume (>100 req/min)
- Concurrent trading across many markets
- Need observability (metrics, logs, tracing)
- Require reliability (bounded retries, auto-recovery, fail-closed reads)
- Want concurrent batch operations instead of sequential round-trips

## Migration from py-clob-client

```python
# Before (py-clob-client)
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

client = ClobClient("https://clob.polymarket.com", key=KEY, chain_id=137)
client.set_api_creds(client.create_or_derive_api_creds())

order = OrderArgs(token_id="...", price=0.55, size=100.0, side="BUY")
signed = client.create_order(order)
resp = client.post_order(signed, OrderType.GTC)

# After (this library)
import asyncio
from decimal import Decimal

from polymarket import OrderRequest, PolymarketClient, Side, WalletConfig


async def main():
    async with PolymarketClient() as client:
        wallet = WalletConfig(private_key=KEY)
        await client.add_wallet(wallet, wallet_id="primary", set_default=True)

        order = OrderRequest(
            token_id="...",
            price=Decimal("0.55"),  # Decimal instead of float
            size=Decimal("100.0"),
            side=Side.BUY,
        )
        return await client.place_order(order, wallet_id="primary")


resp = asyncio.run(main())
```

**Key changes:**
1. The client is async—use `async with` and `await` network calls
2. `price`/`size` are `Decimal` not `float` (auto-converts from float)
3. `side` is enum not string (accepts both)
4. Single `place_order()` instead of `create_order()` + `post_order()`
5. Specify `wallet_id` for multi-wallet support

**Migration time:** ~1 hour for typical codebase.

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

## Documentation

This README provides an overview and comparison. For detailed usage, see:

- **[polymarket/README.md](polymarket/README.md)** - Comprehensive library
  documentation: endpoint usage strategy (Gamma vs CLOB), public CLOB access,
  CTF and neg-risk utilities, WebSocket streams, and troubleshooting.
- **[polymarket/API_REFERENCE.md](polymarket/API_REFERENCE.md)** - Complete
  method and model reference, including authentication routing, typed result
  surfaces, error taxonomy, and rate limits.
- **[polymarket/QUICKSTART.md](polymarket/QUICKSTART.md)** - Public and
  authenticated examples for fast integration.
- **[polymarket/examples/](polymarket/examples/)** - 13 runnable patterns,
  including `10_production_safe_trading.py` (required reading),
  `11_ctf_neg_risk_features.py`, `12_public_clob_api.py`, and
  `13_portfolio_whale_discovery.py`.

## Validation

The default suite is hermetic. Live-network and testnet checks are opt-in.

```bash
PYTHONPATH=. python -m pytest -q polymarket/tests
```

Live or funded operations require explicit environment configuration. The
offline suite proves local contracts; it does not prove account entitlement,
funding, regional access, or exchange transport.

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## Author & Contact

Built and maintained by **Chaeho Shin**

- GitHub: [@perpetual-s](https://github.com/perpetual-s)
- Email: cogh0972@gmail.com
- Issues: [Report bugs or request features](https://github.com/perpetual-s/polymarket-python-infrastructure/issues)

## Contributing

Contributions welcome:
- Bug reports with reproduction steps
- Feature requests with real use cases
- Pull requests (must include tests)

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

MIT. See [LICENSE](LICENSE). Use however you want.

**Acknowledgments:**
- [py-clob-client](https://github.com/Polymarket/py-clob-client) - Reference implementation (MIT)
- [python-order-utils](https://github.com/Polymarket/python-order-utils) - EIP-712 signing (MIT)
- Polymarket team for excellent API infrastructure
