#!/usr/bin/env python
"""
Live RTDS operator smoke.

Connects to PRODUCTION Polymarket RTDS (no API keys required) and proves:

  1. ACTIVE phase  — the transport connects and receives real traffic on a
     busy stream (activity/trades, platform-wide, many messages per second).
  2. QUIET phase   — a connection subscribed only to a near-silent stream
     (clob_market/market_created) survives >= 120 s with ZERO reconnections.
     Freshness comes from protocol pongs (last_pong_seconds_ago keeps
     resetting) while last_message_age_seconds may climb freely.
  3. Clean shutdown after each phase.

This is a __main__ operator script, NOT a pytest module: pytest.ini sets
python_files = test_*.py, so this file is never collected. Keep it that way.

Usage:
    python tests/live_rtds_test.py
"""

import asyncio
import sys
from pathlib import Path

# Add repository root to path so local `polymarket` imports resolve
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))

from polymarket import PolymarketClient  # noqa: E402
from polymarket.config import PolymarketSettings  # noqa: E402

ACTIVE_PHASE_TIMEOUT = 60  # seconds to wait for the first trade message
QUIET_PHASE_DURATION = 120  # seconds the quiet stream must hold
SAMPLE_INTERVAL = 5  # seconds between stat samples in the quiet phase


async def phase_active_traffic() -> bool:
    """Connect and receive real traffic on a busy stream.

    Uses activity/trades (unfiltered, platform-wide) — as of 2026-07 the
    server ignores the {"symbol": ...} filter on crypto_prices, so the
    filtered crypto subscription receives no updates and cannot serve as
    the active-traffic proof.
    """
    print("=" * 60)
    print("PHASE 1: active stream (activity/trades)")
    print("=" * 60)

    received = []
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    try:
        client.subscribe_activity_trades(received.append)
        for elapsed in range(ACTIVE_PHASE_TIMEOUT):
            await asyncio.sleep(1)
            if received:
                print(f"received first message after ~{elapsed + 1}s")
                break
        ok = bool(received)
        print(f"PHASE 1: {'PASS' if ok else 'FAIL'} ({len(received)} message(s))")
        return ok
    finally:
        await client.close()
        print("phase 1 shutdown complete\n")


async def phase_quiet_hold() -> bool:
    """Hold a near-silent stream without watchdog churn."""
    print("=" * 60)
    print(f"PHASE 2: quiet stream hold ({QUIET_PHASE_DURATION}s, clob_market/market_created)")
    print("=" * 60)

    created = []
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    try:
        client.subscribe_market_created(created.append)
        rtds = client._rtds
        print(f"config: ping_interval={rtds.ping_interval}s max_staleness={rtds.max_staleness}s")

        pong_ages, reconnects = [], []
        for i in range(QUIET_PHASE_DURATION // SAMPLE_INTERVAL):
            await asyncio.sleep(SAMPLE_INTERVAL)
            s = client.get_rtds_stats()
            pong_ages.append(s["last_pong_seconds_ago"])
            reconnects.append(s["total_reconnections"])
            print(
                f"t+{(i + 1) * SAMPLE_INTERVAL:>3}s status={s['status']} "
                f"msgs={s['total_messages_received']} age={s['last_message_age_seconds']} "
                f"reconn={s['total_reconnections']} pong_ago={s['last_pong_seconds_ago']}"
            )

        no_churn = reconnects[-1] == 0
        # Protocol-pong proof: freshness must keep resetting on a quiet stream.
        pong_fresh = max(pong_ages) <= rtds.ping_interval * 3
        print(f"\nstream messages received: {len(created)} (quiet as intended)")
        print(f"max observed pong age: {max(pong_ages)}s (limit {rtds.ping_interval * 3:g}s)")
        print(f"total_reconnections: {reconnects[-1]}")
        ok = no_churn and pong_fresh
        print(f"PHASE 2: {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        await client.close()
        print("phase 2 shutdown complete\n")


async def main() -> int:
    print()
    print("POLYMARKET RTDS LIVE OPERATOR SMOKE (production servers, ~3 min)")
    print()
    active_ok = await phase_active_traffic()
    quiet_ok = await phase_quiet_hold()
    print("=" * 60)
    print(f"RTDS LIVE SMOKE: {'PASS' if active_ok and quiet_ok else 'FAIL'}")
    print(f"  active traffic: {'PASS' if active_ok else 'FAIL'}")
    print(f"  quiet hold:     {'PASS' if quiet_ok else 'FAIL'}")
    print("=" * 60)
    return 0 if active_ok and quiet_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
