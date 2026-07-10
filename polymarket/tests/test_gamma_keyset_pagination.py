"""Regression tests for Gamma /markets/keyset pagination."""

import pytest

from polymarket.api.gamma import GammaAPI
from polymarket.models import Market


@pytest.mark.asyncio
async def test_get_markets_keyset_uses_after_cursor_and_parses_markets():
    gamma = object.__new__(GammaAPI)
    calls = []

    async def fake_get(path, *, params, rate_limit_key):
        calls.append((path, dict(params), rate_limit_key))
        return {
            "markets": [
                {
                    "id": "123",
                    "question": "Will the market refresh?",
                    "slug": "market-refresh",
                    "conditionId": "0xabc",
                    "category": "infra",
                    "outcomes": '["YES", "NO"]',
                    "outcomePrices": '["0.42", "0.58"]',
                    "clobTokenIds": '["111", "222"]',
                    "volumeNum": 1000,
                    "liquidityNum": 500,
                    "active": True,
                    "closed": False,
                    "archived": False,
                }
            ],
            "next_cursor": "cursor-next",
        }

    gamma.get = fake_get

    result = await gamma.get_markets_keyset(
        limit=250,
        after_cursor="cursor-start",
        active=True,
        closed=False,
        archived=False,
    )

    assert calls == [
        (
            "/markets/keyset",
            {
                "limit": 100,
                "after_cursor": "cursor-start",
                "active": "true",
                "closed": "false",
                "archived": "false",
            },
            "GET:/markets/keyset",
        )
    ]
    assert "offset" not in calls[0][1]
    assert result["next_cursor"] == "cursor-next"
    assert len(result["markets"]) == 1
    assert result["markets"][0].condition_id == "0xabc"
    assert result["markets"][0].tokens == ["111", "222"]


@pytest.mark.asyncio
async def test_get_all_current_markets_paginates_with_keyset_cursor():
    gamma = object.__new__(GammaAPI)
    pages = [
        {
            "markets": [
                Market(
                    id="1",
                    question="First",
                    slug="first",
                    condition_id="0x1",
                    category="infra",
                    outcomes=["YES", "NO"],
                    outcome_prices=["0.4", "0.6"],
                    active=True,
                    closed=False,
                    archived=False,
                    volume=0,
                    liquidity=0,
                )
            ],
            "next_cursor": "cursor-2",
        },
        {
            "markets": [
                Market(
                    id="2",
                    question="Second",
                    slug="second",
                    condition_id="0x2",
                    category="infra",
                    outcomes=["YES", "NO"],
                    outcome_prices=["0.5", "0.5"],
                    active=True,
                    closed=False,
                    archived=False,
                    volume=0,
                    liquidity=0,
                )
            ],
        },
    ]
    cursors = []

    async def fake_keyset(**kwargs):
        cursors.append(kwargs.get("after_cursor"))
        return pages.pop(0)

    gamma.get_markets_keyset = fake_keyset

    markets = await gamma.get_all_current_markets(limit=1)

    assert cursors == [None, "cursor-2"]
    assert [market.condition_id for market in markets] == ["0x1", "0x2"]
