# Test suite

Run the hermetic suite from the repository root:

```bash
python -m pip install -e ".[test,onchain]"
python -m pytest -q polymarket/tests
```

The default run covers request and response contracts, signing, wallet
identity, fee and tick arithmetic, reservation behavior, retries, circuit
breakers, caching, WebSocket lifecycle, redaction, and regressions. It must not
place orders, use real funds, or require private credentials.

## Focused runs

```bash
python -m pytest -q polymarket/tests/unit
python -m pytest -q polymarket/tests/integration
python -m pytest -q polymarket/tests/test_clob_v2_signing.py
python -m pytest -q polymarket/tests/test_security_credential_redaction.py
```

Markers are declared in `polymarket/pytest.ini`:

- `benchmark` for performance-oriented checks.
- `integration` for tests that need an external service or live connection.
- `testnet` for explicitly configured testnet checks.
- `live_network` for explicitly enabled live-network requests.
- `manual_operator` for tests requiring a human-controlled wallet or funds.

Some files and cases are intentionally skipped when their external service,
mock context, credentials, or manual opt-in is absent. Review skipped reasons
with:

```bash
python -m pytest -ra polymarket/tests
```

Possessing credentials is not enough to activate external tests. Testnet
checks additionally require `POLYMARKET_RUN_TESTNET_TESTS=1`; live/manual
checks require `POLYMARKET_RUN_LIVE_TESTS=1`. Review the selected tests before
setting either value.

## Writing tests

- Prefer deterministic fakes and fixtures over network access.
- Use `pytest.mark.asyncio` for async behavior.
- Assert typed empty, incomplete, failed, and not-found outcomes separately.
- Include boundary cases for `Decimal`, tick size, fees, wallet identity, and
  ambiguous submission/reconciliation.
- Never use or print a real private key, API secret, passphrase, or
  authenticated response.
- Update public documentation when a method or model contract changes.

Compile and whitespace checks used by CI:

```bash
python -m compileall -q polymarket
git diff --check
```

Passing this suite proves local contracts only. It does not establish wallet
funding, allowance, account entitlement, regional access, current exchange
behavior, or profitable trading.
