"""CLOB /prices-history wrapper (monitor M0)."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from polymarket.api.clob_public import PublicCLOBAPI
from polymarket.exceptions import APIError
from polymarket.models import PricePoint


def _api():
    api = object.__new__(PublicCLOBAPI)
    return api


@pytest.mark.asyncio
async def test_get_prices_history_params_rate_limit_key_and_parsing():
    api = _api()
    calls = []

    async def fake_get(path, *, params=None, rate_limit_key=None, retry=True):
        calls.append((path, dict(params), rate_limit_key))
        return {
            "history": [
                {"t": 1751000000, "p": 0.12},
                {"t": "bad", "p": None},
                {"t": 1751000600, "p": "0.15"},
            ]
        }

    api.get = fake_get
    points = await api.get_prices_history("123token", interval="1h", fidelity=10)
    assert calls == [
        (
            "/prices-history",
            {"market": "123token", "interval": "1h", "fidelity": 10},
            "GET:/prices-history",
        )
    ]
    assert [(p.timestamp, p.price) for p in points] == [
        (1751000000, Decimal("0.12")),
        (1751000600, Decimal("0.15")),
    ]  # malformed row skipped


@pytest.mark.asyncio
async def test_get_prices_history_time_range_excludes_interval_and_404_is_empty():
    api = _api()
    with pytest.raises(ValueError):
        await api.get_prices_history("t", interval="1d", start_ts=1)
    api.get = AsyncMock(side_effect=APIError("nope", status_code=404))
    assert await api.get_prices_history("t", start_ts=1, end_ts=2) == []
    sent = api.get.await_args.kwargs["params"]
    assert sent == {"market": "t", "startTs": 1, "endTs": 2}


@pytest.mark.asyncio
async def test_get_prices_history_null_history_returns_empty():
    api = _api()

    async def fake_get(path, *, params=None, rate_limit_key=None, retry=True):
        return {"history": None}

    api.get = fake_get
    assert await api.get_prices_history("t", interval="1h") == []


@pytest.mark.asyncio
async def test_prices_history_live_contract():
    """Guarded live-contract test (spec §8.1). Opt-in: RUN_LIVE_CONTRACT_TESTS=1."""
    import os

    if not os.getenv("RUN_LIVE_CONTRACT_TESTS"):
        pytest.skip("live-contract tests require RUN_LIVE_CONTRACT_TESTS=1")
    from polymarket import PolymarketClient

    async with PolymarketClient() as client:
        markets = await client.get_markets(active=True, limit=1)
        token = markets[0].tokens[0]
        points = await client.get_prices_history(token, interval="1h", fidelity=10)
        assert points and all(p.timestamp > 0 and p.price >= 0 for p in points)
