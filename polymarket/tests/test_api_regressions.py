"""Regression tests for public API payload changes."""

import logging
from unittest.mock import AsyncMock

import pytest

from ..api.clob import CLOBAPI
from ..api.clob_public import PublicCLOBAPI
from ..api.data_api import DataAPI
from ..api.gamma import GammaAPI
from ..auth.authenticator import Authenticator
from ..config import PolymarketSettings
from ..exceptions import APIError, MarketDataError
from ..exceptions import TimeoutError as PolymarketTimeoutError
from ..models import Activity, LeaderboardTrader


def test_leaderboard_trader_accepts_current_payload_shape():
    """Current /v1/leaderboard payload should parse."""
    trader = LeaderboardTrader(
        rank="1",
        proxyWallet="0x123",
        userName="texaskid",
        xUsername="",
        verifiedBadge=False,
        vol=123.45,
        pnl=67.89,
        profileImage="",
    )

    assert trader.user_id == "0x123"
    assert trader.user_name == "texaskid"
    assert trader.verified_badge is False


def test_leaderboard_trader_accepts_legacy_payload_shape():
    """Legacy leaderboard payload should remain backwards compatible."""
    trader = LeaderboardTrader(
        rank="1",
        user_id="legacy-id",
        user_name="legacy-user",
        vol=123.45,
        pnl=67.89,
        profile_image="avatar.png",
    )

    assert trader.user_id == "legacy-id"
    assert trader.user_name == "legacy-user"
    assert trader.profile_image == "avatar.png"


def test_activity_accepts_new_type_and_blank_side():
    """Non-trade activities with blank side should not fail validation."""
    activity = Activity(
        timestamp=1,
        type="MAKER_REBATE",
        transactionHash="0xabc",
        size="1.0",
        usdcSize="0.5",
        side="   ",
    )

    assert activity.type == "MAKER_REBATE"
    assert activity.side is None


@pytest.mark.asyncio
async def test_get_public_profile_returns_none_on_404():
    """Profile misses should return None without retrying."""
    api = GammaAPI(PolymarketSettings())
    api.get = AsyncMock(side_effect=APIError("profile not found", status_code=404))

    try:
        assert await api.get_public_profile("0x1111111111111111111111111111111111111111") is None
        api.get.assert_awaited_once()
        assert api.get.await_args.kwargs["retry"] is False
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_get_public_profile_raises_marketdataerror_on_non_404():
    """Non-404 API failures should still raise MarketDataError."""
    api = GammaAPI(PolymarketSettings())
    api.get = AsyncMock(side_effect=APIError("server error", status_code=500))

    try:
        with pytest.raises(MarketDataError, match="server error"):
            await api.get_public_profile("0x1111111111111111111111111111111111111111")
        api.get.assert_awaited_once()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_get_activity_returns_empty_on_404_without_retry():
    """Activity misses are no recent wallet activity, not circuit-breaker failures."""
    api = DataAPI(PolymarketSettings())
    api.get = AsyncMock(side_effect=APIError("activity not found", status_code=404))

    try:
        assert await api.get_activity("0x1111111111111111111111111111111111111111") == []
        api.get.assert_awaited_once()
        assert api.get.await_args.kwargs["retry"] is False
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_get_activity_polymarket_timeout_does_not_log_error(caplog):
    """Activity poller timeouts should propagate without marker-blocking logs."""
    api = DataAPI(PolymarketSettings())
    api.get = AsyncMock(
        side_effect=PolymarketTimeoutError("Request timeout: Timeout on reading data from socket")
    )

    try:
        with caplog.at_level(logging.WARNING, logger="polymarket.api.data_api"):
            with pytest.raises(PolymarketTimeoutError):
                await api.get_activity("0x1111111111111111111111111111111111111111")
    finally:
        await api.close()

    assert not any(
        record.name == "polymarket.api.data_api" and record.levelno >= logging.ERROR
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_get_markets_keyset_uses_after_cursor_and_parses_payload():
    """Gamma keyset pagination must avoid offset and return parsed markets."""
    api = GammaAPI(PolymarketSettings())
    api.get = AsyncMock(
        return_value={
            "markets": [
                {
                    "id": "1",
                    "question": "Will this regression pass?",
                    "slug": "will-this-regression-pass",
                    "conditionId": "0xabc",
                    "category": "Testing",
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": ["0.5", "0.5"],
                    "active": True,
                    "closed": False,
                }
            ],
            "next_cursor": "cursor-2",
        }
    )

    try:
        result = await api.get_markets_keyset(
            limit=250,
            after_cursor="cursor-1",
            active=True,
            closed=False,
        )
    finally:
        await api.close()

    params = api.get.await_args.kwargs["params"]
    assert api.get.await_args.args == ("/markets/keyset",)
    assert params == {
        "limit": 100,
        "active": "true",
        "closed": "false",
        "after_cursor": "cursor-1",
    }
    assert "offset" not in params
    assert result["next_cursor"] == "cursor-2"
    assert [market.condition_id for market in result["markets"]] == ["0xabc"]


@pytest.mark.asyncio
async def test_get_midpoint_returns_none_on_no_orderbook_404():
    """No-orderbook token misses are price absence, not upstream failure."""
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(
        side_effect=APIError(
            "GET /midpoint failed with 404: {'error': 'No orderbook exists for the requested token id'}",
            status_code=404,
            response={"error": "No orderbook exists for the requested token id"},
        )
    )

    try:
        assert await api.get_midpoint("stale-token") is None
        api.get.assert_awaited_once()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_public_get_best_bid_ask_returns_none_on_no_orderbook_404_without_error_log(caplog):
    """No-orderbook /book misses should not emit marker-blocking public CLOB errors."""
    api = PublicCLOBAPI(PolymarketSettings())
    api.get = AsyncMock(
        side_effect=APIError(
            "GET /book failed with 404: "
            "{'error': 'No orderbook exists for the requested token id'}",
            status_code=404,
            response={"error": "No orderbook exists for the requested token id"},
        )
    )

    try:
        with caplog.at_level(logging.WARNING, logger="polymarket.api.clob_public"):
            assert await api.get_best_bid_ask("stale-token") is None
    finally:
        await api.close()

    api.get.assert_awaited_once()
    assert any(
        record.name == "polymarket.api.clob_public"
        and record.levelno == logging.WARNING
        and "No orderbook exists" in record.getMessage()
        for record in caplog.records
    )
    assert not any(
        record.name == "polymarket.api.clob_public" and record.levelno >= logging.ERROR
        for record in caplog.records
    )
