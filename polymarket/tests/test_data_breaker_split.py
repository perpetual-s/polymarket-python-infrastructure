"""Per-surface data-plane breakers: Data API failures must not block Gamma (spec M0/Sol-4)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from polymarket import PolymarketClient
from polymarket.exceptions import APIError, PriceUnavailableError
from polymarket.models import Side


@pytest.mark.asyncio
async def test_each_data_surface_has_its_own_breaker():
    async with PolymarketClient(enable_metrics=False) as client:
        assert client.gamma.retry_strategy.circuit_breaker is client.gamma_circuit_breaker
        assert client.data.retry_strategy.circuit_breaker is client.data_circuit_breaker
        assert (
            client.public_clob.retry_strategy.circuit_breaker is client.public_clob_circuit_breaker
        )
        assert (
            client.market_clob.retry_strategy.circuit_breaker
            is client.public_clob_circuit_breaker
        )
        assert (
            client.market_clob.retry_strategy.circuit_breaker
            is not client.circuit_breaker
        )
        names = {
            client.gamma_circuit_breaker.name,
            client.data_circuit_breaker.name,
            client.public_clob_circuit_breaker.name,
        }
        assert names == {"polymarket-gamma", "polymarket-data", "polymarket-clob-public"}
        assert client.circuit_breaker.name == "polymarket-trading"  # unchanged


@pytest.mark.asyncio
async def test_open_data_breaker_does_not_open_gamma_and_worst_state_reported():
    async with PolymarketClient(enable_metrics=False) as client:
        client.data_circuit_breaker._state = "OPEN"
        assert client.gamma_circuit_breaker.state == "CLOSED"
        assert client.get_data_circuit_breaker_state() == "OPEN"  # worst-of aggregate
        states = client.get_data_circuit_breaker_states()
        assert states == {
            "polymarket-gamma": "CLOSED",
            "polymarket-data": "OPEN",
            "polymarket-clob-public": "CLOSED",
        }
        client.reset_circuit_breaker()
        assert client.data_circuit_breaker.state == "CLOSED"


@pytest.mark.asyncio
async def test_market_read_failures_do_not_open_or_block_trading_breaker():
    async with PolymarketClient(
        enable_rate_limiting=False,
        enable_circuit_breaker=True,
        enable_metrics=False,
    ) as client:
        client.public_clob_circuit_breaker.failure_threshold = 2
        client.market_clob.retry_strategy.max_retries = 0
        client.market_clob._make_request = AsyncMock(
            side_effect=APIError("public CLOB unavailable")
        )

        for _ in range(2):
            with pytest.raises(PriceUnavailableError):
                await client.get_midpoint("token-1")

        assert client.public_clob_circuit_breaker.state == "OPEN"
        assert client.circuit_breaker.state == "CLOSED"

        client.key_manager.get_wallet = lambda _wallet_id: SimpleNamespace(
            address="0x" + "1" * 40,
            api_key="key",
            api_secret="secret",
            api_passphrase="passphrase",
        )
        client.clob.cancel_order = AsyncMock(return_value=True)
        assert await client.cancel_order("order-1", wallet_id="wallet-1") is True
        client.clob.cancel_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_facade_reads_route_through_market_clob():
    async with PolymarketClient(
        enable_rate_limiting=False,
        enable_circuit_breaker=False,
        enable_metrics=False,
    ) as client:
        expected = object()
        client.market_clob.get_orderbook = AsyncMock(return_value=expected)
        client.market_clob.get_midpoint = AsyncMock(return_value=expected)
        client.market_clob.get_price = AsyncMock(return_value=expected)
        client.market_clob.get_last_trade_price = AsyncMock(return_value=expected)
        client.market_clob.get_last_trades_prices = AsyncMock(return_value=expected)
        client.market_clob.get_orderbooks_batch = AsyncMock(return_value=expected)

        assert await client.get_orderbook("token-1") is expected
        assert await client.get_midpoint("token-1") is expected
        assert await client.get_price("token-1", Side.BUY) is expected
        assert await client.get_last_trade_price("token-1") is expected
        assert await client.get_last_trades_prices(["token-1"]) is expected
        assert await client.get_orderbooks_batch(["token-1"]) is expected

        client.clob.get_orderbook = AsyncMock()
        client.clob.get_orderbook.assert_not_awaited()
