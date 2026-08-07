"""
Live RTDS transport checks against production Polymarket servers.

No API keys are required, but these tests open real connections and take
roughly three minutes, so they are gated behind the ``live_network`` marker
and skipped unless ``POLYMARKET_RUN_LIVE_TESTS=1`` is set.

Run them with:

    POLYMARKET_RUN_LIVE_TESTS=1 python -m pytest -q \
        polymarket/tests/test_live_rtds.py

They prove two properties the hermetic suite cannot:

  1. ACTIVE stream — the transport connects and receives real traffic on a
     busy stream (activity/trades, platform-wide, many messages per second).
  2. QUIET stream — a connection subscribed only to a near-silent stream
     (clob_market/market_created) survives >= 120 s with ZERO reconnections.
     Freshness comes from protocol pongs (``last_pong_seconds_ago`` keeps
     resetting) while ``last_message_age_seconds`` may climb freely.
"""

import asyncio

import pytest

from polymarket import PolymarketClient
from polymarket.config import PolymarketSettings

pytestmark = pytest.mark.live_network

ACTIVE_PHASE_TIMEOUT = 60  # seconds to wait for the first trade message
QUIET_PHASE_DURATION = 120  # seconds the quiet stream must hold
SAMPLE_INTERVAL = 5  # seconds between stat samples in the quiet phase


async def test_active_stream_receives_traffic():
    """Connect and receive real traffic on a busy stream.

    Uses activity/trades (unfiltered, platform-wide) — as of 2026-07 the
    server ignores the {"symbol": ...} filter on crypto_prices, so the
    filtered crypto subscription receives no updates and cannot serve as
    the active-traffic proof.
    """
    received = []
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    try:
        client.subscribe_activity_trades(received.append)
        for _ in range(ACTIVE_PHASE_TIMEOUT):
            await asyncio.sleep(1)
            if received:
                break

        assert received, (
            f"no activity/trades message within {ACTIVE_PHASE_TIMEOUT}s"
        )
    finally:
        await client.close()


async def test_quiet_stream_holds_without_reconnect_churn():
    """Hold a near-silent stream without watchdog churn."""
    created = []
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    try:
        client.subscribe_market_created(created.append)
        rtds = client._rtds

        pong_ages, reconnects = [], []
        for _ in range(QUIET_PHASE_DURATION // SAMPLE_INTERVAL):
            await asyncio.sleep(SAMPLE_INTERVAL)
            stats = client.get_rtds_stats()
            pong_ages.append(stats["last_pong_seconds_ago"])
            reconnects.append(stats["total_reconnections"])

        assert reconnects[-1] == 0, (
            f"quiet stream reconnected {reconnects[-1]} time(s)"
        )
        # Protocol-pong proof: freshness must keep resetting on a quiet stream.
        pong_limit = rtds.ping_interval * 3
        assert max(pong_ages) <= pong_limit, (
            f"max pong age {max(pong_ages)}s exceeded limit {pong_limit:g}s"
        )
    finally:
        await client.close()
