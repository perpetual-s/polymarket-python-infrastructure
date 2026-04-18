# WebSocket Enhancement Roadmap

**Status:** Phase 1 in progress
**Priority:** Production hardening → Console integration → Nice-to-have

---

## Current State

**CLOB WebSocket** (`api/websocket.py`):
- Market channel (orderbook updates)
- User channel (order fills, authenticated)
- Auto-reconnect, thread-safe callbacks
- ❌ Raw Dict responses (no typed models)
- ❌ No health monitoring
- ❌ No metrics

**Real-Time Data Service** (`api/real_time_data.py`):
- 12+ stream types (activity, crypto_prices, comments, RFQ, etc.)
- Typed messages (Message dataclass)
- Connection status tracking
- Health monitoring (stats())
- ✅ Production-ready

---

## Phase 1: Production Hardening (1-2 days)

### 1.1 Message Typing
**Problem:** Raw Dict responses, no type safety
**Solution:** Add Pydantic models matching official API specs

**Models:**
```python
# Enums
class CLOBEventType(str, Enum):
    BOOK = "book"
    TRADE = "trade"
    ORDER = "order"
    PRICE_CHANGE = "price_change"
    TICK_SIZE_CHANGE = "tick_size_change"
    LAST_TRADE_PRICE = "last_trade_price"

class TradeStatus(str, Enum):
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"

class OrderEventType(str, Enum):
    PLACEMENT = "PLACEMENT"
    UPDATE = "UPDATE"
    CANCELLATION = "CANCELLATION"

# Market Channel Messages
@dataclass
class OrderbookMessage:
    event_type: str  # "book"
    asset_id: str
    market: str
    timestamp: str
    hash: str
    buys: List[Dict[str, str]]   # [{"price": "0.48", "size": "30"}]
    sells: List[Dict[str, str]]

@dataclass
class PriceChangeMessage:
    event_type: str  # "price_change"
    market: str
    timestamp: str
    price_changes: List[Dict[str, str]]  # asset_id, price, size, side, hash, best_bid, best_ask

@dataclass
class TickSizeChangeMessage:
    event_type: str  # "tick_size_change"
    asset_id: str
    market: str
    old_tick_size: str
    new_tick_size: str
    side: str
    timestamp: str

@dataclass
class LastTradePriceMessage:
    event_type: str  # "last_trade_price"
    asset_id: str
    market: str
    price: str
    side: str
    size: str
    fee_rate_bps: str
    timestamp: str

# User Channel Messages
@dataclass
class MakerOrder:
    asset_id: str
    matched_amount: str
    order_id: str
    outcome: str
    owner: str
    price: str

@dataclass
class TradeMessage:
    event_type: str  # "trade"
    type: str  # "TRADE"
    id: str
    asset_id: str
    market: str
    status: TradeStatus
    side: Side
    size: str
    price: str
    outcome: str
    owner: str
    trade_owner: str
    taker_order_id: str
    maker_orders: List[MakerOrder]
    timestamp: str
    last_update: str
    matchtime: str

@dataclass
class OrderMessage:
    event_type: str  # "order"
    type: OrderEventType
    id: str
    asset_id: str
    market: str
    outcome: str
    side: Side
    price: str
    original_size: str
    size_matched: str
    owner: str
    order_owner: str
    associate_trades: List[str]
    timestamp: str
```

**Changes:**
- `api/websocket_models.py` - New file with typed models
- `api/websocket.py` - Parse and validate messages
- `client.py` - Update callback signatures to use typed models
- `examples/04_real_time_websocket.py` - Update to use typed responses

**Impact:** Type-safe handling, better IDE support, runtime validation

---

### 1.2 Health Monitoring
**Problem:** No visibility into connection health, latency, message counts

**Solution:** Add stats() and health_check() methods

**Implementation:**
```python
class WebSocketClient:
    def __init__(self, ...):
        self._message_count = 0
        self._last_message_time = time.time()
        self._connection_start_time: Optional[float] = None
        self._total_reconnections = 0

    def stats(self) -> Dict[str, Any]:
        """Connection statistics."""
        uptime = None
        if self._connection_start_time and self._running:
            uptime = int(time.time() - self._connection_start_time)

        return {
            "status": "connected" if self._running else "disconnected",
            "connected": self._running,
            "uptime_seconds": uptime,
            "messages_received": self._message_count,
            "reconnections": self._total_reconnections,
            "current_reconnect_attempts": self._reconnect_count,
            "subscriptions": len(self._subscriptions),
            "last_message_seconds_ago": int(time.time() - self._last_message_time),
        }

    def health_check(self) -> Dict[str, str]:
        """Quick health status."""
        if not self._running:
            return {"status": "disconnected"}

        # Check message freshness
        time_since_last = time.time() - self._last_message_time
        if time_since_last > 60:  # No messages for 60s (stale)
            return {"status": "degraded", "reason": "no_recent_messages"}

        return {"status": "healthy"}
```

**Impact:** Better observability, easier debugging, dashboard integration

---

### 1.3 Prometheus Metrics
**Problem:** No production metrics for WebSocket performance

**Solution:** Add metrics tracking to existing metrics.py

