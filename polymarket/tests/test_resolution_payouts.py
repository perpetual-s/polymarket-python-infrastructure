"""Hermetic validation matrix for the typed resolution-payout facade method.

The fixtures below are trimmed real payload shapes captured from
`GET https://clob.polymarket.com/markets/{condition_id}`:
- ordinary binary: condition 0xa9096ff7...401468a ("Games Total: O/U 2.5")
- normal negRisk: condition 0xc6485bb7... ("Will Kamala Harris win the 2024 US
  Presidential Election?" -- part of the 2024 election negRisk group, added to
  the group mid-race)
- fifty_fifty: condition 0xcbfd837f... ("Lima vs. Goncalves: Set 2 Games O/U
  10.5", resolved 50-50 after the set was not completed)
- open/unresolved: condition 0xa467b14d... ("Xi Jinping out before 2027?")

There is no confirmed live example of an augmented negRisk payload: the raw
CLOB per-condition payload carries no `neg_risk_augmented`-style flag in any
sample captured, including the Harris market, which was added to an
already-live negRisk group and is structurally identical to the
original-group Trump market. Gamma's event-level `negRiskAugmented` field was
also never observed `true` across roughly 1200 scanned closed events. The
`neg_risk_augmented` key this client checks defensively is its own
established name for the concept (`models.Market.neg_risk_augmented`,
`ctf/utils.is_safe_to_trade`), not a confirmed CLOB field.
"""

import logging
from decimal import Decimal
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from polymarket.api.clob_public import PublicCLOBAPI
from polymarket.client import PolymarketClient
from polymarket.config import PolymarketSettings
from polymarket.exceptions import UnsupportedResolution
from polymarket.models import ResolutionPayouts

LOGGER_NAME = "polymarket.api.clob_public"
CONDITION_ID = "0xa9096ff7e25f808b537e7f95e4d6b690c88f7dc4a49cf01c05ff13e9b401468a"
TOKEN_OVER = "29077156840639689524290844748497075134859101419488137161855938292332322833996"
TOKEN_UNDER = "72807432407448219978213764871076858305555184752167161746245074527600556620311"


def _public_clob_api() -> PublicCLOBAPI:
    return PublicCLOBAPI(PolymarketSettings())


def _raw_market(**overrides: Any) -> Dict[str, Any]:
    """Baseline raw CLOB market payload, trimmed from a real capture.

    Ordinary resolved binary market ("Games Total: O/U 2.5"): closed,
    non-negRisk, "Under" won. Callers override only the fields their test
    cares about.
    """
    payload: Dict[str, Any] = {
        "active": True,
        "closed": True,
        "archived": False,
        "condition_id": CONDITION_ID,
        "question": "Games Total: O/U 2.5",
        "neg_risk": False,
        "neg_risk_market_id": "",
        "neg_risk_request_id": "",
        "is_50_50_outcome": False,
        "tokens": [
            {
                "token_id": TOKEN_OVER,
                "outcome": "Over",
                "price": 0,
                "winner": False,
            },
            {
                "token_id": TOKEN_UNDER,
                "outcome": "Under",
                "price": 1,
                "winner": True,
            },
        ],
    }
    payload.update(overrides)
    return payload


async def _resolve(raw: Dict[str, Any]) -> Optional[ResolutionPayouts]:
    api = _public_clob_api()
    api.get = AsyncMock(return_value=raw)
    try:
        return await api.get_resolution_payouts(CONDITION_ID)
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_ordinary_binary_winner_pays_one_loser_pays_zero() -> None:
    result = await _resolve(_raw_market())
    assert result == ResolutionPayouts(
        condition_id=CONDITION_ID,
        payouts={TOKEN_OVER: Decimal("0"), TOKEN_UNDER: Decimal("1")},
        kind="winner",
    )


