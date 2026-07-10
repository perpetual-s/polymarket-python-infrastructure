"""Market price-change fields parsed from Gamma payloads (monitor M0)."""
from decimal import Decimal

import pytest

from polymarket.api.gamma import GammaAPI
from polymarket.models import Market


def _payload(**extra):
    base = {"id": "1", "question": "q", "slug": "s", "conditionId": "0xc",
            "category": "c", "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]',
            "volumeNum": 10, "liquidityNum": 5, "active": True, "closed": False}
    base.update(extra)
    return base


def test_parse_market_payload_maps_price_change_fields():
    gamma = object.__new__(GammaAPI)
    m = gamma._parse_market_payload(_payload(oneHourPriceChange="0.12", oneDayPriceChange=-0.3))
    assert m.one_hour_price_change == Decimal("0.12")
    assert m.one_day_price_change == Decimal("-0.3")


def test_price_change_fields_default_none_and_coerce_garbage_to_none():
    gamma = object.__new__(GammaAPI)
    m = gamma._parse_market_payload(_payload())
    assert m.one_hour_price_change is None and m.one_day_price_change is None
    # Construct Market directly (core fields use the model's snake_case names; the
    # price-change aliases exercise both alias population and garbage->None coercion).
    m2 = Market(
        id="1", question="q", slug="s", condition_id="0xc", category="c",
        outcomes=["Yes", "No"], outcome_prices=["0.5", "0.5"], volume=10, liquidity=5,
        active=True, closed=False,
        oneHourPriceChange="not-a-number", oneDayPriceChange={"x": 1},
    )
    assert m2.one_hour_price_change is None and m2.one_day_price_change is None
