"""
Public CLOB API client for market data (no authentication required).

This module provides access to all public Polymarket CLOB endpoints that don't
require authentication. Per CLAUDE.md policy, public endpoints should be used
for market data to avoid consuming authenticated rate limit quotas.

Rate Limits (per official Polymarket documentation):
- General CLOB: 5,000 req/10s (baseline across all endpoints)
- Health check (/ok): 50 req/10s
- Single endpoints (/book, /price, /midprice): 200 req/10s
- Batch endpoints (/books, /prices, /midprices): 80 req/10s
- Markets (general): 250 req/10s
- Markets (listing): 100 req/10s
- Markets (individual /0x): 50 req/10s
- Price history: 100 req/10s
- Tick size: 50 req/10s

Benefits of using public endpoints:
- No authentication overhead (faster response)
- Doesn't consume wallet's trading rate limit quota
- Can be called from anywhere without wallet credentials
- Higher throughput for market data queries

Adapted from py-clob-client (MIT License).
"""

import hashlib
import json
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..config import PolymarketSettings
from ..exceptions import (
    APIError,
    AuthenticationError,
    MarketNotFoundError,
    OrderBookError,
    PriceUnavailableError,
    RateLimitError,
    TradingError,
)
from ..models import MarketTradeEventV1, MarketTradeEventsResultV1
from ..models import OrderBook as OrderBookType
from ..models import (
    PriceHistoryCoverageV1,
    PriceHistoryPointV1,
    PriceHistoryQueryV1,
    PriceHistoryResultV1,
)
from ..models import PricePoint
from ..models import PublicDataStatus, PublicRequestEvidenceV1
from ..utils.numeric import to_decimal
from ..utils.rate_limiter import RateLimiter
from .base import BaseAPIClient

logger = logging.getLogger(__name__)

ALLOWED_TICK_SIZES = frozenset(
    {
        Decimal("0.1"),
        Decimal("0.01"),
        Decimal("0.005"),
        Decimal("0.0025"),
        Decimal("0.001"),
        Decimal("0.0001"),
    }
)


def _parse_tick_field(payload: Dict[str, Any], field: str) -> Decimal:
    if field not in payload:
        raise ValueError(f"response is missing {field}")
    tick_size = Decimal(str(payload[field]))
    if tick_size not in ALLOWED_TICK_SIZES:
        raise ValueError(f"unsupported {field}: {tick_size}")
    return tick_size


def _parse_book_levels(
    payload: Dict[str, Any],
    side: str,
    *,
    token_id: str,
) -> List[Tuple[Decimal, Decimal]]:
    """Parse every level or reject the snapshot rather than change its economics."""
    raw_levels = payload.get(side)
    if not isinstance(raw_levels, list):
        raise ValueError(f"orderbook {token_id} is missing {side}")

    levels: List[Tuple[Decimal, Decimal]] = []
    for index, raw_level in enumerate(raw_levels):
        if not isinstance(raw_level, dict):
            raise ValueError(f"orderbook {token_id} has malformed {side}[{index}]")
        if "price" not in raw_level or "size" not in raw_level:
            raise ValueError(f"orderbook {token_id} has incomplete {side}[{index}]")
        price = to_decimal(raw_level["price"])
        size = to_decimal(raw_level["size"])
        if (
            not price.is_finite()
            or not size.is_finite()
            or price <= 0
            or price >= 1
            or size <= 0
        ):
            raise ValueError(f"orderbook {token_id} has invalid {side}[{index}]")
        levels.append((price, size))

    return levels


def _parse_health_response(response: Any) -> bool:
    """Parse only the health shapes published by the CLOB API."""
    if isinstance(response, str):
        if response.strip().upper() == "OK":
            return True
        raise ValueError(f"unexpected health response string: {response!r}")
    if isinstance(response, dict):
        status = response.get("ok")
        if isinstance(status, bool):
            return status
    raise ValueError(f"unexpected health response: {response!r}")


