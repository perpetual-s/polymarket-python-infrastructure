# Examples

These examples demonstrate the standalone `polymarket` package. Start with
public, read-only calls and inspect each file before running it.

## Install

From the repository root:

```bash
python -m pip install -e ".[examples,onchain]"
```

The `onchain` extra is needed only for CTF/Web3 operations. Public market data
does not require a wallet, API credentials, or Web3.

## Safety

- Example code is educational infrastructure, not a profitable strategy or a
  substitute for exchange documentation.
- Files that add a wallet, submit an order, cancel an order, or call an
  on-chain adapter can affect real accounts. They require explicit environment
  configuration.
- `OrderRequest.size` is token quantity. BUY collateral is approximately
  `price × size` plus the current taker fee.
- Fee schedules are market-specific. Query the token's current fee metadata;
  never assume all markets have zero fees.
- Never put a private key in a source file or command history. Use a secret
  manager or an ignored local environment file.
- Offline validation does not prove funding, allowance, entitlement, regional
  access, or live exchange availability.

## Suggested order

| File | Purpose | Account impact |
|---|---|---|
| `12_public_clob_api.py` | Public prices, order books, markets, and metadata | Read-only |
| `13_portfolio_whale_discovery.py` | Public portfolio and leaderboard surfaces | Read-only unless a wallet is explicitly added |
| `05_structured_logging.py` | Structured, credential-redacting logging | Local only |
| `04_real_time_websocket.py` | CLOB WebSocket lifecycle | Read-only unless user subscriptions are enabled |
| `06_real_time_streams.py` | RTDS streams and callbacks | Read-only |
| `01_simple_trading.py` | Build and inspect a limit-order request | Preview only |
| `10_production_safe_trading.py` | Explicitly gated authenticated submission | Can submit a real order |
| `03_batch_orders.py` | Authenticated batch-order mechanics | Can submit real orders |
| `11_ctf_neg_risk_features.py` | Fee utilities and optional CTF adapter overview | On-chain methods can move assets |
| `02_multi_wallet.py` | Multi-wallet data aggregation | Depends on configured wallets |
| `08_phase4_5_6_features.py` | Metrics, health, and resilience surfaces | Review before running |
| `09_strategy4_order_scoring.py` | Illustrative local scoring | Local/read-only |
| `check_all_wallets.py` | Authenticated balance inspection | Read-only account access |
| `rtds_live_monitoring.py` | Live RTDS monitoring | Read-only |

Run a public example from the repository root:

```bash
python polymarket/examples/12_public_clob_api.py
```

The authenticated submission example defaults to preview mode. It will submit
only when its required token, wallet, and `POLYMARKET_SUBMIT=1` environment
values are all present.

See [Quick Start](../QUICKSTART.md) and the
[API reference](../API_REFERENCE.md) for the current contracts.
