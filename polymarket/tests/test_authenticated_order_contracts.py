"""Hermetic regressions for current authenticated CLOB contracts."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from polymarket.api.clob import CLOBAPI
from polymarket.api.websocket import ChannelType, WebSocketClient
from polymarket.auth.authenticator import Authenticator
from polymarket.auth.key_manager import KeyManager
from polymarket.config import PolymarketSettings
from polymarket.exceptions import (
    APIError,
    AuthenticationError,
    InvalidOrderError,
    MarketNotReadyError,
    TradingError,
)
from polymarket.models import OrderStatus, SignatureType, WalletConfig


def _documented_order(**overrides):
    row = {
        "id": "0x" + "a" * 64,
        "status": "ORDER_STATUS_LIVE",
        "owner": "owner-id",
        "maker_address": "0x" + "1" * 40,
        "market": "0x" + "2" * 64,
        "asset_id": "123456789",
        "side": "BUY",
        "original_size": "10.5",
        "size_matched": "2.25",
        "price": "0.57",
        "outcome": "YES",
        "expiration": "1735689600",
        "order_type": "GTC",
        "associate_trades": ["trade-1"],
        "created_at": 1700000000,
    }
    row.update(overrides)
    return row


def test_user_subscription_uses_current_authenticated_frame():
    client = WebSocketClient(
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        enable_metrics=False,
        enable_queue=False,
    )
    socket = Mock()
    client._ws = socket

    client._send_subscribe(ChannelType.USER, None)

    assert json.loads(socket.send.call_args.args[0]) == {
        "auth": {
            "apiKey": "key",
            "secret": "secret",
            "passphrase": "passphrase",
        },
        "markets": [],
        "type": "user",
    }


def test_user_subscription_replays_on_initial_open():
    client = WebSocketClient(
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        enable_metrics=False,
        enable_queue=False,
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(client, "connect", Mock())
        client.subscribe_user(Mock())

    socket = Mock()
    client._ws = socket
    client._running = True
    client._on_open(socket)

    assert json.loads(socket.send.call_args.args[0])["type"] == "user"
    assert client.wait_until_connected(0) is True


def test_poly_1271_wallet_accepts_explicit_funder_field():
    manager = KeyManager()
    funder = "0x" + "3" * 40

    manager.add_wallet(
        WalletConfig(
            private_key="0x" + "1" * 64,
            funder=funder,
            signature_type=SignatureType.POLY_1271,
        ),
        wallet_id="deposit-wallet",
    )

    credentials = manager.get_wallet("deposit-wallet")
    assert credentials.signature_type == SignatureType.POLY_1271
    assert credentials.funder.lower() == funder.lower()


@pytest.mark.parametrize(
    ("wire_status", "expected"),
    [
        ("ORDER_STATUS_CANCELED_MARKET_RESOLVED", OrderStatus.CANCELLED),
        ("ORDER_STATUS_INVALID", OrderStatus.REJECTED),
        ("ORDER_STATUS_MATCHED", OrderStatus.MATCHED),
    ],
)
def test_official_terminal_order_statuses_normalize(wire_status, expected):
    assert OrderStatus.normalize(wire_status) is expected


@pytest.mark.asyncio
async def test_get_orders_parses_documented_fields_without_loss():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(
        return_value={"data": [_documented_order()], "next_cursor": "LTE="}
    )
    try:
        orders = await api.get_orders(
            address="0x" + "1" * 40,
            api_key="key",
            api_secret="c2VjcmV0",
            api_passphrase="passphrase",
        )
    finally:
        await api.close()

    assert len(orders) == 1
    order = orders[0]
    assert order.token_id == "123456789"
    assert order.original_size == Decimal("10.5")
    assert order.size == Decimal("10.5")
    assert order.size_matched == Decimal("2.25")
    assert order.status == "live"
    assert order.associate_trades == ["trade-1"]


@pytest.mark.asyncio
async def test_get_order_returns_terminal_order_and_404_none():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(
        return_value=_documented_order(
            status="ORDER_STATUS_MATCHED", size_matched="10.5"
        )
    )
    kwargs = {
        "order_id": "0x" + "a" * 64,
        "address": "0x" + "1" * 40,
        "api_key": "key",
        "api_secret": "c2VjcmV0",
        "api_passphrase": "passphrase",
    }
    try:
        order = await api.get_order(**kwargs)
        assert order is not None
        assert order.status == "matched"
        assert order.size_matched == Decimal("10.5")

        api.get.side_effect = APIError("not found", status_code=404)
        assert await api.get_order(**kwargs) is None
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wire_status", "expected"),
    [
        ("ORDER_STATUS_CANCELED_MARKET_RESOLVED", OrderStatus.CANCELLED),
        ("ORDER_STATUS_INVALID", OrderStatus.REJECTED),
    ],
)
async def test_get_order_parses_all_documented_terminal_statuses(
    wire_status, expected
):
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(return_value=_documented_order(status=wire_status))
    try:
        order = await api.get_order(
            order_id="0x" + "a" * 64,
            address="0x" + "1" * 40,
            api_key="key",
            api_secret="c2VjcmV0",
            api_passphrase="passphrase",
        )
    finally:
        await api.close()

    assert order is not None
    assert order.status == expected.value


@pytest.mark.asyncio
async def test_get_authenticated_trades_paginates_and_parses_maker_economics():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    trade = {
        "id": "trade-1",
        "taker_order_id": "0x" + "a" * 64,
        "market": "0x" + "2" * 64,
        "asset_id": "123456789",
        "side": "BUY",
        "size": "4.5",
        "fee_rate_bps": "625",
        "price": "0.40",
        "status": "CONFIRMED",
        "maker_orders": [{
            "order_id": "0x" + "b" * 64,
            "matched_amount": "4.5",
            "price": "0.40",
            "fee_rate_bps": "625",
        }],
    }
    api.get = AsyncMock(side_effect=[
        {"data": [trade], "next_cursor": "cursor-2"},
        {"data": [], "next_cursor": "LTE="},
    ])
    try:
        trades = await api.get_trades(
            address="0x" + "1" * 40,
            api_key="key",
            api_secret="c2VjcmV0",
            api_passphrase="passphrase",
            trade_id="trade-1",
            asset_id="123456789",
        )
    finally:
        await api.close()

    assert len(trades) == 1
    assert trades[0].size == Decimal("4.5")
    assert trades[0].fee_rate_bps == 625
    assert trades[0].maker_orders[0].matched_amount == Decimal("4.5")
    assert api.get.await_args_list[0].kwargs["params"] == {
        "id": "trade-1",
        "asset_id": "123456789",
        "next_cursor": "MA==",
    }
    assert api.get.await_args_list[1].kwargs["params"]["next_cursor"] == "cursor-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"data": []},
        {"data": "not-a-list", "next_cursor": "LTE="},
        [],
    ],
)
async def test_get_authenticated_trades_rejects_incomplete_pages(response):
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(return_value=response)
    try:
        with pytest.raises(TradingError):
            await api.get_trades(
                address="0x" + "1" * 40,
                api_key="key",
                api_secret="c2VjcmV0",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_get_authenticated_trades_rejects_repeated_cursor():
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.get = AsyncMock(
        return_value={"data": [], "next_cursor": "MA=="}
    )
    try:
        with pytest.raises(TradingError, match="repeated cursor"):
            await api.get_trades(
                address="0x" + "1" * 40,
                api_key="key",
                api_secret="c2VjcmV0",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_msg", "expected_error"),
    [
        ("INVALID_SIGNATURE", AuthenticationError),
        ("SIZE_TOO_SMALL", InvalidOrderError),
        ("MARKET_CLOSED", MarketNotReadyError),
    ],
)
async def test_post_order_preserves_definitive_rejection_types(
    error_msg,
    expected_error,
):
    """The facade must be able to release durable intents on definite rejects."""
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock(
        return_value={
            "success": False,
            "errorMsg": error_msg,
            "orderID": None,
            "status": None,
        }
    )
    try:
        with pytest.raises(expected_error):
            await api.post_order(
                signed_order={
                    "salt": 1,
                    "maker": "0x" + "1" * 40,
                    "signer": "0x" + "1" * 40,
                    "taker": "0x" + "0" * 40,
                    "tokenId": "123",
                    "makerAmount": 1,
                    "takerAmount": 1,
                    "expiration": 0,
                    "nonce": 0,
                    "feeRateBps": 0,
                    "side": 0,
                    "signatureType": 0,
                    "signature": "0x",
                },
                address="0x" + "1" * 40,
                api_key="key",
                api_secret="c2VjcmV0",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"success": "false", "errorMsg": "INVALID_PRICE"},
        {"success": True, "orderID": None, "errorMsg": None},
        {"success": True, "orderID": "order-id", "errorMsg": "unexpected"},
        {"success": False, "orderID": None, "errorMsg": None},
        {"success": False, "orderID": None, "errorMsg": "rate limited"},
    ],
)
async def test_post_order_rejects_malformed_or_unclassified_response_truth(
    response,
):
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock(return_value=response)
    try:
        with pytest.raises(TradingError):
            await api.post_order(
                signed_order={
                    "salt": 1,
                    "maker": "0x" + "1" * 40,
                    "signer": "0x" + "1" * 40,
                    "taker": "0x" + "0" * 40,
                    "tokenId": "123",
                    "makerAmount": 1,
                    "takerAmount": 1,
                    "expiration": 0,
                    "nonce": 0,
                    "feeRateBps": 0,
                    "side": 0,
                    "signatureType": 0,
                    "signature": "0x",
                },
                address="0x" + "1" * 40,
                api_key="key",
                api_secret="c2VjcmV0",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()
