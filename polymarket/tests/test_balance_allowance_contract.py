"""Authenticated CLOB balance-allowance response contracts."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket.api.clob import CLOBAPI
from polymarket.auth.authenticator import Authenticator
from polymarket.config import PolymarketSettings
from polymarket.exceptions import TradingError


def _clob_api() -> CLOBAPI:
    settings = PolymarketSettings()
    api = CLOBAPI(settings, Authenticator(chain_id=settings.chain_id))
    api._create_l2_headers = MagicMock(return_value={})
    return api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wire_balance", "expected"),
    [
        ("0", Decimal("0")),
        ("500000", Decimal("0.5")),
    ],
)
async def test_authenticated_conditional_balance_requires_explicit_truth(
    wire_balance: str,
    expected: Decimal,
) -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value={"balance": wire_balance})
    try:
        balance = await api.get_balances(
            address="0x" + "1" * 40,
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
            signature_type=2,
            funder="0x" + "2" * 40,
            asset_type="CONDITIONAL",
            token_id="12345",
        )

        assert balance.collateral == expected
        request = api.get.await_args
        assert request.args == ("/balance-allowance",)
        assert request.kwargs["params"] == {
            "address": "0x" + "1" * 40,
            "asset_type": "CONDITIONAL",
            "signature_type": 2,
            "token_id": "12345",
            "funder": "0x" + "2" * 40,
        }
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        "0",
        {},
        {"tokens": {}},
        {"balance": None},
        {"balance": ""},
        {"balance": "not-a-number"},
        {"balance": "NaN"},
        {"balance": "Infinity"},
        {"balance": "-1"},
    ],
)
async def test_authenticated_balance_rejects_incomplete_or_invalid_truth(
    response: object,
) -> None:
    api = _clob_api()
    api.get = AsyncMock(return_value=response)
    try:
        with pytest.raises(TradingError, match="Balance-allowance response"):
            await api.get_balances(
                address="0x" + "1" * 40,
                api_key="key",
                api_secret="secret",
                api_passphrase="passphrase",
                asset_type="CONDITIONAL",
                token_id="12345",
            )
    finally:
        await api.close()
