# Polymarket Client Examples

Clear, copy-paste ready examples for Strategy-1 and Strategy-3.

## ⚠️ IMPORTANT: Production-Safe Trading

**ALL production bots MUST use example `10_production_safe_trading.py` pattern!**

This includes:
- ✅ Fee calculation BEFORE placing orders
- ✅ Order validation (price, size, parameters)
- ✅ Balance validation (including fees!)
- ✅ Profitability checks
- ✅ Proper error handling

**See example 10 for complete implementation.**

**v2.8 Update:** All examples validated against official py-clob-client patterns. Our implementations exceed official reference with defensive programming, resource cleanup, and type safety. See ROBUSTNESS_AUDIT.md.

---

## Quick Start (Production-Safe Pattern)

```python
from shared.polymarket import (
    PolymarketClient,
    WalletConfig,
    OrderRequest,
    Side,
    # CRITICAL: Import validation utilities
    validate_order,
    validate_balance,
    calculate_net_cost,
    check_order_profitability,
)

# Initialize client
client = PolymarketClient()

# Add wallet
client.add_wallet(
    WalletConfig(private_key="0x..."),
    wallet_id="my_wallet"
)

# Create order
order = OrderRequest(
    token_id="123456",
    price=0.55,
    size=100.0,
    side=Side.BUY
)

# STEP 1: Validate order
valid, error = validate_order(order)
if not valid:
    print(f"Invalid: {error}")
    return

# STEP 2: Calculate fees (Polymarket charges 0%)
net_cost, fee = calculate_net_cost(Side.BUY, 0.55, 100.0, 0)
print(f"Total cost: ${net_cost:.2f} (fee: ${fee:.2f})")

# STEP 3: Check balance
balance = client.get_balance("my_wallet")
valid, error = validate_balance(Side.BUY, 0.55, 100.0, balance.collateral, 0, 0)
if not valid:
    print(f"Insufficient balance: {error}")
    return

# STEP 4: Check profitability
profitable, profit = check_order_profitability(0.55, 0.60, 100.0, 100, 1.0)
if not profitable:
    print(f"Not profitable: ${profit:.2f}")
    return

# STEP 5: Place order (after all checks passed)
response = client.place_order(order, wallet_id="my_wallet")
print(f"Order placed: {response.order_id}, cost ${net_cost:.2f}")
```

---

## Examples by Use Case

| File | Use Case | Priority | Notes |
|------|----------|----------|-------|
| **`10_production_safe_trading.py`** | **Production-safe order placement** | **⭐ START HERE** | **All validations, fee calculations, best practices** |
| **`11_ctf_neg_risk_features.py`** | **CTF & Neg-Risk utilities** | **⭐ NEW** | **Fee calculations, validations, NegRiskAdapter** |
| `01_simple_trading.py` | Basic order placement | Strategy-1 | ⚠️ Lacks validations - use example 10 instead |
| `02_multi_wallet.py` | Multi-wallet tracking | Strategy-3 | 100+ wallets, batch operations |
| `03_batch_orders.py` | Batch order placement | Strategy-3 | 10x faster with batch endpoint |
| `04_real_time_websocket.py` | Real-time market data | Both | WebSocket orderbook updates |
| `05_structured_logging.py` | Production logging | Both | JSON logs with correlation IDs |
| `06_real_time_streams.py` | Real-time data streams | Both | WebSocket + async patterns |
| `08_phase4_5_6_features.py` | Advanced features | Both | Metrics, circuit breaker, health |
| `09_strategy4_order_scoring.py` | Order scoring system | Strategy-4 | ML-based order quality |

---

## Running Examples

```bash
# Set environment
export POLYMARKET_PRIVATE_KEY="0x..."

# Run production-safe example (RECOMMENDED)
python examples/10_production_safe_trading.py

# View CTF features
python examples/11_ctf_neg_risk_features.py

# Run any example
python examples/01_simple_trading.py
```

---

## Key Features Demonstrated

### Production Safety (Example 10)
- Pre-flight order validation
- Fee calculation before ordering
- Balance checks (including fees)
- Profitability analysis
- Comprehensive error handling

### CTF Infrastructure (Example 11)
- Fee calculation utilities (6 functions)
- Order validation utilities (9 functions)
- Neg-risk market detection
- NegRiskAdapter for conversions
- ConversionCalculator for estimates

### Multi-Wallet Operations (Example 02)
- Batch position fetching (10x faster)
- Consensus signal detection
- Aggregated metrics across wallets
- Topic performance analysis

### Real-Time Data (Example 04, 06)
- WebSocket orderbook updates
- Order fill notifications
- Auto-reconnect logic
- Event-driven architecture

---

## Documentation

- **QUICKSTART.md** - Fast 5-minute reference
- **API_REFERENCE.md** - Complete API documentation
- **Documentation/NEG_RISK_CTF.md** - CTF features guide
