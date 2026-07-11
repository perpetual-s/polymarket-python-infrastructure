"""Keyless market-trades / address-activity facades (monitor M0)."""

from unittest.mock import AsyncMock

import pytest

from polymarket import PolymarketClient


@pytest.mark.asyncio
async def test_get_market_trades_delegates_keyless():
    async with PolymarketClient() as client:  # no wallets configured
        client.data.get_trades = AsyncMock(return_value=[])
        await client.get_market_trades("0xcond", filter_type="CASH", filter_amount=500.0)
        kwargs = client.data.get_trades.await_args.kwargs
        assert kwargs["user"] is None and kwargs["market"] == "0xcond"
        assert kwargs["filter_type"] == "CASH" and kwargs["filter_amount"] == 500.0


@pytest.mark.asyncio
async def test_get_address_activity_uses_raw_address_not_key_manager():
    async with PolymarketClient() as client:
        client.data.get_activity = AsyncMock(return_value=[])
        addr = "0x" + "a" * 40
        await client.get_address_activity(addr, limit=50)
        kwargs = client.data.get_activity.await_args.kwargs
        assert kwargs["user"] == addr and kwargs["limit"] == 50


@pytest.mark.asyncio
async def test_get_address_activity_rejects_non_address():
    async with PolymarketClient() as client:
        with pytest.raises(ValueError):
            await client.get_address_activity("not-an-address")


@pytest.mark.asyncio
async def test_get_market_trades_window_flags_truncation():
    from unittest.mock import AsyncMock

    from polymarket.models import Trade

    def _trade(ts):
        return Trade(
            id=str(ts),
            market="m",
            conditionId="0xc",
            asset="a",
            side="BUY",
            size="1",
            price="0.5",
            feeRateBps=0,
            timestamp=ts,
        )

    async with PolymarketClient() as client:
        # Page 1: all rows still inside the window and page is full -> keep paging;
        # with max_pages=1 the budget is exhausted -> complete=False.
        client.get_market_trades = AsyncMock(return_value=[_trade(2000)] * 500)
        out = await client.get_market_trades_window("0xc", start_ts=1000, max_pages=1)
        assert out["complete"] is False and len(out["trades"]) == 500

        # A page containing rows older than start_ts proves the boundary was reached.
        client.get_market_trades = AsyncMock(return_value=[_trade(2000), _trade(500)])
        out = await client.get_market_trades_window("0xc", start_ts=1000)
        assert out["complete"] is True and [t.timestamp for t in out["trades"]] == [2000]