async def _get_with_request_evidence(
    api: "PublicCLOBAPI",
    path: str,
    *,
    params: Optional[Dict[str, Any]],
    rate_limit_key: str,
) -> tuple[Any, Optional[Exception], PublicRequestEvidenceV1]:
    """Run one logical GET while counting every retry attempt exactly."""
    attempt_count = 0
    rate_limit_error_count = 0
    start_ns = time.perf_counter_ns()
    retry_strategy = getattr(api, "retry_strategy", None)

    async def request_once() -> Any:
        nonlocal attempt_count, rate_limit_error_count
        attempt_count += 1
        try:
            return await api.get(
                path,
                params=params,
                rate_limit_key=rate_limit_key,
                retry=False,
            )
        except RateLimitError:
            rate_limit_error_count += 1
            raise

    response: Any = None
    error: Optional[Exception] = None
    try:
        if retry_strategy is None:
            response = await request_once()
        else:
            response = await retry_strategy.execute_async(request_once)
    except Exception as exc:
        error = exc

    limiter = getattr(api, "rate_limiter", None)
    limiter_enabled = bool(limiter is not None and getattr(limiter, "enabled", False))
    evidence = PublicRequestEvidenceV1(
        wall_time_ms=Decimal(time.perf_counter_ns() - start_ns) / Decimal(1_000_000),
        attempt_count=attempt_count,
        retry_count=max(0, attempt_count - 1),
        rate_limit_error_count=rate_limit_error_count,
        rate_limit_key=rate_limit_key,
        retry_enabled=retry_strategy is not None,
        max_retries=(
            int(retry_strategy.max_retries) if retry_strategy is not None else 0
        ),
        local_limiter_enabled=limiter_enabled,
        local_limiter_applied=limiter_enabled and attempt_count > 0,
    )
    return response, error, evidence


def _price_history_coverage(
    query: PriceHistoryQueryV1,
    points: List[PriceHistoryPointV1],
    *,
    successful: bool,
    parse_complete: bool = False,
) -> PriceHistoryCoverageV1:
    """Derive strict, reproducible timestamp-grid coverage diagnostics."""
    explicit_range = query.start_ts is not None and query.end_ts is not None
    fidelity_seconds = query.fidelity * 60 if query.fidelity is not None else None
    if not successful:
        return PriceHistoryCoverageV1(
            explicit_range=explicit_range,
            fidelity_seconds=fidelity_seconds,
        )

    timestamps = [point.timestamp for point in points]
    unique_timestamps = sorted(set(timestamps))
    observed_start = unique_timestamps[0] if unique_timestamps else None
    observed_end = unique_timestamps[-1] if unique_timestamps else None
    ordered = timestamps == sorted(timestamps)
    duplicate_count = len(timestamps) - len(unique_timestamps)
    maximum_gap = (
        max(
            right - left
            for left, right in zip(unique_timestamps, unique_timestamps[1:])
        )
        if len(unique_timestamps) > 1
        else 0
        if unique_timestamps
        else None
    )
    out_of_range_count = (
        sum(
            timestamp < query.start_ts or timestamp > query.end_ts
            for timestamp in timestamps
        )
        if explicit_range
        else 0
    )

    if explicit_range and fidelity_seconds is not None:
        start_covered: Optional[bool] = bool(
            observed_start is not None
            and query.start_ts <= observed_start
            and observed_start - query.start_ts <= fidelity_seconds
        )
        end_covered: Optional[bool] = bool(
            observed_end is not None
            and observed_end <= query.end_ts
            and query.end_ts - observed_end <= fidelity_seconds
        )
        full_bucket_coverage: Optional[bool] = bool(
            parse_complete
            and ordered
            and duplicate_count == 0
            and out_of_range_count == 0
            and start_covered
            and end_covered
            and maximum_gap is not None
            and maximum_gap <= fidelity_seconds
        )
    else:
        start_covered = None
        end_covered = None
        full_bucket_coverage = None

    return PriceHistoryCoverageV1(
        explicit_range=explicit_range,
        fidelity_seconds=fidelity_seconds,
        observed_start_ts=observed_start,
        observed_end_ts=observed_end,
        timestamps_ordered=ordered,
        duplicate_timestamp_count=duplicate_count,
        out_of_range_count=out_of_range_count,
        maximum_gap_seconds=maximum_gap,
        start_boundary_covered=start_covered,
        end_boundary_covered=end_covered,
        full_bucket_coverage=full_bucket_coverage,
    )


def _is_no_orderbook_404(error: Exception) -> bool:
    """Return True when Polymarket reports a token has no CLOB orderbook."""
    if not isinstance(error, APIError) or error.status_code != 404:
        return False
    response = error.response if isinstance(error.response, dict) else {}
    detail = str(response.get("error", ""))
    return (
        "No orderbook exists for the requested token id" in str(error)
        or "No orderbook exists for the requested token id" in detail
    )


