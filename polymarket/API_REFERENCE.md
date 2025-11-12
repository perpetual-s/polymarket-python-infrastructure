# Polymarket API Reference

Complete API reference for shared/polymarket library. Use this instead of official Polymarket docs.

**Version:** 3.1 (Security & Performance Hardening)
**Updated:** 2025-11-09
**Latest:** Critical security patches and performance optimizations
**v3.1 Improvements:** 🔒 Security hardening (credential redaction, crypto nonces) + ⚡ Performance (100x cache speedup)
**Phase 1 Improvements:** ✅ Completed (GitHub analysis integration)
**Phase 2 Real-Time Data:** ✅ Completed (12+ WebSocket streams)
**Phase 3 RFQ System:** ✅ Completed (OTC trading)
**Phase 4-6 Enhancements:** ✅ Completed (Batch orderbooks, Missing endpoints, Tick validation)
**CTF Integration:** ✅ Completed (v1.0.3 - Fee calculations, Validation, Neg-Risk adapter)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Authentication](#authentication)
3. [Market Data API](#market-data-api)
4. **🆕 [Public CLOB API (No Auth Required)](#public-clob-api-no-auth-required)** - Spreads, Liquidity, Batch operations
5. [Trading API (CLOB)](#trading-api-clob)
6. **⭐ [CTF & Neg-Risk Utilities](#ctf--neg-risk-utilities)** - Fee calculations, Validation, NegRiskAdapter
7. [Dashboard API](#dashboard-api)
8. [WebSocket API](#websocket-api)
9. **🆕 [Real-Time Data Streams (Phase 2)](#real-time-data-streams-phase-2)**
10. [Data Types](#data-types)
11. [Rate Limits](#rate-limits)
12. [Error Handling](#error-handling)
13. [Advanced Usage](#advanced-usage)

---

## What's New in v3.1 (2025-11-09)

### 🔒 Security Hardening

**Automatic Credential Redaction**
- Private keys, API secrets, and passphrases automatically redacted in logs
- Prevents credential leakage in exception traces and debug output
- Enabled by default when using `configure_structured_logging()`
- See: `examples/05_structured_logging.py` for demonstration

**Cryptographic Nonce Randomization**
- Nonce generation now uses `secrets.randbelow()` for unpredictable values
- Prevents nonce prediction and front-running attacks
- Internal security improvement (no API changes)

### ⚡ Performance Optimizations

**100x Faster Cache Eviction**
- Replaced O(n) min() scan with OrderedDict for O(1) LRU operations
- Performance improvement: 100μs → 1μs at 10,000 cache entries
- Prevents cache operations from becoming bottleneck at scale
- Affects: Market metadata caching (tick sizes, fee rates, neg_risk flags)

**Memory Leak Fixes**
- AtomicNonceManager now properly cleans up inactive addresses
- Prevents unbounded memory growth with ephemeral wallet addresses
- Long-running processes no longer leak memory with many wallets

### 🛠️ Stability Improvements

**Better Price Validation**
- `_price_valid()` now raises ValidationError instead of silent failure
- Prevents invalid prices from being accepted
- Clearer error messages for debugging

**Enhanced Error Handling**
- Improved exception messages for price validation
- Better logging of validation failures
- Production-ready error reporting

### Migration Notes

**No breaking changes** - All improvements are backward compatible.

**Automatic benefits:**
- Credential redaction: Enabled automatically in structured logging
- Performance: Cache speedup applies transparently
- Security: Nonce randomization works automatically
- Stability: Better errors without code changes

---

## Quick Start

### Simple Example

```python
from decimal import Decimal
from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side

# Initialize
client = PolymarketClient()

# Add wallet
wallet = WalletConfig(private_key="0x...")
client.add_wallet(wallet, wallet_id="strategy1")

# Get markets
markets = client.get_markets(active=True, limit=10)

# Place order
order = OrderRequest(
    token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
    price=Decimal("0.55"),  # Use Decimal for exact precision
    size=Decimal("100.0"),  # Use Decimal for exact precision
    side=Side.BUY
)
response = client.place_order(order, wallet_id="strategy1")
```

### Production-Safe Pattern (RECOMMENDED)

```python
from decimal import Decimal
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    # CRITICAL: Import validation utilities
    validate_order,
    validate_balance,
    calculate_net_cost,
)

client = PolymarketClient()
client.add_wallet(WalletConfig(private_key="0x..."), wallet_id="strategy1")

# Create order
order = OrderRequest(
    token_id="123",
    price=Decimal("0.55"),   # Use Decimal for exact precision
    size=Decimal("100.0"),   # Use Decimal for exact precision
    side=Side.BUY
)

# Validate order
valid, error = validate_order(order)
if not valid:
    raise ValueError(error)

# Calculate fees (accepts Decimal)
net_cost, fee = calculate_net_cost(Side.BUY, Decimal("0.55"), Decimal("100.0"), 100)

# Validate balance (accepts Decimal)
balance = client.get_balance("strategy1")
valid, error = validate_balance(
    Side.BUY, Decimal("0.55"), Decimal("100.0"),
    balance.collateral, Decimal("0"), 100
)
if not valid:
    raise InsufficientBalanceError(error)

# Place order (after all checks passed)
response = client.place_order(order, wallet_id="strategy1")
```

**See:** [CTF & Neg-Risk Utilities](#ctf--neg-risk-utilities) for complete validation guide

---

## Authentication

### Wallet Registration

Polymarket uses wallet-based authentication. **First-time setup:**

1. **Visit app.polymarket.com** and connect your wallet
2. **Derive API credentials** (done automatically by library)
3. **Set token allowances** for USDC + CTF contracts (one-time)

### Token Allowances (CRITICAL)

Before trading, approve 6 contracts:
```python
from shared.polymarket.utils.allowances import AllowanceManager

manager = AllowanceManager()

# Check allowances
status = manager.has_sufficient_allowances(wallet.address)
if not status["ready"]:
    # Set allowances (costs ~$3-5 gas)
    tx_hashes = manager.set_allowances(wallet.private_key)
    manager.wait_for_approvals(tx_hashes)
```

**Contracts approved:**
- CTF Exchange (USDCExchange, NegRiskUSDCExchange, NegRiskCTFExchange)
- Collateral Token (collateralApproval, negRiskCollateralApproval)
- CTF Adapter (collateralApprovalCTFExchange)

### API Key Derivation

Library handles this automatically when adding wallet:
1. Creates L1 auth headers (EIP-712 signature)
2. Calls POST /auth/api-key or GET /auth/derive-api-key
3. Stores credentials in KeyManager (memory only, never persisted)

---

## Market Data API

### Get Markets

```python
markets = client.get_markets(
    limit=100,           # Max results (default: 100, max: 1000)
    offset=0,            # Pagination offset
    active=True,         # Filter by active status
    closed=False,        # Filter by closed status
    tag_id=None,         # Filter by category tag
    slug=None            # Filter by market slug
)
```

**Returns:** `List[Market]`

**Market fields:**
- `id` (str) - Market ID
- `question` (str) - Market question
- `slug` (str) - URL-friendly slug
- `condition_id` (str) - Condition ID on blockchain
- `category` (str) - Category name
- `outcomes` (list[str]) - Outcome names (e.g., ["Yes", "NO"])
- `outcome_prices` (list[Decimal]) - Current prices for each outcome
- `tokens` (list[str]) - ERC1155 token IDs for each outcome
- `volume` (Decimal) - Total USD volume
- `liquidity` (Decimal) - Current liquidity
- `active` (bool) - Is market active
- `closed` (bool) - Is market closed
- `start_date` (datetime) - Market start
- `end_date` (datetime) - Market end

**New fields (Phase 1 - from official Polymarket agents):**
- `rewards_min_size` (Decimal, optional) - Minimum size for rewards
- `rewards_max_spread` (Decimal, optional) - Maximum spread for rewards
- `ticker` (str, optional) - Short ticker/code for market
- `new` (bool, optional) - Newly created market flag
- `featured` (bool, optional) - Featured market flag
- `restricted` (bool, optional) - Geographic/access restrictions
- `archived` (bool, optional) - Archived/deprecated market

**Example:**
```python
# Get top 5 active markets
markets = client.get_markets(active=True, limit=5)

for market in markets:
    print(f"{market.question}")
    print(f"  Outcomes: {', '.join(market.outcomes)}")
    print(f"  Volume: ${market.volume:,.0f}")
    print(f"  Token IDs: {market.tokens}")
```

### Get Market by Slug

```python
market = client.get_market_by_slug("trump-2024-election")
```

**Returns:** `Optional[Market]` (None if not found)

### Get Market by ID

```python
market = client.get_market_by_id("12345")
```

**Returns:** `Optional[Market]`

### Search Markets

```python
results = client.search_markets(query="bitcoin", limit=20)
```

**Returns:** `List[Market]`

**Note:** Requires authentication (401 without wallet)

### Get Simplified Markets (RECOMMENDED for Real-Time)

**⚡ 10-20x faster than `get_markets()` - use for bot operations**

```python
markets = client.get_simplified_markets(
    limit=100,           # Max results (default: 100)
    offset=0,            # Pagination offset
    active=True,         # Filter by active status
    closed=False         # Filter by closed status
)
```

**Returns:** `List[dict]` with minimal fields:
- `condition_id` (str) - Condition ID
- `tokens` (list[str]) - Token IDs
- `question` (str) - Market question
- `active` (bool) - Active status
- `closed` (bool) - Closed status

**Performance Comparison:**
- `get_markets(limit=500)`: 30-60 seconds (full data, 20+ fields)
- `get_simplified_markets(limit=500)`: 2-5 seconds (essential fields only)

**Use Cases:**
- ✅ Bot creation (fast market discovery)
- ✅ Real-time trading decisions
- ✅ Market status checks
- ❌ Analytics (use `get_markets()` instead)

---

### 🆕 Phase 1: Helper Methods (from official Polymarket agents)

#### Get All Current Markets (Auto-Pagination)

```python
# Fetches ALL active, non-closed, non-archived markets (auto-pagination)
all_markets = client.get_all_current_markets(limit=100)  # limit is batch size
```

**Returns:** `List[Market]`

**Features:**
- Automatically paginates through all pages
- Only returns active, non-closed, non-archived markets
- Batch size controlled by `limit` parameter

**Example:**
```python
# Get all current tradable markets
all_markets = client.get_all_current_markets()
print(f"Found {len(all_markets)} total current markets")

# Filter for high volume
high_volume = [m for m in all_markets if m.volume > 100000]
```

#### Get CLOB Tradable Markets

```python
# Only get markets with order book enabled (tokens assigned)
tradable = client.get_clob_tradable_markets(limit=100)
```

**Returns:** `List[Market]`

**Features:**
- Filters for markets with `tokens` (CLOB trading available)
- Only active, non-closed markets
- Perfect for Strategy-1 (spread farming)

**Example:**
```python
# Get tradable markets and check spreads
tradable_markets = client.get_clob_tradable_markets(limit=50)

for market in tradable_markets:
    if len(market.tokens) >= 2:
        token_id = market.tokens[0]
        book = client.get_orderbook(token_id)
        if book.spread and book.spread > 0.02:  # 2% spread
            print(f"High spread opportunity: {market.question}")
```

#### Get Events

```python
# Get events (groups of related markets)
events = client.get_events(
    limit=100,
    offset=0,
    active=True,
    closed=False,
    archived=False  # NEW: Phase 1 parameter
)
```

**Returns:** `List[Event]`

**Event fields:**
- `id` (str) - Event ID
- `slug` (str) - URL-friendly slug
- `title` (str) - Event title
- `description` (str, optional) - Event description
- `ticker` (str, optional) - Short ticker/code
- `active` (bool) - Is event active
- `closed` (bool) - Is event closed
- `archived` (bool) - Is event archived
- `new` (bool, optional) - Newly created event
- `featured` (bool, optional) - Featured event
- `restricted` (bool, optional) - Geographic restrictions
- `start_date` (datetime, optional) - Event start
- `end_date` (datetime, optional) - Event end
- `markets` (list[str]) - Market IDs in this event
- `neg_risk` (bool, optional) - Negative risk event

**Example:**
```python
# Get all active events
events = client.get_events(active=True, limit=50)

for event in events:
    print(f"{event.title} ({len(event.markets)} markets)")
```

#### Filter Events for Trading

```python
# Filter events to only tradeable ones (no restrictions, not archived/closed)
tradeable_events = client.filter_events_for_trading(events)
```

**Returns:** `List[Event]`

**Filters out:**
- Restricted events
- Archived events
- Closed events
- Inactive events

**Example:**
```python
all_events = client.get_events(limit=100)
tradeable = client.filter_events_for_trading(all_events)
print(f"Tradeable: {len(tradeable)} out of {len(all_events)}")
```

#### Get All Tradeable Events

```python
# Convenience method: get_events() + filter_events_for_trading()
tradeable_events = client.get_all_tradeable_events(limit=100)
```

**Returns:** `List[Event]`

**Example:**
```python
# Get only tradeable events in one call
events = client.get_all_tradeable_events()

for event in events:
    # Get all markets in this event
    market_ids = event.markets
    print(f"{event.title}: {len(market_ids)} markets")
```

---

### Get Market Holders

```python
holders = client.get_market_holders(
    market="0x1234...",  # Market condition ID
    limit=100,           # Max results (default: 100, max: 500)
    min_balance=1        # Minimum position size (default: 1) 🆕
)
```

**Returns:** `List[Holder]`

**Holder fields:**
- `proxy_wallet` (str) - Proxy wallet address
- `amount` (Decimal) - Position size
- `pseudonym` (str) - User pseudonym (if public)

**Example:**
```python
# Get top 10 holders
holders = client.get_market_holders(market.condition_id, limit=10)

for i, holder in enumerate(holders, 1):
    print(f"{i}. {holder.pseudonym or 'Anonymous'}: {holder.amount:.2f} shares")

# Whale discovery (new in v3.0+) 🆕
whales = client.get_market_holders(
    market=market.condition_id,
    limit=100,
    min_balance=5000  # Only positions > $5000
)
print(f"Found {len(whales)} whales with >$5000 positions")
```

---

## Public CLOB API (No Auth Required)

**New in v3.1:** Comprehensive public API for market data without authentication.

### Why Use Public Endpoints?

- ✅ **No wallet needed** - No private keys or API credentials
- ✅ **Faster** - No signature overhead (~50-100ms saved per request)
- ✅ **Higher throughput** - Doesn't consume trading rate limits
- ✅ **Batch operations** - 10x more efficient (80 req/10s for batch vs 200 req/10s single)

**Use cases:** Price monitoring, liquidity analysis, market research, dashboards, backtesting.

**Complete example:** See `examples/12_public_clob_api.py`

---

### Get Spread

Get bid-ask spread for a token.

**Rate limit:** General CLOB (5,000 req/10s)

```python
spread = client.get_spread(token_id)
# Returns: 0.05 (float) or None if unavailable
```

---

### Get Spreads (Batch)

Get spreads for multiple tokens in one call.

**Rate limit:** 80 req/10s (10x more efficient!)

```python
token_ids = [token_id1, token_id2, token_id3]
spreads = client.get_spreads(token_ids)
# Returns: {token_id1: 0.05, token_id2: 0.03, token_id3: None}
```

---

### Get Midpoints (Batch)

Get midpoint prices for multiple tokens.

**Rate limit:** 80 req/10s

```python
token_ids = [token_id1, token_id2, token_id3]
midpoints = client.get_midpoints(token_ids)
# Returns: {token_id1: 0.55, token_id2: 0.72, token_id3: 0.45}
```

---

### Get Prices (Batch)

Get prices for multiple tokens and sides.

**Rate limit:** 80 req/10s

```python
params = [
    {"token_id": token_id1, "side": "BUY"},
    {"token_id": token_id1, "side": "SELL"},
    {"token_id": token_id2, "side": "BUY"}
]
prices = client.get_prices(params)
# Returns: {
#     "token_id1_BUY": 0.55,
#     "token_id1_SELL": 0.60,
#     "token_id2_BUY": 0.72
# }
```

---

### Get Best Bid/Ask

Get top of book (best bid and ask prices).

**Rate limit:** 200 req/10s (uses get_orderbook internally)

```python
bid, ask = client.get_best_bid_ask(token_id)
# Returns: (0.54, 0.56) or None if orderbook empty
```

---

### Get Liquidity Depth

Calculate liquidity depth within price range.

**Rate limit:** 200 req/10s (uses get_orderbook internally)

```python
# Liquidity within 5% of best bid/ask
depth = client.get_liquidity_depth(token_id, price_range=0.05)
# Returns: {
#     "bid_depth": 1500.50,      # Total USDC on bid side
#     "ask_depth": 2300.25,      # Total USDC on ask side
#     "bid_levels": 8,            # Number of bid price levels
#     "ask_levels": 12,           # Number of ask price levels
#     "total_depth": 3800.75      # Total liquidity
# }

# Tight liquidity (1%)
tight = client.get_liquidity_depth(token_id, price_range=0.01)

# Wide liquidity (10%)
wide = client.get_liquidity_depth(token_id, price_range=0.10)
```

---

### Get Markets (Full)

Get complete market list with all data.

**Rate limit:** 250 req/10s (general markets endpoint)

```python
# Get first page
markets = client.get_markets_full(next_cursor="MA==")
# Returns: {
#     "data": [...],  # List of complete market objects
#     "next_cursor": "..." # Use for pagination
# }

# Get next page
markets_page2 = client.get_markets_full(next_cursor=markets["next_cursor"])
```

**Note:** Slower than `get_simplified_markets()` but includes complete data. Use for analytics, not real-time trading.

---

### Get Market by Condition ID

Get individual market details.

**Rate limit:** 50 req/10s

```python
market = client.get_market_by_condition(condition_id)
# Returns: Full market dictionary with all fields
```

---

### Get Market Trade Events

Get trade events for a market.

**Rate limit:** General CLOB (5,000 req/10s)

```python
events = client.get_market_trades_events(condition_id)
# Returns: List of trade event dictionaries
for event in events:
    print(f"Trade: {event['side']} {event['size']} @ {event['price']}")
```

---

### Rate Limits Summary (Public Endpoints)

From [official Polymarket docs](https://docs.polymarket.com/quickstart/introduction/rate-limits):

| Endpoint | Rate Limit | Notes |
|----------|------------|-------|
| General CLOB | 5,000 req/10s | Baseline for unlisted endpoints |
| /book, /price, /midprice | 200 req/10s | Single token queries |
| /books, /prices, /midprices, /spreads | 80 req/10s | Batch operations (10x more efficient) |
| /markets (general) | 250 req/10s | Full market data |
| /markets (listing) | 100 req/10s | Market listing |
| /markets/0x (individual) | 50 req/10s | Single market lookup |
| /ok (health check) | 50 req/10s | Server health |

**Enforcement:** Requests over limit are delayed/queued (Cloudflare throttling), not dropped.

---

### Public vs Authenticated Comparison

| Feature | Public API | Authenticated API |
|---------|------------|-------------------|
| **Authentication** | None required | API key + wallet signature |
| **Speed** | Faster (no signing) | ~50-100ms overhead per request |
| **Rate Limits** | 200-5,000 req/10s | 2,400 req/10s (trading) |
| **Use Case** | Market data, monitoring | Order placement, account queries |
| **Consumes Trading Quota** | No | Yes |

**Best Practice:** Use public endpoints for all market data queries to maximize trading throughput.

---

## Trading API (CLOB)

### Place Order

**Phase 6 Enhancement:** Orders now automatically fetch tick size, fee rate, and neg risk from API before signing (prevents invalid orders).

```python
from decimal import Decimal
from shared.polymarket import OrderRequest, Side, OrderType

order = OrderRequest(
    token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
    price=Decimal("0.555"),  # Validated against market's tick size automatically
    size=Decimal("100.0"),   # Order size in USDC
    side=Side.BUY,           # BUY or SELL
    order_type=OrderType.GTC # GTC, GTD, FOK, FAK
)

response = client.place_order(
    order,
    wallet_id="strategy1",
    skip_balance_check=False  # Set True to skip pre-flight balance check
)
```

**Returns:** `OrderResponse`

**Phase 6 Automatic Validation:**
- ✅ Tick size fetched from API (validates price is valid multiple)
- ✅ Fee rate fetched from API (ensures correct maker/taker fees)
- ✅ Neg risk flag fetched (handles multi-outcome markets correctly)
- ✅ Invalid orders rejected BEFORE signing (saves gas fees)

**OrderResponse fields:**
- `success` (bool) - Did order succeed
- `order_id` (str) - Order ID if successful
- `status` (OrderStatus) - LIVE, MATCHED, DELAYED, UNMATCHED, CANCELLED
- `error_msg` (str) - Error message if failed
- `order_hashes` (list[str]) - Transaction hashes

**Order Types:**
- `GTC` (Good-til-Cancelled) - Stays until filled or cancelled
- `GTD` (Good-til-Date) - Expires at timestamp (requires `expiration` field, min 60s from now)
- `FOK` (Fill-or-Kill) - Fill immediately or cancel
- `FAK` (Fill-and-Kill) - Fill partial and cancel rest

**Example:**
```python
# GTC limit order
order = OrderRequest(
    token_id=market.tokens[0],
    price=Decimal("0.55"),
    size=Decimal("100.0"),
    side=Side.BUY,
    order_type=OrderType.GTC
)
response = client.place_order(order, wallet_id="strategy1")

if response.success:
    print(f"Order placed: {response.order_id}")
else:
    print(f"Order failed: {response.error_msg}")
```

### Place Batch Orders

```python
orders = [
    OrderRequest(token_id="123", price=Decimal("0.55"), size=Decimal("100.0"), side=Side.BUY),
    OrderRequest(token_id="456", price=Decimal("0.60"), size=Decimal("200.0"), side=Side.BUY),
]

responses = client.place_orders_batch(orders, wallet_id="strategy1")

# Check results
successful = sum(1 for r in responses if r.success)
print(f"{successful}/{len(orders)} orders placed")
```

**Returns:** `List[OrderResponse]`

**Performance:** 10x faster than sequential for 10+ orders

### Cancel Order

```python
cancelled = client.cancel_order(
    order_id="abc123",
    wallet_id="strategy1"
)
```

**Returns:** `bool` (True if cancelled)

### Cancel All Orders

```python
count = client.cancel_all_orders(
    wallet_id="strategy1",
    market_id=None  # Optional: cancel only for specific market
)
```

**Returns:** `int` (number of orders cancelled)

### 🆕 Cancel Market Orders (Convenient Market Exit)

Cancel all orders for a specific market.

```python
# Exit all positions on a market quickly
cancelled = client.cancel_market_orders(
    market_id="0x123...",  # Market condition ID
    wallet_id="strategy1"
)
print(f"Cancelled {cancelled} orders on market")
```

**Returns:** `int` (number of orders cancelled)

**Use case:** Quick market exit, risk management, position cleanup.

### Get Orders

```python
orders = client.get_orders(
    wallet_id="strategy1",
    market=None  # Optional: filter by market
)
```

**Returns:** `List[Order]`

**Order fields:**
- `id` (str) - Order ID
- `market` (str) - Market slug
- `asset_id` (str) - Asset ID
- `token_id` (str) - Token ID
- `price` (Decimal) - Order price
- `size` (Decimal) - Order size
- `side` (Side) - BUY or SELL
- `status` (OrderStatus) - Order status
- `created_at` (datetime) - Creation time

### Get Balances

```python
balance = client.get_balances("strategy1")
```

**Returns:** `Balance`

**Balance fields:**
- `collateral` (Decimal) - USDC balance
- `tokens` (dict[str, Decimal]) - Token ID -> token balance

**Example:**
```python
balance = client.get_balances("strategy1")
print(f"USDC: ${balance.collateral:.2f}")

for token_id, amount in balance.tokens.items():
    print(f"  Token {token_id[:20]}...: {amount:.2f}")
```

### Get Orderbook

```python
book = client.get_orderbook(token_id)
```

**Returns:** `OrderBook`

**OrderBook fields:**
- `token_id` (str) - Token ID
- `bids` (list[tuple[Decimal, Decimal]]) - [(price, size), ...]
- `asks` (list[tuple[Decimal, Decimal]]) - [(price, size), ...]
- `timestamp` (datetime) - Snapshot time

**Properties:**
- `best_bid` (Decimal) - Highest bid price
- `best_ask` (Decimal) - Lowest ask price
- `midpoint` (Decimal) - (best_bid + best_ask) / 2
- `spread` (Decimal) - best_ask - best_bid

**Example:**
```python
book = client.get_orderbook(token_id)

print(f"Best Bid: ${book.best_bid:.4f}")
print(f"Best Ask: ${book.best_ask:.4f}")
print(f"Spread:   ${book.spread:.4f}")

print("\nTop 3 Bids:")
for price, size in book.bids[:3]:
    print(f"  ${price:.4f} x {size:.2f}")
```

### Get Batch Orderbooks ⚡ (Phase 4 - Enhanced)

**10x Performance Improvement:** Now uses native POST /books endpoint (single API call vs 10+ concurrent requests).

```python
token_ids = [market.tokens[0] for market in markets]
books = client.get_orderbooks_batch(token_ids)

# Access by token_id
for token_id, book in books.items():
    print(f"{token_id}: ${book.midpoint:.4f}")
```

**Returns:** `Dict[str, OrderBook]`

**Performance:** 10x faster than sequential fetches (uses native POST /books endpoint).

**Technical Details:** Previously used ThreadPoolExecutor with 10 concurrent requests. Now uses single POST request to official Polymarket batch API.

**Use case:** Strategy-1 spread farming and Strategy-3 wallet monitoring.

### Get Midpoint

```python
midpoint = client.get_midpoint(token_id)  # Returns Decimal
```

**Returns:** `Decimal` (midpoint price)

### Get Price

```python
price = client.get_price(token_id, side=Side.BUY)
```

**Returns:** `Decimal`

**Args:**
- `token_id` (str) - Token ID
- `side` (Side) - BUY or SELL

---

### 🆕 Get Last Trade Price (Phase 5)

Get last trade price without fetching full orderbook (faster for price checks only).

```python
# Fast price check (no orderbook overhead)
price = client.get_last_trade_price(token_id)
print(f"Last trade: ${price:.3f}")
```

**Returns:** `Optional[Decimal]` (None if no recent trades)

**Performance:** 3-5x faster than get_orderbook() when you only need price.

**Use case:** Quick price checks, threshold monitoring, price alerts.

---

### 🆕 Get Last Trades Prices (Batch) (Phase 5)

Batch version of get_last_trade_price() for multiple tokens.

```python
token_ids = [market.tokens[0] for market in markets]
prices = client.get_last_trades_prices(token_ids)

for token_id, price in prices.items():
    if price:
        print(f"{token_id[:10]}...: ${price:.3f}")
    else:
        print(f"{token_id[:10]}...: No trades")
```

**Returns:** `Dict[str, Optional[Decimal]]` (mapping token_id to price)

**Performance:** Single API call for multiple prices.

---

### 🆕 Get Server Time (Phase 5)

Get Polymarket server timestamp for clock synchronization.

```python
import time

server_time_ms = client.get_server_time()
local_time_ms = int(time.time() * 1000)

drift_ms = abs(server_time_ms - local_time_ms)
if drift_ms > 5000:
    print(f"⚠️ Clock drift: {drift_ms}ms")
```

**Returns:** `int` (UNIX timestamp in milliseconds)

**Use case:** GTD order validation, clock synchronization checks.

---

### 🆕 Get Health Check (Phase 5)

Check if CLOB server is operational.

```python
if client.get_ok():
    print("✅ CLOB server operational")
else:
    print("❌ CLOB server down")
```

**Returns:** `bool`

**Use case:** Pre-trading health checks, monitoring, error handling.

---

### 🆕 Get Simplified Markets (Phase 5)

Get lightweight market list with pagination (no full market details).

```python
# First page
response = client.get_simplified_markets()
markets = response["data"]
next_cursor = response.get("next_cursor")

# Next page (if available)
if next_cursor and next_cursor != "LTE=":
    more_markets = client.get_simplified_markets(next_cursor)
```

**Returns:** `Dict[str, Any]` with `data` (list of markets) and `next_cursor` fields.

**Args:**
- `next_cursor` (str) - Pagination cursor (default: "MA==", end marker: "LTE=")

**Performance:** Faster than get_markets() for market discovery.

**Use case:** Market browsing, finding tradeable markets, pagination.

---

### 🆕 Check Order Scoring (Strategy-4)

Check if an order earns maker rebates (2% on Polymarket).

```python
# Check single order
is_scoring = client.is_order_scoring("0x123...")
if is_scoring:
    print("✅ Order earning 2% maker rebate!")
```

**Returns:** `bool`

**Use case:** Strategy-4 liquidity mining - identify which orders earn rewards.

---

### 🆕 Check Orders Scoring (Batch) (Strategy-4)

Check multiple orders for maker rebates in a single request.

```python
order_ids = ["0x123...", "0x456...", "0x789..."]
scoring = client.are_orders_scoring(order_ids)

earning_count = sum(scoring.values())
print(f"{earning_count}/{len(order_ids)} orders earning rebates")

# Show which orders are scoring
for order_id, is_scoring in scoring.items():
    status = "✅" if is_scoring else "❌"
    print(f"{status} {order_id[:10]}...")
```

**Returns:** `Dict[str, bool]` (mapping order_id to scoring status)

**Performance:** Single API call for multiple orders.

**Use case:** Strategy-4 - batch check maker rebate eligibility.

---

### Get Tick Size

**Phase 6 Enhancement:** Tick sizes are now automatically fetched from CLOB API when placing orders (no manual fetching required).

```python
tick_size = client.get_tick_size(token_id)
```

**Returns:** `Decimal` (minimum price increment)

**Common values:** Decimal("0.01"), Decimal("0.001")

### Get Neg Risk Status

```python
neg_risk = client.get_neg_risk(token_id)
```

**Returns:** `bool` (True if negative risk market)

---

## CTF & Neg-Risk Utilities

**Version:** 1.0.3
**Added:** October 2025
**Sources:** neg-risk-ctf-adapter, ctf-exchange, go-order-utils (MIT)

Production-ready utilities for conditional token trading, fee calculations, and order validation.

### Fee Calculation Utilities

#### Calculate Order Fee

```python
from decimal import Decimal
from shared.polymarket import calculate_order_fee, Side

fee = calculate_order_fee(
    side=Side.BUY,
    price=Decimal("0.60"),
    size=Decimal("100.0"),
    fee_rate_bps=100  # 1% fee
)
# Returns: Decimal("0.67") (fee in USDC)
```

**Formula (SYMMETRIC after v2.6 fix):**
- BUY: `fee = fee_rate × min(price, 1-price) × (size/price)`
- SELL: `fee = fee_rate × min(price, 1-price) × (size/price)` ← NOW SAME AS BUY!

**Parameters:**
- `size`: Order size in USDC (USD amount to trade) for BOTH BUY and SELL

**Returns:** `Decimal` - Fee amount in USDC

---

#### Calculate Net Cost

```python
from decimal import Decimal
from shared.polymarket import calculate_net_cost, Side

# BUY: Spend $100 USD to buy tokens at $0.60 each
net_cost, fee = calculate_net_cost(
    side=Side.BUY,
    price=Decimal("0.60"),
    size=Decimal("100.0"),  # $100 USD to spend
    fee_rate_bps=100
)
# Returns: (Decimal("100.67"), Decimal("0.67")) - Need $100.67 total ($100 + $0.67 fee)
# You receive 100/0.60 = 166.67 tokens
```

**Parameters:**
- `size`: Order size in USDC (USD amount to trade) for BOTH BUY and SELL

**Returns:** `Tuple[Decimal, Decimal]` - (net_amount, fee)
- BUY: net_amount = total USDC needed (size + fee)
- SELL: net_amount = USDC received (size - fee)

---

#### Calculate Profit After Fees

```python
from shared.polymarket import calculate_profit_after_fees, Side

# Round-trip: Spend $100 to buy at $0.60, sell same tokens at $0.70
pnl = calculate_profit_after_fees(
    entry_side=Side.BUY,
    entry_price=0.60,
    exit_price=0.70,
    size=100.0,  # $100 USD at entry
    entry_fee_rate_bps=100,
    exit_fee_rate_bps=100
)
# Returns: {
#   'token_count': 166.67,    # 100/0.60 tokens traded
#   'gross_profit': 16.67,    # 166.67 tokens × $0.10 price increase
#   'entry_fee': 0.67,        # Fee on buying $100 worth
#   'exit_fee': 0.78,         # Fee on selling 166.67 tokens at $0.70
#   'total_fees': 1.45,
#   'net_profit': 15.22,      # $116.67 exit - $100.67 entry - fees
#   'roi_pct': 15.11,         # 15.22 / 100.67
#   'entry_cost': 100.67,     # $100 + $0.67 fee
#   'exit_proceeds': 115.89   # $116.67 - $0.78 fee
# }
```

**Parameters:**
- `size`: Trading size in USDC at entry (determines token quantity for round-trip)

**Returns:** `dict` - Complete P&L breakdown including fees and ROI

---

#### Get Effective Spread

```python
from shared.polymarket import get_effective_spread

spread = get_effective_spread(
    bid=0.59,
    ask=0.61,
    size=100.0,
    fee_rate_bps=100
)
# Returns: {
#   'raw_spread': 0.02,
#   'raw_spread_bps': 200,
#   'buy_cost': 61.68,
#   'sell_proceeds': 58.64,
#   'effective_spread': 3.04,
#   'effective_spread_bps': 304
# }
```

**Returns:** `dict` - Spread metrics including fees

---

#### Check Order Profitability

```python
from shared.polymarket import check_order_profitability

profitable, net_profit = check_order_profitability(
    entry_price=0.60,
    exit_price=0.70,
    size=100.0,
    fee_rate_bps=100,
    min_profit_usdc=1.0  # Minimum $1 profit required
)
# Returns: (True, 15.50) if profitable
```

**Returns:** `Tuple[bool, float]` - (is_profitable, net_profit)

---

### Order Validation Utilities

#### Validate Order

```python
from shared.polymarket import validate_order, OrderRequest, Side

order = OrderRequest(
    token_id="123",
    price=0.60,
    size=100.0,
    side=Side.BUY
)

valid, error = validate_order(order)
if not valid:
    print(f"Invalid order: {error}")
```

**Validates:**
- Price bounds (0.01-0.99)
- Size constraints (≥ MIN_SIZE)
- Fee rate limits (0-1000 bps)
- GTD expiration (≥ 60s in future)
- Token ID format

**Returns:** `Tuple[bool, Optional[str]]` - (is_valid, error_message)

---

#### Validate Balance

```python
from shared.polymarket import validate_balance, Side

valid, error = validate_balance(
    side=Side.BUY,
    price=0.60,
    size=100.0,
    available_usdc=70.0,
    available_tokens=0.0,
    fee_rate_bps=100
)
# Returns: (True, None) if sufficient, (False, "error") otherwise
```

**Checks:**
- BUY: USDC balance ≥ (cost + fee)
- SELL: Token balance ≥ size, proceeds > fees

**Returns:** `Tuple[bool, Optional[str]]` - (is_valid, error_message)

---

#### Validate Neg-Risk Market

```python
from shared.polymarket import validate_neg_risk_market, Market

try:
    validate_neg_risk_market(market)
    # Market is safe for trading
except ValidationError as e:
    print(f"Unsafe market: {e}")
```

**Validates:**
- Not augmented (no unnamed outcomes)
- Has valid outcome set
- Properly configured

**Raises:** `ValidationError` if market is unsafe

---

### Neg-Risk Adapter (On-Chain Operations)

#### NegRiskAdapter Overview

```python
from shared.polymarket import NegRiskAdapter

adapter = NegRiskAdapter(web3_provider="https://polygon-rpc.com")

# Health check
health = adapter.health_check()
# Returns: {'healthy': True, 'checks': {...}, 'errors': []}
```

**Capabilities:**
- Convert NO → YES positions + collateral
- Split USDC → YES + NO tokens
- Merge YES + NO → USDC
- Redeem winning positions

**Security Features:**
- Gas price validation (max 500 gwei)
- Private key sanitization in errors
- Thread-safe nonce management
- Contract address verification
- Balance validation before transactions

#### Check CTF Approval

```python
approved = adapter.check_ctf_approval(wallet_address="0x...")
if not approved:
    tx_hash = adapter.approve_ctf_tokens(private_key="0x...")
```

**Returns:** `Optional[bool]` - True if approved, None if check failed

---

#### Convert Positions

```python
tx_hash = adapter.convert_positions(
    private_key="0x...",
    market_id=b"...",  # bytes32 market identifier
    index_set=3,       # 0b11 = outcomes [0, 1]
    amount=1000000,    # 1 USDC (6 decimals)
    gas_price_gwei=50,
    wait_for_receipt=True
)
```

**Parameters:**
- `market_id`: bytes32 market identifier
- `index_set`: Bitmask of NO positions to convert
- `amount`: Amount in wei (1 USDC = 1000000)
- `gas_price_gwei`: Max 500 gwei

**Returns:** `str` - Transaction hash

---

### ConversionCalculator

```python
from shared.polymarket import ConversionCalculator

calc = ConversionCalculator()
result = calc.calculate_conversion(
    no_tokens=["token_a_no", "token_b_no"],
    amount=1.0,
    total_outcomes=3
)
# Returns: {
#   'collateral': 1.0,
#   'yes_token_count': 1,
#   'yes_outcomes': [2]
# }
```

**Formula:** `collateral = amount × (no_token_count - 1)`

---

### Market Safety Utilities

#### Is Safe to Trade

```python
from shared.polymarket import is_safe_to_trade, Market

if is_safe_to_trade(market):
    # Market is safe for automated trading
    pass
```

**Filters:**
- Augmented neg-risk markets (incomplete outcome universe)
- Markets with unnamed outcomes ("Candidate_3", "Option_2", "Other")

**Returns:** `bool` - True if safe to trade

---

### Production-Safe Order Placement Pattern

**CRITICAL:** Always follow this pattern for production trading:

```python
from decimal import Decimal
from shared.polymarket import (
    PolymarketClient,
    OrderRequest,
    Side,
    validate_order,
    validate_balance,
    calculate_net_cost,
    check_order_profitability,
)

# 1. Create order
order = OrderRequest(
    token_id="123",
    price=Decimal("0.60"),
    size=Decimal("100.0"),
    side=Side.BUY
)

# 2. Validate order
valid, error = validate_order(order)
if not valid:
    raise ValueError(f"Invalid order: {error}")

# 3. Calculate fees
net_cost, fee = calculate_net_cost(
    Side.BUY, Decimal("0.60"), Decimal("100.0"), 100
)

# 4. Validate balance
balance = client.get_balance("wallet_id")
valid, error = validate_balance(
    Side.BUY, Decimal("0.60"), Decimal("100.0"),
    balance.collateral, Decimal("0"), 100
)
if not valid:
    raise InsufficientBalanceError(error)

# 5. Check profitability
profitable, profit = check_order_profitability(
    Decimal("0.60"), Decimal("0.65"), Decimal("100.0"), 100, Decimal("1.0")
)
if not profitable:
    raise ValueError(f"Not profitable: ${profit:.2f}")

# 6. Place order (after all validations passed)
response = client.place_order(order, wallet_id="wallet_id")
```

**See:** `examples/10_production_safe_trading.py` for complete implementation

---

## Dashboard API

### Get Positions

```python
positions = client.get_positions(
    wallet_id="strategy1",
    size_threshold=0.0,     # Min position size
    sortBy="CASHPNL",       # CASHPNL, PERCENTPNL, SIZE
    sortDirection="DESC",   # DESC or ASC
    limit=100
)
```

**Returns:** `List[Position]`

**Position fields:**
- `title` (str) - Market question
- `outcome` (str) - Outcome held (e.g., "Yes")
- `size` (Decimal) - Position size
- `current_price` (Decimal) - Current market price
- `current_value` (Decimal) - Position value (size * current_price)
- `cash_pnl` (Decimal) - Realized + unrealized P&L in USD
- `percent_pnl` (Decimal) - P&L as percentage
- `realized_pnl` (Decimal) - Realized P&L from closed positions
- `redeemable` (bool) - Can redeem (market resolved)

**Example:**
```python
positions = client.get_positions("strategy1", sortBy="CASHPNL", sortDirection="DESC")

total_value = sum(p.current_value for p in positions)
total_pnl = sum(p.cash_pnl for p in positions)

print(f"Portfolio Value: ${total_value:.2f}")
print(f"Total P&L: ${total_pnl:+.2f}")

for pos in positions[:5]:
    print(f"\n{pos.title}")
    print(f"  Outcome: {pos.outcome}")
    print(f"  Size: {pos.size:.2f} shares")
    print(f"  Value: ${pos.current_value:.2f}")
    print(f"  P&L: ${pos.cash_pnl:+.2f} ({pos.percent_pnl:+.1%})")
```

### Get Trades

```python
trades = client.get_trades(
    wallet_id="strategy1",
    limit=50
)
```

**Returns:** `List[Trade]`

**Trade fields:**
- `market` (str) - Market slug
- `outcome` (str) - Outcome traded
- `side` (Side) - BUY or SELL
- `price` (Decimal) - Execution price
- `size` (Decimal) - Trade size
- `fee_rate_bps` (int) - Fee rate in basis points
- `timestamp` (datetime) - Execution time
- `transaction_hash` (str) - Transaction hash
- `participants` (list[str]) - Counterparty addresses

**Example:**
```python
trades = client.get_trades("strategy1", limit=10)

for trade in trades:
    print(f"{trade.timestamp}: {trade.side.value} {trade.size:.2f} @ ${trade.price:.4f}")
```

### Get Activity

```python
activity = client.get_activity(
    wallet_id="strategy1",
    limit=100,
    type=None  # Optional: filter by ActivityType
)
```

**Returns:** `List[Activity]`

**Activity fields:**
- `type` (ActivityType) - TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION
- `timestamp` (datetime) - Event time
- `usd_value` (Decimal) - USD value of activity
- `transaction_hash` (str) - Transaction hash
- `details` (dict) - Type-specific details

**ActivityType enum:**
- `TRADE` - Order execution
- `SPLIT` - Collateral split into outcome tokens
- `MERGE` - Outcome tokens merged into collateral
- `REDEEM` - Redeem resolved market
- `REWARD` - Trading rewards
- `CONVERSION` - Token conversion

**Example:**
```python
activity = client.get_activity("strategy1", limit=20)

for event in activity:
    print(f"{event.timestamp}: {event.type.value} - ${event.usd_value:.2f}")
```

### Get Portfolio Value

```python
portfolio = client.get_portfolio_value(
    wallet_id="strategy1",
    market=None  # Optional: value for specific market
)
```

**Returns:** `PortfolioValue` (portfolio breakdown) 🆕

**PortfolioValue fields:**
- `value` (Decimal) - Total value (legacy field)
- `bets` (Decimal) - Active bet value 🆕
- `cash` (Decimal) - Available USDC 🆕
- `equity_total` (Decimal) - Total portfolio value 🆕

**Example:**
```python
# Get portfolio breakdown (new in v3.0+)
portfolio = client.get_portfolio_value("strategy1")
print(f"Total Value: ${portfolio.equity_total or portfolio.value:.2f}")
print(f"Active Bets: ${portfolio.bets or 0:.2f}")
print(f"Available Cash: ${portfolio.cash or 0:.2f}")

# Calculate allocation percentage
if portfolio.equity_total and portfolio.equity_total > 0:
    allocation = (portfolio.bets / portfolio.equity_total) * 100
    print(f"Deployed: {allocation:.1f}%")
```

### Batch Operations (Strategy-3)

**Get Positions for Multiple Wallets:**
```python
wallet_addresses = ["0xabc...", "0xdef...", ...]  # 100+ wallets

positions_by_wallet = client.get_positions_batch(
    wallet_addresses,
    size_threshold=1.0
)

# Returns: Dict[str, List[Position]]
for address, positions in positions_by_wallet.items():
    print(f"{address}: {len(positions)} positions")
```

**Get Trades for Multiple Wallets:**
```python
trades_by_wallet = client.get_trades_batch(wallet_addresses, limit=50)
```

**Get Activity for Multiple Wallets:**
```python
activity_by_wallet = client.get_activity_batch(wallet_addresses, limit=100)
```

**Performance:** 10x faster than sequential (100 wallets in 20-40s vs 200-400s)

### Multi-Wallet Analytics

**Aggregate Metrics:**
```python
metrics = client.aggregate_multi_wallet_metrics(wallet_addresses)

print(f"Total Wallets: {metrics['total_wallets']}")
print(f"Total Positions: {metrics['total_positions']}")
print(f"Total P&L: ${metrics['total_pnl']:.2f}")
print(f"Avg P&L per Wallet: ${metrics['avg_pnl_per_wallet']:.2f}")
print(f"Top Performer: {metrics['top_performers'][0]}")
```

**Detect Consensus Signals:**
```python
signals = client.detect_signals(
    wallet_addresses,
    min_wallets=5,       # Min wallets agreeing
    min_agreement=0.6    # Min % agreement (0.6 = 60%)
)

for signal in signals:
    print(f"{signal['title']}")
    print(f"  {signal['wallet_count']} wallets on {signal['outcome']}")
    print(f"  Agreement: {signal['agreement_ratio']:.1%}")
    print(f"  Total Value: ${signal['total_value']:.2f}")
```

---

## WebSocket API

### Subscribe to Orderbook

```python
def on_orderbook_update(book: OrderBook):
    print(f"Bid: ${book.best_bid:.4f}, Ask: ${book.best_ask:.4f}")

client.subscribe_orderbook(token_id, on_orderbook_update)

# Runs in background, ~100ms updates
```

**Performance:** 100ms updates vs 1s polling (10x faster, less bandwidth)

### Subscribe to User Orders

```python
def on_order_update(order_data: dict):
    print(f"Order {order_data['orderId']}: {order_data['status']}")

client.subscribe_user_orders(on_order_update, wallet_id="strategy1")
```

**Events:** Order fills, status changes, cancellations

### Unsubscribe All

```python
client.unsubscribe_all()
```

**Auto-reconnect:** Built-in with exponential backoff

---

## Data Types

### Enums

**Side:**
- `Side.BUY` - Buy order
- `Side.SELL` - Sell order

**OrderType:**
- `OrderType.GTC` - Good-til-cancelled
- `OrderType.GTD` - Good-til-date (requires expiration)
- `OrderType.FOK` - Fill-or-kill
- `OrderType.FAK` - Fill-and-kill

**OrderStatus:**
- `OrderStatus.LIVE` - Active on orderbook
- `OrderStatus.MATCHED` - Fully filled
- `OrderStatus.DELAYED` - Delayed (not rejected, wait and check status)
- `OrderStatus.UNMATCHED` - Partially filled
- `OrderStatus.CANCELLED` - Cancelled

**SignatureType:**
- `SignatureType.EOA` (0) - MetaMask, hardware wallet
- `SignatureType.MAGIC` (1) - Magic/Email wallet
- `SignatureType.PROXY` (2) - Proxy wallet

---

## Rate Limits

### CLOB API

| Endpoint | Limit |
|----------|-------|
| POST /order | 2,400 per 10s burst, 24,000 per 10min sustained |
| Other endpoints | 120 requests per minute |

### Implementation

Library automatically rate limits with 80% margin:
```python
client = PolymarketClient(enable_rate_limiting=True)  # Default
```

**Disable for testing:**
```python
client = PolymarketClient(enable_rate_limiting=False)
```

**Monitor rate limit usage:**
```python
# Check metrics (if prometheus_client installed)
from shared.polymarket.metrics import get_metrics
metrics = get_metrics()
```

---

## Error Handling

### Exception Types

```python
from shared.polymarket.exceptions import (
    PolymarketError,            # Base exception
    AuthenticationError,        # Auth failed
    ValidationError,            # Invalid input
    InsufficientBalanceError,   # Not enough USDC
    InsufficientAllowanceError, # Need token approval
    TickSizeError,              # Price violates tick size
    OrderDelayedError,          # Order delayed (not rejected)
    OrderExpiredError,          # Invalid expiration
    FOKNotFilledError,          # FOK couldn't fill
    OrderRejectedError,         # Generic rejection
    RateLimitError,             # Rate limit exceeded
    CircuitBreakerError,        # Circuit breaker open
    MarketDataError             # Market data fetch failed
)
```

### Error Handling Patterns

**Basic:**
```python
from decimal import Decimal, ROUND_HALF_UP

try:
    response = client.place_order(order, wallet_id="strategy1")
except InsufficientBalanceError as e:
    print(f"Not enough funds: {e.message}")
except TickSizeError as e:
    # Adjust price to valid tick size (e.g., 0.01)
    order.price = order.price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    response = client.place_order(order, wallet_id="strategy1")
except ValidationError as e:
    print(f"Invalid order: {e.message}")
```

**Order Delayed (NOT rejected):**
```python
try:
    response = client.place_order(order, wallet_id="strategy1")
except OrderDelayedError as e:
    # Order is delayed, not rejected
    # Wait and check status
    time.sleep(5)
    orders = client.get_orders(wallet_id="strategy1")
    # Find your order and check status
```

**Allowance Check:**
```python
try:
    response = client.place_order(order, wallet_id="strategy1")
except InsufficientAllowanceError:
    # Set allowances
    from shared.polymarket.utils.allowances import AllowanceManager
    manager = AllowanceManager()
    tx_hashes = manager.set_allowances(private_key)
    manager.wait_for_approvals(tx_hashes)
    # Retry order
    response = client.place_order(order, wallet_id="strategy1")
```

---

## Advanced Usage

### Configuration

**Environment Variables:**
```bash
# .env
POLYMARKET_CHAIN_ID=137
POLYMARKET_CLOB_URL=https://clob.polymarket.com
POLYMARKET_GAMMA_URL=https://gamma-api.polymarket.com
POLYMARKET_ENABLE_RATE_LIMITING=true
POLYMARKET_POOL_CONNECTIONS=50
POLYMARKET_POOL_MAXSIZE=100
POLYMARKET_BATCH_MAX_WORKERS=10
```

**Python Config:**
```python
from shared.polymarket import PolymarketClient

# Strategy-1 (single wallet)
client = PolymarketClient()

# Strategy-3 (100+ wallets)
client = PolymarketClient(
    pool_connections=100,
    pool_maxsize=200,
    batch_max_workers=20
)
```

### Health Check

```python
status = client.health_check()
# Returns: {"status": "healthy"} or {"status": "degraded"}
```

### Graceful Shutdown

```python
# Always call when done
client.close()
```

**Auto-cleanup:**
```python
# Registered at initialization, called on exit
import atexit
atexit.register(client.close)
```

### Circuit Breaker

Automatically opens after 5 consecutive failures, closes after 60s:
```python
client = PolymarketClient(enable_circuit_breaker=True)  # Default
```

**Manual override:**
```python
client.circuit_breaker.open()   # Manually open
client.circuit_breaker.close()  # Manually close
client.circuit_breaker.reset()  # Reset failure count
```

### Metrics (Prometheus)

```python
# Install: pip install prometheus-client

from shared.polymarket.metrics import get_metrics

# Start metrics server
client = PolymarketClient(enable_metrics=True)

# Access metrics at http://localhost:9090/metrics
```

**Available metrics:**
- `polymarket_orders_total{status}` - Order counts by status
- `polymarket_order_latency_seconds` - Order placement latency
- `polymarket_balance_usdc` - USDC balance
- `polymarket_api_requests_total{endpoint}` - API call counts

### Structured Logging

```python
from shared.polymarket.utils.structured_logging import (
    configure_structured_logging,
    get_logger,
    set_correlation_id
)

# Configure JSON logging
configure_structured_logging(level="INFO", enable_json=True)

# Get logger
logger = get_logger("strategy1.trading")

# Set correlation ID for request tracing
correlation_id = set_correlation_id()

# Log with structured fields
from decimal import Decimal
logger.info("order_placed", "Order successful",
            order_id="abc123", price=Decimal("0.55"), wallet="strategy1")

# Output: {"timestamp": "...", "level": "INFO", "event": "order_placed",
#          "correlation_id": "...", "order_id": "abc123", "price": "0.55", ...}
```

**Integration with PostgreSQL:**
```python
import psycopg2
import json

# Parse JSON log
log_entry = json.loads(log_line)

# Store in database
conn.execute("""
    INSERT INTO trading_logs (timestamp, level, event, correlation_id, data)
    VALUES (%s, %s, %s, %s, %s)
""", (log_entry['timestamp'], log_entry['level'], log_entry['event'],
      log_entry.get('correlation_id'), json.dumps(log_entry)))

# Query by correlation_id for full request trace
conn.execute("SELECT * FROM trading_logs WHERE correlation_id = %s
              ORDER BY timestamp", (correlation_id,))
```

---

## Complete Examples

### Strategy-1: Single Wallet Trading

```python
from decimal import Decimal
from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side

# Initialize
client = PolymarketClient()
wallet = WalletConfig(private_key=os.getenv("WALLET_PRIVATE_KEY"))
client.add_wallet(wallet, wallet_id="strategy1")

# Get active markets
markets = client.get_markets(active=True, limit=10)

for market in markets:
    # Get orderbook
    token_id = market.tokens[0] if market.tokens else None
    if not token_id:
        continue

    book = client.get_orderbook(token_id)

    # Check spread
    if book.spread and book.spread < Decimal("0.05"):  # Tight spread
        # Place buy order below midpoint
        order = OrderRequest(
            token_id=token_id,
            price=book.midpoint - Decimal("0.01"),
            size=Decimal("10.0"),
            side=Side.BUY
        )
        response = client.place_order(order, wallet_id="strategy1")
        print(f"Order: {response.order_id if response.success else response.error_msg}")

# Check positions
positions = client.get_positions("strategy1")
for pos in positions:
    print(f"{pos.title}: ${pos.cash_pnl:+.2f}")
```

### Strategy-3: Multi-Wallet Tracking

```python
# Track 100+ external wallets
tracked_wallets = ["0xabc...", "0xdef...", ...]  # 100+ addresses

# Batch fetch positions (10x faster)
wallet_positions = client.get_positions_batch(tracked_wallets)

# Aggregate metrics
metrics = client.aggregate_multi_wallet_metrics(tracked_wallets)
print(f"Total P&L: ${metrics['total_pnl']:.2f}")
print(f"Top Performer: {metrics['top_performers'][0]}")

# Detect consensus signals
signals = client.detect_signals(
    tracked_wallets,
    min_wallets=5,
    min_agreement=0.6
)

for signal in signals[:5]:
    print(f"{signal['title']}: {signal['wallet_count']} wallets on {signal['outcome']}")

    # Execute copy trade
    token_id = ...  # Get from market
    order = OrderRequest(
        token_id=token_id,
        price=Decimal("0.55"),
        size=Decimal("100.0"),
        side=Side.BUY
    )
    client.place_order(order, wallet_id="strategy3")
```

---

## Troubleshooting

**Order rejected: "INVALID_ORDER_MIN_TICK_SIZE"**
→ Round price to valid tick size: `price = price.quantize(tick_size, rounding=ROUND_HALF_UP)`

**Order rejected: "INVALID_ORDER_NOT_ENOUGH_BALANCE"**
→ Check BOTH balance AND allowances (EOA wallets need approvals)

**"ORDER_DELAYED" error**
→ Not rejected, just delayed. Wait 5s and query order status.

**GTD order rejected**
→ Expiration must be >= current_time + 60 seconds

**ImportError for web3**
→ `pip install web3>=7.0.0` (required for allowance management)

**401 authentication errors**
→ Wallet not registered on Polymarket. Visit app.polymarket.com first.

**Rate limit errors**
→ Reduce request rate or enable rate limiting: `PolymarketClient(enable_rate_limiting=True)`

---

## See Also

- **README.md** - Library overview and quick start
- **QUICKSTART.md** - 5-minute integration guide
- **examples/** - Complete usage examples
- **tests/README.md** - Testing guide
- **tests/benchmarks/** - Performance benchmarks
- **tests/testnet/** - Live API testing on testnet

---

**Questions?** Check examples/ or run tests to see working code.
