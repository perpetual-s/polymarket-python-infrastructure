# Polymarket API Reference

Complete API reference for polymarket library. Use this instead of official Polymarket docs.

**Version:** 3.6 (Production Trading Fixes)
**Updated:** 2025-11-24
**Latest:** Cancel Order API fix, Order parsing robustness, Paginated response handling
**v3.6:** Cancel order body param fix, get_orders pagination, timestamp/status parsing
**v3.5:** WebSocket compression, message deduplication, multi-token subscription, failure callbacks
**v3.3:** Message queue (async processing) + Unified manager + Batch subscriptions
**v3.1:** Security hardening (credential redaction, crypto nonces) + Performance (100x cache speedup)
**Phase 1:** Completed (GitHub analysis integration)
**Phase 2:** Completed (12+ WebSocket streams)
**Phase 3:** Completed (Message queue, Unified manager, Batch subscriptions)
**Phase 4-6:** Completed (Batch orderbooks, Missing endpoints, Tick validation)
**CTF:** Completed (v1.0.3 - Fee calculations, Validation, Neg-Risk adapter)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Authentication](#authentication)
3. [Market Data API](#market-data-api)
4. [Public CLOB API (No Auth Required)](#public-clob-api-no-auth-required) - Spreads, Liquidity, Batch operations
5. [Trading API (CLOB)](#trading-api-clob)
6. [CTF & Neg-Risk Utilities](#ctf--neg-risk-utilities) - Fee calculations, Validation, NegRiskAdapter
7. [Dashboard API](#dashboard-api)
8. [WebSocket API](#websocket-api)
9. [Real-Time Data Streams (Phase 2)](#real-time-data-streams-phase-2)
10. [Data Types](#data-types)
11. [Rate Limits](#rate-limits)
12. [Error Handling](#error-handling)
13. [Advanced Usage](#advanced-usage)

---

## What's New in v3.6 (2025-11-24)

### Cancel Order API Fix (CRITICAL)

**Fixed:** Cancel order now correctly uses body parameter instead of URL path parameter.

**Before (broken):**
```python
# DELETE /order/{order_id} ← WRONG (404 error)
```

**After (correct):**
```python
# DELETE /order with body {"orderID": order_id} ← CORRECT
```

**API Response Format:**
```python
# Response is NOT {"success": true, "errorMsg": "..."}
# Response IS:
{
    "canceled": ["order_id_1", "order_id_2"],  # Successfully cancelled
    "not_canceled": {"order_id_3": "REASON"}   # Failed with reason
}
```

**Behavior:**
- Returns `True` if order_id is in `canceled` list
- Returns `True` if order_id is in `not_canceled` with "NOT_FOUND" (already cancelled/filled)
- Raises `TradingError` if order_id is in `not_canceled` with other reason

---

### Order Parsing Robustness

**Fixed:** `get_orders()` now handles all response formats from Polymarket API.

**Timestamp Parsing:**
```python
# API returns timestamps in various formats:
created_at: 1732449600        # Unix timestamp (seconds)
created_at: 1732449600000     # Unix timestamp (milliseconds)
created_at: "2024-11-24T..."  # ISO 8601 string

# All formats now handled automatically
```

**Status Normalization:**
```python
# API returns UPPERCASE status: "LIVE", "MATCHED", "CANCELLED"
# Model expects lowercase: "live", "matched", "cancelled"
# Now auto-normalized
```

**Paginated Response Handling:**
```python
# API returns paginated format:
{"data": [...orders...], "next_cursor": "..."}

# Now correctly extracts orders from "data" field
# Handles both paginated and direct array responses
```

---

### Migration Notes

**No breaking changes** - All improvements are backward compatible.

**Automatic benefits:**
- Cancel orders now work correctly
- Order listing works with all API response formats
- Timestamp parsing handles all Polymarket formats
- Status values normalized automatically

---

## What's New in v3.5 (2025-11-13)

### WebSocket Compression (L3)

**50-70% Bandwidth Reduction**
- permessage-deflate compression enabled by default
- Reduces message size for high-frequency orderbook updates
- Configurable via `enable_compression` parameter (default: True)

**Configuration:**
```python
from polymarket.api.websocket import WebSocketClient

# Enable compression (default)
ws = WebSocketClient(enable_compression=True)

# Disable for debugging
ws = WebSocketClient(enable_compression=False)
```

---

### Message Deduplication (L2)

**Prevent Duplicate Processing on Reconnects**
- SHA256 hash-based deduplication with 5-minute TTL
- Rolling deque (maxlen=10000) for memory efficiency
- Thread-safe with dedicated lock
- Configurable via `enable_deduplication` parameter (default: True)

**Hash Strategy:**
- book: event_type + timestamp + asset_id + market + hash
- trade/order: event_type + timestamp + asset_id + market + id
- price_change: event_type + timestamp + market + price_changes.hash

**Metrics:**
```python
stats = ws.stats()
# Returns: {"duplicates_blocked": 5, "dedup_cache_size": 120, ...}
```

**Configuration:**
```python
ws = WebSocketClient(
    enable_deduplication=True,    # Enable (default: True)
    dedup_window_seconds=300      # TTL window (default: 300s)
)
```

---

### Multi-Token Single Subscription (L1)

**Reduce Message Overhead**
- Subscribe to multiple markets in single WebSocket message
- More efficient than `subscribe_markets_batch()` (separate messages per token)
- Uses official Polymarket batch subscription format

**Usage:**
```python
# Single subscription for 10+ markets
token_ids = [token_id1, token_id2, token_id3, ...]
ws.subscribe_markets_multi(token_ids, callback)

# vs subscribe_markets_batch() which sends separate message per token
```

**Performance:** Lower protocol overhead, faster subscription processing.

---

### Graceful Shutdown Callbacks (L4)

**Permanent Failure Handling**
- Callback invoked when max reconnects exceeded
- Allows application alerting, cleanup, failover
- Receives detailed failure reason string

**Configuration:**
```python
def on_failure(reason: str):
    logger.critical(f"WebSocket permanent failure: {reason}")
    # Alert ops, initiate failover, cleanup resources

ws = WebSocketClient(on_failure_callback=on_failure)
```

**Callback receives:** "Max reconnects exceeded (5 attempts)" or similar detailed reason.

---

## What's New in v3.3 (2025-11-13)

### Message Queue/Buffer (Phase 3.3)

**Async Message Processing**
- `queue.Queue` with consumer task for non-blocking WebSocket callbacks
- Prevents WebSocket thread exhaustion from slow I/O (database operations)
- Configurable queue size (default: 10,000 messages)
- Queue metrics: depth, drops, processing lag
- Backward compatible (enable_queue parameter, default=True)

**Production Impact:**
- Prevents connection instability from 5+ second database operations in callbacks
- Handles high-frequency message bursts without blocking WebSocket thread
- Proven pattern from Strategy-3 production use

**Configuration:**
```python
from polymarket.api.websocket import WebSocketClient

# Enable queue (default)
ws = WebSocketClient(enable_queue=True, queue_maxsize=10000)

# Start with event loop for consumer task
ws.connect(event_loop=asyncio.get_running_loop())

# Check queue status
stats = ws.stats()
# Returns: {"queue_size": 5, "queue_drops": 0, "consumer_task_running": True}
```

---

### Unified WebSocket Manager (Phase 3.1)

**Coordinated Lifecycle Management**
- Single interface for CLOB + RTDS WebSocket connections
- Atomic start/stop for both connections
- Unified health monitoring endpoint
- Graceful degradation (continue if one fails)
- Context manager support

**Usage:**
```python
from polymarket.api.unified_websocket import UnifiedWebSocketManager
from polymarket.api.websocket import WebSocketClient
from polymarket.api.real_time_data import RealTimeDataClient

# Initialize connections
clob_ws = WebSocketClient(...)
rtds = RealTimeDataClient(...)

# Create unified manager
manager = UnifiedWebSocketManager(clob_ws, rtds)

# Start both connections atomically
status = manager.start_all(event_loop=asyncio.get_running_loop())
# Returns: {"clob": True, "rtds": True}

# Unified health check
health = manager.health_status()
# Returns: {
#   "overall_status": "healthy"|"degraded"|"unhealthy",
#   "clob": {"health": {...}, "stats": {...}},
#   "rtds": {"stats": {...}}
# }

# Stop both connections
manager.stop_all()
```

---

### Batch Subscriptions (Phase 3.2)

**Transaction Semantics**
- Subscribe to multiple markets in one call
- All-or-nothing: rollback on partial failure
- Error aggregation for cleaner calling code
- Prevents orphaned subscriptions from failures

**Usage:**
```python
# Subscribe to multiple markets atomically
token_ids = [token_id1, token_id2, token_id3]
result = ws.subscribe_markets_batch(token_ids, callback)

# Returns: {
#   "success": True,
#   "succeeded": [token_id1, token_id2, token_id3],
#   "failed": [],
#   "error": None
# }

# On partial failure, all subscriptions rolled back:
# Returns: {
#   "success": False,
#   "succeeded": [],  # All rolled back!
#   "failed": [token_id2],
#   "error": "Connection error for token_id2"
# }
```

---

## What's New in v3.1 (2025-11-09)

### Security Hardening

**Automatic Credential Redaction**
- Private keys, API secrets, and passphrases automatically redacted in logs
- Prevents credential leakage in exception traces and debug output
- Enabled by default when using `configure_structured_logging()`
- See: `examples/05_structured_logging.py` for demonstration

**Cryptographic Nonce Randomization**
- Nonce generation now uses `secrets.randbelow()` for unpredictable values
- Prevents nonce prediction and front-running attacks
- Internal security improvement (no API changes)

### Performance Optimizations

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
import asyncio
from decimal import Decimal
from polymarket import PolymarketClient, WalletConfig, OrderRequest, Side

async def main():
    # Initialize
    client = PolymarketClient()

    # Add wallet
    wallet = WalletConfig(private_key="0x...")
    client.add_wallet(wallet, wallet_id="strategy1")

    # Get markets
    markets = await client.get_markets(active=True, limit=10)

    # Place order
    order = OrderRequest(
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
        price=Decimal("0.55"),  # Use Decimal for exact precision
        size=Decimal("100.0"),  # Use Decimal for exact precision
        side=Side.BUY
    )
    response = await client.place_order(order, wallet_id="strategy1")

asyncio.run(main())
```

### Production-Safe Pattern (RECOMMENDED)

```python
import asyncio
from decimal import Decimal
from polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    # CRITICAL: Import validation utilities
    validate_order,
    validate_balance,
    calculate_net_cost,
)

async def main():
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
    balance = await client.get_balance("strategy1")
    valid, error = validate_balance(
        Side.BUY, Decimal("0.55"), Decimal("100.0"),
        balance.collateral, Decimal("0"), 100
    )
    if not valid:
        raise InsufficientBalanceError(error)

    # Place order (after all checks passed)
    response = await client.place_order(order, wallet_id="strategy1")

asyncio.run(main())
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
from polymarket.utils.allowances import AllowanceManager

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

**⚠️ IMPORTANT:** All Gamma API methods are async and require `await`.

All market data methods have been converted to async (as of 2025-11-15). This includes:
- `get_markets()`, `get_market_by_slug()`, `get_market_by_id()`, `search_markets()`
- `get_events()`, `get_all_current_markets()`, `get_clob_tradable_markets()`, `get_all_tradeable_events()`

### Get Markets

```python
markets = await client.get_markets(
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
markets = await client.get_markets(active=True, limit=5)

for market in markets:
    print(f"{market.question}")
    print(f"  Outcomes: {', '.join(market.outcomes)}")
    print(f"  Volume: ${market.volume:,.0f}")
    print(f"  Token IDs: {market.tokens}")
```

### Get Market by Slug

```python
market = await client.get_market_by_slug("trump-2024-election")
```

**Returns:** `Optional[Market]` (None if not found)

### Get Market by ID

```python
market = await client.get_market_by_id("12345")
```

**Returns:** `Optional[Market]`

### Search Markets

```python
results = await client.search_markets(query="bitcoin", limit=20)
```

**Returns:** `List[Market]`

**Note:** Requires authentication (401 without wallet)

### Get Simplified Markets (RECOMMENDED for Real-Time)

**10-20x faster than `get_markets()` - use for bot operations**

```python
markets = await client.get_simplified_markets(
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
- Bot creation (fast market discovery)
- Real-time trading decisions
- Market status checks
- Analytics (use `get_markets()` instead)

---

### Phase 1: Helper Methods (from official Polymarket agents)

#### Get All Current Markets (Auto-Pagination)

```python
# Fetches ALL active, non-closed, non-archived markets (auto-pagination)
all_markets = await client.get_all_current_markets(limit=100)  # limit is batch size
```

**Returns:** `List[Market]`

**Features:**
- Automatically paginates through all pages
- Only returns active, non-closed, non-archived markets
- Batch size controlled by `limit` parameter

**Example:**
```python
import asyncio

async def main():
    # Get all current tradable markets
    all_markets = await client.get_all_current_markets()
    print(f"Found {len(all_markets)} total current markets")

    # Filter for high volume
    high_volume = [m for m in all_markets if m.volume > 100000]

asyncio.run(main())
```

#### Get CLOB Tradable Markets

```python
# Only get markets with order book enabled (tokens assigned)
tradable = await client.get_clob_tradable_markets(limit=100)
```

**Returns:** `List[Market]`

**Features:**
- Filters for markets with `tokens` (CLOB trading available)
- Only active, non-closed markets
- Perfect for Strategy-1 (spread farming)

**Example:**
```python
import asyncio

async def main():
    # Get tradable markets and check spreads
    tradable_markets = await client.get_clob_tradable_markets(limit=50)

    for market in tradable_markets:
        if len(market.tokens) >= 2:
            token_id = market.tokens[0]
            book = await client.get_orderbook(token_id)
            if book.spread and book.spread > 0.02:  # 2% spread
                print(f"High spread opportunity: {market.question}")

asyncio.run(main())
```

#### Get Events

```python
# Get events (groups of related markets)
events = await client.get_events(
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
import asyncio

async def main():
    # Get all active events
    events = await client.get_events(active=True, limit=50)

    for event in events:
        print(f"{event.title} ({len(event.markets)} markets)")

asyncio.run(main())
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
import asyncio

async def main():
    all_events = await client.get_events(limit=100)
    tradeable = client.filter_events_for_trading(all_events)
    print(f"Tradeable: {len(tradeable)} out of {len(all_events)}")

asyncio.run(main())
```

#### Get All Tradeable Events

```python
# Convenience method: get_events() + filter_events_for_trading()
tradeable_events = await client.get_all_tradeable_events(limit=100)
```

**Returns:** `List[Event]`

**Example:**
```python
import asyncio

async def main():
    # Get only tradeable events in one call
    events = await client.get_all_tradeable_events()

    for event in events:
        # Get all markets in this event
        market_ids = event.markets
        print(f"{event.title}: {len(market_ids)} markets")

asyncio.run(main())
```

---

### Get Market Holders

```python
holders = await client.get_market_holders(
    market="0x1234...",  # Market condition ID
    limit=100,           # Max results (default: 100, max: 500)
    min_balance=1        # Minimum position size (default: 1)
)
```

**Returns:** `List[Holder]`

**Holder fields:**
- `proxy_wallet` (str) - Proxy wallet address
- `amount` (Decimal) - Position size
- `pseudonym` (str) - User pseudonym (if public)

**Example:**
```python
import asyncio

async def main():
    # Get top 10 holders
    holders = await client.get_market_holders(market.condition_id, limit=10)

    for i, holder in enumerate(holders, 1):
        print(f"{i}. {holder.pseudonym or 'Anonymous'}: {holder.amount:.2f} shares")

    # Whale discovery (new in v3.0+)
    whales = await client.get_market_holders(
        market=market.condition_id,
        limit=100,
        min_balance=5000  # Only positions > $5000
    )
    print(f"Found {len(whales)} whales with >$5000 positions")

asyncio.run(main())
```

---

## Public CLOB API (No Auth Required)

**New in v3.1:** Comprehensive public API for market data without authentication.

### Why Use Public Endpoints?

- **No wallet needed** - No private keys or API credentials
- **Faster** - No signature overhead (~50-100ms saved per request)
- **Higher throughput** - Doesn't consume trading rate limits
- **Batch operations** - 10x more efficient (80 req/10s for batch vs 200 req/10s single)

**Use cases:** Price monitoring, liquidity analysis, market research, dashboards, backtesting.

**Complete example:** See `examples/12_public_clob_api.py`

---

### Get Spread

Get bid-ask spread for a token.

**Rate limit:** General CLOB (5,000 req/10s)

```python
spread = await client.get_spread(token_id)
# Returns: 0.05 (float) or None if unavailable
```

---

### Get Spreads (Batch)

Get spreads for multiple tokens in one call.

**Rate limit:** 80 req/10s (10x more efficient!)

```python
token_ids = [token_id1, token_id2, token_id3]
spreads = await client.get_spreads(token_ids)
# Returns: {token_id1: 0.05, token_id2: 0.03, token_id3: None}
```

---

### Get Midpoints (Batch)

Get midpoint prices for multiple tokens.

**Rate limit:** 80 req/10s

```python
token_ids = [token_id1, token_id2, token_id3]
midpoints = await client.get_midpoints(token_ids)
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
prices = await client.get_prices(params)
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
bid, ask = await client.get_best_bid_ask(token_id)
# Returns: (0.54, 0.56) or None if orderbook empty
```

---

### Get Liquidity Depth

Calculate liquidity depth within price range.

**Rate limit:** 200 req/10s (uses get_orderbook internally)

```python
# Liquidity within 5% of best bid/ask
depth = await client.get_liquidity_depth(token_id, price_range=0.05)
# Returns: {
#     "bid_depth": 1500.50,      # Total USDC on bid side
#     "ask_depth": 2300.25,      # Total USDC on ask side
#     "bid_levels": 8,            # Number of bid price levels
#     "ask_levels": 12,           # Number of ask price levels
#     "total_depth": 3800.75      # Total liquidity
# }

# Tight liquidity (1%)
tight = await client.get_liquidity_depth(token_id, price_range=0.01)

# Wide liquidity (10%)
wide = await client.get_liquidity_depth(token_id, price_range=0.10)
```

---

### Get Markets (Full)

Get complete market list with all data.

**Rate limit:** 250 req/10s (general markets endpoint)

```python
# Get first page
markets = await client.get_markets_full(next_cursor="MA==")
# Returns: {
#     "data": [...],  # List of complete market objects
#     "next_cursor": "..." # Use for pagination
# }

# Get next page
markets_page2 = await client.get_markets_full(next_cursor=markets["next_cursor"])
```

**Note:** Slower than `get_simplified_markets()` but includes complete data. Use for analytics, not real-time trading.

---

### Get Market by Condition ID

Get individual market details.

**Rate limit:** 50 req/10s

```python
market = await client.get_market_by_condition(condition_id)
# Returns: Full market dictionary with all fields
```

---

### Get Market Trade Events

Get trade events for a market.

**Rate limit:** General CLOB (5,000 req/10s)

```python
import asyncio

async def main():
    events = await client.get_market_trades_events(condition_id)
    # Returns: List of trade event dictionaries
    for event in events:
        print(f"Trade: {event['side']} {event['size']} @ {event['price']}")

asyncio.run(main())
```

---

### Rate Limits Summary (Public Endpoints)

From [official Polymarket docs](https://docs.polymarket.com/quickstart/introduction/rate-limits) (re-audited 2026-04-23):

| Endpoint | Rate Limit | Notes |
|----------|------------|-------|
| CLOB default | 9,000 req/10s | Baseline for unlisted endpoints |
| `/book`, `/price`, `/midpoint`, `/last-trade-price`, `/spread` | 1,500 req/10s | Single token queries |
| `/books`, `/prices`, `/midpoints`, `/last-trades-prices`, `/simplified-markets` | 500 req/10s | Batch variants |
| `/prices-history` | 1,000 req/10s | Historical price series |
| `/tick-size`, `/neg-risk` | 200 req/10s | Market metadata |
| `/ok` (health check) | 100 req/10s | Server health |
| Gamma `/markets` | 300 req/10s | Full market data |
| Gamma `/events`, `/events/pagination` | 500 req/10s | Event listing |
| Gamma `/search` | 300 req/10s | Market search |
| Gamma `/public-profile` | 100 req/10s | Public profile lookup |
| Gamma default | 4,000 req/10s | Unlisted Gamma endpoints |

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
from polymarket import OrderRequest, Side, OrderType

order = OrderRequest(
    token_id="71321045679252212594626385532706912750332728571942532289631379312455583992833",
    price=Decimal("0.555"),  # Validated against market's tick size automatically
    size=Decimal("100.0"),   # Order size in USDC
    side=Side.BUY,           # BUY or SELL
    order_type=OrderType.GTC # GTC, GTD, FOK, FAK
)

response = await client.place_order(
    order,
    wallet_id="strategy1",
    skip_balance_check=False  # Set True to skip pre-flight balance check
)
```

**Returns:** `OrderResponse`

**Phase 6 Automatic Validation:**
- Tick size fetched from API (validates price is valid multiple)
- Fee rate fetched from API (ensures correct maker/taker fees)
- Neg risk flag fetched (handles multi-outcome markets correctly)
- Invalid orders rejected BEFORE signing (saves gas fees)

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
import asyncio

async def main():
    # GTC limit order
    order = OrderRequest(
        token_id=market.tokens[0],
        price=Decimal("0.55"),
        size=Decimal("100.0"),
        side=Side.BUY,
        order_type=OrderType.GTC
    )
    response = await client.place_order(order, wallet_id="strategy1")

    if response.success:
        print(f"Order placed: {response.order_id}")
    else:
        print(f"Order failed: {response.error_msg}")

asyncio.run(main())
```

### Place Batch Orders

```python
import asyncio

async def main():
    orders = [
        OrderRequest(token_id="123", price=Decimal("0.55"), size=Decimal("100.0"), side=Side.BUY),
        OrderRequest(token_id="456", price=Decimal("0.60"), size=Decimal("200.0"), side=Side.BUY),
    ]

    responses = await client.place_orders_batch(orders, wallet_id="strategy1")

    # Check results
    successful = sum(1 for r in responses if r.success)
    print(f"{successful}/{len(orders)} orders placed")

asyncio.run(main())
```

**Returns:** `List[OrderResponse]`

**Performance:** 10x faster than sequential for 10+ orders

### Cancel Order

```python
cancelled = await client.cancel_order(
    order_id="abc123",
    wallet_id="strategy1"
)
```

**Returns:** `bool` (True if cancelled or already cancelled/filled)

**API Details (v3.6):**
- Endpoint: `DELETE /order` with body `{"orderID": order_id}`
- Response: `{"canceled": [...], "not_canceled": {...}}`
- Returns True if order in `canceled` list
- Returns True if order NOT_FOUND (already cancelled/filled)
- Raises `TradingError` if cancellation failed for other reason

**Example:**
```python
import asyncio

async def main():
    # Cancel single order
    cancelled = await client.cancel_order("abc123", wallet_id="strategy1")
    if cancelled:
        print("Order cancelled (or was already cancelled/filled)")

    # Safe pattern: check if order exists first
    orders = await client.get_orders(wallet_id="strategy1")
    live_orders = [o for o in orders if o.status == "live"]
    for order in live_orders:
        await client.cancel_order(order.id, wallet_id="strategy1")

asyncio.run(main())
```

### Cancel All Orders

```python
count = await client.cancel_all_orders(
    wallet_id="strategy1",
    market_id=None  # Optional: cancel only for specific market
)
```

**Returns:** `int` (number of orders cancelled)

### Cancel Market Orders (Convenient Market Exit)

Cancel all orders for a specific market.

```python
import asyncio

async def main():
    # Exit all positions on a market quickly
    cancelled = await client.cancel_market_orders(
        market_id="0x123...",  # Market condition ID
        wallet_id="strategy1"
    )
    print(f"Cancelled {cancelled} orders on market")

asyncio.run(main())
```

**Returns:** `int` (number of orders cancelled)

**Use case:** Quick market exit, risk management, position cleanup.

### Get Orders

```python
orders = await client.get_orders(
    wallet_id="strategy1",
    market=None  # Optional: filter by market
)
```

**Returns:** `List[Order]`

**v3.6 Improvements:**
- Now async (`await` required)
- Handles paginated API responses automatically
- Normalizes status to lowercase (API returns "LIVE", model uses "live")
- Handles all timestamp formats (int, float, ISO string)

**Order fields:**
- `id` (str) - Order ID
- `market` (str) - Market slug
- `asset_id` (str) - Asset ID
- `token_id` (str) - Token ID
- `price` (Decimal) - Order price
- `size` (Decimal) - Order size
- `side` (Side) - BUY or SELL
- `status` (str) - Order status ("live", "matched", "cancelled")
- `created_at` (datetime) - Creation time

### Get Balances

```python
balance = await client.get_balances("strategy1")
```

**Returns:** `Balance`

**Balance fields:**
- `collateral` (Decimal) - USDC balance
- `tokens` (dict[str, Decimal]) - Token ID -> token balance

**Example:**
```python
import asyncio

async def main():
    balance = await client.get_balances("strategy1")
    print(f"USDC: ${balance.collateral:.2f}")

    for token_id, amount in balance.tokens.items():
        print(f"  Token {token_id[:20]}...: {amount:.2f}")

asyncio.run(main())
```

### Get Orderbook

```python
book = await client.get_orderbook(token_id)
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
import asyncio

async def main():
    book = await client.get_orderbook(token_id)

    print(f"Best Bid: ${book.best_bid:.4f}")
    print(f"Best Ask: ${book.best_ask:.4f}")
    print(f"Spread:   ${book.spread:.4f}")

    print("\nTop 3 Bids:")
    for price, size in book.bids[:3]:
        print(f"  ${price:.4f} x {size:.2f}")

asyncio.run(main())
```

### Get Batch Orderbooks **(Phase 4 - Enhanced)

**10x Performance Improvement:** Now uses native POST /books endpoint (single API call vs 10+ concurrent requests).

```python
import asyncio

async def main():
    token_ids = [market.tokens[0] for market in markets]
    books = await client.get_orderbooks_batch(token_ids)

    # Access by token_id
    for token_id, book in books.items():
        print(f"{token_id}: ${book.midpoint:.4f}")

asyncio.run(main())
```

**Returns:** `Dict[str, OrderBook]`

**Performance:** 10x faster than sequential fetches (uses native POST /books endpoint).

**Technical Details:** Previously used ThreadPoolExecutor with 10 concurrent requests. Now uses single POST request to official Polymarket batch API.

**Use case:** Strategy-1 spread farming and Strategy-3 wallet monitoring.

### Get Midpoint

```python
midpoint = await client.get_midpoint(token_id)  # Returns Decimal
```

**Returns:** `Decimal` (midpoint price)

### Get Price

```python
price = await client.get_price(token_id, side=Side.BUY)
```

**Returns:** `Decimal`

**Args:**
- `token_id` (str) - Token ID
- `side` (Side) - BUY or SELL

---

### Get Last Trade Price (Phase 5)

Get last trade price without fetching full orderbook (faster for price checks only).

```python
import asyncio

async def main():
    # Fast price check (no orderbook overhead)
    price = await client.get_last_trade_price(token_id)
    print(f"Last trade: ${price:.3f}")

asyncio.run(main())
```

**Returns:** `Optional[Decimal]` (None if no recent trades)

**Performance:** 3-5x faster than get_orderbook() when you only need price.

**Use case:** Quick price checks, threshold monitoring, price alerts.

---

### Get Last Trades Prices (Batch) (Phase 5)

Batch version of get_last_trade_price() for multiple tokens.

```python
import asyncio

async def main():
    token_ids = [market.tokens[0] for market in markets]
    prices = await client.get_last_trades_prices(token_ids)

    for token_id, price in prices.items():
        if price:
            print(f"{token_id[:10]}...: ${price:.3f}")
        else:
            print(f"{token_id[:10]}...: No trades")

asyncio.run(main())
```

**Returns:** `Dict[str, Optional[Decimal]]` (mapping token_id to price)

**Performance:** Single API call for multiple prices.

---

### Get Server Time (Phase 5)

Get Polymarket server timestamp for clock synchronization.

```python
import asyncio
import time

async def main():
    server_time_ms = await client.get_server_time()
    local_time_ms = int(time.time() * 1000)

    drift_ms = abs(server_time_ms - local_time_ms)
    if drift_ms > 5000:
        print(f"⚠️ Clock drift: {drift_ms}ms")

asyncio.run(main())
```

**Returns:** `int` (UNIX timestamp in milliseconds)

**Use case:** GTD order validation, clock synchronization checks.

---

### Get Health Check (Phase 5)

Check if CLOB server is operational.

```python
import asyncio

async def main():
    if await client.get_ok():
        print("CLOB server operational")
    else:
        print("CLOB server down")

asyncio.run(main())
```

**Returns:** `bool`

**Use case:** Pre-trading health checks, monitoring, error handling.

---

### Get Simplified Markets (Phase 5)

Get lightweight market list with pagination (no full market details).

```python
import asyncio

async def main():
    # First page
    response = await client.get_simplified_markets()
    markets = response["data"]
    next_cursor = response.get("next_cursor")

    # Next page (if available)
    if next_cursor and next_cursor != "LTE=":
        more_markets = await client.get_simplified_markets(next_cursor)

asyncio.run(main())
```

**Returns:** `Dict[str, Any]` with `data` (list of markets) and `next_cursor` fields.

**Args:**
- `next_cursor` (str) - Pagination cursor (default: "MA==", end marker: "LTE=")

**Performance:** Faster than get_markets() for market discovery.

**Use case:** Market browsing, finding tradeable markets, pagination.

---

### Check Order Scoring (Strategy-4)

Check if an order earns maker rebates (2% on Polymarket).

```python
import asyncio

async def main():
    # Check single order
    is_scoring = await client.is_order_scoring("0x123...")
    if is_scoring:
        print("Order earning 2% maker rebate!")

asyncio.run(main())
```

**Returns:** `bool`

**Use case:** Strategy-4 liquidity mining - identify which orders earn rewards.

---

### Check Orders Scoring (Batch) (Strategy-4)

Check multiple orders for maker rebates in a single request.

```python
import asyncio

async def main():
    order_ids = ["0x123...", "0x456...", "0x789..."]
    scoring = await client.are_orders_scoring(order_ids)

    earning_count = sum(scoring.values())
    print(f"{earning_count}/{len(order_ids)} orders earning rebates")

    # Show which orders are scoring
    for order_id, is_scoring in scoring.items():
        status = "[OK]" if is_scoring else "[NO]"
        print(f"{status} {order_id[:10]}...")

asyncio.run(main())
```

**Returns:** `Dict[str, bool]` (mapping order_id to scoring status)

**Performance:** Single API call for multiple orders.

**Use case:** Strategy-4 - batch check maker rebate eligibility.

---

### Get Tick Size

**Phase 6 Enhancement:** Tick sizes are now automatically fetched from CLOB API when placing orders (no manual fetching required).

```python
tick_size = await client.get_tick_size(token_id)
```

**Returns:** `Decimal` (minimum price increment)

**Common values:** Decimal("0.01"), Decimal("0.001")

### Get Neg Risk Status

```python
neg_risk = await client.get_neg_risk(token_id)
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
from polymarket import calculate_order_fee, Side

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
from polymarket import calculate_net_cost, Side

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
from polymarket import calculate_profit_after_fees, Side

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
from polymarket import get_effective_spread

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
from polymarket import check_order_profitability

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
from polymarket import validate_order, OrderRequest, Side

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
from polymarket import validate_balance, Side

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
from polymarket import validate_neg_risk_market, Market

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
from polymarket import NegRiskAdapter

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
from polymarket import ConversionCalculator

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
from polymarket import is_safe_to_trade, Market

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
from polymarket import (
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
balance = await client.get_balance("wallet_id")
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
response = await client.place_order(order, wallet_id="wallet_id")
```

**See:** `examples/10_production_safe_trading.py` for complete implementation

---

## Dashboard API

### Get Positions

```python
positions = await client.get_positions(
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
import asyncio

async def main():
    positions = await client.get_positions("strategy1", sortBy="CASHPNL", sortDirection="DESC")

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

asyncio.run(main())
```

### Get Trades

```python
trades = await client.get_trades(
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
import asyncio

async def main():
    trades = await client.get_trades("strategy1", limit=10)

    for trade in trades:
        print(f"{trade.timestamp}: {trade.side.value} {trade.size:.2f} @ ${trade.price:.4f}")

asyncio.run(main())
```

### Get Activity

```python
activity = await client.get_activity(
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
import asyncio

async def main():
    activity = await client.get_activity("strategy1", limit=20)

    for event in activity:
        print(f"{event.timestamp}: {event.type.value} - ${event.usd_value:.2f}")

asyncio.run(main())
```

### Get Portfolio Value

```python
portfolio = await client.get_portfolio_value(
    wallet_id="strategy1",
    market=None  # Optional: value for specific market
)
```

**Returns:** `PortfolioValue` (portfolio breakdown)

**PortfolioValue fields:**
- `value` (Decimal) - Total value (legacy field)
- `bets` (Decimal) - Active bet value
- `cash` (Decimal) - Available USDC
- `equity_total` (Decimal) - Total portfolio value

**Example:**
```python
import asyncio

async def main():
    # Get portfolio breakdown (new in v3.0+)
    portfolio = await client.get_portfolio_value("strategy1")
    print(f"Total Value: ${portfolio.equity_total or portfolio.value:.2f}")
    print(f"Active Bets: ${portfolio.bets or 0:.2f}")
    print(f"Available Cash: ${portfolio.cash or 0:.2f}")

    # Calculate allocation percentage
    if portfolio.equity_total and portfolio.equity_total > 0:
        allocation = (portfolio.bets / portfolio.equity_total) * 100
        print(f"Deployed: {allocation:.1f}%")

asyncio.run(main())
```

### Batch Operations (Strategy-3)

**Get Positions for Multiple Wallets:**
```python
import asyncio

async def main():
    wallet_addresses = ["0xabc...", "0xdef...", ...]  # 100+ wallets

    positions_by_wallet = await client.get_positions_batch(
        wallet_addresses,
        size_threshold=1.0
    )

    # Returns: Dict[str, List[Position]]
    for address, positions in positions_by_wallet.items():
        print(f"{address}: {len(positions)} positions")

asyncio.run(main())
```

**Get Trades for Multiple Wallets:**
```python
trades_by_wallet = await client.get_trades_batch(wallet_addresses, limit=50)
```

**Get Activity for Multiple Wallets:**
```python
activity_by_wallet = await client.get_activity_batch(wallet_addresses, limit=100)
```

**Performance:** 10x faster than sequential (100 wallets in 20-40s vs 200-400s)

### Multi-Wallet Analytics

**Aggregate Metrics:**
```python
import asyncio

async def main():
    metrics = await client.aggregate_multi_wallet_metrics(wallet_addresses)

    print(f"Total Wallets: {metrics['total_wallets']}")
    print(f"Total Positions: {metrics['total_positions']}")
    print(f"Total P&L: ${metrics['total_pnl']:.2f}")
    print(f"Avg P&L per Wallet: ${metrics['avg_pnl_per_wallet']:.2f}")
    print(f"Top Performer: {metrics['top_performers'][0]}")

asyncio.run(main())
```

**Detect Consensus Signals:**
```python
import asyncio

async def main():
    signals = await client.detect_signals(
        wallet_addresses,
        min_wallets=5,       # Min wallets agreeing
        min_agreement=0.6    # Min % agreement (0.6 = 60%)
    )

    for signal in signals:
        print(f"{signal['title']}")
        print(f"  {signal['wallet_count']} wallets on {signal['outcome']}")
        print(f"  Agreement: {signal['agreement_ratio']:.1%}")
        print(f"  Total Value: ${signal['total_value']:.2f}")

asyncio.run(main())
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
from polymarket.api.websocket_models import TradeMessage, OrderMessage

def on_order_update(message):
    """Receives typed messages: TradeMessage or OrderMessage."""
    if isinstance(message, TradeMessage):
        print(f"Trade {message.id}: {message.status} @ ${message.price}")
    elif isinstance(message, OrderMessage):
        print(f"Order {message.id}: {message.type} (matched: {message.size_matched})")

client.subscribe_user_orders(on_order_update, wallet_id="strategy1")
```

**Events:** Order fills, status changes, cancellations (typed messages in v3.2)

### Unsubscribe All

```python
client.unsubscribe_all()
```

**Auto-reconnect:** Built-in with exponential backoff

### Health Monitoring (v3.2)

```python
# Get connection statistics
stats = client._ws.stats()
# Returns: {"status": "connected", "uptime_seconds": 120, "messages_received": 450,
#           "reconnections": 0, "subscriptions": 2, "last_message_seconds_ago": 0}

# Quick health check
health = client._ws.health_check()
# Returns: {"status": "healthy"} or {"status": "degraded", "reason": "no_recent_messages"}
```

**Metrics:** Prometheus integration for production monitoring (see metrics.py)

### Batch Subscriptions (v3.3)

```python
# Subscribe to multiple markets atomically
token_ids = [token_id1, token_id2, token_id3]
result = client._ws.subscribe_markets_batch(token_ids, on_orderbook_update)

# Returns: {
#   "success": True,
#   "succeeded": [token_id1, token_id2, token_id3],
#   "failed": [],
#   "error": None
# }

# On failure, all subscriptions rolled back (transaction semantics)
```

**Transaction Semantics:** All-or-nothing. If any subscription fails, all are rolled back.

---

### Multi-Token Subscription (v3.5)

**Single WebSocket message for multiple markets**

```python
# More efficient than subscribe_markets_batch() (separate messages)
token_ids = [token_id1, token_id2, token_id3, ...]
client._ws.subscribe_markets_multi(token_ids, on_orderbook_update)

# Sends single subscription: {"type": "MARKET", "asset_ids": [...]}
```

**Performance:** Lower protocol overhead vs batch (single message vs N messages).

**Use case:** Subscribe to 10+ markets at startup.

### Message Queue Status (v3.3+)

```python
# Check queue metrics
stats = client._ws.stats()
# Returns: {
#   "queue_enabled": True,
#   "queue_size": 5,                # Current messages in queue
#   "queue_drops": 0,               # Total dropped messages (queue full)
#   "consumer_task_running": True,  # Is consumer task active
#   "duplicates_blocked": 3,        # v3.5: Dedup count
#   "dedup_cache_size": 120         # v3.5: Hash cache entries
# }
```

**Queue Configuration (v3.3+):**
```python
from polymarket.api.websocket import WebSocketClient

ws = WebSocketClient(
    # Queue (v3.3)
    enable_queue=True,             # Async processing (default: True)
    queue_maxsize=10000,           # Max queue size (default: 10000)
    queue_drop_threshold=1000,     # Circuit breaker threshold (default: 1000)

    # v3.5 enhancements
    enable_compression=True,       # permessage-deflate (default: True)
    enable_deduplication=True,     # Hash-based dedup (default: True)
    dedup_window_seconds=300,      # Dedup TTL window (default: 300s)
    on_failure_callback=None,      # Permanent failure handler

    # Ping/pong
    ping_interval=30,              # Ping interval seconds (default: 30)
    ping_timeout=10                # Ping timeout seconds (default: 10)
)
```

### Unified WebSocket Manager (v3.3)

```python
from polymarket.api.unified_websocket import UnifiedWebSocketManager
from polymarket.api.websocket import WebSocketClient
from polymarket.api.real_time_data import RealTimeDataClient

# Initialize connections
clob_ws = WebSocketClient(...)
rtds = RealTimeDataClient(...)

# Create unified manager
manager = UnifiedWebSocketManager(clob_ws, rtds)

# Start both connections atomically
status = manager.start_all(event_loop=asyncio.get_running_loop())
# Returns: {"clob": True, "rtds": True}

# Unified health check
health = manager.health_status()
# Returns: {
#   "manager_running": True,
#   "overall_status": "healthy"|"degraded"|"unhealthy",
#   "clob": {"health": {...}, "stats": {...}},
#   "rtds": {"stats": {...}}
# }

# Stop both connections
manager.stop_all()

# Context manager support
with UnifiedWebSocketManager(clob_ws, rtds) as manager:
    # Both connections active
    pass
# Both connections stopped automatically
```

**Features:**
- Coordinated lifecycle (start/stop both atomically)
- Unified health monitoring
- Graceful degradation (continue if one fails)
- Context manager support

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

**WebSocket Enums (v3.2):**
- `TradeStatus` - MATCHED, MINED, CONFIRMED, RETRYING, FAILED
- `OrderEventType` - PLACEMENT, UPDATE, CANCELLATION
- `CLOBEventType` - BOOK, TRADE, ORDER, PRICE_CHANGE, TICK_SIZE_CHANGE, LAST_TRADE_PRICE

---

## Rate Limits

Source: [official Polymarket docs](https://docs.polymarket.com/quickstart/introduction/rate-limits). Last audited 2026-04-23. Every `rate_limit_key` passed by `polymarket/api/*.py` has an explicit entry in `polymarket/config.py` RATE_LIMITS; unknown keys fall through to a conservative 100 req/10s default.

### CLOB API — Trading (burst / sustained)

| Endpoint | Pre-margin cap |
|----------|---:|
| `POST /order` | 3,500 req/10s, sustained 36,000 req/10min |
| `DELETE /order` | 3,000 req/10s, sustained 30,000 req/10min |
| `POST /orders`, `DELETE /orders` | 1,000 req/10s, sustained 15,000 req/10min |
| `DELETE /cancel-all` | 250 req/10s, sustained 6,000 req/10min |
| `DELETE /cancel-market-orders` | 1,000 req/10s, sustained 1,500 req/10min |

### CLOB API — Market data

| Endpoint | Pre-margin cap |
|----------|---:|
| `GET /book`, `/midpoint`, `/price`, `/last-trade-price`, `/spread` | 1,500 req/10s |
| `GET /books`, `/midpoints`, `/prices`, `/last-trades-prices`, `/simplified-markets` (and `POST /books`) | 500 req/10s |
| `GET /prices-history` | 1,000 req/10s |
| `GET /tick-size`, `/neg-risk` | 200 req/10s |

### CLOB API — Ledger / balance / auth / general

| Endpoint | Pre-margin cap |
|----------|---:|
| `GET /data/order`, `/order-scoring`, `POST /orders-scoring` | 900 req/10s |
| `GET /data/orders`, `/data/trades` | 500 req/10s |
| `GET /notifications` | 125 req/10s |
| `GET /balance-allowance` | 200 req/10s |
| `GET /balance-allowance/update` | 50 req/10s |
| Auth endpoints (`POST /auth/api-key`, `GET /auth/derive-api-key`, `POST /auth/nonce`) | 100 req/10s |
| `GET /ok`, `GET /`, `GET /time` | 100 req/10s |
| CLOB default | 9,000 req/10s |

### Gamma API

| Endpoint | Pre-margin cap |
|----------|---:|
| `GET /markets` | 300 req/10s |
| `GET /events`, `/events/pagination` | 500 req/10s |
| `GET /comments`, `/tags` | 200 req/10s |
| `GET /search` | 300 req/10s (docs also list `/public-search` at 350 req/10s) |
| `GET /public-profile` | 100 req/10s |
| Gamma default | 4,000 req/10s |

### Data API

| Endpoint | Pre-margin cap |
|----------|---:|
| `GET /positions`, `/closed-positions` | 150 req/10s |
| `GET /trades`, `/v1/leaderboard` | 200 req/10s |
| `GET /activity`, `/holders`, `/value` | 1,000 req/10s |
| Data API default | 1,000 req/10s |

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
from polymarket.metrics import get_metrics
metrics = get_metrics()
```

---

## Error Handling

### Exception Types

```python
from polymarket.exceptions import (
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
import asyncio
from decimal import Decimal, ROUND_HALF_UP

async def main():
    try:
        response = await client.place_order(order, wallet_id="strategy1")
    except InsufficientBalanceError as e:
        print(f"Not enough funds: {e.message}")
    except TickSizeError as e:
        # Adjust price to valid tick size (e.g., 0.01)
        order.price = order.price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        response = await client.place_order(order, wallet_id="strategy1")
    except ValidationError as e:
        print(f"Invalid order: {e.message}")

asyncio.run(main())
```

**Order Delayed (NOT rejected):**
```python
import asyncio
import time

async def main():
    try:
        response = await client.place_order(order, wallet_id="strategy1")
    except OrderDelayedError as e:
        # Order is delayed, not rejected
        # Wait and check status
        time.sleep(5)
        orders = client.get_orders(wallet_id="strategy1")  # Note: get_orders is sync
        # Find your order and check status

asyncio.run(main())
```

**Allowance Check:**
```python
import asyncio

async def main():
    try:
        response = await client.place_order(order, wallet_id="strategy1")
    except InsufficientAllowanceError:
        # Set allowances
        from polymarket.utils.allowances import AllowanceManager
        manager = AllowanceManager()
        tx_hashes = manager.set_allowances(private_key)
        manager.wait_for_approvals(tx_hashes)
        # Retry order
        response = await client.place_order(order, wallet_id="strategy1")

asyncio.run(main())
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
from polymarket import PolymarketClient

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
status = await client.health_check()
# Returns: {"status": "healthy"} or {"status": "degraded"}
```

### Graceful Shutdown

```python
# Always call when done
await client.close()
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

from polymarket.metrics import get_metrics

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
from polymarket.utils.structured_logging import (
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
import asyncio
from decimal import Decimal
from polymarket import PolymarketClient, WalletConfig, OrderRequest, Side

async def main():
    # Initialize
    client = PolymarketClient()
    wallet = WalletConfig(private_key=os.getenv("WALLET_PRIVATE_KEY"))
    client.add_wallet(wallet, wallet_id="strategy1")

    # Get active markets
    markets = await client.get_markets(active=True, limit=10)

    for market in markets:
        # Get orderbook
        token_id = market.tokens[0] if market.tokens else None
        if not token_id:
            continue

        book = await client.get_orderbook(token_id)

        # Check spread
        if book.spread and book.spread < Decimal("0.05"):  # Tight spread
            # Place buy order below midpoint
            order = OrderRequest(
                token_id=token_id,
                price=book.midpoint - Decimal("0.01"),
                size=Decimal("10.0"),
                side=Side.BUY
            )
            response = await client.place_order(order, wallet_id="strategy1")
            print(f"Order: {response.order_id if response.success else response.error_msg}")

    # Check positions
    positions = await client.get_positions("strategy1")
    for pos in positions:
        print(f"{pos.title}: ${pos.cash_pnl:+.2f}")

asyncio.run(main())
```

### Strategy-3: Multi-Wallet Tracking

```python
import asyncio

async def main():
    # Track 100+ external wallets
    tracked_wallets = ["0xabc...", "0xdef...", ...]  # 100+ addresses

    # Batch fetch positions (10x faster)
    wallet_positions = await client.get_positions_batch(tracked_wallets)

    # Aggregate metrics
    metrics = await client.aggregate_multi_wallet_metrics(tracked_wallets)
    print(f"Total P&L: ${metrics['total_pnl']:.2f}")
    print(f"Top Performer: {metrics['top_performers'][0]}")

    # Detect consensus signals
    signals = await client.detect_signals(
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
        await client.place_order(order, wallet_id="strategy3")

asyncio.run(main())
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
