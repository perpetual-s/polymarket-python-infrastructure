# polymarket-python-infrastructure

Production-grade infrastructure for Polymarket prediction markets. Built for reliability, performance, and scale.

## Why This Exists

When I started, I used the official [py-clob-client](https://github.com/Polymarket/py-clob-client), but quickly ran into limitations when scaling to production. I needed to track 100+ wallets simultaneously, handle concurrent requests safely, and recover from failures automatically—things the official client wasn't built for.

So I built my own. After months of development and iteration, it became clear this infrastructure could help others avoid the same problems. I'm open-sourcing it hoping it helps other traders build more robust systems.

**This is what I actually use in production with real money.** Every feature exists because I needed it, every edge case handled because I hit it, every safety check added because something broke without it.

## Why "Infrastructure" not "Client"?

A client makes API requests. Infrastructure provides the foundation for production systems.

This includes:
- **Thread-safe multi-wallet architecture** for tracking hundreds of wallets concurrently
- **Production safety** with circuit breakers, rate limiting, and comprehensive error handling
- **Observability** through structured logging and Prometheus metrics
- **Performance** via batch operations and lock-free atomic operations
- **Reliability** with auto-recovery, retry logic, and defensive programming

Think of it as the difference between a bicycle (client) and a highway system (infrastructure).

## The Problem with py-clob-client

The official client works well for simple use cases, but has fundamental limitations for production:

**Architecture Limitations:**
- **Single wallet only** - Can't track multiple wallets or build copy-trading strategies
- **Not thread-safe** - Race conditions in nonce management cause order failures
- **No rate limiting** - Easy to hit API limits and get blocked
- **No circuit breaker** - Keeps hammering broken APIs, burning rate limits
- **Basic error handling** - Only 2 exception types, debugging production issues is difficult

**Implementation Issues:**
- **Float precision** - Uses floats for prices/amounts, causing rounding errors in financial calculations
- **No batch operations** - 10x slower when fetching data for multiple markets
- **No observability** - Can't debug production issues, no metrics or structured logging
- **No auto-recovery** - WebSocket disconnects require manual reconnection

These aren't criticisms—py-clob-client is a reference implementation, not production infrastructure. But if you're building something serious, you need more.

## What This Offers

### Production Safety

**Thread Safety**
- Atomic nonce management with cryptographic randomization
- Lock-free operations where possible
- No race conditions in concurrent order placement
- Safe multi-threaded batch operations

**Failure Resilience**
- Circuit breaker with auto-recovery after API failures
- Exponential backoff retry logic for transient errors
- Graceful degradation when services are unavailable
- Resource cleanup in exception paths (no memory leaks)

**Error Handling**
- 17 typed exceptions vs 2 in official client
- Each exception includes context (token_id, price, amounts, etc.)
- Know exactly what failed and how to fix it
- Actionable error messages for debugging at 3am

**Rate Limiting**
- Per-endpoint rate limits matching Polymarket's quotas
- Automatic throttling to prevent blocking
- Configurable safety margins (default: 80% of limits)
- Lock-free implementation for minimal overhead

**Observability**
- Structured JSON logging with correlation IDs
- Prometheus metrics (latency, success/failure rates, balances)
- Request tracing across components
- Ready for Elasticsearch/Datadog/Grafana

### Multi-Wallet Architecture

The primary motivation for building this. Supports:

**Unlimited Wallets**
- Track 100+ profitable wallets in real-time
- Thread-safe credential management
- Per-wallet rate limiting (no quota contamination)
- Isolated failure domains

**Batch Operations**
- Fetch positions for 100 wallets in seconds, not minutes
- Place multiple orders in single request (10x faster)
- Parallel operations with ThreadPoolExecutor
- Efficient resource utilization

**Analytics**
- Aggregate P&L across all tracked wallets
- Detect consensus signals (when N+ wallets agree on outcome)
- Calculate win rates, top performers, market exposure
- Portfolio-level metrics and dashboards

### Performance

Not just features—measurably faster:

| Operation | py-clob-client | This Library | Improvement |
|-----------|---------------|--------------|-------------|
| Batch orders (10) | 2-5 sec | 300-700ms | **7-10x** |
| Positions (100 wallets) | 200-400 sec | 20-40 sec | **10x** |
| Nonce generation | Race conditions | Lock-free atomic | **Race-free** |
| Cache eviction | O(n) scan | O(1) LRU | **100x** |

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
- Batch order placement (POST /orders endpoint)
- Token allowance automation for MetaMask/EOA wallets
- Fee calculations before trading
- Order profitability validation
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
<tr><td>Lines of code</td><td>~1,900</td><td>~8,700</td></tr>
<tr><td>Multi-wallet support</td><td>❌</td><td>✅ Unlimited</td></tr>
<tr><td>Thread safety</td><td>⚠️ Race conditions</td><td>✅ Lock-free atomic</td></tr>
<tr><td>Rate limiting</td><td>❌</td><td>✅ Per-endpoint</td></tr>
<tr><td>Circuit breaker</td><td>❌</td><td>✅ Auto-recovery</td></tr>
<tr><td>Exception types</td><td>2</td><td>17 typed</td></tr>
<tr><td>Batch operations</td><td>❌</td><td>✅ 10x faster</td></tr>
<tr><td>Numeric precision</td><td>float</td><td>Decimal</td></tr>
<tr><td>Structured logging</td><td>❌</td><td>✅ JSON + correlation IDs</td></tr>
<tr><td>Prometheus metrics</td><td>❌</td><td>✅ Built-in</td></tr>
<tr><td>WebSocket reconnect</td><td>❌</td><td>✅ Exponential backoff</td></tr>
<tr><td>Consensus detection</td><td>❌</td><td>✅ Multi-wallet analytics</td></tr>
</table>

## Installation

```bash
git clone https://github.com/perpetual-s/polymarket-python-infrastructure.git
cd polymarket-python-infrastructure
pip install -r polymarket/requirements.txt
```

**Requirements:** Python 3.9+

**Dependencies:**
- web3>=7.14.0
- eth-account>=0.13.7
- pydantic>=2.12.3
- pydantic-settings>=2.7.0
- requests>=2.32.3
- websocket-client>=1.8.0
- prometheus-client>=0.23.1

**Polymarket-specific:**
- poly_eip712_structs==0.0.1
- py_order_utils==0.3.2

## Usage Examples

### Single Wallet Trading

```python
from decimal import Decimal
from polymarket import PolymarketClient, WalletConfig, OrderRequest, Side, OrderType

client = PolymarketClient()

# Add wallet
wallet = WalletConfig(private_key="0x...")
client.add_wallet(wallet, wallet_id="main", set_default=True)

# Place order with automatic retry, balance checks, fee validation
order = OrderRequest(
    token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
    price=Decimal("0.55"),  # Exact precision, no rounding errors
    size=Decimal("100.0"),
    side=Side.BUY,
    order_type=OrderType.GTC
)

try:
    response = client.place_order(order, wallet_id="main")
    print(f"Order placed: {response.order_id}")
except InsufficientBalanceError as e:
    print(f"Not enough USDC: {e.message}")
except TickSizeError as e:
    # Auto-fix: round to valid tick size
    order.price = order.price.quantize(Decimal("0.01"))
    response = client.place_order(order, wallet_id="main")
```

### Multi-Wallet Tracking

```python
# Track 100+ wallets (e.g., profitable traders for copy trading)
tracked_wallets = [
    "0xabc...",  # Wallet with 85% win rate
    "0xdef...",  # Whale with $500k positions
    # ... 100+ more
]

# Batch fetch all positions (10x faster than sequential)
wallet_positions = client.get_positions_batch(tracked_wallets)

# Aggregate P&L across all wallets
metrics = client.aggregate_multi_wallet_metrics(tracked_wallets)
print(f"Total P&L: ${metrics['total_pnl']:,.2f}")
print(f"Best performer: {metrics['top_performers'][0]}")

# Detect consensus signals (5+ wallets betting same outcome)
signals = client.detect_signals(
    tracked_wallets,
    min_wallets=5,
    min_agreement=0.6  # 60% agreement threshold
)

for signal in signals[:3]:
    print(f"Signal: {signal['wallet_count']} wallets → {signal['outcome']}")
    print(f"Market: {signal['title']}")
    print(f"Agreement: {signal['agreement_ratio']:.0%}")
    print(f"Total stake: ${signal['total_value']:,.0f}")
```

### Production-Safe Order Placement

```python
from polymarket.utils.fees import calculate_order_fee
from polymarket.utils.validation import validate_order_profitability

# Calculate fees before trading
fee = calculate_order_fee(
    size=Decimal("100"),
    price=Decimal("0.55"),
    side=Side.BUY,
    fee_rate_bps=10  # 0.1%
)

# Validate profitability (reject if fees eat all profit)
is_profitable = validate_order_profitability(
    entry_price=Decimal("0.55"),
    exit_price=Decimal("0.60"),
    size=Decimal("100"),
    fee_rate_bps=10,
    min_profit_pct=Decimal("2.0")  # Require 2% minimum profit
)

if is_profitable:
    response = client.place_order(order, wallet_id="main")
else:
    print("Skipping trade: fees too high relative to expected profit")
```

### Real-Time Data Streams

```python
# WebSocket: Live orderbook updates (~100ms vs 1s polling)
def on_book_update(data):
    print(f"Best bid: {data['bids'][0]['price']}")
    print(f"Best ask: {data['asks'][0]['price']}")

client.subscribe_orderbook(token_id, callback=on_book_update)

# RTDS: Price changes across multiple tokens
def on_price_change(message):
    print(f"Price update: {message.data['token_id']} → ${message.data['price']}")

client.subscribe_market_price_changes(
    callback=on_price_change,
    token_ids=["token1", "token2", "token3"]
)
```

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

### Why thread-safe nonce management?

Without atomic operations, race conditions cause duplicate nonces:

1. Thread A reads nonce = 5
2. Thread B reads nonce = 5
3. Both increment to 6
4. Both sign orders with nonce = 6
5. Second order rejected (duplicate)
6. Trade opportunity lost

This library uses lock-free atomic operations with cryptographic randomization. No race conditions, ever.

### Why circuit breaker?

Prevents cascade failures when Polymarket API has issues:

**Without circuit breaker:**
1. API returns 500 errors
2. Bot retries every request
3. Burns through rate limits
4. API recovers but you're blocked for 10 minutes
5. Miss profitable opportunities

**With circuit breaker:**
1. After 5 failures, circuit opens
2. Fail fast for 60 seconds (no rate limit burn)
3. Try one request (half-open state)
4. If successful, resume normal operation
5. If failed, stay open another 60 seconds

### Why 17 exception types?

Because "API Error" tells you nothing at 3am when production breaks.

Specific exceptions enable specific fixes:
- `TickSizeError` → Adjust price rounding
- `InsufficientAllowanceError` → Run token approval
- `RateLimitError` → Back off for N seconds
- `CircuitBreakerError` → Wait for recovery
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
- Concurrent trading across strategies
- Need observability (metrics, logs, tracing)
- Require reliability (circuit breaker, retry, recovery)
- Want performance (batch operations, 10-100x faster)

## Documentation

This README provides an overview and comparison. For detailed usage, see:

**Core Documentation:**
- **[README.md](polymarket/README.md)** - Comprehensive library documentation covering:
  - All 40+ methods with parameters and return types
  - Endpoint usage strategy (Gamma vs CLOB APIs)
  - Public CLOB API (no authentication required)
  - CTF & Neg-Risk utilities
  - Real-time WebSocket streams (12+ types)
  - Production checklist and troubleshooting
  - Architecture overview (36 files, 9 modules)

- **[API_REFERENCE.md](polymarket/API_REFERENCE.md)** - Complete API reference:
  - What's new in v3.1 (security & performance improvements)
  - Authentication methods (L1/L2, EOA/Proxy)
  - Market data API (Gamma, CLOB, simplified markets)
  - Trading API (orders, cancellations, batch operations)
  - Dashboard API (positions, trades, P&L, portfolio)
  - WebSocket API (orderbook updates, real-time streams)
  - Data types and error handling
  - Rate limits and advanced patterns

- **[QUICKSTART.md](polymarket/QUICKSTART.md)** - 5-minute integration:
  - Production-safe order placement pattern
  - Fee calculation and validation
  - Balance checks before trading
  - Common tasks with code examples

**Examples:**
- **[examples/](polymarket/examples/)** - 13 production-ready patterns:
  - `10_production_safe_trading.py` - Complete production pattern (required reading)
  - `11_ctf_neg_risk_features.py` - Fee calculations and validation
  - `12_public_clob_api.py` - Market data without authentication
  - `13_portfolio_whale_discovery.py` - Portfolio analytics and whale tracking
  - ... and 9 more examples covering all features

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
from polymarket import PolymarketClient, WalletConfig, OrderRequest, Side, OrderType
from decimal import Decimal

client = PolymarketClient()
wallet = WalletConfig(private_key=KEY)
client.add_wallet(wallet, wallet_id="main", set_default=True)

order = OrderRequest(
    token_id="...",
    price=Decimal("0.55"),  # Decimal instead of float
    size=Decimal("100.0"),
    side=Side.BUY,
    order_type=OrderType.GTC
)
resp = client.place_order(order, wallet_id="main")
```

**Key changes:**
1. `price`/`size` are `Decimal` not `float` (auto-converts from float)
2. `side` is enum not string (accepts both)
3. Single `place_order()` instead of `create_order()` + `post_order()`
4. Specify `wallet_id` for multi-wallet support

**Migration time:** ~1 hour for typical codebase.

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

## Version History

**v3.1** - Security & Performance Hardening
  - Credential redaction filter (prevents key leakage in logs)
  - Cryptographic nonce randomization (prevents front-running attacks)
  - 100x faster cache eviction (OrderedDict O(1) LRU vs O(n) scan)
  - Memory leak fixes (AtomicNonceManager cleanup)
  - Price validation error handling
  - All improvements backward compatible

**v3.0** - Decimal Migration
  - Financial-grade Decimal precision for all numeric types
  - Pydantic validators for backward compatibility (float auto-converts)
  - Breaking change: Return types updated (float → Decimal)

**v2.8** - Robustness Audit
  - 6 critical fixes validated against official py-clob-client
  - Exception imports, division by zero, balance cleanup
  - Thread leak prevention, response validation, WebSocket cleanup
  - All implementations exceed official reference in defensive programming

**v2.7** - Critical Fixes
  - Fee calculation formula symmetric (BUY/SELL consistent USD semantics)

**v2.3** - CTF Integration
  - Fee calculations, validation utilities, NegRiskAdapter

**v2.1** - Multi-Wallet Features
  - Batch operations, dashboard helpers, consensus detection

**v2.0** - Data API Integration
  - Positions, trades, activity endpoints

**v1.0** - Initial Release
  - Production-ready trading + market data

## License

MIT - Use however you want.

**Acknowledgments:**
- [py-clob-client](https://github.com/Polymarket/py-clob-client) - Reference implementation (MIT)
- [python-order-utils](https://github.com/Polymarket/python-order-utils) - EIP-712 signing (MIT)
- Polymarket team for excellent API infrastructure
