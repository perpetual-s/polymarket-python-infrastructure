"""Restart and validation coverage for the local reservation projection."""

import asyncio
from decimal import Decimal

import pytest

from polymarket.client import PolymarketClient
from polymarket.exceptions import BalanceTrackingError


def _client() -> PolymarketClient:
    client = object.__new__(PolymarketClient)
    client._reserved_balances = {}
    client._balance_lock = asyncio.Lock()
    return client


@pytest.mark.asyncio
async def test_restore_is_idempotent_but_cannot_replace_active_projection() -> None:
    client = _client()

    await client.restore_reserved_balance(Decimal("50"), wallet_id="wallet-a")
    await client.restore_reserved_balance(Decimal("50"), wallet_id="wallet-a")

    assert await client.get_reserved_balance("wallet-a") == Decimal("50")
    with pytest.raises(BalanceTrackingError, match="Cannot replace active"):
        await client.restore_reserved_balance(Decimal("25"), wallet_id="wallet-a")


@pytest.mark.asyncio
async def test_release_can_be_compensated_after_durable_rollback() -> None:
    client = _client()
    await client.restore_reserved_balance(Decimal("50"), wallet_id="wallet-a")

    await client.release_reserved_balance(
        Decimal("20"), wallet_id="wallet-a", order_id="order-1"
    )
    await client.reapply_reserved_balance(
        Decimal("20"), wallet_id="wallet-a", order_id="order-1"
    )

    assert await client.get_reserved_balance("wallet-a") == Decimal("50")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
async def test_reservation_mutations_reject_invalid_amounts(amount: Decimal) -> None:
    client = _client()

    with pytest.raises(BalanceTrackingError):
        await client.restore_reserved_balance(amount, wallet_id="wallet-a")
    with pytest.raises(BalanceTrackingError):
        await client.reapply_reserved_balance(amount, wallet_id="wallet-a")
    with pytest.raises(BalanceTrackingError):
        await client.release_reserved_balance(amount, wallet_id="wallet-a")
