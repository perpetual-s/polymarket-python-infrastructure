# Quick Start Guide for Other Claudes

Ultra-fast reference for using this library in Strategy-1 and Strategy-3.

**v2.8:** Thread-safe request deduplication (2-10x efficiency), background worker pattern (prevents thread exhaustion), division-by-zero protection, balance cleanup, response validation, WebSocket cleanup. See `Documentation/ROBUSTNESS_AUDIT.md`.

## Installation

```python
# In your strategy backend:
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    # IMPORTANT: Import validation and fee utilities
    validate_order,
    validate_balance,
    calculate_net_cost,
)
```

## 5-Minute Setup (Production-Safe Pattern)

```python
# 1. Initialize
client = PolymarketClient()

# 2. Add Wallet
client.add_wallet(
    WalletConfig(private_key="0x..."),
    wallet_id="strategy1"
)

# 3. PRODUCTION-SAFE ORDER PLACEMENT
order = OrderRequest(
    token_id="123456",
    price=0.55,
    size=100.0,
    side=Side.BUY
)

# 3a. Validate order (CRITICAL)
valid, error = validate_order(order)
if not valid:
    print(f"Invalid order: {error}")
    return

# 3b. Calculate fees BEFORE placing order (CRITICAL)
net_cost, fee = calculate_net_cost(
    side=Side.BUY,
    price=0.55,
    size=100.0,
    fee_rate_bps=100  # 1% fee
)
print(f"Total cost: ${net_cost:.2f} (including ${fee:.2f} fee)")

# 3c. Validate balance including fees (CRITICAL)
balance = client.get_balance("strategy1")
valid, error = validate_balance(
    side=Side.BUY,
    price=0.55,
    size=100.0,
    available_usdc=balance.collateral,
    fee_rate_bps=100
)
if not valid:
    print(f"Insufficient balance: {error}")
    return

# 3d. Place order (after all validations passed)
response = client.place_order(order, wallet_id="strategy1")

# 4. Check Status
if response.success:
    print(f"Order placed: {response.order_id}")
    print(f"Cost: ${net_cost:.2f}")
else:
    print(f"Failed: {response.error_msg}")
```

## Common Tasks

### Get Market Data (Public API - No Auth Required)

**New in v3.1:** Public CLOB API for market data without authentication.

```python
# No wallet needed for public endpoints!
client = PolymarketClient()

# Single queries (200 req/10s)
spread = client.get_spread(token_id)
bid, ask = client.get_best_bid_ask(token_id)
midpoint = client.get_midpoint(token_id)

# Batch queries (80 req/10s - 10x more efficient!)
spreads = client.get_spreads([token_id1, token_id2, token_id3])
midpoints = client.get_midpoints([token_id1, token_id2, token_id3])

# Liquidity analysis
depth = client.get_liquidity_depth(token_id, price_range=0.05)
print(f"Total liquidity: ${depth['total_depth']:,.2f}")

# Find markets
markets = client.get_markets(slug="trump-vs-biden-2024")

# Get orderbook
book = client.get_orderbook(token_id="123")
print(f"Best ask: {book.asks[0][0]}")  # asks are tuples (price, size)
```

**See:** `examples/12_public_clob_api.py` for comprehensive examples.

### Check Balances
```python
balance = client.get_balance("strategy1")
print(f"USDC: ${balance.collateral:.2f}")
```

### Get Positions & Portfolio
```python
# Get positions with P&L
positions = client.get_positions("strategy1")
for pos in positions:
    print(f"{pos.title}: P&L ${pos.cash_pnl:.2f}")

# Get portfolio breakdown (new in v3.0+) 🆕
portfolio = client.get_portfolio_value("strategy1")
print(f"Total: ${portfolio.equity_total or portfolio.value:.2f}")
print(f"Active Bets: ${portfolio.bets or 0:.2f}")
print(f"Cash: ${portfolio.cash or 0:.2f}")
```

### Cancel Orders
```python
# Cancel single order
client.cancel_order(order_id, wallet_id="strategy1")

# Cancel all orders
client.cancel_all_orders(wallet_id="strategy1")
```

### Whale Discovery (Market Analysis) 🆕
```python
# Find large holders in a market (new in v3.0+)
whales = client.get_market_holders(
    market="0x123...",        # condition_id
    limit=100,
    min_balance=5000          # Filter: positions > $5000
)

for whale in whales:
    print(f"{whale.pseudonym}: ${whale.amount:,.2f}")

# Use for Strategy-3: Discover whales to track
```

## Strategy-3 Specific (100+ Wallets)

