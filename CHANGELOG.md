# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file starts at 3.7.0. Earlier versions predate the changelog, were never
tagged, and never bumped the declared version in `pyproject.toml`, so the
history below is reconstructed from commit themes rather than from releases.
Dates are commit dates, not publication dates.

## [Unreleased]

### Added

- `get_resolution_payouts(condition_id)` returns a validated
  `ResolutionPayouts` for a settled CTF condition, or `None` when resolution
  truth is not yet stated. It reuses the existing per-condition CLOB read and
  adds no new HTTP surface. Anything open, disputed, or malformed returns
  `None` instead of an inferred payout, and a token id absent from `.payouts`
  means skip, never a 0.0 payout.
- `UnsupportedResolution` is raised, defensively, when a market looks like an
  augmented neg-risk market whose outcome universe is incomplete. The
  detection heuristic and its limits are documented in `API_REFERENCE.md`.
- PEP 561 support: the package ships `py.typed`, so the existing annotations
  are now visible to type checkers in installed environments.
- Error-handling examples in `QUICKSTART.md` and `API_REFERENCE.md`, including
  how to branch on `is_definitive_order_rejection`.
- A `metrics` extra for the optional Prometheus dependency, and `keywords`
  plus `Intended Audience`, `Framework :: AsyncIO`, and `Typing :: Typed`
  metadata for the distribution.
- This changelog, and a scope note in `README.md` stating what the library is
  and what its offline suite does and does not prove.

### Changed

- Export policy: everything named in `API_REFERENCE.md` is importable from
  `polymarket`. `exceptions.py` and `models.py` now declare explicit `__all__`
  lists, and the package root re-exports the complete error taxonomy (26
  classes plus `is_definitive_order_rejection`) and the complete public model
  set (42 models and enums). The policy is stated in `CONTRIBUTING.md`.
- `polymarket.TimeoutError` also derives from `builtins.TimeoutError`, so a
  caller's plain `except TimeoutError:` no longer silently misses a client
  timeout.
- `prometheus-client` moved from a required dependency to the `metrics`
  extra. `polymarket/metrics.py` already degraded gracefully without it.
- Documentation and docstrings that claimed "thread-safe" now describe the
  actual guarantee: safe for concurrent use from a single event loop.
- The `PolymarketClient` docstring shows `async with PolymarketClient()`.
  Construction opens an aiohttp session and therefore requires a running loop.
- Wallet and logging examples use neutral wallet identifiers, and the
  archived documentation set was trimmed to what still describes the shipped
  client.

### Removed

- Unused runtime dependencies `requests` and `py_order_utils`. Neither is
  imported anywhere in the library; CLOB v2 order construction and signing are
  implemented in `polymarket/trading/order_builder.py`.
- `polymarket/api/unified_websocket.py`, which had no references in the
  library, tests, examples, or documentation.
- Tests and examples are excluded from built wheels.
- An undocumented environment fallback for a wallet signature-type override.
- The unused `benchmark` pytest marker.

### Fixed

- `polymarket/tests/live_rtds_test.py` was tracked but never collected, since
  `python_files = test_*.py`. It is now `test_live_rtds.py`, marked
  `live_network`, and skipped unless `POLYMARKET_RUN_LIVE_TESTS=1`.
- Repaired tests that had been skipped as stale: `Event.markets` coverage now
  reflects that it holds full `Market` objects, the Gamma event-filter test
  runs in an async context and closes its session, and the rate-limiter
  over-limit test is deterministic instead of timing-sensitive.

## [3.7.0] - 2026-08-02

The first version covered by this changelog. It summarizes the whole committed
history to date by theme; there is no earlier tagged release to diff against.

### Added

- Public Gamma, CLOB, and Data API market data with no wallet required,
  including keyless market-trades and address-activity facades, keyset market
  pagination, and Gamma price-change fields.
- Typed public-flow result models for trades, market trade events, and price
  history, each carrying request evidence and explicit completeness limits so
  partial public data is not mistaken for a complete observation.
- Multi-wallet CLOB authentication for EOA, Polymarket proxy, Gnosis Safe, and
  EIP-1271 deposit-wallet routing, with signer/funder resolution helpers.
- In-repository CLOB v2 EIP-712 order construction and signing, including
  type-3 ERC-7739 wrapping, tick-aware price normalization, per-market minimum
  order size from CLOB `mos` metadata, and fee-schedule parsing that keeps
  protocol `base_fee` separate from the economic taker curve.
- Fee-inclusive BUY collateral reservation with exact release, restore, and
  reconciliation semantics, plus `is_definitive_order_rejection` to separate a
  proven non-submission from an ambiguous exchange outcome.
- CLOB WebSocket and RTDS subscription lifecycles: offline subscription
  queueing, unconditional keepalive, a staleness watchdog, protocol pings from
  the read loop, per-topic message routing, and immutable telemetry snapshots.
- Optional Web3-backed CTF and neg-risk adapter support, loaded lazily so
  public market-data use needs neither Web3 nor a private key.
- Per-endpoint rate limiting, bounded retries, TTL caching, Prometheus
  metrics, and structured JSON logging.
- A public profile API and a runnable example set.

### Changed (2026-04 to 2026-08)

- Rate limits audited against the official Polymarket documentation, with the
  activity endpoint pinned to a conservative measured limit.
- Rate-limiter state bounded with a monotonic clock and TTL cleanup of unused
  endpoints.
- The data-plane circuit breaker was split per upstream surface, so one
  failing surface no longer disables the others.
- Order recovery state hardened around restart and reconstruction.
- The logging namespace is derived from the package rather than hardcoded.
- The source tree was made standalone and namespace-neutral for publishing,
  and formatted with black and isort at line length 100.

### Fixed

- An aiohttp session leak and inconsistent API response handling.
- The leaderboard endpoint and `Activity` validation, including referral
  reward activity.
- Keyset pagination now ends only on a raw-empty page and survives per-row
  parse losses instead of truncating the universe.
- A null prices-history payload returns an empty history instead of failing.
- The `price_change` subscribe path is serialized against unsubscribe handler
  cleanup, RTDS lifecycle transitions are locked, callers receive a strong
  transport reference, ping-timer scheduling is guarded, and the RTDS handle is
  cleared when close fails.
- The wallet address guard was tightened and a `user` keyword is rejected in
  `get_address_activity`.
- Typed market-data exceptions preserve the public `token_id` for callers.

### Security

- Credentials are redacted at output boundaries: log records, exception
  messages, and exception `details` all pass through the redactor before they
  leave the process.

### Removed

- The ignored aiohttp `enable_cleanup_closed` connector argument.
- Alternate safety runtimes and RTDS integration tests that pinned a removed
  single-callback contract.
