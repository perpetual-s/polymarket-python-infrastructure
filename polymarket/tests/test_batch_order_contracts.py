"""Hermetic regressions for the authenticated CLOB batch-order contract."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from polymarket.api.clob import CLOBAPI
from polymarket.auth.authenticator import Authenticator
from polymarket.config import PolymarketSettings
from polymarket.exceptions import TradingError
from polymarket.models import OrderStatus


def _signed_order(token_id: str, salt: int) -> dict:
    return {
        "maker": "0x" + "1" * 40,
        "signer": "0x" + "1" * 40,
        "tokenId": token_id,
        "makerAmount": "1000000",
        "takerAmount": "2000000",
        "side": "BUY",
        "expiration": "0",
        "timestamp": "1700000000000",
        "salt": salt,
        "signatureType": 0,
        "signature": "0xsigned",
        "_orderHash": "0xlocal-only",
    }


@pytest.mark.asyncio
async def test_post_orders_batch_uses_documented_array_contract() -> None:
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(return_value={"POLY_SIGNATURE": "signature"})
    api.post = AsyncMock(
        return_value=[
            {
                "success": True,
                "orderID": "0xorder-1",
                "status": "live",
                "errorMsg": "",
            },
            {
                "success": False,
                "orderID": "",
                "status": "delayed",
                "errorMsg": "rate limited",
                "tradeIDs": ["trade-2"],
            },
        ]
    )
    signed_orders = [
        _signed_order("token-1", 2**70),
        _signed_order("token-2", 2**70 + 1),
    ]

    try:
        responses = await api.post_orders_batch(
            signed_orders=signed_orders,
            address="0x" + "1" * 40,
            api_key="api-key",
            api_secret="secret",
            api_passphrase="passphrase",
            order_types=["GTD", "FAK"],
        )
    finally:
        await api.close()

    post_kwargs = api.post.await_args.kwargs
    body = json.loads(post_kwargs["data"])
    assert body == [
        {
            "order": {
                key: value
                for key, value in signed_orders[0].items()
                if not key.startswith("_")
            },
            "owner": "api-key",
            "orderType": "GTD",
            "deferExec": False,
            "postOnly": False,
        },
        {
            "order": {
                key: value
                for key, value in signed_orders[1].items()
                if not key.startswith("_")
            },
            "owner": "api-key",
            "orderType": "FAK",
            "deferExec": False,
            "postOnly": False,
        },
    ]
    assert isinstance(body[0]["order"]["salt"], int)
    assert post_kwargs["headers"]["Content-Type"] == "application/json"
    assert api._create_l2_headers.call_args.kwargs["body"] == post_kwargs["data"]
    assert responses[0].order_id == "0xorder-1"
    assert responses[0].status == OrderStatus.LIVE
    assert responses[1].success is False
    assert responses[1].definitive_rejection is False
    assert responses[1].trade_ids == ["trade-2"]


@pytest.mark.asyncio
async def test_post_orders_batch_classifies_only_proven_item_rejections_definitive() -> None:
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(return_value={})
    api.post = AsyncMock(
        return_value=[
            {
                "success": False,
                "errorMsg": "DUPLICATE_ORDER",
                "status": "delayed",
            },
            {
                "success": False,
                "errorMsg": "INVALID_PRICE",
                "status": "rejected",
            },
        ]
    )

    try:
        responses = await api.post_orders_batch(
            signed_orders=[
                _signed_order("token-1", 1),
                _signed_order("token-2", 2),
            ],
            address="0x" + "1" * 40,
            api_key="api-key",
            api_secret="secret",
            api_passphrase="passphrase",
        )
    finally:
        await api.close()

    assert responses[0].definitive_rejection is False
    assert responses[1].definitive_rejection is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "match"),
    [
        ({"orders": []}, "expected list"),
        ([], "expected 1, got 0"),
        ([{"success": True, "status": "live"}], "missing orderID"),
        (
            [{"success": "true", "orderID": "0xorder", "status": "live"}],
            "success must be boolean",
        ),
    ],
)
async def test_post_orders_batch_rejects_incomplete_response_contract(
    response, match
) -> None:
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = Mock(return_value={})
    api.post = AsyncMock(return_value=response)

    try:
        with pytest.raises(TradingError, match=match):
            await api.post_orders_batch(
                signed_orders=[_signed_order("token-1", 123)],
                address="0x" + "1" * 40,
                api_key="api-key",
                api_secret="secret",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_post_orders_batch_rejects_more_than_documented_limit() -> None:
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api.post = AsyncMock()

    try:
        with pytest.raises(TradingError, match="at most 15"):
            await api.post_orders_batch(
                signed_orders=[
                    _signed_order(f"token-{index}", index)
                    for index in range(16)
                ],
                address="0x" + "1" * 40,
                api_key="api-key",
                api_secret="secret",
                api_passphrase="passphrase",
            )
    finally:
        await api.close()

    api.post.assert_not_awaited()
