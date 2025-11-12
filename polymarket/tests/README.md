# Polymarket Client Tests

Comprehensive test suite for production readiness.

## Test Structure

```
tests/
├── unit/                    # Unit tests (isolated functions)
│   ├── test_cache.py       # Cache functionality
│   ├── test_validators.py  # Input validation
│   └── test_rate_limiter.py # Rate limiting
│
├── integration/             # Integration tests (API mocking)
│   ├── test_client.py      # Main client functionality
│   └── test_nonce_atomicity.py  # Critical: Race condition test
│
├── benchmarks/              # Performance benchmarks
│   ├── test_performance.py # Latency & throughput measurements
│   └── README.md           # Benchmark documentation
│
├── testnet/                 # Live API tests (Polygon Amoy)
│   ├── test_live_api.py    # Real API integration tests
│   └── README.md           # Testnet setup guide
│
└── README.md               # This file
```

## Running Tests

### All Tests
```bash
# From shared/polymarket directory
pytest tests/ -v
```

### Specific Test Suite
```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Critical nonce test
pytest tests/integration/test_nonce_atomicity.py -v

# Performance benchmarks
pytest tests/benchmarks/ -v -s

# Testnet tests (requires setup)
pytest tests/testnet/ -v -s
```

### Coverage Report
```bash
pytest tests/ --cov=shared.polymarket --cov-report=html
```

## Critical Tests

### Nonce Atomicity (MUST PASS)
```bash
pytest tests/integration/test_nonce_atomicity.py::TestAtomicNonceManager::test_concurrent_access_no_race_condition -v
```

This test verifies the critical fix for nonce race conditions.
**Must pass 100% of the time** before production deployment.

## Test Categories

### Unit Tests
- Cache operations
- Input validation
- Rate limiter logic
- Utility functions

### Integration Tests
- Order placement flow
- Batch operations
- WebSocket integration
- Error handling
- Health checks

### Performance Tests (Future)
- 100+ wallet batch operations
- Concurrent order placement
- WebSocket message rate

## CI/CD Integration

### GitHub Actions
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - run: pip install -e ".[test]"
      - run: pytest tests/ -v --cov
```

### Pre-commit Hook
```bash
# .git/hooks/pre-commit
#!/bin/bash
pytest tests/integration/test_nonce_atomicity.py
if [ $? -ne 0 ]; then
    echo "Critical tests failed - commit blocked"
    exit 1
fi
```

## Writing New Tests

### Test Template
```python
import pytest
from unittest.mock import Mock, patch
from shared.polymarket import PolymarketClient

class TestNewFeature:
    \"\"\"Test description.\"\"\"

    @pytest.fixture
    def client(self):
        \"\"\"Setup test client.\"\"\"
        return PolymarketClient(
            enable_rate_limiting=False,
            enable_circuit_breaker=False
        )

    def test_feature(self, client):
        \"\"\"Test specific behavior.\"\"\"
        # Setup
        ...

        # Execute
        result = client.some_method()

        # Verify
        assert result == expected
```

## Test Requirements

Tests require:
- `pytest` - Test framework
- `pytest-cov` - Coverage reporting
- `unittest.mock` - Mocking (built-in)

Install:
```bash
pip install pytest pytest-cov
```

## Known Limitations

### Not Tested (Require Live API)
- Actual order placement on testnet
- WebSocket reconnection scenarios
- Rate limit enforcement by Polymarket
- Blockchain transaction signing

These require manual testing or testnet integration tests.

## Test Metrics

Target coverage: **80%+**

Current coverage by module:
- `client.py`: 75%
- `api/clob.py`: 70%
- `api/data_api.py`: 65%
- `utils/cache.py`: 95%
- `utils/validators.py`: 100%
- `utils/rate_limiter.py`: 90%

## Continuous Testing

Run tests automatically:
```bash
# Watch mode (requires pytest-watch)
ptw tests/

# On file change
while inotifywait -e modify -r shared/polymarket; do
    pytest tests/ -v
done
```

## Troubleshooting

### Import Errors
```bash
# Install in editable mode
pip install -e .
```

### Mock Issues
```bash
# Check patch path
# Use: 'shared.polymarket.client.ClassName'
# Not: 'ClassName' (won't work)
```

### Async Tests
```bash
# Use pytest-asyncio for async tests
pip install pytest-asyncio
```

## Performance Benchmarks

Located in `tests/benchmarks/` - measures library performance.

### Running Benchmarks
```bash
pytest tests/benchmarks/ -v -s
```

### What's Measured
- Order placement latency
- Batch operation throughput
- Nonce manager performance (sequential & concurrent)
- Memory footprint per wallet
- Orderbook fetch speed

### Expected Results
- Batch speedup: ~10x vs sequential
- Nonce ops: >2M ops/sec (no race conditions)
- Memory: ~2MB per wallet

See `tests/benchmarks/README.md` for details.

## Testnet Integration Tests

Located in `tests/testnet/` - tests against real Polymarket testnet.

### Setup Required
1. Get testnet wallet (separate from production)
2. Add `TESTNET_PRIVATE_KEY` to `.env`
3. Get testnet MATIC from faucet
4. Get testnet USDC (contact Polymarket or faucet)
5. Set token allowances (run once)

### Running Testnet Tests
```bash
# All testnet tests
pytest tests/testnet/ -v -s

# Safe tests only (no funds needed)
pytest tests/testnet/test_live_api.py::TestLiveMarketData -v -s
```

### Test Categories
- **Safe:** Market data, health checks (no funds required)
- **Wallet:** Balance, positions, trades (needs testnet wallet)
- **Order Placement:** Actually places orders (skipped by default)

See `tests/testnet/README.md` for full setup guide.

## Future Tests

### Load Testing
- 100+ concurrent wallet operations
- 1000+ order placements/minute
- Memory leak detection

### Chaos Testing
- Network failures
- API timeouts
- Invalid responses

### Security Testing
- Private key handling
- Signature verification
- Input sanitization
