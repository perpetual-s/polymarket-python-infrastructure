"""Per-surface data-plane breakers: Data API failures must not block Gamma (spec M0/Sol-4)."""
import pytest

from polymarket import PolymarketClient


@pytest.mark.asyncio
async def test_each_data_surface_has_its_own_breaker():
    async with PolymarketClient() as client:
        assert client.gamma.retry_strategy.circuit_breaker is client.gamma_circuit_breaker
        assert client.data.retry_strategy.circuit_breaker is client.data_circuit_breaker
        assert (client.public_clob.retry_strategy.circuit_breaker
                is client.public_clob_circuit_breaker)
        names = {client.gamma_circuit_breaker.name, client.data_circuit_breaker.name,
                 client.public_clob_circuit_breaker.name}
        assert names == {"polymarket-gamma", "polymarket-data", "polymarket-clob-public"}
        assert client.circuit_breaker.name == "polymarket-trading"  # unchanged


@pytest.mark.asyncio
async def test_open_data_breaker_does_not_open_gamma_and_worst_state_reported():
    async with PolymarketClient() as client:
        client.data_circuit_breaker._state = "OPEN"
        assert client.gamma_circuit_breaker.state == "CLOSED"
        assert client.get_data_circuit_breaker_state() == "OPEN"  # worst-of aggregate
        states = client.get_data_circuit_breaker_states()
        assert states == {"polymarket-gamma": "CLOSED", "polymarket-data": "OPEN",
                          "polymarket-clob-public": "CLOSED"}
        client.reset_circuit_breaker()
        assert client.data_circuit_breaker.state == "CLOSED"
