"""Offline contract tests for wallet-performance surfaces (core-loop A1).

Pins the leaderboard param passthrough (current endpoint contract:
category/timePeriod/orderBy/limit/offset) and the /closed-positions surface.
Documented shapes; the A1 live smoke corrects and re-pins on mismatch.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from polymarket.api.data_api import DataAPI
from polymarket.exceptions import APIError
from polymarket.models import ClosedPosition, LeaderboardTrader


def _api_returning(response):
    api = object.__new__(DataAPI)
    api.get = AsyncMock(return_value=response)
    return api


def _documented_trader(**overrides):
    row = {
        "rank": "1",
        "proxyWallet": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        "userName": "sharp-owl",
        "vol": "1234567.89",
        "pnl": "98765.43",
        "profileImage": None,
        "verifiedBadge": True,
    }
    row.update(overrides)
    return row


def _live_verified_closed_position(**overrides):
    # Shape captured from the real endpoint 2026-07-17 (leaderboard #1 wallet).
    row = {
        "proxyWallet": "0x09b428f7c2b469786286214aa5c90dd9015f7320",
        "asset": "46421815574359643620514688756609814860051932583278915389775335554413697029363",
        "conditionId": "0xecba7b1db1c698ab73b3572abfcde327a02b5112676144e049edcc1e1e0f5b0b",
        "avgPrice": 0.444787,
        "totalBought": 10980911.728675,
        "realizedPnl": 6096744.943612,
        "curPrice": 1,
        "title": "France vs. Spain: Team to Advance",
        "slug": "fifwc-fra-esp-2026-07-14-team-to-advance",
        "icon": "https://example.invalid/icon.png",
        "eventSlug": "fifwc-fra-esp-2026-07-14-more-markets",
        "outcome": "Spain",
        "outcomeIndex": 1,
        "oppositeOutcome": "France",
        "oppositeAsset": "112548421964662546558474258688565408276000153279440324883721010878524791926004",
        "endDate": "2026-07-14T00:00:00Z",
        "timestamp": 1784064509,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_passes_documented_query_params():
    api = _api_returning([])
    await api.get_leaderboard(
        category="CRYPTO", time_period="WEEK", order_by="VOL", limit=25, offset=50
    )
    api.get.assert_awaited_once_with(
        "/v1/leaderboard",
        params={
            "category": "CRYPTO",
            "timePeriod": "WEEK",
            "orderBy": "VOL",
            "limit": 25,
            "offset": 50,
        },
        rate_limit_key="GET:/v1/leaderboard",
        retry=True,
    )


@pytest.mark.asyncio
async def test_leaderboard_defaults_are_overall_month_pnl():
    api = _api_returning([])
    await api.get_leaderboard()
    params = api.get.await_args.kwargs["params"]
    assert params == {
        "category": "OVERALL",
        "timePeriod": "MONTH",
        "orderBy": "PNL",
        "limit": 50,
        "offset": 0,
    }


@pytest.mark.asyncio
async def test_leaderboard_parses_documented_rows():
    api = _api_returning(
        [_documented_trader(), _documented_trader(rank="2", userName="second")]
    )
    traders = await api.get_leaderboard()
    assert [t.rank for t in traders] == ["1", "2"]
    assert traders[0].user_id == "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
    assert traders[0].user_name == "sharp-owl"
    assert traders[0].vol == Decimal("1234567.89")
    assert traders[0].pnl == Decimal("98765.43")
    assert traders[0].verified_badge is True
    assert all(isinstance(t, LeaderboardTrader) for t in traders)


@pytest.mark.asyncio
async def test_leaderboard_skips_malformed_rows_without_raising():
    api = _api_returning(
        [_documented_trader(), {"garbage": True}, _documented_trader(rank="3")]
    )
    traders = await api.get_leaderboard()
    assert [t.rank for t in traders] == ["1", "3"]


@pytest.mark.asyncio
async def test_leaderboard_non_list_response_returns_empty():
    api = _api_returning({"error": "nope"})
    assert await api.get_leaderboard() == []


# ---------------------------------------------------------------------------
# Closed positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_positions_passes_user_and_paging_params():
    api = _api_returning([])
    await api.get_closed_positions(
        "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01", limit=77, offset=100
    )
    api.get.assert_awaited_once_with(
        "/closed-positions",
        params={
            "user": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
            "limit": 77,
            "offset": 100,
        },
        rate_limit_key="GET:/closed-positions",
        retry=True,
    )


@pytest.mark.asyncio
async def test_closed_positions_sort_params_are_passed_through_upper_cased():
    """Live-verified 2026-07-27: the endpoint's own default is REALIZEDPNL DESC.

    Callers reading a truncated history therefore get the wallet's biggest
    winners unless they say otherwise, which is why the monitor asks for
    TIMESTAMP explicitly. Case is normalized because the API accepts either.
    """
    api = _api_returning([])
    await api.get_closed_positions(
        "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        limit=50,
        offset=0,
        sort_by="timestamp",
        sort_direction="desc",
    )
    params = api.get.await_args.kwargs["params"]
    assert params["sortBy"] == "TIMESTAMP"
    assert params["sortDirection"] == "DESC"


@pytest.mark.asyncio
async def test_closed_positions_rejects_an_undocumented_sort_field():
    """A typo must not silently fall back to the winner-biased default."""
    api = _api_returning([])
    with pytest.raises(ValueError, match="sort_by must be one of"):
        await api.get_closed_positions("0xabc", sort_by="RECENT")
    with pytest.raises(ValueError, match="sort_direction must be one of"):
        await api.get_closed_positions(
            "0xabc", sort_by="TIMESTAMP", sort_direction="UP"
        )
    api.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_positions_parses_live_verified_rows():
    api = _api_returning(
        [_live_verified_closed_position(), _live_verified_closed_position(asset="987")]
    )
    positions = await api.get_closed_positions("0xabc")
    assert len(positions) == 2
    assert all(isinstance(p, ClosedPosition) for p in positions)
    assert positions[0].realized_pnl == Decimal("6096744.943612")
    assert positions[0].total_bought == Decimal("10980911.728675")
    assert positions[0].avg_price == Decimal("0.444787")
    assert positions[0].outcome == "Spain"
    assert positions[1].asset == "987"


@pytest.mark.asyncio
async def test_closed_positions_tolerates_missing_optional_fields():
    minimal = {
        "proxyWallet": "0xabc",
        "asset": "1",
        "conditionId": "0xc",
        "avgPrice": "0.5",
        "totalBought": "10",
        "realizedPnl": "-2.5",
    }
    api = _api_returning([minimal])
    positions = await api.get_closed_positions("0xabc")
    assert positions[0].realized_pnl == Decimal("-2.5")
    assert positions[0].title is None
    assert positions[0].cur_price == Decimal("0.0")


@pytest.mark.asyncio
async def test_closed_positions_skips_malformed_rows_without_raising():
    api = _api_returning(
        [
            _live_verified_closed_position(),
            {"broken": 1},
            _live_verified_closed_position(asset="42"),
        ]
    )
    positions = await api.get_closed_positions("0xabc")
    assert [p.asset[:2] for p in positions] == ["46", "42"]


@pytest.mark.asyncio
async def test_closed_positions_strict_parse_refuses_a_partial_page():
    api = _api_returning([_live_verified_closed_position(), {"broken": 1}])

    with pytest.raises(APIError, match="complete observation unavailable"):
        await api.get_closed_positions("0xabc", strict_parse=True)


@pytest.mark.asyncio
async def test_closed_positions_non_list_response_returns_empty():
    api = _api_returning(None)
    assert await api.get_closed_positions("0xabc") == []


@pytest.mark.asyncio
async def test_closed_positions_strict_parse_refuses_a_non_list_response():
    api = _api_returning(None)
    with pytest.raises(APIError, match="complete observation unavailable"):
        await api.get_closed_positions("0xabc", strict_parse=True)


# ---------------------------------------------------------------------------
# Transient-failure severity (429s during paper windows are WARNING, not ERROR)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_retry_is_opt_in_for_bulk_history_reads():
    api = _api_returning([])
    await api.get_activity(user="0x" + "ab" * 20, retry=True)
    assert api.get.await_args.kwargs["retry"] is True


@pytest.mark.asyncio
async def test_activity_preserves_zero_time_bounds():
    api = _api_returning([])
    await api.get_activity(user="0x" + "ab" * 20, start=0, end=0)
    assert api.get.await_args.kwargs["params"]["start"] == 0
    assert api.get.await_args.kwargs["params"]["end"] == 0


@pytest.mark.asyncio
async def test_activity_rate_limit_logs_warning_not_error(caplog):
    import logging

    from polymarket.exceptions import RateLimitError

    api = object.__new__(DataAPI)
    api.get = AsyncMock(
        side_effect=RateLimitError("GET /activity failed with 429", "GET:/activity")
    )
    with caplog.at_level(logging.WARNING, logger="polymarket.api.data_api"):
        with pytest.raises(RateLimitError):
            await api.get_activity(user="0x" + "ab" * 20)
    records = [r for r in caplog.records if "Failed to get activity" in r.message]
    assert records and all(r.levelno == logging.WARNING for r in records)
