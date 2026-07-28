"""Complete-position pagination contracts for the public Data API."""

from unittest.mock import AsyncMock

import pytest

from polymarket.api.data_api import DataAPI
from polymarket.exceptions import APIError


USER = "0x" + "ab" * 20


def _position_row(index: int, *, user: str = USER) -> dict:
    return {
        "proxyWallet": user,
        "asset": f"asset-{index}",
        "conditionId": f"condition-{index}",
        "size": "10",
        "avgPrice": "0.50",
        "currentValue": "5",
        "initialValue": "5",
        "curPrice": "0.50",
        "cashPnl": "0",
        "percentPnl": "0",
        "title": f"Market {index}",
        "slug": f"market-{index}",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "oppositeOutcome": "No",
    }


def _api_with_get(side_effect) -> DataAPI:
    api = object.__new__(DataAPI)
    api.get = AsyncMock(side_effect=side_effect)
    return api


@pytest.mark.asyncio
async def test_complete_positions_paginates_past_500_without_loss_or_duplicates():
    api = _api_with_get(
        [
            [_position_row(index) for index in range(500)],
            [_position_row(500)],
            [_position_row(index) for index in range(500)],
            [_position_row(500)],
        ]
    )

    positions = await api.get_positions_complete(user=USER)

    assert len(positions) == 501
    assert len({(position.condition_id, position.asset) for position in positions}) == 501
    assert [call.kwargs["params"]["offset"] for call in api.get.await_args_list] == [
        0,
        500,
        0,
        500,
    ]
    assert all(call.kwargs["params"]["limit"] == 500 for call in api.get.await_args_list)


@pytest.mark.asyncio
async def test_complete_positions_probes_after_an_exact_page_boundary():
    api = _api_with_get(
        [
            [_position_row(index) for index in range(500)],
            [],
            [_position_row(index) for index in range(500)],
            [],
        ]
    )

    positions = await api.get_positions_complete(user=USER)

    assert len(positions) == 500
    assert [call.kwargs["params"]["offset"] for call in api.get.await_args_list] == [
        0,
        500,
        0,
        500,
    ]


@pytest.mark.asyncio
async def test_complete_positions_rejects_identity_shift_between_complete_passes():
    api = _api_with_get(
        [
            [_position_row(index) for index in range(500)],
            [],
            [_position_row(index) for index in range(500)],
            [_position_row(500)],
        ]
    )

    with pytest.raises(APIError, match="changed between complete passes"):
        await api.get_positions_complete(user=USER)


@pytest.mark.asyncio
async def test_complete_positions_ignores_raw_order_when_identity_and_size_are_stable():
    first = [_position_row(1), _position_row(2)]
    second = list(reversed(first))
    api = _api_with_get([first, second])

    positions = await api.get_positions_complete(user=USER)

    assert {(position.condition_id, position.asset) for position in positions} == {
        ("condition-1", "asset-1"),
        ("condition-2", "asset-2"),
    }


@pytest.mark.asyncio
async def test_complete_positions_rejects_parse_loss():
    malformed = _position_row(1)
    malformed["size"] = "not-a-number"
    api = _api_with_get([[malformed]])

    with pytest.raises(APIError, match="parse"):
        await api.get_positions_complete(user=USER)


@pytest.mark.asyncio
async def test_complete_positions_rejects_non_list_page():
    api = _api_with_get([{"positions": []}])

    with pytest.raises(APIError, match="response"):
        await api.get_positions_complete(user=USER)


@pytest.mark.asyncio
async def test_complete_positions_rejects_wallet_mismatch():
    api = _api_with_get(
        [[_position_row(1, user="0x" + "cd" * 20)]]
    )

    with pytest.raises(APIError, match="wallet"):
        await api.get_positions_complete(user=USER)


@pytest.mark.asyncio
async def test_complete_positions_rejects_a_full_page_at_the_offset_ceiling():
    async def full_page(_path, *, params, **_kwargs):
        offset = params["offset"]
        return [_position_row(offset + index) for index in range(500)]

    api = _api_with_get(full_page)

    with pytest.raises(APIError, match="offset ceiling"):
        await api.get_positions_complete(user=USER)

    assert api.get.await_count == 21
    assert api.get.await_args_list[-1].kwargs["params"]["offset"] == 10_000