@pytest.mark.asyncio
async def test_open_market_returns_none() -> None:
    raw = _raw_market(
        closed=False,
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0.5, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 0.5, "winner": False},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_closed_with_no_winner_flags_returns_none() -> None:
    """Disputed or pending-oracle: closed but no winner yet declared."""
    raw = _raw_market(
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0.5, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 0.5, "winner": False},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_fifty_fifty_resolution_pays_half_to_every_token() -> None:
    raw = _raw_market(
        is_50_50_outcome=True,
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0.5, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 0.5, "winner": False},
        ],
    )
    result = await _resolve(raw)
    assert result == ResolutionPayouts(
        condition_id=CONDITION_ID,
        payouts={TOKEN_OVER: Decimal("0.5"), TOKEN_UNDER: Decimal("0.5")},
        kind="fifty_fifty",
    )


@pytest.mark.asyncio
async def test_fifty_fifty_with_a_winner_flag_is_contradictory_and_returns_none() -> None:
    raw = _raw_market(is_50_50_outcome=True)  # baseline has Under as winner=True
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_normal_neg_risk_market_resolves_like_ordinary_binary() -> None:
    """Real capture: Harris/2024 election negRisk group, added mid-race."""
    harris_condition = "0xc6485bb7ea46d7bb89beb9c91e7572ecfc72a6273789496f78bc5e989e4d1638"
    yes_token = "69236923620077691027083946871148646972011131466059644796654161903044970987404"
    no_token = "87584955359245246404952128082451897287778571240979823316620093987046202296181"
    raw = {
        "active": True,
        "closed": True,
        "condition_id": harris_condition,
        "question": "Will Kamala Harris win the 2024 US Presidential Election?",
        "neg_risk": True,
        "neg_risk_market_id": "0xe3b1bc389210504ebcb9cffe4b0ed06ccac50561e0f24abb6379984cec030f00",
        "neg_risk_request_id": "0x8b8cfdd89ae4706df00ef877ee2387079b51c14d248d09c7fd5642a578c6709a",
        "is_50_50_outcome": False,
        "tokens": [
            {"token_id": yes_token, "outcome": "Yes", "price": 0, "winner": False},
            {"token_id": no_token, "outcome": "No", "price": 1, "winner": True},
        ],
    }
    api = _public_clob_api()
    api.get = AsyncMock(return_value=raw)
    try:
        result = await api.get_resolution_payouts(harris_condition)
    finally:
        await api.close()
    assert result == ResolutionPayouts(
        condition_id=harris_condition,
        payouts={yes_token: Decimal("0"), no_token: Decimal("1")},
        kind="winner",
    )


@pytest.mark.asyncio
async def test_augmented_neg_risk_raises_unsupported() -> None:
    raw = _raw_market(neg_risk=True, neg_risk_augmented=True)
    api = _public_clob_api()
    api.get = AsyncMock(return_value=raw)
    try:
        with pytest.raises(UnsupportedResolution, match="augmented neg-risk") as exc_info:
            await api.get_resolution_payouts(CONDITION_ID)
    finally:
        await api.close()
    assert exc_info.value.condition_id == CONDITION_ID


@pytest.mark.asyncio
async def test_duplicate_token_ids_return_none() -> None:
    raw = _raw_market(
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0, "winner": False},
            {"token_id": TOKEN_OVER, "outcome": "Under", "price": 1, "winner": True},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_missing_tokens_return_none() -> None:
    raw = _raw_market(tokens=[])
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_multiple_winners_return_none() -> None:
    raw = _raw_market(
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 1, "winner": True},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 1, "winner": True},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_an_open_market_is_debug_noise_and_never_warns(caplog) -> None:
    """The routine not-yet-resolved state must not surface as a warning.

    Callers skip a `None` payout silently, so this method's log level is the
    only signal separating "not resolved yet" from "malformed payload". A
    refactor that promotes this branch to warning drowns the real signal.
    """
    raw = _raw_market(
        closed=False,
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0.5, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 0.5, "winner": False},
        ],
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        assert await _resolve(raw) is None

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert [r for r in records if r.levelno >= logging.WARNING] == []
    assert [r for r in records if r.levelno == logging.DEBUG]