**Metrics:**
```python
# In polymarket/metrics.py

websocket_messages_total = Counter(
    "polymarket_websocket_messages_total",
    "Total WebSocket messages received",
    ["channel", "event_type"]
)

websocket_connections_active = Gauge(
    "polymarket_websocket_connections_active",
    "Active WebSocket connections",
    ["channel"]
)

websocket_reconnections_total = Counter(
    "polymarket_websocket_reconnections_total",
    "Total WebSocket reconnections",
    ["channel"]
)

websocket_message_processing_seconds = Histogram(
    "polymarket_websocket_message_processing_seconds",
    "Time to process WebSocket message",
    ["channel", "event_type"]
)

websocket_connection_uptime_seconds = Gauge(
    "polymarket_websocket_connection_uptime_seconds",
    "WebSocket connection uptime",
    ["channel"]
)
```

**Integration:**
- Track messages in `_on_message()`
- Track reconnections in `_on_close()` and `_on_error()`
- Update uptime gauge in background thread
- Expose via existing Prometheus endpoint (port 9090)

**Impact:** Production monitoring, alerting, grafana dashboards

---

## Phase 2: Console Integration (3-5 days)

### 2.1 Real-Time Dashboard Updates
**Goal:** Stream live data to Next.js console

**Architecture:**
```
Bot (Python) → WebSocket → PolymarketClient → PostgreSQL
                                             ↓
                                           tRPC API
                                             ↓
                                    Server-Sent Events (SSE)
                                             ↓
                                      Next.js Console (Browser)
```

**Features:**
- Live orderbook updates (price ticker component)
- Real-time position P&L updates (positions table)
- Order fill notifications (toast notifications)
- Market price changes (chart component)

**Implementation:**
- Create SSE endpoint in Next.js API routes
- Subscribe to WebSocket in bot
- Push updates to PostgreSQL → tRPC → SSE
- React components subscribe to SSE stream

---

### 2.2 Live Position P&L Tracking
**Goal:** Show P&L changes in real-time (not just on refresh)

**Approach:**
- Subscribe to user channel (order fills)
- On fill: recalculate position P&L immediately
- Push update via SSE to console
- Update positions table without page refresh

---

### 2.3 Order Fill Notifications
**Goal:** Toast notifications when orders fill

**Approach:**
- Subscribe to user channel
- On TradeMessage with status=MATCHED/CONFIRMED
- Push notification via SSE
- Display toast in console

---

## Phase 3: Production-Grade Features ✅ COMPLETED (v3.3)

### 3.1 Unified WebSocket Manager
**Goal:** Single interface for both CLOB + RTDS WebSockets

**Implementation:**
```python
class UnifiedWebSocketManager:
    def __init__(self, clob_ws: WebSocketClient, rtds: RealTimeDataClient):
        self.clob = clob_ws
        self.rtds = rtds

    def start_all(self):
        """Start both connections."""
        self.clob.connect()
        self.rtds.connect()

    def stop_all(self):
        """Stop both connections."""
        self.clob.disconnect()
        self.rtds.disconnect()

    def health_status(self) -> Dict[str, Any]:
        """Combined health."""
        return {
            "clob": self.clob.health_check(),
            "rtds": self.rtds.stats(),
        }
```

---

### 3.2 Batch Subscriptions
**Goal:** Subscribe to multiple markets at once

**Implementation:**
```python
def subscribe_markets_batch(
    self,
    token_ids: List[str],
    callback: Callable
) -> None:
    """Subscribe to multiple markets."""
    for token_id in token_ids:
        self.subscribe_market(token_id, callback)
```

---

### 3.3 Message Queue/Buffer
**Goal:** Handle high-frequency message bursts

**Approach:**
- Add Queue for buffering messages
- Worker thread processes queue
- Prevents callback blocking

**Implementation Status (v3.3):**
- ✅ Message queue with queue.Queue (maxsize=10000)
- ✅ Consumer task with asyncio for async processing
- ✅ Metrics: queue_drops, queue_lag, queue_size
- ✅ Backward compatible (enable_queue parameter)
- ✅ Used in api/websocket.py

**Implementation Status for All Phase 3:**
- ✅ 3.1 UnifiedWebSocketManager - api/unified_websocket.py (196 lines)
- ✅ 3.2 Batch subscriptions - subscribe_markets_batch() with rollback
- ✅ 3.3 Message queue - Integrated into WebSocketClient
- ✅ All features production-ready and documented

---

## Testing Requirements

**Phase 1:**
- Unit tests for message parsing/validation
- Integration tests with mocked WebSocket
- Testnet live tests for message types

**Phase 2:**
- E2E tests: Bot → PostgreSQL → Console
- SSE connection stability tests
- Latency benchmarks (WebSocket → Browser)

**Phase 3:**
- Multi-connection stress tests
- Batch subscription performance tests
- Message buffer overflow tests

---

## Official API References

**Polymarket Docs:**
- User channel: https://docs.polymarket.com/developers/CLOB/websocket/user-channel
- Market channel: https://docs.polymarket.com/developers/CLOB/websocket/market-channel
- Overview: https://docs.polymarket.com/developers/CLOB/websocket/wss-overview

**Message Schemas:**
- Book: asset_id, market, timestamp, hash, buys[], sells[]
- Trade: event_type="trade", status (MATCHED/MINED/CONFIRMED/RETRYING/FAILED)
- Order: event_type="order", type (PLACEMENT/UPDATE/CANCELLATION)
- PriceChange: price_changes[] with asset_id, price, size, side, best_bid, best_ask
- TickSizeChange: old_tick_size, new_tick_size
- LastTradePrice: price, side, size, fee_rate_bps

---

**Last Updated:** 2025-11-13
**Version:** 1.0.0