### Configuration
```python
# CRITICAL: Optimize for multi-wallet
client = PolymarketClient(
    pool_connections=100,
    pool_maxsize=200,
    batch_max_workers=20
)
```

### Batch Operations
```python
# Fetch 100+ wallet positions in parallel
wallets = ["0x...", "0x...", ...]  # 100+ addresses
positions = client.get_positions_batch(wallets)  # ~10s for 100 wallets

# Place multiple orders simultaneously
orders = [OrderRequest(...), OrderRequest(...)]
responses = client.place_orders_batch(orders)  # 10x faster

# Get multiple orderbooks
token_ids = ["123", "456", "789"]
books = client.get_orderbooks_batch(token_ids)  # 10x faster
```

### Aggregate Metrics
```python
metrics = client.aggregate_multi_wallet_metrics(wallet_addresses)
print(f"Total P&L: ${metrics['total_pnl']:,.2f}")
print(f"Active Wallets: {metrics['active_count']}")
```

### Detect Signals
```python
signals = client.detect_signals(
    wallet_addresses,
    min_wallets=5,      # At least 5 wallets
    min_agreement=0.7   # 70% agree
)
```

## Error Handling

```python
from shared.polymarket.exceptions import (
    InsufficientBalanceError,
    OrderRejectedError,
    RateLimitError,
    AuthenticationError
)

try:
    response = client.place_order(order)

except InsufficientBalanceError:
    print("Not enough USDC")

except OrderRejectedError as e:
    print(f"Order rejected: {e.reason}")

except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")

except AuthenticationError:
    print("API credentials invalid")
```

## Configuration via Environment

```bash
# .env file
POLYMARKET_POOL_CONNECTIONS=100
POLYMARKET_POOL_MAXSIZE=200
POLYMARKET_BATCH_MAX_WORKERS=20
POLYMARKET_REQUEST_TIMEOUT=30.0
POLYMARKET_MAX_RETRIES=3
```

## Troubleshooting

### "INVALID_NONCE" Error
✅ **FIXED**: Atomic nonce manager prevents this
- If still occurs, wait 30s and retry
- Library handles nonce synchronization

### "INSUFFICIENT_BALANCE" Error
```python
# Check balance first
balance = client.get_balance("wallet_id")
if balance.collateral < required_amount:
    print("Need more USDC")
```

### "TICK_SIZE" Error
```python
# Library auto-fetches tick size
# If error persists, use official tick size:
tick_size = client.clob.get_tick_size(token_id)
```

### Slow Performance with 100+ Wallets
```python
# ❌ Don't do this (sequential)
for wallet in wallets:
    positions = client.get_positions(wallet)

# ✅ Do this (parallel)
positions = client.get_positions_batch(wallets)  # 10x faster
```

### Rate Limiting
```python
# Library handles automatically
# If you hit limits:
client = PolymarketClient(
    rate_limit_margin=0.9  # Use 90% of limit (default: 80%)
)
```

## Examples

See `examples/` directory for complete examples:
- `01_simple_trading.py` - Strategy-1 basic trading
- `02_multi_wallet.py` - Strategy-3 multi-wallet tracking
- `03_batch_orders.py` - Batch order placement

## Need Help?

1. Check `Documentation/PRODUCTION_FIXES.md` - All features documented
2. Check `examples/` - Copy-paste ready code
3. Check error message - Library has helpful error messages
4. Check health: `client.health_check()` - Debug connectivity

## Performance Tips

### Strategy-1 (Single Wallet)
- Default settings work fine
- Use `place_order()` for single orders
- Enable metrics: `enable_metrics=True`

### Strategy-3 (100+ Wallets)
- **CRITICAL**: Set `pool_connections=100`, `pool_maxsize=200`
- **CRITICAL**: Use batch methods: `place_orders_batch()`, `get_positions_batch()`
- **CRITICAL**: Set `batch_max_workers=20`
- Monitor: `client.health_check()` shows performance

## Integration with PostgreSQL

```python
import psycopg2

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cursor = conn.cursor()

# Get data from Polymarket
positions = client.get_positions("wallet_id")

# Store in YOUR database (library doesn't write to DB)
for pos in positions:
    cursor.execute("""
        INSERT INTO positions (market, size, pnl, timestamp)
        VALUES (%s, %s, %s, NOW())
    """, (pos.slug, pos.size, pos.cash_pnl))

conn.commit()
```

## Health Check for Docker

```python
# For your dashboard's health endpoint
def health():
    status = client.health_check()
    return {
        "polymarket": status["status"],
        "circuit_breaker": status["circuit_breaker"],
        "inflight_orders": status["inflight_orders"]
    }
```

**That's it!** You're ready to build Strategy-1 and Strategy-3 dashboards.
