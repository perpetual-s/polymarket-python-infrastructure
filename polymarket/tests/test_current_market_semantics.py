"""Current CLOB market metadata and price-contract regressions."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from polymarket.api.clob import CLOBAPI
from polymarket.api.clob_public import PublicCLOBAPI
from polymarket.api.gamma import GammaAPI
from polymarket.auth.authenticator import Authenticator
from polymarket.client import PolymarketClient
from polymarket.config import PolymarketSettings
from polymarket.exceptions import (
    InsufficientBalanceError,
    OrderBookError,
    TradingError,
    ValidationError,
)
from polymarket.models import (
    FeeInfo,
    FeeSchedule,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    Side,
)
from polymarket.trading.order_builder import OrderBuilder


def _clob_api() -> CLOBAPI:
    settings = PolymarketSettings()
    return CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))


def _public_clob_api() -> PublicCLOBAPI:
    return PublicCLOBAPI(PolymarketSettings())


@pytest.mark.asyncio
@pytest.mark.parametrize("api_factory", [_clob_api, _public_clob_api])
async def test_clob_health_accepts_documented_ok_string(api_factory) -> None:
    api = api_factory()
    api.get = AsyncMock(return_value="OK")
    try:
        assert await api.get_ok() is True
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_factory", [_clob_api, _public_clob_api])
async def test_clob_health_preserves_explicit_false(api_factory) -> None:
    api = api_factory()
    api.get = AsyncMock(return_value={"ok": False})
    try:
        assert await api.get_ok() is False
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_factory", [_clob_api, _public_clob_api])
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"ok": "true"},
        {"status": "healthy"},
        "healthy",
        [],
    ],
)
async def test_clob_health_rejects_malformed_success_shapes(
    api_factory,
    response,
) -> None:
    api = api_factory()
    api.get = AsyncMock(return_value=response)
    try:
        with pytest.raises(TradingError, match="unavailable"):
            await api.get_ok()
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_parses_current_minimum_tick_size_field() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"minimum_tick_size": 0.001})
    try:
        assert await api.get_tick_size("token-1") == Decimal("0.001")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_rejects_unrecognized_tick_response_instead_of_defaulting() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"tick_size": 0.001})
    try:
        with pytest.raises(TradingError, match="minimum_tick_size"):
            await api.get_tick_size("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_rejects_unsupported_tick_before_client_can_cache_it() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"minimum_tick_size": "0.003"})
    try:
        with pytest.raises(TradingError, match="Unsupported"):
            await api.get_tick_size("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_parses_current_base_fee_field() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"base_fee": 1000})
    try:
        assert await api.get_fee_rate_bps("token-1") == 1000
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_rejects_unrecognized_fee_response_instead_of_zeroing() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"fee_rate_bps": 0})
    try:
        with pytest.raises(TradingError, match="base_fee"):
            await api.get_fee_rate_bps("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_rejects_unrecognized_neg_risk_response() -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={})
    try:
        with pytest.raises(TradingError, match="boolean neg_risk"):
            await api.get_neg_risk("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_parses_v2_fd_fee_curve() -> None:
    api = _clob_api()
    api.get = AsyncMock(
        side_effect=[
            {"condition_id": "0x" + "1" * 64},
            {"fd": {"r": "0.05", "e": "2", "to": True}},
        ]
    )
    try:
        assert await api.get_fee_schedule("token-1") == FeeSchedule(
            rate=Decimal("0.05"),
            exponent=Decimal("2"),
            taker_only=True,
        )
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_clob_rejects_missing_fd_contract_fields() -> None:
    api = _clob_api()
    api.get = AsyncMock(
        side_effect=[
            {"condition_id": "0x" + "1" * 64},
            {"fd": {"e": "1", "to": True}},
        ]
    )
    try:
        with pytest.raises(TradingError, match="missing"):
            await api.get_fee_schedule("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("market_payload", [{}, {"fd": None}])
async def test_clob_accepts_omitted_or_null_fd_as_fee_free(
    market_payload: dict,
) -> None:
    api = _clob_api()
    api.get = AsyncMock(
        side_effect=[
            {"condition_id": "0x" + "1" * 64},
            market_payload,
        ]
    )
    try:
        assert await api.get_fee_schedule("token-1") is None
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_payload", [{}, {"tick_size": "0.003"}])
async def test_clob_orderbook_rejects_missing_or_unsupported_tick(
    tick_payload: dict,
) -> None:
    api = _clob_api()
    api.get = AsyncMock(
        return_value={
            "asset_id": "token-1",
            "bids": [],
            "asks": [],
            **tick_payload,
        }
    )
    try:
        with pytest.raises(TradingError, match="tick_size"):
            await api.get_orderbook("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_factory", "error_type"),
    [
        (_clob_api, TradingError),
        (_public_clob_api, OrderBookError),
    ],
)
async def test_clob_orderbook_rejects_incomplete_level_instead_of_dropping_it(
    api_factory,
    error_type,
) -> None:
    api = api_factory()
    api.get = AsyncMock(
        return_value={
            "asset_id": "token-1",
            "bids": [{"price": "0.49"}],
            "asks": [{"price": "0.51", "size": "10"}],
            "tick_size": "0.01",
        }
    )
    try:
        with pytest.raises(error_type, match="incomplete"):
            await api.get_orderbook("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_factory", "error_type"),
    [
        (_clob_api, TradingError),
        (_public_clob_api, OrderBookError),
    ],
)
@pytest.mark.parametrize("response_asset_id", [None, "", "token-2"])
async def test_clob_single_orderbook_requires_requested_asset_identity(
    api_factory,
    error_type,
    response_asset_id,
) -> None:
    api = api_factory()
    api.get = AsyncMock(
        return_value={
            "asset_id": response_asset_id,
            "bids": [],
            "asks": [],
            "tick_size": "0.01",
        }
    )
    try:
        with pytest.raises(error_type, match="asset_id"):
            await api.get_orderbook("token-1")
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_factory", [_clob_api, _public_clob_api])
async def test_clob_batch_orderbook_requires_every_requested_token(
    api_factory,
) -> None:
    api = api_factory()
    api.post = AsyncMock(
        return_value=[
            {
                "asset_id": "token-1",
                "bids": [],
                "asks": [],
                "tick_size": "0.01",
                "timestamp": 1,
            }
        ]
    )
    try:
        with pytest.raises(TradingError, match="missing requested tokens"):
            await api.get_orderbooks_batch(["token-1", "token-2"])
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_factory", [_clob_api, _public_clob_api])
async def test_clob_batch_orderbook_rejects_invalid_level_instead_of_partial_book(
    api_factory,
) -> None:
    api = api_factory()
    api.post = AsyncMock(
        return_value=[
            {
                "asset_id": "token-1",
                "bids": [{"price": "NaN", "size": "10"}],
                "asks": [],
                "tick_size": "0.01",
            }
        ]
    )
    try:
        with pytest.raises(TradingError, match="invalid bids"):
            await api.get_orderbooks_batch(["token-1"])
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["tick", "fee", "neg_risk"])
async def test_order_metadata_transport_failure_is_not_cached_as_default(
    metadata: str,
) -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    token_id = "token-transport-failure"
    if metadata == "tick":
        client.market_clob.get_tick_size = AsyncMock(
            side_effect=TradingError("tick unavailable")
        )
        resolver = client._resolve_tick_size
        cache_getter = client.metadata_cache.get_tick_size
    elif metadata == "fee":
        client.market_clob.get_fee_rate_bps = AsyncMock(
            side_effect=TradingError("fee unavailable")
        )
        resolver = client._resolve_fee_rate
        cache_getter = client.metadata_cache.get_fee_rate
    else:
        client.market_clob.get_neg_risk = AsyncMock(
            side_effect=TradingError("neg_risk unavailable")
        )
        resolver = client._resolve_neg_risk
        cache_getter = client.metadata_cache.get_neg_risk

    try:
        with pytest.raises(TradingError, match="unavailable"):
            await resolver(token_id)
        assert cache_getter(token_id) is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_order_tick_refresh_overrides_stale_cached_tick_change() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.metadata_cache.set_tick_size("token-1", Decimal("0.01"))
    client.market_clob.get_tick_size = AsyncMock(return_value=Decimal("0.001"))
    try:
        assert await client._resolve_tick_size("token-1") == Decimal("0.001")
        assert client.metadata_cache.get_tick_size("token-1") == Decimal("0.001")
    finally:
        await client.close()


@pytest.mark.parametrize("price", ["0.002", "0.936", "0.998"])
def test_order_request_accepts_any_price_strictly_between_zero_and_one(
    price: str,
) -> None:
    order = OrderRequest(
        token_id="123",
        price=Decimal(price),
        size=Decimal("5"),
        side=Side.BUY,
    )
    assert order.price == Decimal(price)


@pytest.mark.parametrize("price", ["0", "1", "-0.001", "1.001"])
def test_order_request_rejects_prices_outside_open_unit_interval(price: str) -> None:
    with pytest.raises(PydanticValidationError):
        OrderRequest(
            token_id="123",
            price=Decimal(price),
            size=Decimal("5"),
            side=Side.BUY,
        )


@pytest.mark.parametrize("price", ["0.002", "0.936", "0.998"])
def test_order_builder_honors_001_tick_edges_without_signing_fee_fields(
    price: str,
) -> None:
    builder = OrderBuilder()
    builder._sign_typed_data = MagicMock(
        return_value=("0x" + "11" * 65, "0x" + "22" * 32)
    )
    order = OrderRequest(
        token_id="123",
        price=Decimal(price),
        size=Decimal("5"),
        side=Side.BUY,
    )

    signed = builder.build_order(
        order,
        private_key="0x" + "01" * 32,
        address="0x" + "12" * 20,
        tick_size=Decimal("0.001"),
        fee_rate_bps=1000,
        timestamp_ms=1_750_000_000_000,
    )

    assert Decimal(signed["makerAmount"]) / Decimal(signed["takerAmount"]) == Decimal(
        price
    )
    assert "feeRateBps" not in signed


@pytest.mark.parametrize(
    ("price", "tick_size"),
    [
        (Decimal("0.936"), Decimal("0.005")),
        (Decimal("0.9365"), Decimal("0.001")),
        (Decimal("0.936009"), Decimal("0.001")),
    ],
)
def test_order_builder_rejects_off_tick_price(
    price: Decimal,
    tick_size: Decimal,
) -> None:
    builder = OrderBuilder()
    order = OrderRequest(
        token_id="123",
        price=price,
        size=Decimal("5"),
        side=Side.BUY,
    )

    with pytest.raises(ValidationError, match="invalid for tick size"):
        builder.build_order(
            order,
            private_key="0x" + "01" * 32,
            address="0x" + "12" * 20,
            tick_size=tick_size,
            timestamp_ms=1_750_000_000_000,
        )


def test_order_builder_rejects_unknown_tick_instead_of_using_cent_precision() -> None:
    builder = OrderBuilder()
    order = OrderRequest(
        token_id="123",
        price=Decimal("0.936"),
        size=Decimal("5"),
        side=Side.BUY,
    )
    with pytest.raises(ValidationError, match="Unsupported CLOB tick size"):
        builder.build_order(
            order,
            private_key="0x" + "01" * 32,
            address="0x" + "12" * 20,
            tick_size=Decimal("0.003"),
            timestamp_ms=1_750_000_000_000,
        )


def test_order_builder_requires_tick_metadata_instead_of_defaulting() -> None:
    builder = OrderBuilder()
    order = OrderRequest(
        token_id="123",
        price=Decimal("0.5"),
        size=Decimal("5"),
        side=Side.BUY,
    )
    with pytest.raises(ValidationError, match="Tick size metadata is required"):
        builder.build_order(
            order,
            private_key="0x" + "01" * 32,
            address="0x" + "12" * 20,
            timestamp_ms=1_750_000_000_000,
        )


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        (Side.BUY, Decimal("0.5025")),
        (Side.SELL, Decimal("0.5050")),
    ],
)
def test_computed_price_aligns_directionally_to_0025_tick(
    side: Side,
    expected: Decimal,
) -> None:
    order = OrderRequest(
        token_id="123",
        price=Decimal("0.503"),
        size=Decimal("10"),
        side=side,
    )
    normalized = PolymarketClient._normalize_order_for_tick(
        order,
        Decimal("0.0025"),
    )
    assert normalized.price == expected


def test_buy_collateral_uses_same_tick_aligned_price_as_builder() -> None:
    client = object.__new__(PolymarketClient)
    order = OrderRequest(
        token_id="123",
        price=Decimal("0.9366"),
        size=Decimal("10"),
        side=Side.BUY,
    )
    normalized = client._normalize_order_for_tick(order, Decimal("0.001"))
    assert normalized.price == Decimal("0.936")
    assert client._calculate_buy_collateral(normalized) == Decimal("9.360000")


@pytest.mark.parametrize(
    ("price", "side", "expected"),
    [
        (Decimal("0.50004"), Side.BUY, Decimal("0.5000")),
        (Decimal("0.50006"), Side.SELL, Decimal("0.5001")),
    ],
)
def test_request_preserves_sub_tick_precision_until_directional_alignment(
    price: Decimal,
    side: Side,
    expected: Decimal,
) -> None:
    order = OrderRequest(
        token_id="123",
        price=price,
        size=Decimal("10"),
        side=side,
    )
    assert order.price == price
    assert (
        PolymarketClient._normalize_order_for_tick(order, Decimal("0.0001")).price
        == expected
    )


@pytest.mark.asyncio
async def test_position_balance_propagates_incomplete_observation() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.key_manager.get_wallet = MagicMock(
        return_value=SimpleNamespace(
            address="0x" + "1" * 40,
            funder="0x" + "2" * 40,
        )
    )
    client.data.get_positions_complete = AsyncMock(
        side_effect=TradingError("positions incomplete")
    )
    try:
        with pytest.raises(TradingError, match="incomplete"):
            await client.get_position_balance("token-1", wallet_id="WALLET_TEST")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_position_balance_complete_absence_is_authoritative_zero() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    funder = "0x" + "2" * 40
    client.key_manager.get_wallet = MagicMock(
        return_value=SimpleNamespace(
            address="0x" + "1" * 40,
            funder=funder,
        )
    )
    client.data.get_positions_complete = AsyncMock(
        return_value=[SimpleNamespace(asset="other-token", size=Decimal("0.5"))]
    )
    try:
        assert await client.get_position_balance(
            "token-1", wallet_id="WALLET_TEST"
        ) == Decimal("0")
        client.data.get_positions_complete.assert_awaited_once_with(
            user=funder,
            size_threshold=0,
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_gamma_parses_fee_schedule_exponent_and_rate() -> None:
    settings = PolymarketSettings()
    api = GammaAPI(settings)
    try:
        market = api._parse_market_payload(
            {
                "id": "market-1",
                "question": "Question?",
                "slug": "question",
                "conditionId": "condition-1",
                "category": "crypto",
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.5", "0.5"],
                "clobTokenIds": ["token-1", "token-2"],
                "active": True,
                "closed": False,
                "feesEnabled": True,
                "takerBaseFee": 1000,
                "feeSchedule": {
                    "rate": 0.05,
                    "exponent": 2,
                    "takerOnly": True,
                    "rebateRate": 0.25,
                },
            }
        )

        assert market.fees_enabled is True
        assert market.taker_base_fee == 1000
        assert market.fee_schedule is not None
        assert market.fee_schedule.rate == Decimal("0.05")
        assert market.fee_schedule.exponent == Decimal("2")
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_client_fee_info_uses_clob_fd_schedule_not_raw_base_fee() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.market_clob.get_fee_rate_bps = AsyncMock(return_value=1000)
    client.market_clob.get_fee_schedule = AsyncMock(
        return_value=FeeSchedule(
            rate=Decimal("0.05"),
            exponent=Decimal("2"),
            taker_only=True,
        )
    )
    try:
        assert await client.get_fee_info("token-1") == FeeInfo(
            base_fee_bps=1000,
            rate_bps=500,
            exponent=Decimal("2"),
            taker_only=True,
            rebate_rate=Decimal("0"),
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_treats_missing_clob_fd_as_fee_free_only_with_zero_base_fee() -> (
    None
):
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.market_clob.get_fee_rate_bps = AsyncMock(return_value=0)
    client.market_clob.get_fee_schedule = AsyncMock(return_value=None)
    try:
        assert await client.get_fee_info("token-free") == FeeInfo(
            base_fee_bps=0,
            rate_bps=0,
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_rejects_missing_clob_fd_when_base_fee_is_nonzero() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.market_clob.get_fee_rate_bps = AsyncMock(return_value=1000)
    client.market_clob.get_fee_schedule = AsyncMock(return_value=None)
    try:
        with pytest.raises(TradingError, match="no CLOB fd schedule"):
            await client.get_fee_info("token-fee")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_buy_preflight_reserves_fee_aware_collateral() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    order = OrderRequest(
        token_id="token-1",
        price=Decimal("0.5"),
        size=Decimal("10"),
        side=Side.BUY,
    )
    client.get_fee_info = AsyncMock(
        return_value=FeeInfo(
            base_fee_bps=1000,
            rate_bps=500,
            exponent=Decimal("2"),
        )
    )
    client.get_balances = AsyncMock(
        return_value=SimpleNamespace(collateral=Decimal("5.02"))
    )
    try:
        with pytest.raises(InsufficientBalanceError):
            await client._check_and_reserve_buy_balance(order, "WALLET_TEST")
        assert await client.get_reserved_balance("WALLET_TEST") == Decimal("0")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_buy_preflight_metadata_failure_happens_before_balance_or_reservation() -> (
    None
):
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    order = OrderRequest(
        token_id="token-1",
        price=Decimal("0.5"),
        size=Decimal("10"),
        side=Side.BUY,
    )
    client.get_fee_info = AsyncMock(
        side_effect=TradingError("fee metadata unavailable")
    )
    client.get_balances = AsyncMock()
    try:
        with pytest.raises(TradingError, match="metadata unavailable"):
            await client._check_and_reserve_buy_balance(order, "WALLET_TEST")
        client.get_balances.assert_not_awaited()
        assert await client.get_reserved_balance("WALLET_TEST") == Decimal("0")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_uses_one_normalized_price_for_reservation_and_signature() -> (
    None
):
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    wallet = SimpleNamespace(
        address="0x" + "1" * 40,
        private_key="0x" + "2" * 64,
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        signature_type=0,
        funder=None,
    )
    client.key_manager.get_wallet = MagicMock(return_value=wallet)
    client.key_manager.has_api_credentials = MagicMock(return_value=True)
    client.get_fee_info = AsyncMock(return_value=FeeInfo(base_fee_bps=0, rate_bps=0))
    client.get_balances = AsyncMock(
        return_value=SimpleNamespace(collateral=Decimal("100"), tokens={})
    )
    client._resolve_fee_rate = AsyncMock(return_value=0)
    client._resolve_neg_risk = AsyncMock(return_value=False)

    def build_order(**kwargs):
        return {
            "signedPrice": str(kwargs["order"].price),
            "_orderHash": "0x" + "a" * 64,
        }

    client.order_builder.build_order = MagicMock(side_effect=build_order)
    client.clob.post_order = AsyncMock(
        return_value=OrderResponse(
            success=True,
            order_id="0x" + "a" * 64,
            status=OrderStatus.LIVE,
        )
    )
    try:
        await client.place_order(
            OrderRequest(
                token_id="12345",
                price=Decimal("0.9366"),
                size=Decimal("10"),
                side=Side.BUY,
            ),
            wallet_id="WALLET_TEST",
            tick_size=Decimal("0.001"),
        )

        signed_order = client.clob.post_order.await_args.kwargs["signed_order"]
        assert signed_order["signedPrice"] == "0.936"
        assert await client.get_reserved_balance("WALLET_TEST") == Decimal("9.360000")
        assert client.order_builder.build_order.call_args.kwargs[
            "order"
        ].price == Decimal("0.936")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sell_fee_metadata_failure_suppresses_single_submission() -> None:
    client = PolymarketClient(
        enable_rate_limiting=False,
        enable_metrics=False,
    )
    client.key_manager.get_wallet = MagicMock(return_value=SimpleNamespace())
    client.key_manager.has_api_credentials = MagicMock(return_value=True)
    client.get_fee_info = AsyncMock(
        side_effect=TradingError("fee metadata unavailable")
    )
    client._build_signed_order = AsyncMock()
    client.clob.post_order = AsyncMock()
    try:
        with pytest.raises(TradingError, match="fee metadata unavailable"):
            await client.place_order(
                OrderRequest(
                    token_id="12345",
                    price=Decimal("0.50"),
                    size=Decimal("10"),
                    side=Side.SELL,
                ),
                wallet_id="WALLET_TEST",
                skip_balance_check=True,
                tick_size=Decimal("0.01"),
            )
        client._build_signed_order.assert_not_awaited()
        client.clob.post_order.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_clob_parses_v2_minimum_order_size() -> None:
    api = _clob_api()
    api.get = AsyncMock(
        side_effect=[
            {"condition_id": "0x" + "1" * 64},
            {"mos": 5, "mts": 0.01},
        ]
    )
    try:
        assert await api.get_minimum_order_size("token-1") == Decimal("5")
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"mos": None}, {"mos": 0}, {"mos": -1}, {"mos": "NaN"}],
)
async def test_clob_rejects_missing_or_invalid_minimum_order_size(payload) -> None:
    """The minimum is order-facing market truth; absence is an error, not zero."""
    api = _clob_api()
    api.get = AsyncMock(
        side_effect=[
            {"condition_id": "0x" + "1" * 64},
            payload,
        ]
    )
    try:
        with pytest.raises(TradingError):
            await api.get_minimum_order_size("token-1")
    finally:
        await api.close()