@pytest.mark.asyncio
async def test_a_malformed_payload_warns_with_the_condition_id(caplog) -> None:
    """...and the contradictory state must be greppable, or it is invisible."""
    raw = _raw_market(
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 1, "winner": True},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 1, "winner": True},
        ],
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        assert await _resolve(raw) is None

    warnings = [
        r
        for r in caplog.records
        if r.name == LOGGER_NAME and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert CONDITION_ID in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_condition_id_mismatch_returns_none() -> None:
    raw = _raw_market(condition_id="0x" + "9" * 64)
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_non_boolean_winner_flag_returns_none() -> None:
    raw = _raw_market(
        tokens=[
            {"token_id": TOKEN_OVER, "outcome": "Over", "price": 0, "winner": "false"},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 1, "winner": "true"},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_malformed_top_level_payload_returns_none() -> None:
    api = _public_clob_api()
    api.get = AsyncMock(return_value={"error": "market not found"})
    try:
        assert await api.get_resolution_payouts(CONDITION_ID) is None
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_non_dict_payload_returns_none() -> None:
    """The whole response is the wrong shape (e.g. a list), not just a field."""
    api = _public_clob_api()
    api.get = AsyncMock(return_value=["not", "a", "dict"])
    try:
        assert await api.get_resolution_payouts(CONDITION_ID) is None
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_non_boolean_closed_returns_none() -> None:
    raw = _raw_market(closed="true")
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_non_boolean_is_50_50_outcome_returns_none() -> None:
    raw = _raw_market(is_50_50_outcome="false")
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_missing_token_id_returns_none() -> None:
    raw = _raw_market(
        tokens=[
            {"token_id": None, "outcome": "Over", "price": 0, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 1, "winner": True},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_non_string_token_id_returns_none() -> None:
    raw = _raw_market(
        tokens=[
            {"token_id": 12345, "outcome": "Over", "price": 0, "winner": False},
            {"token_id": TOKEN_UNDER, "outcome": "Under", "price": 1, "winner": True},
        ],
    )
    assert await _resolve(raw) is None


@pytest.mark.asyncio
async def test_empty_condition_id_hits_model_validation_fallback_and_returns_none() -> None:
    """Every parser check can pass while the model's own invariant still rejects it.

    ``condition_id`` must be non-empty per the model, but the parser never
    checks that itself -- this exercises the ``except PydanticValidationError``
    fallback for real, not via mocking.
    """
    raw = _raw_market(condition_id="")
    api = _public_clob_api()
    api.get = AsyncMock(return_value=raw)
    try:
        assert await api.get_resolution_payouts("") is None
    finally:
        await api.close()


def test_resolution_payouts_model_rejects_incomplete_winner_vector() -> None:
    """The model itself enforces the invariant, not just the parser."""
    with pytest.raises(PydanticValidationError):
        ResolutionPayouts(
            condition_id=CONDITION_ID,
            payouts={TOKEN_OVER: Decimal("0"), TOKEN_UNDER: Decimal("0.7")},
            kind="winner",
        )


def test_resolution_payouts_model_rejects_uneven_fifty_fifty() -> None:
    with pytest.raises(PydanticValidationError):
        ResolutionPayouts(
            condition_id=CONDITION_ID,
            payouts={TOKEN_OVER: Decimal("0.5"), TOKEN_UNDER: Decimal("0.4")},
            kind="fifty_fifty",
        )


@pytest.mark.asyncio
async def test_client_delegates_to_public_clob() -> None:
    client = PolymarketClient(enable_rate_limiting=False, enable_metrics=False)
    expected = ResolutionPayouts(
        condition_id=CONDITION_ID,
        payouts={TOKEN_OVER: Decimal("0"), TOKEN_UNDER: Decimal("1")},
        kind="winner",
    )
    client.public_clob.get_resolution_payouts = AsyncMock(return_value=expected)
    try:
        result = await client.get_resolution_payouts(CONDITION_ID)
        assert result == expected
        client.public_clob.get_resolution_payouts.assert_awaited_once_with(CONDITION_ID)
    finally:
        await client.close()
