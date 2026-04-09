"""Regression tests for public API payload changes."""

from unittest.mock import Mock

from ..api.gamma import GammaAPI
from ..config import PolymarketSettings
from ..exceptions import APIError, MarketDataError
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
        market="market",
        conditionId="0xcondition",
        asset="asset",
        title="title",
        outcome="Yes",
        side="   ",
        size="1.0",
        usdValue="0.5",
    )

    assert activity.type == "MAKER_REBATE"
    assert activity.side is None


def test_get_public_profile_returns_none_on_404():
    """Profile misses should return None without retrying."""
    api = GammaAPI(PolymarketSettings())
    api.get = Mock(side_effect=APIError("profile not found", status_code=404))

    try:
        assert api.get_public_profile("0x1111111111111111111111111111111111111111") is None
        api.get.assert_called_once()
        assert api.get.call_args.kwargs["retry"] is False
    finally:
        api.close()


def test_get_public_profile_raises_marketdataerror_on_non_404():
    """Non-404 API failures should still raise MarketDataError."""
    api = GammaAPI(PolymarketSettings())
    api.get = Mock(side_effect=APIError("server error", status_code=500))

    try:
        try:
            api.get_public_profile("0x1111111111111111111111111111111111111111")
            raise AssertionError("Expected MarketDataError")
        except MarketDataError as exc:
            assert "server error" in str(exc)
        api.get.assert_called_once()
    finally:
        api.close()
