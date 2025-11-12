"""
Data API client for dashboard features.

Provides endpoints for positions, trades, activity, and portfolio analytics.
Base URL: https://data-api.polymarket.com
"""

from typing import Optional, List, Dict, Any
import logging

from .base import BaseAPIClient
from ..config import PolymarketSettings
from ..models import (
    Position,
    Trade,
    Activity,
    PortfolioValue,
    Holder,
    LeaderboardTrader,
    Side,
    ActivityType
)
from ..exceptions import (
    APIError,
    ValidationError,
    MarketDataError
)
from ..utils.rate_limiter import RateLimiter
from ..utils.retry import CircuitBreaker

logger = logging.getLogger(__name__)


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
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize Data API client.

        Args:
            settings: Optional settings (uses defaults if not provided)
            rate_limiter: Optional rate limiter
            circuit_breaker: Optional circuit breaker
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
            circuit_breaker=circuit_breaker
        )

    # ========== Positions ==========

    def get_positions(
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
        title: Optional[str] = None
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
            "sortDirection": sort_direction
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
            response = self.get(
                "/positions",
                params=params,
                rate_limit_key="GET:/positions",
                retry=True
            )

            # Parse positions
            if not isinstance(response, list):
                logger.warning(f"Unexpected positions response format: {type(response)}")
                return []

            positions = []
            for item in response:
                try:
                    position = Position(**item)
                    positions.append(position)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Failed to parse position: {e}")
                    continue

            logger.info(f"Fetched {len(positions)} positions for {user}")
            return positions

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse positions response for {user}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get positions for {user}: {e}")
            raise

    # ========== Trades ==========

    def get_trades(
        self,
        user: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        taker_only: bool = True,
        filter_type: Optional[str] = None,
        filter_amount: Optional[float] = None,
        market: Optional[str] = None,
        side: Optional[Side] = None
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
            "takerOnly": str(taker_only).lower()
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
            response = self.get(
                "/trades",
                params=params,
                rate_limit_key="GET:/trades",
                retry=True
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

    def get_activity(
        self,
        user: str,
        market: Optional[str] = None,
        activity_type: Optional[ActivityType] = None,
        limit: int = 100,
        offset: int = 0,
        start: Optional[int] = None,
        end: Optional[int] = None,
        side: Optional[Side] = None,
        sort_by: str = "TIMESTAMP"
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
            "sortBy": sort_by
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
            response = self.get(
                "/activity",
                params=params,
                rate_limit_key="GET:/activity",
                retry=True
            )

            if not isinstance(response, list):
                logger.warning(f"Unexpected activity response format: {type(response)}")
                return []

            activities = []
            for item in response:
                try:
                    activity = Activity(**item)
                    activities.append(activity)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Failed to parse activity: {e}")
                    continue

            logger.info(f"Fetched {len(activities)} activities for {user}")
            return activities

        except (APIError, TimeoutError):
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse activity response for {user}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get activity for {user}: {e}")
            raise

    # ========== Portfolio Value ==========

    def get_portfolio_value(
        self,
        user: str,
        market: Optional[str] = None
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
            portfolio = client.data_api.get_portfolio_value("0x123...")
            print(f"Total value: ${portfolio.equity_total}")
            print(f"Bets: ${portfolio.bets}, Cash: ${portfolio.cash}")
        """
        if not user or not user.startswith("0x"):
            raise ValidationError(f"Invalid user address: {user}")

        params: Dict[str, Any] = {
            "user": user.lower()
        }

        if market:
            params["market"] = market

        try:
            response = self.get(
                "/value",
                params=params,
                rate_limit_key="GET:/value",
                retry=True
            )

            # Parse response - API returns dict with bets, cash, equity_total
            if isinstance(response, dict):
                # Add user field for model
                response["user"] = user

                # Legacy field - if value not present, calculate from equity_total
                if "value" not in response:
                    response["value"] = response.get("equityTotal", response.get("equity_total", 0))

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

    def get_holders(
        self,
        market: str,
        limit: int = 100,
        min_balance: int = 1
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
            whales = client.data_api.get_holders(
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
            "minBalance": min_balance
        }

        try:
            response = self.get(
                "/holders",
                params=params,
                rate_limit_key="GET:/holders",
                retry=True
            )

            if not isinstance(response, list):
                logger.warning(f"Unexpected holders response format: {type(response)}")
                return []

            holders = []
            for item in response:
                try:
                    holder = Holder(**item)
                    holders.append(holder)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Failed to parse holder: {e}")
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

    def get_leaderboard(
        self,
        limit: int = 100,
        min_pnl: Optional[float] = None
    ) -> List[LeaderboardTrader]:
        """
        Get leaderboard of top traders.

        Args:
            limit: Max traders to return (default: 100)
            min_pnl: Minimum PnL filter (optional)

        Returns:
            List of leaderboard traders ordered by rank

        Raises:
            APIError: If request fails
        """
        try:
            response = self.get(
                "/leaderboard",
                params={},
                rate_limit_key="GET:/leaderboard",
                retry=True
            )

            if not isinstance(response, list):
                logger.warning(f"Unexpected leaderboard response format: {type(response)}")
                return []

            traders = []
            for item in response:
                try:
                    trader = LeaderboardTrader(**item)
                    # Apply filters
                    if min_pnl is not None and trader.pnl < min_pnl:
                        continue
                    traders.append(trader)
                    # Stop if we have enough
                    if len(traders) >= limit:
                        break
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Failed to parse leaderboard trader: {e}")
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
