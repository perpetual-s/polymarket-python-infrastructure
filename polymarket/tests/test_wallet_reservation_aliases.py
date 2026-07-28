"""Reservation-ledger regressions for the facade's default-wallet alias."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from polymarket.client import PolymarketClient
from polymarket.config import PolymarketSettings
from polymarket.models import (
    Balance,
    FeeInfo,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    Side,
)


def _build_client() -> PolymarketClient:
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
    )
    with patch("polymarket.client.atexit.register"):
        client = PolymarketClient(
            settings=settings,
            enable_rate_limiting=False,
            enable_circuit_breaker=False,
        )

    credentials = SimpleNamespace(
        address="0x1234567890abcdef1234567890abcdef12345678",
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        private_key="0x" + "1" * 64,
        signature_type=0,
        funder=None,
    )
    client.key_manager.get_wallet = Mock(return_value=credentials)
    client.key_manager.has_api_credentials = Mock(return_value=True)
    client.metrics.track_order = Mock()
    client.metrics.track_order_latency = Mock()
    client.metrics.set_balance = Mock()
    client._build_signed_order = AsyncMock(
        return_value={"order": "signed", "_orderHash": "order-1"}
    )
    client._resolve_tick_size = AsyncMock(return_value=Decimal("0.01"))
    client.get_fee_info = AsyncMock(
        return_value=FeeInfo(base_fee_bps=0, rate_bps=0)
    )
    client.get_balances = AsyncMock(
        return_value=Balance(collateral=Decimal("100"), tokens={})
    )
    return client


@pytest.mark.asyncio
async def test_default_alias_and_concrete_wallet_share_reservation_ledger() -> None:
    client = _build_client()
    client.key_manager.get_default_wallet = Mock(return_value="wallet-a")
    try:
        await client._reserve_balance(Decimal("7.25"), wallet_id=None)

        assert await client.get_reserved_balance("wallet-a") == Decimal("7.25")

        await client.release_reserved_balance(
            Decimal("2.25"),
            wallet_id="wallet-a",
        )
        assert await client.get_reserved_balance(None) == Decimal("5.00")

        await client.restore_reserved_balance(
            Decimal("9.50"),
            wallet_id=None,
        )
        assert await client.get_reserved_balance("wallet-a") == Decimal("9.50")
        assert "default" not in client._reserved_balances
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_pins_default_wallet_before_reserving_and_submitting() -> None:
    client = _build_client()
    client.key_manager.get_default_wallet = Mock(
        side_effect=["wallet-a", "wallet-b"]
    )
    client.clob.post_order = AsyncMock(
        return_value=OrderResponse(
            success=True,
            order_id="order-1",
            status=OrderStatus.LIVE,
        )
    )
    try:
        response = await client.place_order(
            OrderRequest(
                token_id="12345",
                price=Decimal("0.50"),
                size=Decimal("10"),
                side=Side.BUY,
            ),
            wallet_id=None,
        )

        assert response.success is True
        client.key_manager.get_default_wallet.assert_called_once_with()
        assert client.key_manager.get_wallet.call_count == 2
        assert all(
            call.args == ("wallet-a",)
            for call in client.key_manager.get_wallet.call_args_list
        )
        client.get_balances.assert_awaited_once_with(wallet_id="wallet-a")
        assert await client.get_reserved_balance("wallet-a") == Decimal("5.00")
        assert await client.get_reserved_balance("wallet-b") == Decimal("0")
    finally:
        await client.close()
