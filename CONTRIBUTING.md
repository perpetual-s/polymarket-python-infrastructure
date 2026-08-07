# Contributing

Thank you for improving the public Polymarket client.

## Set up a development checkout

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The `dev` extra includes the test suite, example-only helpers, and optional
Web3 support. For the smallest public-data installation, use
`python -m pip install -e .`.

## Make a change

- Keep the public namespace standalone: always import from `polymarket`.
- Export policy: anything named in `polymarket/API_REFERENCE.md` must be
  importable from the package root, so a new public model, exception, or
  helper belongs in its module's `__all__`, in `polymarket/__init__.py`, and
  in the reference in the same change.
- Add or update tests for behavioral changes.
- Update `README.md`, `polymarket/QUICKSTART.md`, or
  `polymarket/API_REFERENCE.md` when a public contract changes.
- Keep real credentials and authenticated response captures out of the
  repository.
- Do not use live or funded accounts in the default test suite.

Run the release checks from the repository root:

```bash
python -m compileall -q polymarket
python -m pytest -q polymarket/tests
git diff --check
```

Live-network, testnet, and manual-operator tests are opt-in and may require
separate credentials or services. Set `POLYMARKET_RUN_LIVE_TESTS=1` or
`POLYMARKET_RUN_TESTNET_TESTS=1` to include them. A green hermetic suite does
not prove that a wallet is funded or entitled to trade.

## Commits and pull requests

Use a focused subject such as `polymarket: handle 0.001 tick markets` or
`tests: cover ambiguous order submission`. Avoid broad staging and unrelated
cleanup. Explain compatibility impact and the validation you ran in the pull
request.
