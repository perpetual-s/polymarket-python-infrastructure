"""
Gamma API client for market data.

Read-only API for markets, events, and metadata.
"""

from typing import Optional, List
import logging

from .base import BaseAPIClient
from ..config import PolymarketSettings
from ..models import Market, Event, MarketFilters
from ..exceptions import MarketDataError
from ..utils.rate_limiter import RateLimiter
from ..utils.retry import CircuitBreaker

logger = logging.getLogger(__name__)


class GammaAPI(BaseAPIClient):
    """
    Gamma API client for market data.

    Provides read-only access to markets, events, and metadata.
    """

    def __init__(
        self,
        settings: PolymarketSettings,
        rate_limiter: Optional[RateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize Gamma API client.

        Args:
            settings: Client settings
            rate_limiter: Optional rate limiter
            circuit_breaker: Optional circuit breaker
        """
        super().__init__(
            base_url=settings.gamma_url,
            settings=settings,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker
        )

    def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        archived: Optional[bool] = None,
        tag_id: Optional[int] = None,
        slug: Optional[str] = None,
        **kwargs
    ) -> List[Market]:
        """
        Get markets with filters.

        Args:
            limit: Max results (default: 100, max: 1000)
            offset: Pagination offset
            active: Filter by active status
            closed: Filter by closed status
            archived: Filter by archived status (from official Polymarket API)
            tag_id: Filter by tag ID
            slug: Filter by market slug
            **kwargs: Additional filters

        Returns:
            List of markets

        Raises:
            MarketDataError: If request fails
        """
        try:
            params = {
                "limit": min(limit, 1000),
                "offset": offset,
                **kwargs
            }

            if active is not None:
                params["active"] = str(active).lower()
            if closed is not None:
                params["closed"] = str(closed).lower()
            if archived is not None:
                params["archived"] = str(archived).lower()
            if tag_id is not None:
                params["tag_id"] = tag_id
            if slug:
                params["slug"] = slug

            response = self.get(
                "/markets",
                params=params,
                rate_limit_key="GET:/markets"
            )

            # Parse markets
            markets = []
            for data in response:
                try:
                    market = Market(
                        id=data.get("id", ""),
                        question=data.get("question", ""),
                        slug=data.get("slug", ""),
                        condition_id=data.get("conditionId", ""),
                        category=data.get("category", ""),
                        outcomes=data.get("outcomes", []),
                        outcome_prices=data.get("outcomePrices", []),
                        tokens=data.get("clobTokenIds") or data.get("tokens"),  # Support both field names
                        volume=float(data.get("volumeNum", 0)),
                        liquidity=float(data.get("liquidityNum", 0)),
                        active=data.get("active", False),
                        closed=data.get("closed", False),
                        start_date=data.get("startDate"),
                        end_date=data.get("endDate"),
                        # New fields from official Polymarket agents repo
                        rewards_min_size=data.get("rewardsMinSize"),
                        rewards_max_spread=data.get("rewardsMaxSpread"),
                        ticker=data.get("ticker"),
                        new=data.get("new"),
                        featured=data.get("featured"),
                        restricted=data.get("restricted"),
                        archived=data.get("archived")
                    )
                    markets.append(market)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse market {data.get('id')}: {e}")
                    continue

            logger.info(f"Fetched {len(markets)} markets")
            return markets

        except MarketDataError:
            raise
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Failed to parse markets response: {e}")
            raise MarketDataError(f"Failed to parse markets: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            raise MarketDataError(f"Failed to fetch markets: {e}")

    def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """
        Get market by slug.

        Args:
            slug: Market slug

        Returns:
            Market or None if not found

        Raises:
            MarketDataError: If request fails
        """
        try:
            markets = self.get_markets(slug=slug, limit=1)
            return markets[0] if markets else None
        except MarketDataError:
            raise
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Failed to fetch market {slug}: {e}")
            raise MarketDataError(f"Failed to fetch market: {e}")

    def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """
        Get market by ID.

        Args:
            market_id: Market ID

        Returns:
            Market or None if not found

        Raises:
            MarketDataError: If request fails
        """
        try:
            markets = self.get_markets(id=[market_id], limit=1)
            return markets[0] if markets else None
        except MarketDataError:
            raise
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            raise MarketDataError(f"Failed to fetch market: {e}")

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        archived: Optional[bool] = None,
        **kwargs
    ) -> List[Event]:
        """
        Get events (collections of related markets).

        Args:
            limit: Max results
            offset: Pagination offset
            active: Filter by active status
            closed: Filter by closed status
            archived: Filter by archived status (from official Polymarket API)
            **kwargs: Additional filters

        Returns:
            List of Event objects

        Raises:
            MarketDataError: If request fails
        """
        try:
            params = {
                "limit": min(limit, 1000),
                "offset": offset,
                **kwargs
            }

            if active is not None:
                params["active"] = str(active).lower()
            if closed is not None:
                params["closed"] = str(closed).lower()
            if archived is not None:
                params["archived"] = str(archived).lower()

            response = self.get(
                "/events",
                params=params,
                rate_limit_key="GET:/events"
            )

            # Parse events
            events = []
            for data in response:
                try:
                    # Parse nested markets (FULL objects, not just IDs!)
                    nested_markets = []
                    for m_data in data.get("markets", []):
                        try:
                            market = Market(
                                id=m_data.get("id", ""),
                                question=m_data.get("question", ""),
                                slug=m_data.get("slug", ""),
                                condition_id=m_data.get("conditionId", ""),
                                category=data.get("category", ""),  # Use event category
                                outcomes=m_data.get("outcomes", []),
                                outcome_prices=m_data.get("outcomePrices", []),
                                tokens=m_data.get("clobTokenIds") or m_data.get("tokens"),
                                volume=float(m_data.get("volumeNum", 0)),
                                liquidity=float(m_data.get("liquidityNum", 0)),
                                active=m_data.get("active", False),
                                closed=m_data.get("closed", False),
                                start_date=m_data.get("startDate"),
                                end_date=m_data.get("endDate"),
                            )
                            nested_markets.append(market)
                        except Exception as e:
                            logger.debug(f"Failed to parse nested market {m_data.get('id')}: {e}")
                            continue

                    event = Event(
                        id=data.get("id", ""),
                        slug=data.get("slug", ""),
                        title=data.get("title", ""),
                        description=data.get("description"),
                        ticker=data.get("ticker"),
                        active=data.get("active", False),
                        closed=data.get("closed", False),
                        archived=data.get("archived", False),
                        new=data.get("new"),
                        featured=data.get("featured"),
                        restricted=data.get("restricted"),
                        start_date=data.get("startDate"),
                        end_date=data.get("endDate"),
                        markets=nested_markets,  # Full Market objects!
                        neg_risk=data.get("negRisk")
                    )
                    events.append(event)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse event {data.get('id')}: {e}")
                    continue

            logger.info(f"Fetched {len(events)} events")
            return events

        except MarketDataError:
            raise
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Failed to parse events response: {e}")
            raise MarketDataError(f"Failed to parse events: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch events: {e}")
            raise MarketDataError(f"Failed to fetch events: {e}")

    def get_tags(self) -> List[dict]:
        """
        Get available tags.

        Returns:
            List of tags

        Raises:
            MarketDataError: If request fails
        """
        try:
            response = self.get(
                "/tags",
                rate_limit_key="GET:/tags"
            )

            logger.info(f"Fetched {len(response)} tags")
            return response

        except MarketDataError:
            raise
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse tags response: {e}")
            raise MarketDataError(f"Failed to parse tags: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch tags: {e}")
            raise MarketDataError(f"Failed to fetch tags: {e}")

    def search_markets(self, query: str, limit: int = 20) -> List[Market]:
        """
        Search markets by query string.

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of markets

        Raises:
            MarketDataError: If request fails
        """
        try:
            # Search uses different rate limit
            params = {
                "query": query,
                "limit": min(limit, 100)
            }

            response = self.get(
                "/search",
                params=params,
                rate_limit_key="GET:/markets/search"
            )

            markets = []
            for data in response:
                try:
                    market = Market(
                        id=data.get("id", ""),
                        question=data.get("question", ""),
                        slug=data.get("slug", ""),
                        condition_id=data.get("conditionId", ""),
                        category=data.get("category", ""),
                        outcomes=data.get("outcomes", []),
                        outcome_prices=data.get("outcomePrices", []),
                        volume=float(data.get("volumeNum", 0)),
                        liquidity=float(data.get("liquidityNum", 0)),
                        active=data.get("active", False),
                        closed=data.get("closed", False)
                    )
                    markets.append(market)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse search result: {e}")
                    continue

            logger.info(f"Found {len(markets)} markets for query: {query}")
            return markets

        except MarketDataError:
            raise
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Failed to parse search response for '{query}': {e}")
            raise MarketDataError(f"Search parse error: {e}")
        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")
            raise MarketDataError(f"Search failed: {e}")

    # Helper methods from official Polymarket agents repo
    def get_all_current_markets(self, limit: int = 100) -> List[Market]:
        """
        Auto-paginate through all active, non-closed, non-archived markets.

        Inspired by official Polymarket agents repository.

        Args:
            limit: Batch size for pagination (default: 100)

        Returns:
            List of all current markets

        Raises:
            MarketDataError: If request fails
        """
        logger.info("Fetching all current markets (auto-pagination)")
        offset = 0
        all_markets = []

        while True:
            batch = self.get_markets(
                active=True,
                closed=False,
                archived=False,
                limit=limit,
                offset=offset
            )

            if not batch:
                break

            all_markets.extend(batch)
            offset += len(batch)

            # Stop if we got fewer results than requested (last page)
            if len(batch) < limit:
                break

        logger.info(f"Fetched {len(all_markets)} total current markets")
        return all_markets

    def get_clob_tradable_markets(self, limit: int = 100) -> List[Market]:
        """
        Get markets with order book enabled (CLOB tradable).

        Filters for active, non-closed markets with token IDs assigned.
        From official Polymarket agents repository.

        Args:
            limit: Max results (default: 100)

        Returns:
            List of tradable markets

        Raises:
            MarketDataError: If request fails
        """
        logger.info("Fetching CLOB tradable markets")
        markets = self.get_markets(active=True, closed=False, limit=limit)

        # Filter markets with tokens (indicates CLOB trading available)
        tradable = [m for m in markets if m.tokens and len(m.tokens) > 0]

        logger.info(f"Found {len(tradable)} tradable markets out of {len(markets)}")
        return tradable

    def filter_events_for_trading(self, events: List[Event]) -> List[Event]:
        """
        Filter events for active trading (no restrictions, not archived/closed).

        From official Polymarket agents repository.

        Args:
            events: List of events to filter

        Returns:
            Filtered list of tradable events
        """
        tradable = [
            e for e in events
            if (e.active and
                not e.restricted and
                not e.archived and
                not e.closed)
        ]

        logger.info(f"Filtered {len(tradable)} tradable events from {len(events)} total")
        return tradable

    def get_all_tradeable_events(self, limit: int = 100) -> List[Event]:
        """
        Get all tradeable events in one call.

        Combines get_events() and filter_events_for_trading().
        From official Polymarket agents repository.

        Args:
            limit: Max results (default: 100)

        Returns:
            List of tradeable events

        Raises:
            MarketDataError: If request fails
        """
        logger.info("Fetching all tradeable events")
        all_events = self.get_events(limit=limit)
        return self.filter_events_for_trading(all_events)
