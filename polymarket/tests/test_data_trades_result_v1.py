"""Offline contract tests for the versioned public Data API trades result."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from polymarket.api.data_api import DataAPI
from polymarket.exceptions import APIError, RateLimitError, TimeoutError
from polymarket.models import (
    DataTradesCoverageV1,
    DataTradesQueryV1,
    DataTradesResultV1,
    DataTradeV1,
    PublicDataStatus,
    PublicRequestEvidenceV1,
    Side,
    Trade,
)
from polymarket.utils.retry import RetryStrategy


def _documented_trade(**overrides):
    row = {
        "proxyWallet": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        "side": "BUY",
        "asset": "123456789",
        "conditionId": "0xcondition",
        "size": "123456789.123456789123456789",
        "price": "0.123456789123456789",
        "timestamp": 1_720_000_001,
        "title": "Will the documented row parse?",
        "slug": "documented-row",
        "icon": "https://example.invalid/icon.png",
        "eventSlug": "documented-event",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "name": "Public trader",
        "pseudonym": "careful-owl",
        "bio": None,
        "profileImage": None,
        "profileImageOptimized": None,
        "transactionHash": "0xtransaction",
        "futureEnrichment": {"preserved": True},
    }
    row.update(overrides)
    return row


def _api_returning(response):
    api = object.__new__(DataAPI)
    api.get = AsyncMock(return_value=response)
    return api


def _request_evidence(**overrides):
    values = {
        "wall_time_ms": "1.25",
        "attempt_count": 1,
        "retry_count": 0,
        "rate_limit_error_count": 0,
        "rate_limit_key": "GET:/trades",
        "retry_enabled": True,
        "max_retries": 3,
        "local_limiter_enabled": True,
        "local_limiter_applied": True,
    }
    values.update(overrides)
    return PublicRequestEvidenceV1.model_validate(values)


def test_data_trade_v1_parses_documented_shape_without_precision_or_field_loss():
    trade = DataTradeV1.model_validate(_documented_trade())

    assert trade.proxy_wallet == "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
    assert trade.condition_id == "0xcondition"
    assert trade.asset == "123456789"
    assert trade.side == "BUY"
    assert trade.size == Decimal("123456789.123456789123456789")
    assert trade.price == Decimal("0.123456789123456789")
    assert trade.timestamp == 1_720_000_001
    assert trade.outcome == "Yes" and trade.outcome_index == 0
    assert trade.transaction_hash == "0xtransaction"
    assert trade.event_slug == "documented-event"
    assert trade.model_extra == {"futureEnrichment": {"preserved": True}}


def test_data_trade_v1_accepts_empty_outcome_neg_risk_leg():
    # Live global-feed evidence (2026-07-17): neg-risk conversion legs carry
    # outcome "" with outcomeIndex 999 and full attribution. The truthful row
    # contract must accept them rather than misclassify real flow as parse loss.
    trade = DataTradeV1.model_validate(
        _documented_trade(outcome="", outcomeIndex=999, eventSlug="")
    )
    assert trade.outcome == "" and trade.outcome_index == 999
    assert trade.transaction_hash == "0xtransaction"


@pytest.mark.asyncio
async def test_get_trades_result_v1_sends_bounds_and_echoes_effective_query():
    api = _api_returning([_documented_trade(side="SELL")])

    result = await api.get_trades_result_v1(
        user="0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        market="0xcondition",
        event_id="1234",
        start=0,
        end=1_720_000_099,
        side=Side.SELL,
        taker_only=False,
        filter_type="CASH",
        filter_amount=500.25,
        limit=20_000,
        offset=7,
    )

    assert api.get.await_args.args == ("/trades",)
    assert api.get.await_args.kwargs == {
        "params": {
            "limit": 10_000,
            "offset": 7,
            "takerOnly": "false",
            "user": "0xabcdef0123456789abcdef0123456789abcdef01",
            "filterType": "CASH",
            "filterAmount": 500.25,
            "market": "0xcondition",
            "eventId": "1234",
            "start": 0,
            "end": 1_720_000_099,
            "side": "SELL",
        },
        "rate_limit_key": "GET:/trades",
        "retry": False,
    }
    assert result.contract_version == "v1" and result.source == "data.trades"
    assert result.query.model_dump(by_alias=True) == {
        "user": "0xabcdef0123456789abcdef0123456789abcdef01",
        "market": "0xcondition",
        "eventId": "1234",
        "start": 0,
        "end": 1_720_000_099,
        "side": "SELL",
        "takerOnly": False,
        "filterType": "CASH",
        "filterAmount": 500.25,
        "limit": 10_000,
        "offset": 7,
    }
    assert result.status is PublicDataStatus.SUCCESS
    assert result.raw_count == result.parsed_count == 1
    assert result.parse_loss_count == 0 and result.parse_complete is True
    assert result.source_complete is None
    assert result.coverage == DataTradesCoverageV1(
        explicit_time_bounds=True,
        first_page=False,
        timestamps_within_bounds=True,
        page_full=False,
    )
    assert result.request.rate_limit_key == "GET:/trades"
    assert result.request.attempt_count == 1 and result.request.retry_count == 0
    assert result.request.wall_time_ms >= 0
    assert result.request.retry_enabled is False
    assert result.request.local_limiter_applied is False


@pytest.mark.asyncio
async def test_get_trades_result_v1_reports_parse_loss_without_false_empty():
    missing_wallet = _documented_trade()
    missing_wallet.pop("proxyWallet")
    invalid_size = _documented_trade(size="not-a-decimal")
    impossible_price = _documented_trade(price="1.01")
    api = _api_returning(
        [_documented_trade(), missing_wallet, invalid_size, impossible_price]
    )

    result = await api.get_trades_result_v1(market="0xcondition")

    assert result.status is PublicDataStatus.SUCCESS
    assert len(result.trades) == 1
    assert result.raw_count == 4 and result.parsed_count == 1
    assert result.parse_loss_count == 3
    assert result.parse_complete is False
    assert result.source_complete is False


@pytest.mark.asyncio
async def test_get_trades_result_v1_proves_only_a_clean_exhausted_bounded_slice():
    complete_api = _api_returning(
        [_documented_trade(timestamp=100), _documented_trade(timestamp=200)]
    )
    complete = await complete_api.get_trades_result_v1(
        market="0xcondition", start=100, end=200, limit=3
    )

    assert complete.source_complete is True
    assert complete.coverage == DataTradesCoverageV1(
        explicit_time_bounds=True,
        first_page=True,
        timestamps_within_bounds=True,
        page_full=False,
    )

    full_page_api = _api_returning(
        [_documented_trade(timestamp=100), _documented_trade(timestamp=200)]
    )
    full_page = await full_page_api.get_trades_result_v1(
        market="0xcondition", start=100, end=200, limit=2
    )
    assert full_page.status is PublicDataStatus.SUCCESS
    assert full_page.coverage.page_full is True
    assert full_page.source_complete is False

    out_of_bounds_api = _api_returning([_documented_trade(timestamp=201)])
    out_of_bounds = await out_of_bounds_api.get_trades_result_v1(
        market="0xcondition", start=100, end=200, limit=2
    )
    assert out_of_bounds.coverage.timestamps_within_bounds is False
    assert out_of_bounds.source_complete is False

    unbounded_api = _api_returning([_documented_trade(timestamp=100)])
    unbounded = await unbounded_api.get_trades_result_v1(market="0xcondition", limit=2)
    assert unbounded.coverage.explicit_time_bounds is False
    assert unbounded.coverage.timestamps_within_bounds is None
    assert unbounded.source_complete is None


@pytest.mark.asyncio
async def test_get_trades_result_v1_fails_closed_if_server_exceeds_row_limit():
    api = _api_returning([_documented_trade(), _documented_trade()])

    result = await api.get_trades_result_v1(market="0xcondition", limit=1)

    assert result.status is PublicDataStatus.ERROR
    assert result.error == "Trades response exceeded the requested row limit: 2 > 1"
    assert result.trades == [] and result.source_complete is False


@pytest.mark.asyncio
async def test_get_trades_result_v1_exposes_exact_retry_and_limiter_evidence():
    api = object.__new__(DataAPI)
    api.get = AsyncMock(
        side_effect=[
            RateLimitError("limited", endpoint="GET:/trades"),
            [_documented_trade(timestamp=150)],
        ]
    )
    api.retry_strategy = RetryStrategy(
        max_retries=2,
        base_delay=0,
        max_delay=0,
        jitter=False,
    )
    api.rate_limiter = SimpleNamespace(enabled=True)

    result = await api.get_trades_result_v1(
        market="0xcondition", start=100, end=200, limit=2
    )

    assert result.status is PublicDataStatus.SUCCESS
    assert api.get.await_count == 2
    assert result.request.attempt_count == 2
    assert result.request.retry_count == 1
    assert result.request.rate_limit_error_count == 1
    assert result.request.retry_enabled is True and result.request.max_retries == 2
    assert result.request.local_limiter_enabled is True
    assert result.request.local_limiter_applied is True


@pytest.mark.asyncio
async def test_data_trade_parse_warning_does_not_echo_sensitive_row(caplog):
    sensitive_wallet = "0x1111111111111111111111111111111111111111"
    sensitive_hash = "0xsecret-transaction"
    api = _api_returning(
        [
            _documented_trade(
                proxyWallet=sensitive_wallet,
                transactionHash=sensitive_hash,
                size="invalid",
            )
        ]
    )

    await api.get_trades_result_v1(market="0xcondition")

    assert sensitive_wallet not in caplog.text
    assert sensitive_hash not in caplog.text
    assert "row_index=0" in caplog.text


@pytest.mark.asyncio
async def test_get_trades_result_v1_distinguishes_empty_not_found_and_error():
    empty_api = _api_returning([])
    empty = await empty_api.get_trades_result_v1(event_id="1234", start=10, end=20)
    assert empty.status is PublicDataStatus.SUCCESS
    assert empty.error is None and empty.http_status is None
    assert empty.raw_count == empty.parsed_count == empty.parse_loss_count == 0
    assert empty.parse_complete is True and empty.source_complete is True
    assert empty.coverage.timestamps_within_bounds is True

    not_found_api = object.__new__(DataAPI)
    not_found_api.get = AsyncMock(side_effect=APIError("missing", status_code=404))
    not_found = await not_found_api.get_trades_result_v1(market="0xmissing")
    assert not_found.status is PublicDataStatus.NOT_FOUND
    assert "missing" in not_found.error and not_found.http_status == 404
    assert not_found.trades == [] and not_found.parse_complete is False
    assert not_found.source_complete is False

    error_api = object.__new__(DataAPI)
    error_api.get = AsyncMock(side_effect=TimeoutError("timed out"))
    error = await error_api.get_trades_result_v1(market="0xcondition")
    assert error.status is PublicDataStatus.ERROR
    assert "timed out" in error.error and error.http_status is None
    assert error.trades == [] and error.parse_complete is False
    assert error.source_complete is False


@pytest.mark.asyncio
async def test_get_trades_result_v1_rejects_invalid_envelope_as_error_result():
    api = _api_returning({"trades": [_documented_trade()]})

    result = await api.get_trades_result_v1(market="0xcondition")

    assert result.status is PublicDataStatus.ERROR
    assert result.error == "Unexpected trades response format: dict"
    assert result.raw_count == result.parsed_count == result.parse_loss_count == 0
    assert result.parse_complete is False and result.source_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"user": "not-a-wallet"},
        {"market": ""},
        {"event_id": ""},
        {"start": 20, "end": 10},
        {"filter_type": "USD"},
        {"filter_amount": -1},
        {"filter_amount": float("inf")},
        {"limit": 0},
        {"offset": -1},
    ],
)
async def test_get_trades_result_v1_rejects_invalid_query_before_transport(kwargs):
    api = _api_returning([])

    with pytest.raises(PydanticValidationError):
        await api.get_trades_result_v1(**kwargs)

    api.get.assert_not_awaited()


def test_data_trades_result_v1_rejects_contradictory_evidence():
    query = DataTradesQueryV1(market="0xcondition")

    with pytest.raises(PydanticValidationError, match="raw_count must equal"):
        DataTradesResultV1(
            query=query,
            status=PublicDataStatus.SUCCESS,
            request=_request_evidence(),
            coverage=DataTradesCoverageV1(
                explicit_time_bounds=False,
                first_page=True,
                page_full=False,
            ),
            raw_count=1,
            parsed_count=0,
            parse_loss_count=0,
            parse_complete=True,
        )

    with pytest.raises(PydanticValidationError, match="successful results cannot"):
        DataTradesResultV1(
            query=query,
            status=PublicDataStatus.SUCCESS,
            error="contradiction",
            request=_request_evidence(),
            coverage=DataTradesCoverageV1(
                explicit_time_bounds=False,
                first_page=True,
                page_full=False,
            ),
            parse_complete=True,
        )


def test_data_trades_result_v1_allows_proven_complete_success():
    trade = DataTradeV1.model_validate(_documented_trade())
    result = DataTradesResultV1.model_validate(
        {
            "query": {
                "market": "0xcondition",
                "start": 1_720_000_000,
                "end": 1_720_000_002,
            },
            "status": PublicDataStatus.SUCCESS,
            "request": _request_evidence(),
            "coverage": {
                "explicit_time_bounds": True,
                "first_page": True,
                "timestamps_within_bounds": True,
                "page_full": False,
            },
            "trades": [trade],
            "raw_count": 1,
            "parsed_count": 1,
            "parse_loss_count": 0,
            "parse_complete": True,
            "source_complete": True,
        }
    )

    assert result.source_complete is True


def test_public_request_evidence_rejects_invented_retry_or_limiter_counts():
    with pytest.raises(PydanticValidationError, match="retry_count must equal"):
        _request_evidence(attempt_count=2, retry_count=0)

    with pytest.raises(PydanticValidationError, match="disabled local limiter"):
        _request_evidence(local_limiter_enabled=False, local_limiter_applied=True)

    with pytest.raises(PydanticValidationError, match="cannot exceed max_retries"):
        _request_evidence(attempt_count=3, retry_count=2, max_retries=1)


def test_successful_result_rejects_zero_request_attempts():
    with pytest.raises(PydanticValidationError, match="at least one request attempt"):
        DataTradesResultV1.model_validate(
            {
                "query": {"market": "0xcondition"},
                "status": PublicDataStatus.SUCCESS,
                "request": _request_evidence(
                    attempt_count=0,
                    retry_count=0,
                    local_limiter_applied=False,
                ),
                "coverage": {
                    "explicit_time_bounds": False,
                    "first_page": True,
                    "timestamps_within_bounds": None,
                    "page_full": False,
                },
                "parse_complete": True,
                "source_complete": None,
            }
        )


@pytest.mark.asyncio
async def test_legacy_get_trades_contract_remains_available():
    api = _api_returning(
        [
            {
                "id": "legacy-id",
                "market": "legacy-market",
                "conditionId": "0xcondition",
                "asset": "123456789",
                "side": "BUY",
                "size": "2.5",
                "price": "0.4",
                "feeRateBps": 0,
                "timestamp": 1_720_000_001,
                "transactionHash": "0xlegacy",
            }
        ]
    )

    trades = await api.get_trades(market="0xcondition")

    assert len(trades) == 1 and isinstance(trades[0], Trade)
    assert trades[0].id == "legacy-id" and trades[0].price == Decimal("0.4")