class PublicCLOBAPI(BaseAPIClient):
    """
    Public CLOB API client for market data (no authentication required).

    All methods in this class access public endpoints and don't require
    API credentials or wallet signatures.

    Usage:
        >>> from .clob_public import PublicCLOBAPI
        >>> from ..config import PolymarketSettings
        >>>
        >>> settings = PolymarketSettings()
        >>> client = PublicCLOBAPI(settings)
        >>>
        >>> # Get orderbook
        >>> orderbook = await client.get_orderbook(token_id)
        >>>
        >>> # Get spread
        >>> spread = client.get_spread(token_id)
        >>>
        >>> # Batch operations (more efficient)
        >>> spreads = client.get_spreads([token_id1, token_id2, token_id3])
    """

    def __init__(
        self,
        settings: PolymarketSettings,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        """
        Initialize Public CLOB API client.

        Args:
            settings: Client settings
            rate_limiter: Optional rate limiter
        """
        super().__init__(
            base_url=settings.clob_url,
            settings=settings,
            rate_limiter=rate_limiter,
        )

    # ========== Health & System ==========

    async def get_ok(self) -> bool:
        """
        Health check endpoint.

        Rate limit: 50 req/10s

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            response = await self.get("/", retry=False)
            return _parse_health_response(response)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise TradingError(f"CLOB server unavailable: {e}") from e

    async def get_server_time(self) -> int:
        """
        Get current server timestamp.

        Rate limit: 5,000 req/10s (general)

        Returns:
            Server timestamp in milliseconds
        """
        try:
            response = await self.get("/time")
            if isinstance(response, int):
                timestamp = response
            elif isinstance(response, dict) and response.get("timestamp") is not None:
                timestamp = int(response["timestamp"])
            else:
                raise TradingError(f"Unexpected server time response: {response!r}")
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            return timestamp
        except Exception as e:
            logger.error(f"Failed to get server time: {e}")
            raise TradingError(f"Server time fetch failed: {e}") from e

    # ========== Pricing & Spreads ==========

    async def get_midpoint(self, token_id: str) -> Optional[Decimal]:
        """
        Get midpoint price for a token.

        Rate limit: 200 req/10s

        Args:
            token_id: Token ID

        Returns:
            Midpoint price, or None if unavailable
        """
        try:
            response = await self.get("/midpoint", params={"token_id": token_id})

            mid = response.get("mid")
            if mid is None:
                logger.warning(f"No midpoint data for token {token_id}")
                return None

            return to_decimal(mid)

        except APIError as e:
            if e.status_code == 404:
                logger.warning(f"No midpoint/orderbook for token {token_id}")
                return None
            logger.error(f"Error fetching midpoint for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Midpoint unavailable: {e}", token_id=token_id
            ) from e
        except Exception as e:
            logger.error(f"Error fetching midpoint for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Midpoint unavailable: {e}", token_id=token_id
            ) from e

    async def get_midpoints(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
        """
        Get midpoint prices for multiple tokens (batch operation).

        Rate limit: 80 req/10s (10x more efficient than single calls)

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id -> midpoint price
        """
        if not token_ids:
            return {}

        try:
            # Build request body with BookParams format
            params = [{"token_id": tid} for tid in token_ids]

            response = await self.post("/midpoints", json_data=params)

            # Parse response into dict
            # API returns either:
            # - List of dicts: [{"token_id": "...", "mid": "..."}]
            # - Empty dict: {} (when no orderbooks exist for any token)
            result = {}

            if isinstance(response, list):
                # Normal response: list of dicts
                for item in response:
                    if isinstance(item, dict):
                        token_id = item.get("token_id")
                        mid = item.get("mid")
                        result[token_id] = to_decimal(mid) if mid is not None else None
            elif isinstance(response, dict):
                # Empty response or different format
                # Could also be {token_id: mid, ...} format
                if response:
                    # Try parsing as {token_id: mid} format
                    for token_id, mid in response.items():
                        if token_id in token_ids:
                            result[token_id] = (
                                to_decimal(mid) if mid is not None else None
                            )
                # If empty dict {}, result stays empty

            # Fill in None for tokens that weren't in response
            for tid in token_ids:
                if tid not in result:
                    result[tid] = None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch midpoints: {e}")
            return {tid: None for tid in token_ids}

    async def get_price(self, token_id: str, side: str) -> Optional[Decimal]:
        """
        Get price for a token and side.

        Rate limit: 200 req/10s

        Args:
            token_id: Token ID
            side: "BUY" or "SELL"

        Returns:
            Price for the specified side, or None if unavailable
        """
        try:
            response = await self.get(
                "/price", params={"token_id": token_id, "side": side}
            )

            price = response.get("price")
            if price is None:
                logger.warning(f"No price data for token {token_id}, side={side}")
                return None

            return to_decimal(price)

        except Exception as e:
            logger.error(f"Error fetching price for {token_id}: {e}")
            raise PriceUnavailableError(f"Price unavailable: {e}")

    async def get_prices(
        self, params: List[Dict[str, str]]
    ) -> Dict[str, Optional[Decimal]]:
        """
        Get prices for multiple tokens and sides (batch operation).

        Rate limit: 80 req/10s

        Args:
            params: List of dicts with {"token_id": str, "side": str}

        Returns:
            Dictionary with results for each token/side combination
        """
        if not params:
            return {}

        try:
            response = await self.post("/prices", json_data=params)

            # Parse response into dict with composite key
            result = {}
            for item in response:
                token_id = item.get("token_id")
                side = item.get("side")
                price = item.get("price")

                key = f"{token_id}_{side}"
                result[key] = to_decimal(price) if price is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch prices: {e}")
            return {}

    async def get_spread(self, token_id: str) -> Optional[Decimal]:
        """
        Get bid-ask spread for a token.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            Spread (ask - bid), or None if unavailable
        """
        try:
            response = await self.get("/spread", params={"token_id": token_id})

            spread = response.get("spread")
            if spread is None:
                logger.warning(f"No spread data for token {token_id}")
                return None

            return to_decimal(spread)

        except Exception as e:
            logger.error(f"Error fetching spread for {token_id}: {e}")
            return None

    async def get_spreads(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
        """
        Get bid-ask spreads for multiple tokens (batch operation).

        Rate limit: 80 req/10s

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id -> spread
        """
        if not token_ids:
            return {}

        try:
            params = [{"token_id": tid} for tid in token_ids]

            response = await self.post("/spreads", json_data=params)

            # API returns dict: {"token_id": "spread_value", ...}
            result = {}
            for token_id, spread in response.items():
                result[token_id] = to_decimal(spread) if spread is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch spreads: {e}")
            return {tid: None for tid in token_ids}

    async def get_prices_history(
        self,
        token_id: str,
        interval: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        fidelity: Optional[int] = None,
    ) -> List[PricePoint]:
        """Historical prices for one outcome token via GET /prices-history.

        interval: one of 1h/6h/1d/1w/1m/max — mutually exclusive with start_ts/end_ts.
        fidelity: bucket size in minutes (advisory). 404 -> []. Malformed points skipped.
        """
        if interval and (start_ts is not None or end_ts is not None):
            raise ValueError("interval is mutually exclusive with start_ts/end_ts")

        params: Dict[str, Any] = {"market": token_id}
        if interval:
            params["interval"] = interval
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if fidelity is not None:
            params["fidelity"] = fidelity

        try:
            response = await self.get(
                "/prices-history",
                params=params,
                rate_limit_key="GET:/prices-history",
                retry=True,
            )
        except APIError as e:
            if e.status_code == 404:
                logger.warning(f"No price history for token {token_id}")
                return []
            raise

        points: List[PricePoint] = []
        for item in (
            (response.get("history") or []) if isinstance(response, dict) else []
        ):
            try:
                points.append(PricePoint(**item))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Skipping malformed price point {item!r}: {e}")
                continue
        return points

    async def get_prices_history_result_v1(
        self,
        token_id: str,
        interval: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        fidelity: Optional[int] = None,
    ) -> PriceHistoryResultV1:
        """Return truthful, versioned metadata for one price-history query.

        Clean parsing does not prove full server range coverage, so
        ``range_complete`` remains unknown until API-B proves otherwise.
        """
        query = PriceHistoryQueryV1.model_validate(
            {
                "token_id": token_id,
                "interval": interval,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "fidelity": fidelity,
            }
        )
        params: Dict[str, Any] = {"market": query.token_id}
        if query.interval is not None:
            params["interval"] = query.interval
        if query.start_ts is not None:
            params["startTs"] = query.start_ts
        if query.end_ts is not None:
            params["endTs"] = query.end_ts
        if query.fidelity is not None:
            params["fidelity"] = query.fidelity

        response, request_error, request_evidence = await _get_with_request_evidence(
            self,
            "/prices-history",
            params=params,
            rate_limit_key="GET:/prices-history",
        )
        error_coverage = _price_history_coverage(query, [], successful=False)
        if request_error is not None:
            error = request_error
            http_status = getattr(error, "status_code", None)
            status = (
                PublicDataStatus.NOT_FOUND
                if isinstance(error, APIError) and http_status == 404
                else PublicDataStatus.ERROR
            )
            return PriceHistoryResultV1.model_validate(
                {
                    "query": query,
                    "status": status,
                    "error": f"{type(error).__name__}: {error}",
                    "http_status": http_status,
                    "request": request_evidence,
                    "coverage": error_coverage,
                    "parse_complete": False,
                    "range_complete": False,
                }
            )

        if not isinstance(response, dict) or "history" not in response:
            return PriceHistoryResultV1.model_validate(
                {
                    "query": query,
                    "status": PublicDataStatus.ERROR,
                    "error": (
                        "Invalid prices-history response: expected an object with a history field, "
                        f"got {type(response).__name__}"
                    ),
                    "request": request_evidence,
                    "coverage": error_coverage,
                    "parse_complete": False,
                    "range_complete": False,
                }
            )

        raw_history = response["history"]
        if not isinstance(raw_history, list):
            return PriceHistoryResultV1.model_validate(
                {
                    "query": query,
                    "status": PublicDataStatus.ERROR,
                    "error": (
                        "Invalid prices-history response: expected history to be a list, "
                        f"got {type(raw_history).__name__}"
                    ),
                    "request": request_evidence,
                    "coverage": error_coverage,
                    "parse_complete": False,
                    "range_complete": False,
                }
            )

        points: List[PriceHistoryPointV1] = []
        for row_index, item in enumerate(raw_history):
            try:
                points.append(PriceHistoryPointV1.model_validate(item))
            except (KeyError, ValueError, TypeError) as error:
                logger.warning(
                    "Skipping malformed price point row_index=%d error_type=%s",
                    row_index,
                    type(error).__name__,
                )

        raw_count = len(raw_history)
        parsed_count = len(points)
        parse_loss_count = raw_count - parsed_count
        parse_complete = parse_loss_count == 0
        coverage = _price_history_coverage(
            query,
            points,
            successful=True,
            parse_complete=parse_complete,
        )
        if not parse_complete:
            range_complete: Optional[bool] = False
        else:
            range_complete = coverage.full_bucket_coverage
        return PriceHistoryResultV1.model_validate(
            {
                "query": query,
                "status": PublicDataStatus.SUCCESS,
                "request": request_evidence,
                "coverage": coverage,
                "points": points,
                "raw_count": raw_count,
                "parsed_count": parsed_count,
                "parse_loss_count": parse_loss_count,
                "parse_complete": parse_complete,
                "range_complete": range_complete,
            }
        )

    # ========== Order Books ==========

    async def get_orderbook(self, token_id: str) -> OrderBookType:
        """
        Get full orderbook for a token.

        Rate limit: 200 req/10s

        Args:
            token_id: Token ID

        Returns:
            OrderBook object with bids and asks

        Raises:
            OrderBookError: If orderbook unavailable
        """
        try:
            response = await self.get("/book", params={"token_id": token_id})
            if not isinstance(response, dict):
                raise ValueError("orderbook response is not an object")
            response_token_id = response.get("asset_id")
            if not isinstance(response_token_id, str) or response_token_id != token_id:
                raise ValueError(
                    "orderbook asset_id does not match requested token "
                    f"{token_id}: {response_token_id!r}"
                )

            # Parse bids and asks - MUST be tuples (price, size) per OrderBookType model
            # CRITICAL: Polymarket API returns bids LOW→HIGH and asks HIGH→LOW
            # We need: bids HIGH→LOW (best bid first), asks LOW→HIGH (best ask first)
            bids = _parse_book_levels(response, "bids", token_id=token_id)
            asks = _parse_book_levels(response, "asks", token_id=token_id)

            # Sort: bids descending (best=highest first), asks ascending (best=lowest first)
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            return OrderBookType(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market=response.get("market"),
                tick_size=_parse_tick_field(response, "tick_size"),
                neg_risk=response.get("neg_risk", False),
                timestamp=response.get("timestamp"),
            )

        except Exception as e:
            if _is_no_orderbook_404(e):
                raise OrderBookError(
                    f"No orderbook exists for token {token_id}: {e}",
                    token_id=token_id,
                ) from e
            logger.error(f"Error fetching orderbook for {token_id}: {e}")
            raise OrderBookError(f"Orderbook unavailable: {e}")

    async def get_orderbooks_batch(
        self, token_ids: List[str]
    ) -> Dict[str, OrderBookType]:
        """
        Get orderbooks for multiple tokens (batch operation).

        Rate limit: 80 req/10s (much more efficient than individual calls)

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token ID to OrderBook
        """
        if not token_ids:
            return {}

        try:
            params = [{"token_id": tid} for tid in token_ids]

            response = await self.post("/books", json_data=params)
            if not isinstance(response, list):
                raise ValueError("batch orderbook response is not a list")

            expected_token_ids = set(token_ids)
            orderbooks: Dict[str, OrderBookType] = {}
            for book_data in response:
                if not isinstance(book_data, dict):
                    raise ValueError("batch orderbook entry is not an object")
                token_id = book_data.get("asset_id")
                if not isinstance(token_id, str) or not token_id:
                    raise ValueError("batch orderbook is missing asset_id")
                if token_id not in expected_token_ids:
                    raise ValueError(
                        f"batch orderbook returned unrequested token {token_id}"
                    )
                if token_id in orderbooks:
                    raise ValueError(
                        f"batch orderbook returned duplicate token {token_id}"
                    )
                bids = _parse_book_levels(book_data, "bids", token_id=token_id)
                asks = _parse_book_levels(book_data, "asks", token_id=token_id)

                bids.sort(key=lambda x: x[0], reverse=True)
                asks.sort(key=lambda x: x[0])
                orderbooks[token_id] = OrderBookType(
                    token_id=token_id,
                    bids=bids,
                    asks=asks,
                    market=book_data.get("market"),
                    tick_size=_parse_tick_field(book_data, "tick_size"),
                    neg_risk=book_data.get("neg_risk", False),
                    timestamp=book_data.get("timestamp"),
                )

            missing_token_ids = expected_token_ids.difference(orderbooks)
            if missing_token_ids:
                missing = ", ".join(sorted(missing_token_ids))
                raise ValueError(
                    f"batch orderbook response is missing requested tokens: {missing}"
                )

            return orderbooks

        except Exception as e:
            logger.error(f"Error fetching batch orderbooks: {e}")
            raise TradingError(f"Batch orderbook fetch failed: {e}") from e

    async def get_order_book_hash(self, orderbook: OrderBookType) -> str:
        """
        Compute hash of orderbook state (local computation, no API call).

        This is useful for detecting orderbook changes without comparing
        full data structures.

        Args:
            orderbook: OrderBook object

        Returns:
            SHA-256 hash of orderbook state
        """
        # Create deterministic string representation
        # Note: bids and asks are tuples (price, size)
        book_str = json.dumps(
            {
                "market": orderbook.market,
                "token_id": orderbook.token_id,
                "bids": [
                    [str(b[0]), str(b[1])] for b in orderbook.bids
                ],  # b[0]=price, b[1]=size
                "asks": [
                    [str(a[0]), str(a[1])] for a in orderbook.asks
                ],  # a[0]=price, a[1]=size
                "timestamp": str(orderbook.timestamp) if orderbook.timestamp else "",
            },
            sort_keys=True,
        )

        return hashlib.sha256(book_str.encode()).hexdigest()

    # ========== Market Metadata ==========

    async def get_tick_size(self, token_id: str) -> Decimal:
        """
        Get minimum tick size for a token.

        Rate limit: 50 req/10s

        Args:
            token_id: Token ID

        Returns:
            Current minimum tick size declared by the CLOB
        """
        try:
            response = await self.get("/tick-size", params={"token_id": token_id})
            return _parse_tick_field(response, "minimum_tick_size")

        except Exception as e:
            raise APIError(f"Error fetching tick size for {token_id}: {e}") from e

    async def get_neg_risk(self, token_id: str) -> bool:
        """
        Check if token is in a neg-risk market.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            True if neg-risk enabled, False otherwise
        """
        try:
            response = await self.get("/neg-risk", params={"token_id": token_id})
            if not isinstance(response, dict) or not isinstance(
                response.get("neg_risk"), bool
            ):
                raise ValueError("response is missing boolean neg_risk")
            return response["neg_risk"]

        except Exception as e:
            raise APIError(f"Error fetching neg_risk for {token_id}: {e}") from e

    async def get_fee_rate_bps(self, token_id: str) -> int:
        """
        Get fee rate in basis points for a token.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            CLOB protocol ``base_fee`` metadata. This is not the economic
            ``fd`` fee-curve rate used for sizing or P&L.
        """
        try:
            response = await self.get("/fee-rate", params={"token_id": token_id})
            if not isinstance(response, dict) or "base_fee" not in response:
                raise ValueError("response is missing base_fee")
            base_fee = Decimal(str(response["base_fee"]))
            if (
                not base_fee.is_finite()
                or base_fee < 0
                or base_fee != base_fee.to_integral_value()
            ):
                raise ValueError(f"invalid base_fee: {base_fee}")
            return int(base_fee)

        except Exception as e:
            raise APIError(f"Error fetching fee rate for {token_id}: {e}") from e

    # ========== Market Listings ==========

    async def get_simplified_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get simplified market list (fast, minimal data).

        Rate limit: 100 req/10s

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            {
                "data": [...],  # List of simplified market objects
                "next_cursor": str  # Next page cursor
            }
        """
        try:
            response = await self.get(
                "/simplified-markets", params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching simplified markets: {e}")
            raise TradingError(f"Simplified markets fetch failed: {e}") from e

    async def get_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get full market list (complete data).

        Rate limit: 250 req/10s (general markets endpoint)

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            {
                "data": [...],  # List of complete market objects
                "next_cursor": str  # Next page cursor
            }
        """
        try:
            response = await self.get("/markets", params={"next_cursor": next_cursor})

            return response

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return {"data": [], "next_cursor": ""}

    async def get_sampling_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get sampling market list.

        Rate limit: 5,000 req/10s (general)

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            Market data with pagination
        """
        try:
            response = await self.get(
                "/sampling-markets", params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching sampling markets: {e}")
            return {"data": [], "next_cursor": ""}

    async def get_sampling_simplified_markets(
        self, next_cursor: str = "MA=="
    ) -> Dict[str, Any]:
        """
        Get sampling simplified market list.

        Rate limit: 5,000 req/10s (general)

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            Simplified market data with pagination
        """
        try:
            response = await self.get(
                "/sampling-simplified-markets", params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching sampling simplified markets: {e}")
            return {"data": [], "next_cursor": ""}

    async def get_market(self, condition_id: str) -> Dict[str, Any]:
        """
        Get single market details by condition ID.

        Rate limit: 50 req/10s

        Args:
            condition_id: Market condition ID (0x...)

        Returns:
            Market data dictionary

        Raises:
            MarketNotFoundError: If market doesn't exist
        """
        try:
            response = await self.get(f"/markets/{condition_id}")

            return response

        except Exception as e:
            logger.error(f"Error fetching market {condition_id}: {e}")
            raise MarketNotFoundError(f"Market not found: {condition_id}")

    async def get_market_trades_events(self, condition_id: str) -> List[Dict[str, Any]]:
        """
        Get trade events for a market.

        Rate limit: 5,000 req/10s (general)

        Args:
            condition_id: Market condition ID

        Returns:
            List of trade event dictionaries
        """
        try:
            response = await self.get(
                f"/live-activity/events/{condition_id}",
                rate_limit_key="CLOB:default",
            )

            # Response is a list of trade events
            return response if isinstance(response, list) else []

        except Exception as e:
            logger.error(f"Error fetching market trades events: {e}")
            return []

    async def get_market_trades_events_result_v1(
        self, condition_id: str
    ) -> MarketTradeEventsResultV1:
        """Return typed events with fixed row/decoded-size and local-rate bounds.

        The hard ceilings apply after BaseAPIClient has decoded the JSON response;
        they bound accepted facade data, not streaming transport bytes.  Upstream
        pagination and retention remain undocumented, so clean source completeness
        stays unknown.
        """
        if not condition_id:
            raise ValueError("condition_id is required")

        response, request_error, request_evidence = await _get_with_request_evidence(
            self,
            f"/live-activity/events/{condition_id}",
            params=None,
            rate_limit_key="CLOB:default",
        )
        if request_error is not None:
            error = request_error
            http_status = getattr(error, "status_code", None)
            error_category = (
                "auth"
                if isinstance(error, AuthenticationError) or http_status in (401, 403)
                else "request"
            )
            status = (
                PublicDataStatus.NOT_FOUND
                if isinstance(error, APIError) and http_status == 404
                else PublicDataStatus.ERROR
            )
            return MarketTradeEventsResultV1.model_validate(
                {
                    "condition_id": condition_id,
                    "status": status,
                    "error": f"{type(error).__name__}: {error}",
                    "error_category": error_category,
                    "http_status": http_status,
                    "request": request_evidence,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        if not isinstance(response, list):
            return MarketTradeEventsResultV1.model_validate(
                {
                    "condition_id": condition_id,
                    "status": PublicDataStatus.ERROR,
                    "error": (
                        "Invalid market-trade-events response: expected a list, "
                        f"got {type(response).__name__}"
                    ),
                    "error_category": "non_list",
                    "request": request_evidence,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        try:
            decoded_json_bytes = len(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as error:
            return MarketTradeEventsResultV1.model_validate(
                {
                    "condition_id": condition_id,
                    "status": PublicDataStatus.ERROR,
                    "error": f"Invalid market-trade-events JSON values: {error}",
                    "error_category": "serialization",
                    "request": request_evidence,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        if len(response) > 1_000 or decoded_json_bytes > 1_048_576:
            return MarketTradeEventsResultV1.model_validate(
                {
                    "condition_id": condition_id,
                    "status": PublicDataStatus.ERROR,
                    "error": (
                        "Market-trade-events response exceeded facade acceptance bounds: "
                        f"rows={len(response)}/1000, "
                        f"decoded_json_bytes={decoded_json_bytes}/1048576"
                    ),
                    "error_category": "bounds",
                    "request": request_evidence,
                    "decoded_json_bytes": decoded_json_bytes,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        events: List[MarketTradeEventV1] = []
        for row_index, item in enumerate(response):
            try:
                event = MarketTradeEventV1.model_validate(item)
                if event.condition_id != condition_id:
                    raise ValueError(
                        "event condition_id does not match requested condition"
                    )
                events.append(event)
            except (TypeError, ValueError) as error:
                logger.warning(
                    "Skipping malformed market trade event row_index=%d error_type=%s",
                    row_index,
                    type(error).__name__,
                )

        raw_count = len(response)
        parsed_count = len(events)
        parse_loss_count = raw_count - parsed_count
        parse_complete = parse_loss_count == 0
        return MarketTradeEventsResultV1.model_validate(
            {
                "condition_id": condition_id,
                "status": PublicDataStatus.SUCCESS,
                "request": request_evidence,
                "events": events,
                "raw_count": raw_count,
                "parsed_count": parsed_count,
                "parse_loss_count": parse_loss_count,
                "parse_complete": parse_complete,
                "source_complete": None if parse_complete else False,
                "decoded_json_bytes": decoded_json_bytes,
            }
        )

    # ========== Trade History ==========

    async def get_last_trade_price(self, token_id: str) -> Optional[Decimal]:
        """
        Get last trade price for a token.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            Last trade price, or None if no trades
        """
        try:
            response = await self.get(
                "/last-trade-price", params={"token_id": token_id}
            )

            price = response.get("price")
            if price is None:
                return None

            return to_decimal(price)

        except Exception as e:
            logger.error(f"Error fetching last trade price for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get last trade price: {e}", token_id=token_id
            ) from e

    async def get_last_trades_prices(
        self, token_ids: List[str]
    ) -> Dict[str, Optional[Decimal]]:
        """
        Get last trade prices for multiple tokens (batch operation).

        Rate limit: 5,000 req/10s (general)

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id -> last trade price
        """
        if not token_ids:
            return {}

        try:
            params = [{"token_id": tid} for tid in token_ids]

            response = await self.post("/last-trades-prices", json_data=params)

            result = {}
            for item in response:
                token_id = item.get("token_id")
                price = item.get("price")
                result[token_id] = to_decimal(price) if price is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch last trade prices: {e}")
            raise TradingError(f"Batch last trade price fetch failed: {e}") from e

    # ========== Derived Methods (Convenience) ==========

    async def get_best_bid_ask(
        self, token_id: str
    ) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get best bid and ask prices (top of book).

        More efficient than fetching full orderbook when you only need
        top prices. Uses get_orderbook() internally.

        Args:
            token_id: Token ID

        Returns:
            (best_bid, best_ask) tuple, or None if unavailable
        """
        try:
            orderbook = await self.get_orderbook(token_id)

            if not orderbook.bids or not orderbook.asks:
                logger.warning(f"Empty orderbook for token {token_id}")
                return None

            # Bids and asks are tuples (price, size)
            best_bid = orderbook.bids[0][0]  # First element is price
            best_ask = orderbook.asks[0][0]  # First element is price

            return (best_bid, best_ask)

        except OrderBookError as e:
            if "No orderbook exists" in str(e):
                logger.warning(
                    f"No orderbook exists for token {token_id}; bid/ask unavailable"
                )
                return None
            logger.error(f"Error getting best bid/ask for {token_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting best bid/ask for {token_id}: {e}")
            return None

    async def get_liquidity_depth(
        self, token_id: str, price_range: Decimal = Decimal("0.05")
    ) -> Dict[str, Any]:
        """
        Calculate liquidity depth within price range.

        This analyzes the orderbook to determine how much liquidity is
        available within a percentage of the best bid/ask.

        Args:
            token_id: Token ID
            price_range: Price range (e.g., 0.05 for ±5%)

        Returns:
            {
                "bid_depth": Decimal,  # Total size of bids within range
                "ask_depth": Decimal,  # Total size of asks within range
                "bid_levels": int,     # Number of bid price levels
                "ask_levels": int,     # Number of ask price levels
                "total_depth": Decimal # Total liquidity
            }
        """
        try:
            orderbook = await self.get_orderbook(token_id)

            if not orderbook.bids or not orderbook.asks:
                return {
                    "bid_depth": Decimal("0"),
                    "ask_depth": Decimal("0"),
                    "bid_levels": 0,
                    "ask_levels": 0,
                    "total_depth": Decimal("0"),
                }

            # Bids and asks are tuples (price, size)
            best_bid = orderbook.bids[0][0]  # First element is price
            best_ask = orderbook.asks[0][0]  # First element is price

            # Calculate minimum prices within range
            bid_min_price = best_bid * (Decimal("1") - price_range)
            ask_max_price = best_ask * (Decimal("1") + price_range)

            # Sum liquidity within range
            bid_depth = Decimal("0")
            bid_levels = 0
            for price, size in orderbook.bids:  # Unpack tuple
                if price >= bid_min_price:
                    bid_depth += size
                    bid_levels += 1
                else:
                    break  # Orderbook is sorted, can stop early

            ask_depth = Decimal("0")
            ask_levels = 0
            for price, size in orderbook.asks:  # Unpack tuple
                if price <= ask_max_price:
                    ask_depth += size
                    ask_levels += 1
                else:
                    break

            return {
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "bid_levels": bid_levels,
                "ask_levels": ask_levels,
                "total_depth": bid_depth + ask_depth,
            }

        except Exception as e:
            logger.error(f"Error calculating liquidity depth for {token_id}: {e}")
            return {
                "bid_depth": Decimal("0"),
                "ask_depth": Decimal("0"),
                "bid_levels": 0,
                "ask_levels": 0,
                "total_depth": Decimal("0"),
            }
