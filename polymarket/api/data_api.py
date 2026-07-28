"""
Data API client for dashboard features.

Provides endpoints for positions, trades, activity, and portfolio analytics.
Base URL: https://data-api.polymarket.com
"""

import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..config import PolymarketSettings
from ..exceptions import (
    APIError,
    RateLimitError,
    TimeoutError,
    ValidationError,
)
from ..models import (
    ClosedPosition,
    Activity,
    ActivityType,
    DataTradesCoverageV1,
    DataTradesQueryV1,
    DataTradesResultV1,
    DataTradeV1,
    Holder,
    LeaderboardTrader,
    PortfolioValue,
    Position,
    PublicDataStatus,
    PublicRequestEvidenceV1,
    Side,
    Trade,
)
from ..utils.rate_limiter import RateLimiter
from .base import BaseAPIClient

logger = logging.getLogger(__name__)

# Live-verified 2026-07-27: `/closed-positions` rejects any other `sortBy` with
# HTTP 400 naming this exact set, so an unsupported value fails loudly instead
# of silently falling back to the default REALIZEDPNL ordering.
CLOSED_POSITION_SORT_FIELDS = (
    "REALIZEDPNL",
    "AVGPRICE",
    "PRICE",
    "TITLE",
    "TIMESTAMP",
)
SORT_DIRECTIONS = ("ASC", "DESC")


