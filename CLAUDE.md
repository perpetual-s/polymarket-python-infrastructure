# Repository guide for coding agents

This repository is a standalone public Polymarket client. The public import
namespace is always `polymarket`; keep it that way and do not add
application-specific policy.

## Read first

- `README.md` for supported behavior and installation.
- `polymarket/QUICKSTART.md` for public and authenticated examples.
- `polymarket/API_REFERENCE.md` for method and result contracts.
- `CONTRIBUTING.md` before changing code or tests.

## Working rules

- Keep all Polymarket HTTP, signing, and WebSocket behavior behind the existing
  `PolymarketClient` facade and `polymarket/api/` modules. Do not bypass them
  with ad hoc network calls.
- The package is async-first. Await network and wallet operations; keep local
  registration and state inspection synchronous.
- Use `Decimal` for prices, sizes, collateral, fees, and P&L. Preserve typed
  result objects and typed exceptions.
- Never commit private keys, API credentials, wallet exports, `.env` files, or
  captured authenticated responses. Preserve credential redaction at logging
  and exception boundaries.
- Do not make live, funded, or account-mutating calls while developing or
  testing unless the user explicitly requests them. The default suite is
  hermetic.
- Treat an ambiguous order submission as possibly accepted. Preserve the
  reservation and reconcile by deterministic order ID; do not infer terminal
  state from disappearance from open orders.
- Keep optional on-chain support lazy so the base package imports without
  Web3. Changes to public behavior require matching tests and documentation.
- Offline tests prove local contracts, not funding, entitlement, regional
  access, or live exchange behavior.

## Validation

From the repository root:

```bash
python -m compileall -q polymarket
python -m pytest -q polymarket/tests
git diff --check
```

Install a development checkout with:

```bash
python -m pip install -e ".[dev]"
```

## Git hygiene

Use one scope per commit, such as `polymarket: ...`, `tests: ...`, or
`docs: ...`. Stage specific paths rather than the entire working tree. Do not
commit or push unless the user explicitly authorizes it.
