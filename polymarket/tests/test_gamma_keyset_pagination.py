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


@pytest.mark.asyncio
async def test_keyset_reports_raw_count_and_full_pages_continue_despite_parse_losses():
    gamma = object.__new__(GammaAPI)
    good = {
        "id": "1",
        "question": "q",
        "slug": "s",
        "conditionId": "0xc",
        "category": "c",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]',
        "volumeNum": 1,
        "liquidityNum": 1,
        "active": True,
        "closed": False,
    }
    bad = {"id": None}  # unparseable -> dropped by _parse_market_payload try/except

    async def fake_get(path, *, params, rate_limit_key):
        return {"markets": [good, bad], "next_cursor": "c2"}

    gamma.get = fake_get
    result = await gamma.get_markets_keyset(limit=2)
    assert result["raw_count"] == 2 and len(result["markets"]) == 1

    # get_markets_keyset returns already-parsed markets (parse losses dropped) plus the
    # raw page size. Page 1: raw_count=2 but only 1 parsed market survives (1 parse loss).
    pages = [
        {"markets": [good], "next_cursor": "c2", "raw_count": 2},  # full raw page, 1 parse loss
        {"markets": [good], "next_cursor": None, "raw_count": 1},
    ]

    async def fake_keyset(**kwargs):
        return pages.pop(0)

    gamma.get_markets_keyset = fake_keyset
    all_markets = await gamma.get_all_current_markets(limit=2)
    assert len(all_markets) == 2  # old code would stop after page 1 (len(batch)=1 < page_size=2)


@pytest.mark.asyncio
async def test_total_parse_loss_page_continues_and_raw_empty_page_stops():
    gamma = object.__new__(GammaAPI)
    survivor = Market(
        id="1",
        question="q",
        slug="s",
        condition_id="0xc",
        category="c",
        outcomes=["Yes", "No"],
        outcome_prices=["0.5", "0.5"],
        active=True,
        closed=False,
        archived=False,
        volume=0,
        liquidity=0,
    )
    pages = [
        # Full raw page where EVERY market failed to parse: must keep paginating.
        {"markets": [], "next_cursor": "c2", "raw_count": 2},
        {"markets": [survivor], "next_cursor": "c3", "raw_count": 2},
        # Raw-empty page: the only thing that ends pagination early.
        {"markets": [], "next_cursor": "c4", "raw_count": 0},
    ]
    cursors = []

    async def fake_keyset(**kwargs):
        cursors.append(kwargs.get("after_cursor"))
        return pages.pop(0)

    gamma.get_markets_keyset = fake_keyset
    all_markets = await gamma.get_all_current_markets(limit=2)
    assert cursors == [None, "c2", "c3"]
    assert [m.condition_id for m in all_markets] == ["0xc"]
