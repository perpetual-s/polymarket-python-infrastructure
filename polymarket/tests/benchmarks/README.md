# Performance Benchmarks

Measures latency, throughput, and scalability of shared/polymarket.

## Running Benchmarks

```bash
# All benchmarks
pytest tests/benchmarks/ -v -s

# Specific benchmark
pytest tests/benchmarks/test_performance.py::TestOrderOperationBenchmarks -v -s

# Quick benchmarks only
pytest tests/benchmarks/ -v -s -k "not slow"
```

## Benchmark Categories

### Order Operations
- `test_single_order_placement` - Single order latency
- `test_batch_order_placement` - Batch order throughput

### Data Fetching
- `test_single_wallet_positions` - Position fetch latency
- `test_batch_wallet_positions` - Batch fetch for 100 wallets (Strategy-3)

### Orderbook Operations
- `test_single_orderbook_fetch` - Single orderbook latency
- `test_batch_orderbook_fetch` - Batch orderbook throughput

### Nonce Manager
- `test_nonce_sequential` - Sequential nonce operations
- `test_nonce_concurrent` - Concurrent nonce operations (race condition check)

### Memory
- `test_client_memory_footprint` - Memory usage per wallet

## Expected Results

### Order Operations (Mocked)
```
BENCHMARK: Single Order Placement
  Min:     2.45ms
  Max:     8.92ms
  Avg:     4.23ms
  Median:  3.87ms
  StdDev:  1.32ms
  N:       50

BENCHMARK: Batch Order Placement (10 orders)
  Min:     12.34ms
  Max:     25.67ms
  Avg:     18.45ms
  Median:  17.23ms
  StdDev:  3.21ms
  N:       20
  Throughput: 542.0 orders/sec
```

### Data Fetching (Mocked)
```
BENCHMARK: Fetch Single Wallet Positions (10 positions)
  Min:     1.23ms
  Max:     4.56ms
  Avg:     2.34ms
  Median:  2.12ms

BENCHMARK: Batch Fetch Positions (100 wallets)
  Min:     234.56ms
  Max:     456.78ms
  Avg:     345.67ms
  Median:  334.23ms
  Sequential Est: 3456.70ms
  Speedup: 10.0x
```

### Nonce Manager
```
BENCHMARK: Sequential Nonce Operations (100 ops)
  Min:     0.45ms
  Max:     1.23ms
  Avg:     0.67ms
  Throughput: 149,254 ops/sec

BENCHMARK: Concurrent Nonce Operations (10 threads)
  Min:     2.34ms
  Max:     5.67ms
  Avg:     3.45ms
  Median:  3.23ms
```

### Memory
```
BENCHMARK: Memory Footprint
  Base client:     1,234 bytes
  With 10 wallets: 5,678 bytes
  Per wallet:      ~444 bytes
```

## Real API Performance (Testnet)

For real-world performance, run testnet tests:

```bash
pytest tests/testnet/ -v -s --benchmark
```

Expected latencies with real API:
- Order placement: 200-500ms
- Position fetch: 150-300ms
- Orderbook fetch: 50-150ms
- Batch operations (100 wallets): 20-40s

## Performance Targets

### Strategy-1 (Single Wallet)
- Order placement: < 500ms (99th percentile)
- Market data: < 150ms
- Rate limit usage: < 20%

### Strategy-3 (100+ Wallets)
- Batch position fetch: < 40s (100 wallets)
- Speedup vs sequential: > 8x
- Memory per wallet: < 2MB
- Rate limit usage: < 30%

## Interpreting Results

### Good Performance
- ✅ Avg < 100ms for mocked operations
- ✅ Batch speedup > 8x
- ✅ Concurrent nonce operations with no duplicates
- ✅ Memory footprint < 5MB for 10 wallets

### Performance Issues
- ⚠ Avg > 500ms for mocked operations (check mocking overhead)
- ⚠ Batch speedup < 5x (check ThreadPool configuration)
- ⚠ Nonce duplicates (CRITICAL - race condition)
- ⚠ Memory > 10MB for 10 wallets (memory leak?)

## Optimization Tips

### For Strategy-1
- Disable rate limiting if running < 120 req/min
- Use WebSocket for real-time data (avoid polling)
- Cache market metadata

### For Strategy-3
- Increase ThreadPool workers: `batch_max_workers=20`
- Increase connection pool: `pool_connections=100, pool_maxsize=200`
- Use batch operations for all multi-wallet queries

## CI/CD Integration

```yaml
# .github/workflows/benchmarks.yml
name: Performance Benchmarks
on:
  pull_request:
  schedule:
    - cron: '0 0 * * 1'  # Weekly

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -e ".[test]"
      - run: pytest tests/benchmarks/ -v -s > benchmark_results.txt
      - uses: actions/upload-artifact@v2
        with:
          name: benchmark-results
          path: benchmark_results.txt
```

## Performance Regression Detection

Compare benchmark results over time:

```bash
# Run baseline
pytest tests/benchmarks/ -v -s > baseline.txt

# After changes
pytest tests/benchmarks/ -v -s > current.txt

# Compare (manual for now, could automate)
diff baseline.txt current.txt
```

## Next Steps

1. Run benchmarks: `pytest tests/benchmarks/ -v -s`
2. Document results in main README
3. Run testnet tests for real API performance
4. Set up CI/CD performance monitoring
