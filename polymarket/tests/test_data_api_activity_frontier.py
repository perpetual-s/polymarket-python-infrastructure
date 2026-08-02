"""Activity intake must reach a durable frontier or say it could not.

Copy trading reacts to source trades, so a truncated activity page is a missed
trade, not a small one. `get_activity_since` pages back until the caller's
durable frontier is covered and raises rather than returning a partial history.
"""

from unittest.mock import AsyncMock

import pytest

from polymarket.api.data_api import DataAPI
from polymarket.exceptions import APIError
from polymarket.models import ActivityType


USER = "0x" + "ab" * 20
BASE_TS = 1_777_000_000


def _activity_row(ts: int, *, index: int = 0, user: str = USER) -> dict:
    return {
        "timestamp": ts,
        "type": "TRADE",
        "transactionHash": "0x" + f"{index:064x}",
        "size": "10",
        "usdcSize": "5",
        "proxyWallet": user,
        "conditionId": f"condition-{index}",
        "asset": f"asset-{index}",
        "outcome": "Yes",
        "side": "BUY",
        "price": "0.5",
    }


def _api_with_get(side_effect) -> DataAPI:
    api = object.__new__(DataAPI)
    api.get = AsyncMock(side_effect=side_effect)
    return api


@pytest.mark.asyncio
async def test_activity_pages_back_until_the_frontier_is_covered():
    # A full page whose oldest row is still at the frontier proves nothing
    # about what lies behind it, so the next page must be read.
    first_page = [_activity_row(BASE_TS + 1000 - i, index=i) for i in range(500)]
    second_page = [_activity_row(BASE_TS + 250 - i, index=500 + i) for i in range(500)]

    # The third response is the post-paging stability re-check of offset 0.
    api = _api_with_get([first_page, second_page, list(first_page)])

    activities = await api.get_activity_since(
        user=USER,
        since_ts=BASE_TS,
        activity_type=ActivityType.TRADE,
    )

    assert [call.kwargs["params"]["offset"] for call in api.get.await_args_list] == [
        0,
        500,
        0,
    ]
    assert all(a.timestamp >= BASE_TS for a in activities)
    assert len(activities) == 751


@pytest.mark.asyncio
async def test_short_page_ends_the_history_without_error():
    api = _api_with_get([[_activity_row(BASE_TS + 10, index=1)]])

    activities = await api.get_activity_since(user=USER, since_ts=BASE_TS)

    assert len(activities) == 1
    assert api.get.await_count == 1


@pytest.mark.asyncio
async def test_offset_ceiling_on_a_full_page_refuses_partial_history():
    full_page = [_activity_row(BASE_TS + 5000, index=i) for i in range(500)]
    api = _api_with_get([list(full_page) for _ in range(30)])

    with pytest.raises(APIError, match="offset ceiling"):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)

    offsets = [call.kwargs["params"]["offset"] for call in api.get.await_args_list]
    assert offsets[-1] == 5_000
    assert max(offsets) == 5_000


@pytest.mark.asyncio
async def test_malformed_activity_row_refuses_the_observation():
    bad_row = _activity_row(BASE_TS + 10, index=1)
    bad_row["size"] = "not-a-number"
    api = _api_with_get([[_activity_row(BASE_TS + 11, index=0), bad_row]])

    with pytest.raises(APIError):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)


@pytest.mark.asyncio
async def test_non_list_activity_response_refuses_the_observation():
    api = _api_with_get([{"error": "upstream"}])

    with pytest.raises(APIError):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)


@pytest.mark.asyncio
async def test_activity_for_another_wallet_refuses_the_observation():
    api = _api_with_get([[_activity_row(BASE_TS + 10, index=1, user="0x" + "cd" * 20)]])

    with pytest.raises(APIError, match="wallet mismatch"):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)


@pytest.mark.asyncio
async def test_a_page_shift_during_pagination_refuses_the_observation():
    """New trades arrive at offset 0, so paged rows slide to higher offsets.

    Re-reading a row is not harmless here: the intake digest numbers rows that
    are identical in every field by their ordinal, so a re-read row would be
    stored as a second, fabricated source trade.
    """
    first_page = [_activity_row(BASE_TS + 1000 - i, index=i) for i in range(500)]
    second_page = [_activity_row(BASE_TS + 250 - i, index=500 + i) for i in range(500)]
    shifted_first_page = [_activity_row(BASE_TS + 2000, index=9999)] + first_page[:499]

    api = _api_with_get([first_page, second_page, shifted_first_page])

    with pytest.raises(APIError, match="shifted"):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)


@pytest.mark.asyncio
async def test_a_stable_multi_page_read_is_accepted():
    first_page = [_activity_row(BASE_TS + 1000 - i, index=i) for i in range(500)]
    second_page = [_activity_row(BASE_TS + 250 - i, index=500 + i) for i in range(500)]

    api = _api_with_get([first_page, second_page, list(first_page)])

    activities = await api.get_activity_since(user=USER, since_ts=BASE_TS)

    assert len(activities) == 751
    assert api.get.await_count == 3


@pytest.mark.asyncio
async def test_a_single_page_read_pays_no_stability_check():
    api = _api_with_get([[_activity_row(BASE_TS + 10, index=1)]])

    await api.get_activity_since(user=USER, since_ts=BASE_TS)

    assert api.get.await_count == 1


@pytest.mark.asyncio
async def test_a_shift_below_the_head_row_is_also_refused():
    """The head is not the collection.

    A row inserted below row 0 leaves the head identical while sliding every
    later row one offset down, so the next page re-serves the previous page's
    last row. The intake digest numbers an otherwise-identical repeat by its
    ordinal, so that re-read row becomes a second, fabricated source trade.
    """
    first_page = [_activity_row(BASE_TS + 1000 - i, index=i) for i in range(500)]
    second_page = [_activity_row(BASE_TS + 250 - i, index=500 + i) for i in range(500)]
    # Head unchanged; a new row lands directly beneath it.
    shifted = (
        [first_page[0]]
        + [_activity_row(BASE_TS + 999, index=8888)]
        + first_page[1:498]
    )

    api = _api_with_get([first_page, second_page, shifted])

    with pytest.raises(APIError, match="shifted"):
        await api.get_activity_since(user=USER, since_ts=BASE_TS)
