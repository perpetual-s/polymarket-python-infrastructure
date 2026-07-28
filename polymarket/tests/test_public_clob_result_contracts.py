"""Hermetic contracts for truthful public CLOB result surfaces."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from polymarket.api.clob_public import PublicCLOBAPI
from polymarket.exceptions import APIError, AuthenticationError, RateLimitError
from polymarket.models import (
    MarketTradeEventV1,
    MarketTradeEventsResultV1,
    PriceHistoryCoverageV1,
    PriceHistoryPointV1,
    PriceHistoryResultV1,
    PricePoint,
    PublicDataStatus,
    PublicRequestEvidenceV1,
)
from polymarket.utils.retry import RetryStrategy


def _api() -> PublicCLOBAPI:
    """Build a transport-free API shell whose GET method each test replaces."""
    return object.__new__(PublicCLOBAPI)


def _event(**overrides):
    row = {
        "user": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        "conditionId": "0xcondition",
        "asset": "123456789",
        "side": "BUY",
        "size": "12.34567890123456789",
        "price": "0.4567890123456789",
        "timestamp": 1_751_000_000,
        "transactionHash": "0xtransaction",
    }
    row.update(overrides)
    return row


def _official_nested_event(**overrides):
    row = {
        "event_type": "trade",
        "market": {
            "condition_id": "0xcondition",
            "asset_id": "123456789",
            "question": "Will the example resolve yes?",
            "icon": "https://example.invalid/icon.png",
            "slug": "example-market",
        },
        "user": {
            "address": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
            "username": "not-retained",
            "profile_picture": "https://example.invalid/profile.png",
            "optimized_profile_picture": "https://example.invalid/profile-small.png",
            "pseudonym": "not-retained",
        },
        "side": "BUY",
        "size": "12.34567890123456789",
        "fee_rate_bps": "0",
        "price": "0.4567890123456789",
        "outcome": "Yes",
        "outcome_index": 0,
        "transaction_hash": "0xtransaction",
        "timestamp": "1751000000",
    }
    row.update(overrides)
    return row


def _request_evidence(rate_limit_key: str, **overrides):
    values = {
        "wall_time_ms": "1.25",
        "attempt_count": 1,
        "retry_count": 0,
        "rate_limit_error_count": 0,
        "rate_limit_key": rate_limit_key,
        "retry_enabled": True,
        "max_retries": 3,
        "local_limiter_enabled": True,
        "local_limiter_applied": True,
    }
    values.update(overrides)
    return PublicRequestEvidenceV1.model_validate(values)


@pytest.mark.asyncio
async def test_market_trade_events_result_v1_preserves_successful_empty() -> None:
    api = _api()
    api.get = AsyncMock(return_value=[])

    result = await api.get_market_trades_events_result_v1("0xcondition")

    api.get.assert_awaited_once_with(
        "/live-activity/events/0xcondition",
        params=None,
        rate_limit_key="CLOB:default",
        retry=False,
    )
    assert result.contract_version == "v1"
    assert result.source == "clob.market_trades_events"
    assert result.condition_id == "0xcondition"
    assert result.status == PublicDataStatus.SUCCESS
    assert result.error is None
    assert result.error_category is None
    assert result.http_status is None
    assert result.events == []
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is True
    assert result.source_complete is None
    assert result.decoded_json_bytes == 2
    assert result.max_event_count == 1_000
    assert result.max_decoded_json_bytes == 1_048_576
    assert result.request.rate_limit_key == "CLOB:default"
    assert result.request.attempt_count == 1


@pytest.mark.asyncio
async def test_market_trade_events_project_official_nested_wire_shape() -> None:
    api = _api()
    api.get = AsyncMock(return_value=[_official_nested_event()])

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status is PublicDataStatus.SUCCESS
    assert result.error_category is None
    assert result.parse_complete is True and result.source_complete is None
    assert result.raw_count == result.parsed_count == 1
    event = result.events[0]
    assert event.user == "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
    assert event.condition_id == "0xcondition"
    assert event.asset_id == "123456789"
    assert event.price == Decimal("0.4567890123456789")
    assert event.size == Decimal("12.34567890123456789")
    assert event.timestamp == 1_751_000_000
    assert "market" not in (event.model_extra or {})
    assert "username" not in (event.model_extra or {})


@pytest.mark.parametrize(
    "case",
    ["condition_conflict", "asset_conflict", "nested_condition_missing"],
)
def test_market_trade_event_rejects_conflicting_or_missing_nested_identity(
    case: str,
) -> None:
    row = _official_nested_event()
    if case == "condition_conflict":
        row["condition_id"] = "0xcondition"
        row["market"]["condition_id"] = "0xother"
    elif case == "asset_conflict":
        row["asset_id"] = "flat-asset"
        row["market"]["asset_id"] = "nested-asset"
    else:
        row["condition_id"] = "0xcondition"
        row["market"].pop("condition_id")

    with pytest.raises(PydanticValidationError, match="nested market|conflicts"):
        MarketTradeEventV1.model_validate(row)


@pytest.mark.asyncio
async def test_market_trade_events_reject_malformed_nested_identity() -> None:
    api = _api()
    wrong_condition = _official_nested_event()
    wrong_condition["market"] = {
        **wrong_condition["market"],
        "condition_id": "0xother",
    }
    missing_address = _official_nested_event(user={"username": "missing-address"})
    api.get = AsyncMock(
        return_value=[_official_nested_event(), wrong_condition, missing_address]
    )

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status is PublicDataStatus.SUCCESS
    assert result.raw_count == 3 and result.parsed_count == 1
    assert result.parse_loss_count == 2 and result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_market_trade_events_result_v1_counts_and_drops_non_dict_rows() -> None:
    api = _api()
    first = _event(transactionHash="0x1", price="0.42")
    second = _event(
        transaction_hash="0x2",
        transactionHash=None,
        price="0.43",
        condition_id="0xcondition",
        conditionId=None,
        asset_id="123456790",
        asset=None,
    )
    second = {key: value for key, value in second.items() if value is not None}
    api.get = AsyncMock(return_value=[first, "malformed", second, None])

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status == PublicDataStatus.SUCCESS
    assert all(isinstance(event, MarketTradeEventV1) for event in result.events)
    assert [event.transaction_hash for event in result.events] == ["0x1", "0x2"]
    assert result.events[0].price == Decimal("0.42")
    assert result.events[0].size == Decimal("12.34567890123456789")
    assert result.events[1].asset_id == "123456790"
    assert result.raw_count == 4
    assert result.parsed_count == 2
    assert result.parse_loss_count == 2
    assert result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_market_trade_events_require_all_documented_semantic_fields() -> None:
    missing_user = _event()
    missing_user.pop("user")
    wrong_condition = _event(conditionId="0xother")
    impossible_price = _event(price="1.01")
    api = _api()
    api.get = AsyncMock(
        return_value=[_event(), missing_user, wrong_condition, impossible_price]
    )

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status is PublicDataStatus.SUCCESS
    assert result.raw_count == 4 and result.parsed_count == 1
    assert result.parse_loss_count == 3 and result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        [_event()] * 1_001,
        [_event(extraPayload="x" * 1_048_576)],
    ],
)
async def test_market_trade_events_fail_closed_over_fixed_acceptance_bounds(
    response,
) -> None:
    api = _api()
    api.get = AsyncMock(return_value=response)

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status is PublicDataStatus.ERROR
    assert result.error_category == "bounds"
    assert "exceeded facade acceptance bounds" in result.error
    assert result.events == [] and result.source_complete is False
    assert result.decoded_json_bytes is not None


@pytest.mark.asyncio
async def test_market_trade_events_expose_exact_retry_and_limiter_evidence() -> None:
    api = _api()
    api.get = AsyncMock(
        side_effect=[
            RateLimitError("limited", endpoint="CLOB:default"),
            [_event()],
        ]
    )
    api.retry_strategy = RetryStrategy(
        max_retries=2,
        base_delay=0,
        max_delay=0,
        jitter=False,
    )
    api.rate_limiter = SimpleNamespace(enabled=True)

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert api.get.await_count == 2
    assert result.request.rate_limit_key == "CLOB:default"
    assert result.request.attempt_count == 2
    assert result.request.retry_count == 1
    assert result.request.rate_limit_error_count == 1
    assert result.request.local_limiter_enabled is True
    assert result.request.local_limiter_applied is True


@pytest.mark.asyncio
async def test_market_trade_event_parse_warning_does_not_echo_sensitive_row(caplog) -> None:
    wallet = "0x1111111111111111111111111111111111111111"
    transaction_hash = "0xsecret-transaction"
    api = _api()
    api.get = AsyncMock(
        return_value=[
            _event(user=wallet, transactionHash=transaction_hash, size="invalid")
        ]
    )

    await api.get_market_trades_events_result_v1("0xcondition")

    assert wallet not in caplog.text
    assert transaction_hash not in caplog.text
    assert "row_index=0" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_http_status", "expected_category"),
    [
        (
            APIError("condition missing", status_code=404),
            PublicDataStatus.NOT_FOUND,
            404,
            "request",
        ),
        (
            APIError("upstream failed", status_code=503),
            PublicDataStatus.ERROR,
            503,
            "request",
        ),
        (
            AuthenticationError("forbidden"),
            PublicDataStatus.ERROR,
            None,
            "auth",
        ),
        (RuntimeError("transport failed"), PublicDataStatus.ERROR, None, "request"),
    ],
)
async def test_market_trade_events_result_v1_preserves_failures(
    error: Exception,
    expected_status: PublicDataStatus,
    expected_http_status: Optional[int],
    expected_category: str,
) -> None:
    api = _api()
    api.get = AsyncMock(side_effect=error)

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status == expected_status
    assert result.http_status == expected_http_status
    assert result.error_category == expected_category
    assert str(error) in result.error
    assert result.events == []
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_market_trade_events_result_v1_rejects_non_list_envelope() -> None:
    api = _api()
    api.get = AsyncMock(return_value={"events": []})

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status == PublicDataStatus.ERROR
    assert result.error_category == "non_list"
    assert "expected a list" in result.error
    assert result.http_status is None
    assert result.events == []
    assert result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_market_trade_events_result_v1_classifies_local_serialization_failure() -> None:
    api = _api()
    api.get = AsyncMock(return_value=[{"unsupported": {"set-value"}}])

    result = await api.get_market_trades_events_result_v1("0xcondition")

    assert result.status == PublicDataStatus.ERROR
    assert result.error_category == "serialization"
    assert result.http_status is None
    assert result.decoded_json_bytes is None
    assert result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_market_trade_events_result_v1_rejects_empty_condition_before_transport() -> None:
    api = _api()
    api.get = AsyncMock()

    with pytest.raises(ValueError, match="condition_id is required"):
        await api.get_market_trades_events_result_v1("")

    api.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_market_trade_events_contract_remains_available_and_rate_limited() -> None:
    api = _api()
    raw_events = [{"legacy": "shape"}]
    api.get = AsyncMock(return_value=raw_events)

    events = await api.get_market_trades_events("0xcondition")

    assert events == raw_events
    api.get.assert_awaited_once_with(
        "/live-activity/events/0xcondition",
        rate_limit_key="CLOB:default",
    )


@pytest.mark.asyncio
async def test_prices_history_result_v1_preserves_query_params_and_parse_loss() -> None:
    api = _api()
    api.get = AsyncMock(
        return_value={
            "history": [
                {"t": 1_751_000_000, "p": "0.12"},
                {"t": "bad", "p": None},
                {"t": 1_751_000_600, "p": 0.15},
            ]
        }
    )

    result = await api.get_prices_history_result_v1(
        "123token",
        start_ts=1_751_000_000,
        end_ts=1_751_001_000,
        fidelity=10,
    )

    api.get.assert_awaited_once_with(
        "/prices-history",
        params={
            "market": "123token",
            "startTs": 1_751_000_000,
            "endTs": 1_751_001_000,
            "fidelity": 10,
        },
        rate_limit_key="GET:/prices-history",
        retry=False,
    )
    assert result.contract_version == "v1"
    assert result.source == "clob.prices_history"
    assert result.query.token_id == "123token"
    assert result.query.interval is None
    assert result.query.start_ts == 1_751_000_000
    assert result.query.end_ts == 1_751_001_000
    assert result.query.fidelity == 10
    assert result.status == PublicDataStatus.SUCCESS
    assert [(point.timestamp, point.price) for point in result.points] == [
        (1_751_000_000, Decimal("0.12")),
        (1_751_000_600, Decimal("0.15")),
    ]
    assert result.raw_count == 3
    assert result.parsed_count == 2
    assert result.parse_loss_count == 1
    assert result.parse_complete is False
    assert result.range_complete is False
    assert result.coverage.explicit_range is True
    assert result.coverage.fidelity_seconds == 600
    assert result.coverage.full_bucket_coverage is False
    assert result.request.rate_limit_key == "GET:/prices-history"
    assert result.request.attempt_count == 1


@pytest.mark.asyncio
async def test_prices_history_result_v1_counts_invalid_decimal_as_parse_loss() -> None:
    api = _api()
    api.get = AsyncMock(
        return_value={
            "history": [
                {"t": 1_751_000_000, "p": "0.12"},
                {"t": 1_751_000_600, "p": "not-a-decimal"},
            ]
        }
    )

    result = await api.get_prices_history_result_v1("123token", interval="1h")

    assert result.status == PublicDataStatus.SUCCESS
    assert result.raw_count == 2 and result.parsed_count == 1
    assert result.parse_loss_count == 1 and result.parse_complete is False
    assert result.range_complete is False


@pytest.mark.asyncio
async def test_prices_history_result_v1_preserves_successful_empty_as_unknown() -> None:
    api = _api()
    api.get = AsyncMock(return_value={"history": []})

    result = await api.get_prices_history_result_v1(
        "123token", interval="1h", fidelity=5
    )

    api.get.assert_awaited_once_with(
        "/prices-history",
        params={"market": "123token", "interval": "1h", "fidelity": 5},
        rate_limit_key="GET:/prices-history",
        retry=False,
    )
    assert result.status == PublicDataStatus.SUCCESS
    assert result.error is None
    assert result.points == []
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is True
    assert result.range_complete is None
    assert result.coverage.explicit_range is False
    assert result.coverage.full_bucket_coverage is None


@pytest.mark.asyncio
async def test_prices_history_result_v1_clean_points_keep_range_unknown() -> None:
    api = _api()
    api.get = AsyncMock(return_value={"history": [{"t": 1_751_000_000, "p": "0.12"}]})

    result = await api.get_prices_history_result_v1("123token", interval="1h")

    assert result.status == PublicDataStatus.SUCCESS
    assert [(point.timestamp, point.price) for point in result.points] == [
        (1_751_000_000, Decimal("0.12"))
    ]
    assert result.raw_count == result.parsed_count == 1
    assert result.parse_loss_count == 0
    assert result.parse_complete is True
    assert result.range_complete is None


@pytest.mark.asyncio
async def test_prices_history_result_v1_proves_a_strict_full_fidelity_grid() -> None:
    api = _api()
    api.get = AsyncMock(
        return_value={
            "history": [
                {"t": 100, "p": "0.40"},
                {"t": 160, "p": "0.41"},
                {"t": 220, "p": "0.42"},
            ]
        }
    )

    result = await api.get_prices_history_result_v1(
        "123token", start_ts=100, end_ts=220, fidelity=1
    )

    assert result.range_complete is True
    assert result.coverage == PriceHistoryCoverageV1(
        explicit_range=True,
        fidelity_seconds=60,
        observed_start_ts=100,
        observed_end_ts=220,
        timestamps_ordered=True,
        duplicate_timestamp_count=0,
        out_of_range_count=0,
        maximum_gap_seconds=60,
        start_boundary_covered=True,
        end_boundary_covered=True,
        full_bucket_coverage=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timestamps", "failed_field", "expected_value"),
    [
        ([100, 220], "maximum_gap_seconds", 120),
        ([161, 220], "start_boundary_covered", False),
        ([100, 220, 160], "timestamps_ordered", False),
        ([100, 160, 160, 220], "duplicate_timestamp_count", 1),
        ([99, 100, 160, 220], "out_of_range_count", 1),
        ([], "full_bucket_coverage", False),
    ],
)
async def test_prices_history_result_v1_marks_mechanical_grid_failures_incomplete(
    timestamps,
    failed_field,
    expected_value,
) -> None:
    api = _api()
    api.get = AsyncMock(
        return_value={
            "history": [{"t": timestamp, "p": "0.40"} for timestamp in timestamps]
        }
    )

    result = await api.get_prices_history_result_v1(
        "123token", start_ts=100, end_ts=220, fidelity=1
    )

    assert result.status is PublicDataStatus.SUCCESS
    assert result.parse_complete is True
    assert result.range_complete is False
    assert result.coverage.full_bucket_coverage is False
    assert getattr(result.coverage, failed_field) == expected_value


@pytest.mark.asyncio
async def test_prices_history_explicit_range_without_fidelity_stays_unknown() -> None:
    api = _api()
    api.get = AsyncMock(return_value={"history": [{"t": 100, "p": "0.40"}]})

    result = await api.get_prices_history_result_v1(
        "123token", start_ts=100, end_ts=220
    )

    assert result.parse_complete is True
    assert result.coverage.explicit_range is True
    assert result.coverage.fidelity_seconds is None
    assert result.coverage.full_bucket_coverage is None
    assert result.range_complete is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_http_status"),
    [
        (APIError("history missing", status_code=404), PublicDataStatus.NOT_FOUND, 404),
        (APIError("upstream failed", status_code=500), PublicDataStatus.ERROR, 500),
        (RuntimeError("transport failed"), PublicDataStatus.ERROR, None),
    ],
)
async def test_prices_history_result_v1_preserves_failures(
    error: Exception,
    expected_status: PublicDataStatus,
    expected_http_status: Optional[int],
) -> None:
    api = _api()
    api.get = AsyncMock(side_effect=error)

    result = await api.get_prices_history_result_v1(
        "123token", start_ts=100, end_ts=200
    )

    assert result.query.token_id == "123token"
    assert result.query.start_ts == 100
    assert result.query.end_ts == 200
    assert result.status == expected_status
    assert result.http_status == expected_http_status
    assert str(error) in result.error
    assert result.points == []
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is False
    assert result.range_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        [],
        {},
        {"history": None},
        {"history": {}},
    ],
)
async def test_prices_history_result_v1_rejects_malformed_envelope(
    response: object,
) -> None:
    api = _api()
    api.get = AsyncMock(return_value=response)

    result = await api.get_prices_history_result_v1("123token", interval="1h")

    assert result.status == PublicDataStatus.ERROR
    assert result.error is not None
    assert result.http_status is None
    assert result.points == []
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is False
    assert result.range_complete is False


def test_versioned_price_point_is_strict_without_breaking_legacy_model() -> None:
    legacy = PricePoint(t=-1, p="1.5")

    assert legacy.timestamp == -1
    assert legacy.price == Decimal("1.5")
    with pytest.raises(PydanticValidationError):
        PriceHistoryPointV1(t=-1, p="1.5")


def test_public_clob_results_enforce_surface_specific_completeness() -> None:
    market_events = MarketTradeEventsResultV1.model_validate(
        {
            "condition_id": "0xcondition",
            "status": PublicDataStatus.SUCCESS,
            "request": _request_evidence("CLOB:default"),
            "events": [_event(transactionHash="0x1")],
            "raw_count": 1,
            "parsed_count": 1,
            "parse_loss_count": 0,
            "parse_complete": True,
            "source_complete": None,
            "decoded_json_bytes": 200,
        }
    )
    price_history = PriceHistoryResultV1.model_validate(
        {
            "query": {
                "token_id": "asset-1",
                "start_ts": 100,
                "end_ts": 220,
                "fidelity": 1,
            },
            "status": PublicDataStatus.SUCCESS,
            "request": _request_evidence("GET:/prices-history"),
            "coverage": {
                "explicit_range": True,
                "fidelity_seconds": 60,
                "observed_start_ts": 100,
                "observed_end_ts": 220,
                "timestamps_ordered": True,
                "duplicate_timestamp_count": 0,
                "out_of_range_count": 0,
                "maximum_gap_seconds": 60,
                "start_boundary_covered": True,
                "end_boundary_covered": True,
                "full_bucket_coverage": True,
            },
            "points": [
                {"t": 100, "p": "0.5"},
                {"t": 160, "p": "0.5"},
                {"t": 220, "p": "0.5"},
            ],
            "raw_count": 3,
            "parsed_count": 3,
            "parse_loss_count": 0,
            "parse_complete": True,
            "range_complete": True,
        }
    )

    assert market_events.source_complete is None
    assert price_history.range_complete is True

    with pytest.raises(PydanticValidationError, match="must stay unknown"):
        MarketTradeEventsResultV1.model_validate(
            {
                **market_events.model_dump(),
                "source_complete": True,
            }
        )

    with pytest.raises(PydanticValidationError, match="forbid error_category"):
        MarketTradeEventsResultV1.model_validate(
            {
                **market_events.model_dump(),
                "error_category": "request",
            }
        )

    with pytest.raises(PydanticValidationError, match="require error_category"):
        MarketTradeEventsResultV1.model_validate(
            {
                "condition_id": "0xcondition",
                "status": PublicDataStatus.ERROR,
                "error": "request failed",
                "request": _request_evidence("CLOB:default"),
                "parse_complete": False,
                "source_complete": False,
            }
        )


def test_successful_clob_results_reject_zero_request_attempts() -> None:
    no_attempt = _request_evidence(
        "CLOB:default",
        attempt_count=0,
        retry_count=0,
        local_limiter_applied=False,
    )
    with pytest.raises(PydanticValidationError, match="at least one request attempt"):
        MarketTradeEventsResultV1.model_validate(
            {
                "condition_id": "0xcondition",
                "status": PublicDataStatus.SUCCESS,
                "request": no_attempt,
                "raw_count": 0,
                "parsed_count": 0,
                "parse_loss_count": 0,
                "parse_complete": True,
                "source_complete": None,
                "decoded_json_bytes": 2,
            }
        )

    no_history_attempt = no_attempt.model_copy(
        update={"rate_limit_key": "GET:/prices-history"}
    )
    with pytest.raises(PydanticValidationError, match="at least one request attempt"):
        PriceHistoryResultV1.model_validate(
            {
                "query": {"token_id": "asset-1", "interval": "1h"},
                "status": PublicDataStatus.SUCCESS,
                "request": no_history_attempt,
                "coverage": {"explicit_range": False},
                "raw_count": 0,
                "parsed_count": 0,
                "parse_loss_count": 0,
                "parse_complete": True,
                "range_complete": None,
            }
        )


@pytest.mark.asyncio
async def test_prices_history_result_v1_rejects_interval_with_explicit_range() -> None:
    api = _api()
    api.get = AsyncMock()

    with pytest.raises(ValueError, match="mutually exclusive"):
        await api.get_prices_history_result_v1("123token", interval="1h", start_ts=100)

    api.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"token_id": ""},
        {"token_id": "123token", "interval": "15m"},
        {"token_id": "123token", "start_ts": -1},
        {"token_id": "123token", "start_ts": 200, "end_ts": 100},
        {"token_id": "123token", "fidelity": 0},
    ],
)
async def test_prices_history_result_v1_rejects_invalid_query_before_transport(
    kwargs,
) -> None:
    api = _api()
    api.get = AsyncMock()

    call_kwargs = dict(kwargs)
    token_id = call_kwargs.pop("token_id")
    with pytest.raises((PydanticValidationError, ValueError)):
        await api.get_prices_history_result_v1(token_id, **call_kwargs)

    api.get.assert_not_awaited()