async def _get_with_request_evidence(
    api: "DataAPI",
    path: str,
    *,
    params: Dict[str, Any],
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


class DataAPI(BaseAPIClient):
    """
    Data API client for dashboard features.

    Provides endpoints for:
    - User positions with P&L tracking
    - Trade history
    - Onchain activity monitoring
    - Portfolio analytics
    - Market holder analysis
    """

    def __init__(
        self,
        settings: Optional[PolymarketSettings] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        """
        Initialize Data API client.

        Args:
            settings: Optional settings (uses defaults if not provided)
            rate_limiter: Optional rate limiter
        """
        # Create settings if not provided
        if settings is None:
            from ..config import get_settings

            settings = get_settings()

        # Override data API URL
        data_api_url = "https://data-api.polymarket.com"

        # Initialize with data API URL
        super().__init__(
            base_url=data_api_url,
            settings=settings,
            rate_limiter=rate_limiter,
        )

    # ========== Positions ==========

    async def get_positions(
        self,
        user: str,
        market: Optional[str] = None,
        event_id: Optional[str] = None,
        size_threshold: float = 1.0,
        redeemable: Optional[bool] = None,
        mergeable: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "TOKENS",
        sort_direction: str = "DESC",
        title: Optional[str] = None,
        strict_parse: bool = False,
    ) -> List[Position]:
        """
        Get current positions for a user.

        Args:
            user: User's wallet address
            market: Filter by conditionId (CSV supported)
            event_id: Filter by eventId (mutually exclusive with market)
            size_threshold: Minimum position size (default: 1.0)
            redeemable: Filter redeemable positions
            mergeable: Filter mergeable positions
            limit: Max results (default: 100, max: 500)
            offset: Pagination offset (max: 10,000)
            sort_by: Sort field (TOKENS, CURRENT, INITIAL, CASHPNL, PERCENTPNL, etc.)
            sort_direction: ASC or DESC
            title: Market title filter
            strict_parse: Raise instead of returning a partial/empty parsed page

        Returns:
            List of positions with P&L metrics

        Raises:
            ValidationError: If parameters are invalid
            APIError: If request fails
        """
        # Validate user address
        if not user or not user.startswith("0x"):
            raise ValidationError(f"Invalid user address: {user}")

        # Build params
        params: Dict[str, Any] = {
            "user": user.lower(),
            "sizeThreshold": size_threshold,
            "limit": min(limit, 500),
            "offset": min(offset, 10000),
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }

        if market:
            params["market"] = market
        if event_id:
            params["eventId"] = event_id
        if redeemable is not None:
            params["redeemable"] = str(redeemable).lower()
        if mergeable is not None:
            params["mergeable"] = str(mergeable).lower()
        if title:
            params["title"] = title[:100]  # Max 100 chars

        try:
            response = await self.get(
                "/positions", params=params, rate_limit_key="GET:/positions", retry=True
            )

            # Parse positions
            if not isinstance(response, list):
                logger.warning(
                    f"Unexpected positions response format: {type(response)}"
                )
                if strict_parse:
                    raise APIError(
                        "Unexpected positions response format; complete observation unavailable"
                    )
                return []

            positions = []
            for index, item in enumerate(response):
                try:
                    if strict_parse:
                        # Position's legacy validators intentionally coerce
                        # malformed public numerics to zero for lenient
                        # dashboard callers. A complete observation cannot
                        # silently apply that coercion.
                        for field in (
                            "size",
                            "avgPrice",
                            "currentValue",
                            "initialValue",
                            "curPrice",
                            "cashPnl",
                            "percentPnl",
                            "realizedPnl",
                            "percentRealizedPnl",
                        ):
                            if field not in item:
                                continue
                            numeric = Decimal(str(item[field]))
                            if not numeric.is_finite():
                                raise ValueError(
                                    f"Non-finite position numeric field {field}"
                                )
                    position = Position(**item)
                    positions.append(position)
                except Exception as e:
                    # Catch all exceptions including Decimal conversion errors
                    logger.warning(f"Failed to parse position: {type(e).__name__}: {e}")
                    logger.debug(f"Position data: {item}")
                    if strict_parse:
                        raise APIError(
                            f"Position response parse failure at offset {offset + index}"
                        ) from e
                    continue

            logger.info(f"Fetched {len(positions)} positions for {user}")
            return positions

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse positions response for {user}: {e}")
            raise
        except RateLimitError as e:
            # Retriable/transient: same WARNING-grade treatment as activity.
            logger.warning(f"Failed to get positions for {user}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get positions for {user}: {e}")
            raise

    async def get_positions_complete(
        self,
        user: str,
        market: Optional[str] = None,
        event_id: Optional[str] = None,
        size_threshold: float = 1.0,
        redeemable: Optional[bool] = None,
        mergeable: Optional[bool] = None,
        sort_by: str = "TOKENS",
        sort_direction: str = "DESC",
        title: Optional[str] = None,
    ) -> List[Position]:
        """Retrieve one authoritative current-position observation.

        The Data API exposes offset pagination with a maximum page size of 500
        and a maximum offset of 10,000. A collection is authoritative only
        after two fully parsed passes agree on canonical position identity and
        size. Any malformed page, wallet mismatch, repeated position identity,
        pass mutation, transport error, or full page at the offset ceiling
        raises instead of returning partial state.
        """
        page_size = 500
        max_offset = 10_000
        expected_wallet = user.lower()

        async def fetch_complete_pass() -> List[Position]:
            offset = 0
            positions: List[Position] = []
            seen: set[tuple[str, str]] = set()

            while True:
                page = await self.get_positions(
                    user=user,
                    market=market,
                    event_id=event_id,
                    size_threshold=size_threshold,
                    redeemable=redeemable,
                    mergeable=mergeable,
                    limit=page_size,
                    offset=offset,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    title=title,
                    strict_parse=True,
                )

                for position in page:
                    if not position.condition_id or not position.asset:
                        raise APIError(
                            "Position response is missing identity; "
                            "complete observation unavailable"
                        )
                    if position.proxy_wallet.lower() != expected_wallet:
                        raise APIError(
                            "Position response wallet mismatch; "
                            "complete observation unavailable"
                        )

                    identity = (position.condition_id, position.asset)
                    if identity in seen:
                        raise APIError(
                            "Duplicate position identity across pages; "
                            "complete observation unavailable"
                        )
                    seen.add(identity)

                positions.extend(page)
                if len(page) < page_size:
                    return positions

                if offset == max_offset:
                    raise APIError(
                        "Positions offset ceiling reached on a full page; "
                        "complete observation unavailable"
                    )
                offset += page_size

        first_pass = await fetch_complete_pass()
        second_pass = await fetch_complete_pass()

        def custody_state(
            positions: List[Position],
        ) -> list[tuple[str, str, str, Decimal]]:
            # The API's raw ordering and live price/P&L fields are volatile.
            # Delta truth depends on stable outcome-token identity and size.
            return sorted(
                (
                    position.condition_id,
                    position.asset,
                    position.outcome,
                    position.size,
                )
                for position in positions
            )

        if custody_state(first_pass) != custody_state(second_pass):
            raise APIError(
                "Position identity or size changed between complete passes; "
                "complete observation unavailable"
            )

        logger.info(
            "Fetched stable complete position observation with %s rows for %s",
            len(second_pass),
            user,
        )
        return second_pass

    # ========== Trades ==========

    async def get_trades_result_v1(
        self,
        user: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        taker_only: bool = True,
        filter_type: Optional[str] = None,
        filter_amount: Optional[float] = None,
        market: Optional[str] = None,
        event_id: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        side: Optional[Side] = None,
    ) -> DataTradesResultV1:
        """Get public trades with typed rows and explicit data-quality metadata.

        A clean response leaves source completeness unknown because upstream
        retention and active-market pagination are not yet proven.
        """
        effective_user = user.lower() if user is not None else None
        query = DataTradesQueryV1.model_validate(
            {
                "user": effective_user,
                "market": market,
                "event_id": event_id,
                "start": start,
                "end": end,
                "side": side,
                "taker_only": taker_only,
                "filter_type": filter_type,
                "filter_amount": filter_amount,
                "limit": min(limit, 10_000),
                "offset": offset,
            }
        )

        params: Dict[str, Any] = {
            "limit": query.limit,
            "offset": query.offset,
            "takerOnly": str(query.taker_only).lower(),
        }
        if query.user is not None:
            params["user"] = query.user
        if query.filter_type is not None:
            params["filterType"] = query.filter_type
        if query.filter_amount is not None:
            params["filterAmount"] = query.filter_amount
        if query.market is not None:
            params["market"] = query.market
        if query.event_id is not None:
            params["eventId"] = query.event_id
        if query.start is not None:
            params["start"] = query.start
        if query.end is not None:
            params["end"] = query.end
        if query.side is not None:
            params["side"] = query.side

        response, request_error, request_evidence = await _get_with_request_evidence(
            self,
            "/trades",
            params=params,
            rate_limit_key="GET:/trades",
        )
        coverage_base = {
            "explicit_time_bounds": query.start is not None and query.end is not None,
            "first_page": query.offset == 0,
            "page_full": False,
        }

        if request_error is not None:
            exc = request_error
            http_status = getattr(exc, "status_code", None)
            status = (
                PublicDataStatus.NOT_FOUND
                if isinstance(exc, APIError) and http_status == 404
                else PublicDataStatus.ERROR
            )
            return DataTradesResultV1.model_validate(
                {
                    "query": query,
                    "status": status,
                    "error": f"{type(exc).__name__}: {exc}",
                    "http_status": http_status,
                    "request": request_evidence,
                    "coverage": coverage_base,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        if not isinstance(response, list):
            return DataTradesResultV1.model_validate(
                {
                    "query": query,
                    "status": PublicDataStatus.ERROR,
                    "error": f"Unexpected trades response format: {type(response).__name__}",
                    "request": request_evidence,
                    "coverage": coverage_base,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        if len(response) > query.limit:
            return DataTradesResultV1.model_validate(
                {
                    "query": query,
                    "status": PublicDataStatus.ERROR,
                    "error": (
                        "Trades response exceeded the requested row limit: "
                        f"{len(response)} > {query.limit}"
                    ),
                    "request": request_evidence,
                    "coverage": coverage_base,
                    "parse_complete": False,
                    "source_complete": False,
                }
            )

        trades: List[DataTradeV1] = []
        for row_index, item in enumerate(response):
            try:
                trades.append(DataTradeV1.model_validate(item))
            except Exception as exc:
                logger.warning(
                    "Failed to parse Data trade v1 row_index=%d error_type=%s",
                    row_index,
                    type(exc).__name__,
                )

        raw_count = len(response)
        parsed_count = len(trades)
        parse_loss_count = raw_count - parsed_count
        parse_complete = parse_loss_count == 0
        explicit_time_bounds = query.start is not None and query.end is not None
        timestamps_within_bounds = (
            all(query.start <= trade.timestamp <= query.end for trade in trades)
            if explicit_time_bounds
            else None
        )
        page_full = raw_count >= query.limit
        coverage = DataTradesCoverageV1(
            explicit_time_bounds=explicit_time_bounds,
            first_page=query.offset == 0,
            timestamps_within_bounds=timestamps_within_bounds,
            page_full=page_full,
        )
        if not parse_complete:
            source_complete: Optional[bool] = False
        elif explicit_time_bounds and query.offset == 0:
            source_complete = bool(timestamps_within_bounds and not page_full)
        else:
            source_complete = None
        return DataTradesResultV1.model_validate(
            {
                "query": query,
                "status": PublicDataStatus.SUCCESS,
                "request": request_evidence,
                "coverage": coverage,
                "trades": trades,
                "raw_count": raw_count,
                "parsed_count": parsed_count,
                "parse_loss_count": parse_loss_count,
                "parse_complete": parse_complete,
                "source_complete": source_complete,
            }
        )

    async def get_trades(
        self,
        user: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        taker_only: bool = True,
        filter_type: Optional[str] = None,
        filter_amount: Optional[float] = None,
        market: Optional[str] = None,
        side: Optional[Side] = None,
    ) -> List[Trade]:
        """
        Get user trade history.

        Args:
            user: User wallet address
            limit: Max trades (default: 100, max: 500)
            offset: Pagination offset
            taker_only: Only taker trades (default: True)
            filter_type: CASH or TOKENS
            filter_amount: Amount threshold
            market: Filter by conditionId (CSV supported)
            side: BUY or SELL

        Returns:
            List of trades ordered by most recent first

        Raises:
            APIError: If request fails
        """
        params: Dict[str, Any] = {
            "limit": min(limit, 500),
            "offset": offset,
            "takerOnly": str(taker_only).lower(),
        }

        if user:
            params["user"] = user.lower()
        if filter_type:
            params["filterType"] = filter_type
        if filter_amount is not None:
            params["filterAmount"] = filter_amount
        if market:
            params["market"] = market
        if side:
            params["side"] = side.value

        try:
            response = await self.get(
                "/trades", params=params, rate_limit_key="GET:/trades", retry=True
            )

            if not isinstance(response, list):
                logger.warning(f"Unexpected trades response format: {type(response)}")
                return []

            trades = []
            for item in response:
                try:
                    trade = Trade(**item)
                    trades.append(trade)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Failed to parse trade: {e}")
                    continue

            logger.info(f"Fetched {len(trades)} trades")
            return trades

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse trades response: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get trades: {e}")
            raise

    # ========== Activity ==========

    async def get_activity(
        self,
        user: str,
        market: Optional[str] = None,
        activity_type: Optional[ActivityType] = None,
        limit: int = 100,
        offset: int = 0,
        start: Optional[int] = None,
        end: Optional[int] = None,
        side: Optional[Side] = None,
        sort_by: str = "TIMESTAMP",
        strict_parse: bool = False,
    ) -> List[Activity]:
        """
        Get onchain activity for a user.

        Args:
            user: User address (required)
            market: Filter by conditionId (CSV supported)
            activity_type: TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION
            limit: Max results (default: 100, max: 500)
            offset: Pagination offset
            start: Unix timestamp start
            end: Unix timestamp end
            side: BUY or SELL (trades only)
            sort_by: TIMESTAMP, TOKENS, or CASH

        Returns:
            List of activity records

        Raises:
            ValidationError: If user address is invalid
            APIError: If request fails
        """
        if not user or not user.startswith("0x"):
            raise ValidationError(f"Invalid user address: {user}")

        params: Dict[str, Any] = {
            "user": user.lower(),
            "limit": min(limit, 500),
            "offset": offset,
            "sortBy": sort_by,
        }

        if market:
            params["market"] = market
        if activity_type:
            params["type"] = activity_type.value
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if side:
            params["side"] = side.value

        try:
            response = await self.get(
                "/activity", params=params, rate_limit_key="GET:/activity", retry=False
            )

            if not isinstance(response, list):
                if strict_parse:
                    raise APIError(
                        "Unexpected activity response format; "
                        "complete observation unavailable"
                    )
                logger.warning(f"Unexpected activity response format: {type(response)}")
                return []

            activities = []
            for item in response:
                try:
                    activity = Activity(**item)
                    activities.append(activity)
                except Exception as e:
                    # Numeric coercion raises ArithmeticError, not ValueError,
                    # so an unparseable size would otherwise escape this loop.
                    if strict_parse:
                        raise APIError(
                            f"Failed to parse activity row: {e}; "
                            "complete observation unavailable"
                        ) from e
                    logger.error(f"Failed to parse activity: {e}")
                    continue

            logger.info(f"Fetched {len(activities)} activities for {user}")
            return activities

        except APIError as e:
            if e.status_code == 404:
                logger.warning(f"No activity found for {user}")
                return []
            raise
        except TimeoutError:
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse activity response for {user}: {e}")
            raise
        except RateLimitError as e:
            # Retriable/transient by design: the limiter backs off and the
            # backup position poller covers the gap — WARNING-grade noise,
            # not an operator-actionable ERROR.
            logger.warning(f"Failed to get activity for {user}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get activity for {user}: {e}")
            raise

    async def get_activity_since(
        self,
        *,
        user: str,
        since_ts: int,
        activity_type: Optional[ActivityType] = None,
        side: Optional[Side] = None,
    ) -> List[Activity]:
        """Retrieve every activity at or after `since_ts` as one complete history.

        The caller's durable frontier decides when to stop: pages are read
        newest-first until one reaches behind `since_ts` or history ends. A
        malformed row, a non-list response, a foreign wallet, or the offset
        ceiling on a full page raises instead of returning a short history —
        for copy trading a truncated page is a missed source trade.

        Offset paging is only safe while the collection holds still. New trades
        land at offset 0 and slide everything else to higher offsets, so a
        multi-page read can hand back the same row twice. The caller identifies
        an otherwise-identical repeat by its ordinal, which would turn that
        re-read into a fabricated source trade — so a shift is refused and
        retried on the next poll rather than merged.
        """
        page_size = 500
        max_offset = 10_000
        expected_wallet = user.lower()

        def row_identity(activity: Activity) -> tuple:
            return (
                activity.transaction_hash,
                activity.timestamp,
                activity.asset,
                str(activity.size),
                str(activity.price),
            )

        activities: List[Activity] = []
        offset = 0
        page_zero_identities: Optional[List[tuple]] = None

        while True:
            page = await self.get_activity(
                user=user,
                activity_type=activity_type,
                side=side,
                limit=page_size,
                offset=offset,
                sort_by="TIMESTAMP",
                strict_parse=True,
            )

            for activity in page:
                if (
                    activity.proxy_wallet
                    and activity.proxy_wallet.lower() != expected_wallet
                ):
                    raise APIError(
                        "Activity response wallet mismatch; "
                        "complete observation unavailable"
                    )

            if offset == 0:
                page_zero_identities = [row_identity(a) for a in page]

            activities.extend(a for a in page if a.timestamp >= since_ts)

            # A row behind the frontier proves this page reached back far
            # enough; a short page proves the history ended.
            if len(page) < page_size or any(a.timestamp < since_ts for a in page):
                if offset > 0:
                    # More than one page was read, so the rows could have slid
                    # underneath us. Comparing only the head would miss an
                    # insert *below* row 0, which leaves the head identical
                    # while shifting every later row one offset down — the next
                    # page then re-serves the previous page's last row, and the
                    # caller's ordinal-based digest turns that re-read into a
                    # second, fabricated trade. Compare the whole page.
                    recheck = await self.get_activity(
                        user=user,
                        activity_type=activity_type,
                        side=side,
                        limit=page_size,
                        offset=0,
                        sort_by="TIMESTAMP",
                        strict_parse=True,
                    )
                    if [row_identity(a) for a in recheck] != page_zero_identities:
                        raise APIError(
                            "Activity collection shifted during pagination; "
                            "complete observation unavailable"
                        )
                return activities

            if offset >= max_offset:
                raise APIError(
                    "Activity offset ceiling reached before the durable "
                    "frontier; complete observation unavailable"
                )
            offset += page_size

    # ========== Portfolio Value ==========

    async def get_portfolio_value(
        self, user: str, market: Optional[str] = None
    ) -> PortfolioValue:
        """
        Get total USD value of user's positions with detailed breakdown.

        Args:
            user: User address
            market: Optional conditionId filter (CSV supported)

        Returns:
            PortfolioValue with detailed portfolio metrics:
            - value: Total portfolio value (legacy)
            - bets: Total bet value
            - cash: Available USDC
            - equity_total: Total portfolio value (bets + cash)

        Raises:
            ValidationError: If user address is invalid
            APIError: If request fails

        Example:
            portfolio = await client.data_api.get_portfolio_value("0x123...")
            print(f"Total value: ${portfolio.equity_total}")
            print(f"Bets: ${portfolio.bets}, Cash: ${portfolio.cash}")
        """
        if not user or not user.startswith("0x"):
            raise ValidationError(f"Invalid user address: {user}")

        params: Dict[str, Any] = {"user": user.lower()}

        if market:
            params["market"] = market

        try:
            response = await self.get(
                "/value", params=params, rate_limit_key="GET:/value", retry=True
            )

            # Parse response - API returns list with single item [{user, value}]
            if isinstance(response, list) and len(response) > 0:
                # Extract first item from list
                response = response[0]

            if isinstance(response, dict):
                # Add user field for model if not present
                if "user" not in response:
                    response["user"] = user

                # Legacy field - if value not present, calculate from equity_total
                if "value" not in response:
                    response["value"] = response.get(
                        "equityTotal", response.get("equity_total", 0)
                    )

                portfolio = PortfolioValue(**response)
            elif isinstance(response, (int, float)):
                # Fallback for simple numeric response
                portfolio = PortfolioValue(user=user, value=response)
            else:
                logger.warning(f"Unexpected value response format: {type(response)}")
                portfolio = PortfolioValue(user=user, value=0)

            logger.info(
                f"Portfolio value for {user}: ${portfolio.value:.2f} "
                f"(bets: ${portfolio.bets or 0:.2f}, cash: ${portfolio.cash or 0:.2f})"
            )
            return portfolio

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse portfolio value for {user}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get portfolio value for {user}: {e}")
            raise

    # ========== Market Holders ==========

    async def get_holders(
        self, market: str, limit: int = 100, min_balance: int = 1
    ) -> List[Holder]:
        """
        Get top holders in a specific market.

        Useful for whale discovery and tracking large position holders.

        Args:
            market: conditionId (required)
            limit: Max holders (default: 100, max: 500)
            min_balance: Minimum position size to include (default: 1)

        Returns:
            List of holders grouped by token, sorted by position size

        Raises:
            ValidationError: If market is invalid
            APIError: If request fails

        Example:
            # Find whales with positions > $5000
            whales = await client.data_api.get_holders(
                market="0x123...",
                limit=500,
                min_balance=5000
            )
            for whale in whales:
                print(f"{whale.pseudonym}: {whale.amount} @ {whale.proxy_wallet}")
        """
        if not market:
            raise ValidationError("Market conditionId is required")

        params: Dict[str, Any] = {
            "market": market,
            "limit": min(limit, 500),
            "minBalance": min_balance,
        }

        try:
            response = await self.get(
                "/holders", params=params, rate_limit_key="GET:/holders", retry=True
            )

            if not isinstance(response, list):
                logger.warning(f"Unexpected holders response format: {type(response)}")
                return []

            # API returns nested structure: [{token: str, holders: [...]}, ...]
            # Flatten to list of Holder objects with token_id added
            holders = []
            for token_group in response:
                token_id = token_group.get("token")
                holder_list = token_group.get("holders", [])

                for holder_data in holder_list:
                    try:
                        # Add token_id from parent structure
                        holder_data["token_id"] = token_id
                        holder = Holder(**holder_data)
                        holders.append(holder)
                    except Exception as e:
                        logger.warning(f"Failed to parse holder: {e}")
                        continue

            logger.info(f"Fetched {len(holders)} holders for market {market}")
            return holders

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse holders response for {market}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get holders for market {market}: {e}")
            raise

    # ========== Leaderboard ==========

    async def get_leaderboard(
        self,
        category: str = "OVERALL",
        time_period: str = "MONTH",
        order_by: str = "PNL",
        limit: int = 50,
        offset: int = 0,
    ) -> List[LeaderboardTrader]:
        """
        Get leaderboard of top traders (`GET /v1/leaderboard`).

        Args:
            category: OVERALL | POLITICS | SPORTS | ESPORTS | CRYPTO | ...
            time_period: DAY | WEEK | MONTH | ALL
            order_by: PNL | VOL
            limit: Max traders to return (server cap 50)
            offset: Paging offset (server cap 1000)

        Returns:
            List of leaderboard traders ordered by rank

        Raises:
            APIError: If request fails
        """
        try:
            response = await self.get(
                "/v1/leaderboard",
                params={
                    "category": category,
                    "timePeriod": time_period,
                    "orderBy": order_by,
                    "limit": limit,
                    "offset": offset,
                },
                rate_limit_key="GET:/v1/leaderboard",
                retry=True,
            )

            if not isinstance(response, list):
                logger.warning(
                    f"Unexpected leaderboard response format: {type(response)}"
                )
                return []

            traders = []
            for item in response:
                try:
                    traders.append(LeaderboardTrader(**item))
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Skipping unparseable leaderboard row: {e}")
                    continue

            logger.info(f"Fetched {len(traders)} leaderboard traders")
            return traders

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse leaderboard response: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get leaderboard: {e}")
            raise

    async def get_closed_positions(
        self,
        user: str,
        limit: int = 100,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_direction: str = "DESC",
    ) -> List[ClosedPosition]:
        """
        Get a user's closed positions with realized PnL (`GET /closed-positions`).

        Args:
            user: Proxy wallet address
            limit: Max rows per page
            offset: Paging offset
            sort_by: One of ``CLOSED_POSITION_SORT_FIELDS``. Left unset the
                endpoint sorts by ``REALIZEDPNL`` descending, so any prefix of
                the result is the wallet's biggest winners rather than a sample
                of its history. Pass ``TIMESTAMP`` for a chronological read.
            sort_direction: ``ASC`` or ``DESC``; only meaningful with ``sort_by``

        Returns:
            List of closed positions (``realized_pnl``/``total_bought`` populated)

        Raises:
            ValueError: If ``sort_by``/``sort_direction`` is not a documented value
            APIError: If request fails
        """
        params: dict = {"user": user, "limit": limit, "offset": offset}
        if sort_by is not None:
            normalized = str(sort_by).upper()
            if normalized not in CLOSED_POSITION_SORT_FIELDS:
                raise ValueError(
                    f"sort_by must be one of {list(CLOSED_POSITION_SORT_FIELDS)}; "
                    f"got {sort_by!r}"
                )
            direction = str(sort_direction).upper()
            if direction not in SORT_DIRECTIONS:
                raise ValueError(
                    f"sort_direction must be one of {list(SORT_DIRECTIONS)}; "
                    f"got {sort_direction!r}"
                )
            params["sortBy"] = normalized
            params["sortDirection"] = direction

        try:
            response = await self.get(
                "/closed-positions",
                params=params,
                rate_limit_key="GET:/closed-positions",
                retry=True,
            )

            if not isinstance(response, list):
                logger.warning(
                    f"Unexpected closed-positions response format: {type(response)}"
                )
                return []

            positions = []
            for item in response:
                try:
                    positions.append(ClosedPosition(**item))
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Skipping unparseable closed position: {e}")
                    continue

            logger.info(f"Fetched {len(positions)} closed positions for {user[:10]}...")
            return positions

        except (APIError, TimeoutError):
            raise
        except Exception as e:
            logger.error(f"Failed to get closed positions: {e}")
            raise
