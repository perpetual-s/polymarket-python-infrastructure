# Testnet Integration Tests

Real API tests against Polygon Amoy testnet.

## Setup

### 1. Get Testnet Wallet

Create a separate wallet for testnet (NEVER use production keys):

```bash
# Generate new wallet or use existing testnet wallet
# Add to .env as TESTNET_PRIVATE_KEY
```

### 2. Get Testnet MATIC

Visit Polygon Amoy faucet:
- https://faucet.polygon.technology/
- Request testnet MATIC (~0.1 MATIC for gas)

### 3. Get Testnet USDC

**Option A:** Bridge from mainnet (if supported)
**Option B:** Contact Polymarket for testnet USDC
**Option C:** Use testnet faucet if available

### 4. Configure Environment

Add to `.env`:
```bash
# Testnet wallet (separate from production)
TESTNET_PRIVATE_KEY=0x...

# Testnet RPC (optional, uses public by default)
TESTNET_RPC_URL=https://rpc-amoy.polygon.technology
```

### 5. Set Token Allowances

Before trading on testnet:
```python
from shared.polymarket.utils.allowances import AllowanceManager
from shared.polymarket import WalletConfig

manager = AllowanceManager(chain_id=80002)  # Amoy testnet
wallet = WalletConfig(private_key=os.getenv("TESTNET_PRIVATE_KEY"))

# Check allowances
status = manager.has_sufficient_allowances(wallet.address)
if not status["ready"]:
    # Set allowances (costs testnet MATIC gas)
    tx_hashes = manager.set_allowances(wallet.private_key)
    manager.wait_for_approvals(tx_hashes)
```

## Running Tests

### All Testnet Tests
```bash
pytest tests/testnet/ -v -s
```

### Specific Test Class
```bash
# Market data only (no funds needed)
pytest tests/testnet/test_live_api.py::TestLiveMarketData -v -s

# Wallet operations (needs testnet USDC)
pytest tests/testnet/test_live_api.py::TestLiveWalletOperations -v -s

# Health checks
pytest tests/testnet/test_live_api.py::TestLiveHealthCheck -v -s
```

### Skip Slow Tests
```bash
pytest tests/testnet/ -v -m "not slow"
```

## Test Categories

### Safe Tests (No Funds Required)
- `TestLiveMarketData` - Fetch markets, search, orderbooks
- `TestLiveHealthCheck` - API health checks

### Requires Testnet Wallet
- `TestLiveWalletOperations` - Balances, positions, trades, activity

### Requires Testnet USDC (Skipped by Default)
- `TestLiveOrderPlacement::test_place_small_order` - Actually places order
- `TestLiveWebSocket::test_subscribe_orderbook` - WebSocket subscription

**To enable order placement tests:** Remove `@pytest.mark.skip` decorator

## Expected Results

### With No Testnet Activity
```
TestLiveMarketData::test_get_markets PASSED
  ✓ Found 0 testnet markets

TestLiveWalletOperations::test_get_balances PASSED
  ✓ Testnet balance: 100.0 USDC

TestLiveWalletOperations::test_get_positions PASSED
  ✓ Found 0 positions
```

### With Active Testnet Markets
```
TestLiveMarketData::test_get_markets PASSED
  ✓ Found 5 testnet markets

TestLiveMarketData::test_get_orderbook PASSED
  ✓ Orderbook: 3 bids, 5 asks

TestLiveWalletOperations::test_get_positions PASSED
  ✓ Found 2 positions
    - Will BTC reach $100k?: 10.0 shares
```

## Troubleshooting

### "No testnet markets found"
- Testnet may have limited or no active markets
- Use mainnet connection to test market data instead

### "Insufficient balance"
- Request more testnet USDC from faucet
- Check balance: `testnet_client.get_balances("testnet")`

### "Insufficient allowance"
- Run allowance setup script (see Setup #5)
- Verify allowances: `manager.has_sufficient_allowances(address)`

### Rate limit errors
- Tests use rate limiting by default
- Add delays between tests if needed

## Safety Notes

1. **NEVER use production private keys** - Always use separate testnet wallet
2. **Testnet funds have no value** - Safe to experiment
3. **Order placement tests are skipped** - Enable manually when ready
4. **WebSocket tests need manual verification** - Check output during test run

## CI/CD Integration

```yaml
# .github/workflows/testnet.yml
name: Testnet Tests
on:
  schedule:
    - cron: '0 0 * * 0'  # Weekly

jobs:
  testnet:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -e ".[test]"
      - run: pytest tests/testnet/ -v
        env:
          TESTNET_PRIVATE_KEY: ${{ secrets.TESTNET_PRIVATE_KEY }}
```

## Next Steps

After testnet tests pass:
1. Run benchmark suite: `pytest tests/benchmarks/ -v`
2. Test on mainnet with small amounts
3. Deploy to production
