"""Regression tests for public API payload changes."""

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from ..api.clob import CLOBAPI
from ..api.clob_public import PublicCLOBAPI
from ..api.data_api import DataAPI
from ..api.gamma import GammaAPI
from ..auth.authenticator import Authenticator
from ..config import PolymarketSettings
from ..exceptions import APIError, MarketDataError, TradingError
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
async def test_get_order_uses_official_authenticated_single_order_endpoint():
    """Single-order truth must preserve the provider's raw evidence fields."""
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(  # type: ignore[method-assign]
        return_value={"POLY_API_KEY": "key"}
    )
    response = {
        "id": "order-1",
        "status": "CANCELED",
        "original_size": "10",
        "size_matched": "3",
    }
    api.get = AsyncMock(return_value=response)

    try:
        result = await api.get_order(
            order_id="order-1",
            address="0x123",
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
        )
    finally:
        await api.close()

    assert result is response
    api._create_l2_headers.assert_called_once_with(
        address="0x123",
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        method="GET",
        path="/data/order/order-1",
    )
    api.get.assert_awaited_once_with(
        "/data/order/order-1",
        headers={"POLY_API_KEY": "key"},
        rate_limit_key="GET:/data/order",
        retry=True,
    )


@pytest.mark.asyncio
async def test_get_order_rejects_non_mapping_provider_response():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(  # type: ignore[method-assign]
        return_value={"POLY_API_KEY": "key"}
    )
    api.get = AsyncMock(return_value=[])

    try:
        with pytest.raises(TradingError, match="expected dict"):
            await api.get_order(
                order_id="order-1",
                address="0x123",
                api_key="key",
                api_secret="secret",
                api_passphrase="passphrase",
            )
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


@pytest.mark.asyncio
async def test_batch_orderbooks_empty_input_avoids_provider_request():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock()

    try:
        assert await api.get_orderbooks_batch([]) == {}
        api.post.assert_not_awaited()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_batch_orderbooks_use_one_request_and_parse_only_valid_entries(caplog):
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock(
        return_value=[
            {
                "asset_id": "token-1",
                "bids": [
                    {"price": "0.40", "size": "5"},
                    {"price": "0.60", "size": "3"},
                ],
                "asks": [
                    {"price": "0.70", "size": "2"},
                    {"price": "0.65", "size": "4"},
                ],
                "market": "market-a",
                "tick_size": "0.01",
                "neg_risk": True,
            },
            {"bids": [], "asks": []},
        ]
    )

    try:
        with caplog.at_level(logging.WARNING, logger="polymarket.api.clob"):
            books = await api.get_orderbooks_batch(["token-1", "token-2"])
    finally:
        await api.close()

    api.post.assert_awaited_once_with(
        "/books",
        json_data=[{"token_id": "token-1"}, {"token_id": "token-2"}],
        rate_limit_key="POST:/books",
        retry=True,
    )
    assert list(books) == ["token-1"]
    assert books["token-1"].bids == [
        (Decimal("0.60"), Decimal("3")),
        (Decimal("0.40"), Decimal("5")),
    ]
    assert books["token-1"].asks == [
        (Decimal("0.65"), Decimal("4")),
        (Decimal("0.70"), Decimal("2")),
    ]
    assert books["token-1"].tick_size == Decimal("0.01")
    assert books["token-1"].neg_risk is True
    assert "Missing asset_id" in caplog.text


@pytest.mark.asyncio
async def test_batch_orderbook_provider_failure_is_wrapped():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock(side_effect=RuntimeError("provider down"))

    try:
        with pytest.raises(TradingError, match="Batch orderbook fetch failed"):
            await api.get_orderbooks_batch(["token-1"])
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_health_accepts_official_ok_string_and_wraps_failures():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(return_value="OK")

    try:
        assert await api.get_ok() is True
        api.get.assert_awaited_once_with("/", rate_limit_key="GET:/", retry=False)

        api.get.reset_mock()
        api.get.side_effect = RuntimeError("connection refused")
        with pytest.raises(TradingError, match="CLOB server unavailable"):
            await api.get_ok()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_server_time_converts_seconds_and_rejects_missing_timestamp():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(return_value=1_700_000_000)

    try:
        assert await api.get_server_time() == 1_700_000_000_000

        api.get.return_value = {}
        with pytest.raises(TradingError, match="Server time response missing timestamp"):
            await api.get_server_time()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_last_trade_price_endpoints_preserve_decimal_and_missing_values():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(side_effect=[{"price": "0.55"}, {"price": None}])
    api.post = AsyncMock(
        return_value=[
            {"token_id": "token-1", "price": "0.55"},
            {"token_id": "token-2", "price": None},
            {"price": "0.99"},
        ]
    )

    try:
        assert await api.get_last_trade_price("token-1") == Decimal("0.55")
        assert await api.get_last_trade_price("token-2") is None
        assert await api.get_last_trades_prices(["token-1", "token-2"]) == {
            "token-1": Decimal("0.55"),
            "token-2": None,
        }
    finally:
        await api.close()

    api.post.assert_awaited_once_with(
        "/last-trades-prices",
        json_data=[{"token_id": "token-1"}, {"token_id": "token-2"}],
        rate_limit_key="POST:/last-trades-prices",
        retry=True,
    )


@pytest.mark.asyncio
async def test_order_scoring_endpoints_parse_batch_and_skip_malformed_entries():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(return_value={"scoring": True})
    api.post = AsyncMock(
        return_value=[
            {"order_id": "order-1", "scoring": True},
            {"order_id": "order-2"},
            {"scoring": True},
        ]
    )

    try:
        assert await api.is_order_scoring("order-1") is True
        assert await api.are_orders_scoring(["order-1", "order-2"]) == {
            "order-1": True,
            "order-2": False,
        }

        api.post.reset_mock()
        assert await api.are_orders_scoring([]) == {}
        api.post.assert_not_awaited()
    finally:
        await api.close()

    api.get.assert_awaited_once_with(
        "/order-scoring",
        params={"order_id": "order-1"},
        rate_limit_key="GET:/order-scoring",
        retry=True,
    )


@pytest.mark.asyncio
async def test_cancel_market_orders_signs_and_sends_identical_body():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(return_value={"POLY_API_KEY": "key"})
    api.delete = AsyncMock(return_value={"cancelled": ["order-1", "order-2"]})

    try:
        cancelled = await api.cancel_market_orders(
            market_id="market-a",
            address="0xabc",
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
        )
    finally:
        await api.close()

    assert cancelled == 2
    signed_body = api._create_l2_headers.call_args.kwargs["body"]
    assert api.delete.await_args.kwargs["data"] == signed_body
    api._create_l2_headers.assert_called_once_with(
        address="0xabc",
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        method="DELETE",
        path="/cancel-market-orders",
        body=signed_body,
    )
    api.delete.assert_awaited_once_with(
        "/cancel-market-orders",
        data=signed_body,
        headers={"POLY_API_KEY": "key"},
        rate_limit_key="DELETE:/cancel-market-orders",
        retry=False,
    )
