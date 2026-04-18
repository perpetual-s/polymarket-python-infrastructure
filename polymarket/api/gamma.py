"""
Gamma API client for market data.

Read-only API for markets, events, and metadata.
"""

from typing import Optional, List, Dict, Any
import logging

from .base import BaseAPIClient
from ..config import PolymarketSettings
from ..models import Market, Event
from ..exceptions import APIError, MarketDataError
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

    async def get_markets(
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

            response = await self.get(
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
                        volume=float(data.get("volumeNum", 0) or data.get("volume", 0) or 0),
                        liquidity=float(data.get("liquidityNum", 0) or data.get("liquidityClob", 0) or 0),
                        active=data.get("active", False),
                        closed=data.get("closed", False),
                        start_date=data.get("startDate"),
                        end_date=data.get("endDate"),
                        # Fields from official Polymarket agents repo
                        rewards_min_size=data.get("rewardsMinSize"),
                        rewards_max_spread=data.get("rewardsMaxSpread"),
                        ticker=data.get("ticker"),
                        new=data.get("new"),
                        featured=data.get("featured"),
                        restricted=data.get("restricted"),
                        archived=data.get("archived"),
                        # Neg-risk fields
                        neg_risk=data.get("negRisk"),
                        enable_neg_risk=data.get("enableNegRisk"),
                        neg_risk_augmented=data.get("negRiskAugmented"),
                        neg_risk_market_id=data.get("negRiskMarketID"),
                        neg_risk_request_id=data.get("negRiskRequestID"),
                        # CRITICAL: Grouped market fields (fixes resolution date issue)
                        group_item_title=data.get("groupItemTitle"),
                        group_item_threshold=int(data.get("groupItemThreshold")) if data.get("groupItemThreshold") else None,
                        # Trading state fields
                        best_bid=data.get("bestBid"),
                        best_ask=data.get("bestAsk"),
                        spread=data.get("spread"),
                        last_trade_price=data.get("lastTradePrice"),
                        competitive=data.get("competitive"),
                        # Trading constraints
                        order_min_size=data.get("orderMinSize"),
                        order_price_min_tick_size=data.get("orderPriceMinTickSize"),
                        accepting_orders=data.get("acceptingOrders"),
                        # UMA oracle fields
                        question_id=data.get("questionID"),
                        uma_bond=data.get("umaBond"),
                        uma_reward=data.get("umaReward"),
                        resolution_source=data.get("resolutionSource"),
                        # Time-windowed volumes
                        volume_24h=data.get("volume24hr"),
                        volume_1wk=data.get("volume1wk"),
                        volume_1mo=data.get("volume1mo"),
                        # Creator/resolver fields
                        submitted_by=data.get("submitted_by"),
                        resolved_by=data.get("resolvedBy"),
                        # Date tracking
                        has_reviewed_dates=data.get("hasReviewedDates"),
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

    async def get_market_by_slug(self, slug: str) -> Optional[Market]:
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
            markets = await self.get_markets(slug=slug, limit=1)
            return markets[0] if markets else None
        except MarketDataError:
            raise
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Failed to fetch market {slug}: {e}")
            raise MarketDataError(f"Failed to fetch market: {e}")

    async def get_market_by_id(self, market_id: str) -> Optional[Market]:
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
            markets = await self.get_markets(id=[market_id], limit=1)
            return markets[0] if markets else None
        except MarketDataError:
            raise
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            raise MarketDataError(f"Failed to fetch market: {e}")

    async def get_events(
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

            response = await self.get(
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

    # ========== Events Pagination API (Website Endpoint) ==========

    async def get_events_paginated(
        self,
        tag_slug: Optional[str] = None,
        limit: int = 20,
        order: str = "volume24hr",
        ascending: bool = False,
        cursor: Optional[str] = None,
    ) -> dict:
        """
        Get events using pagination endpoint (what the website uses).

        This is the PREFERRED endpoint for market discovery because it returns:
        - Real-time best_bid/best_ask for each market
        - volume_24h aggregated at event level
        - Cursor-based pagination for efficient fetching

        Args:
            tag_slug: Filter by category (sports, politics, crypto, finance, etc.)
            limit: Results per page (default: 20, max varies)
            order: Sort field (volume24hr, startDate, endDate, liquidity)
            ascending: Sort direction (default: False = descending)
            cursor: Pagination cursor from previous response

        Returns:
            Dict with 'data' (list of events) and 'cursor' (for next page)

        Raises:
            MarketDataError: If request fails
        """
        try:
            params = {
                "limit": limit,
                "order": order,
                "ascending": str(ascending).lower(),
                "active": "true",
                "closed": "false",
            }

            if tag_slug:
                params["tag_slug"] = tag_slug
            if cursor:
                params["cursor"] = cursor

            response = await self.get(
                "/events/pagination",
                params=params,
                rate_limit_key="GET:/events/pagination"
            )

            # Response format: {"data": [...events...], "cursor": "..."}
            data = response.get("data", [])
            next_cursor = response.get("cursor")

            logger.info(f"Fetched {len(data)} events via pagination (tag={tag_slug})")
            return {"data": data, "cursor": next_cursor}

        except MarketDataError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch events pagination: {e}")
            raise MarketDataError(f"Failed to fetch events pagination: {e}")

    async def get_high_volume_events(
        self,
        min_volume_24h: float = 10000,
        tag_slugs: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Event]:
        """
        Get high-volume events with real-time market data.

        This is the PRIMARY method for finding tradeable markets. It:
        1. Uses /events/pagination (website endpoint) for fresh data
        2. Returns best_bid/best_ask for each market
        3. Filters by 24h volume (actual trading activity)

        Args:
            min_volume_24h: Minimum 24h volume in USD (default: $10k)
            tag_slugs: Categories to scan (default: all popular categories)
            limit: Max events to return

        Returns:
            List of Event objects with nested Market objects containing
            best_bid, best_ask, spread fields populated.

        Example:
            >>> events = await gamma.get_high_volume_events(min_volume_24h=50000)
            >>> for event in events:
            ...     for market in event.markets:
            ...         if market.best_bid and market.best_ask:
            ...             spread = float(market.best_ask) - float(market.best_bid)
            ...             print(f"{market.question}: spread={spread:.3f}")
        """
        if tag_slugs is None:
            # Popular categories with active trading
            tag_slugs = ["sports", "politics", "crypto", "finance", "science"]

        all_events = []
        seen_ids = set()

        for tag_slug in tag_slugs:
            cursor = None
            fetched = 0

            while fetched < limit:
                result = await self.get_events_paginated(
                    tag_slug=tag_slug,
                    limit=min(50, limit - fetched),
                    order="volume24hr",
                    ascending=False,
                    cursor=cursor,
                )

                events_data = result.get("data", [])
                cursor = result.get("cursor")

                if not events_data:
                    break

                for e_data in events_data:
                    event_id = e_data.get("id")
                    if event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)

                    # Check volume threshold
                    volume_24h = float(e_data.get("volume24hr", 0) or 0)
                    if volume_24h < min_volume_24h:
                        continue

                    # Parse nested markets with bid/ask data
                    nested_markets = []
                    for m_data in e_data.get("markets", []):
                        try:
                            market = Market(
                                id=m_data.get("id", ""),
                                question=m_data.get("question", ""),
                                slug=m_data.get("slug", ""),
                                condition_id=m_data.get("conditionId", ""),
                                category=e_data.get("category", ""),
                                outcomes=m_data.get("outcomes", []),
                                outcome_prices=m_data.get("outcomePrices", []),
                                tokens=m_data.get("clobTokenIds") or m_data.get("tokens"),
                                volume=float(m_data.get("volumeNum", 0) or 0),
                                liquidity=float(m_data.get("liquidityNum", 0) or 0),
                                active=m_data.get("active", False),
                                closed=m_data.get("closed", False),
                                start_date=m_data.get("startDate"),
                                end_date=m_data.get("endDate"),
                                # CRITICAL: Real-time bid/ask from pagination endpoint
                                best_bid=m_data.get("bestBid"),
                                best_ask=m_data.get("bestAsk"),
                                spread=m_data.get("spread"),
                                last_trade_price=m_data.get("lastTradePrice"),
                                # Trading constraints
                                accepting_orders=m_data.get("acceptingOrders"),
                                neg_risk=m_data.get("negRisk"),
                                # Rewards
                                rewards_min_size=m_data.get("rewardsMinSize"),
                                rewards_max_spread=m_data.get("rewardsMaxSpread"),
                            )
                            nested_markets.append(market)
                        except Exception as e:
                            logger.debug(f"Failed to parse market: {e}")
                            continue

                    event = Event(
                        id=event_id,
                        slug=e_data.get("slug", ""),
                        title=e_data.get("title", ""),
                        description=e_data.get("description"),
                        ticker=e_data.get("ticker"),
                        active=e_data.get("active", False),
                        closed=e_data.get("closed", False),
                        archived=e_data.get("archived", False),
                        new=e_data.get("new"),
                        featured=e_data.get("featured"),
                        restricted=e_data.get("restricted"),
                        start_date=e_data.get("startDate"),
                        end_date=e_data.get("endDate"),
                        markets=nested_markets,
                        neg_risk=e_data.get("negRisk"),
                        # Event-level volume (24h)
                        volume=float(e_data.get("volume", 0) or 0),
                        liquidity=float(e_data.get("liquidity", 0) or 0),
                    )
                    all_events.append(event)
                    fetched += 1

                if not cursor:
                    break

        # Sort by volume descending
        all_events.sort(key=lambda e: e.volume, reverse=True)

        logger.info(
            f"Found {len(all_events)} high-volume events "
            f"(min_volume_24h=${min_volume_24h:,.0f})"
        )
        return all_events[:limit]

    def extract_tradeable_markets(
        self,
        events: List[Event],
        min_spread: float = 0.0,
        max_spread: float = 0.15,
        min_price: float = 0.10,
        max_price: float = 0.90,
        min_days_to_resolution: int = 3,
    ) -> List[Market]:
        """
        Extract tradeable markets from events with spread/price filtering.

        Filters markets to find optimal trading opportunities:
        - Mid-range prices (avoid near-certainty markets)
        - Reasonable spreads (too wide = illiquid, too tight = no profit)
        - Has valid bid/ask quotes
        - Not closed or about to resolve (avoid resolution risk)

        Args:
            events: List of events from get_high_volume_events()
            min_spread: Minimum spread in dollars (default: $0.00)
            max_spread: Maximum spread in dollars (default: $0.15)
            min_price: Minimum bid price (default: 0.10 = 10%)
            max_price: Maximum ask price (default: 0.90 = 90%)
            min_days_to_resolution: Minimum days until resolution (default: 3)

        Returns:
            List of Market objects sorted by spread (widest first = most profit)

        Example:
            >>> events = await gamma.get_high_volume_events()
            >>> markets = gamma.extract_tradeable_markets(
            ...     events, min_spread=0.01, max_spread=0.10
            ... )
            >>> for m in markets[:5]:
            ...     print(f"{m.question}: spread={m.spread}")
        """
        from datetime import datetime, timezone, timedelta

        tradeable = []
        now = datetime.now(timezone.utc)
        min_end_date = now + timedelta(days=min_days_to_resolution)

        for event in events:
            for market in event.markets:
                # Skip closed markets (already resolved or no longer trading)
                if market.closed:
                    continue

                # Skip markets not accepting orders
                if hasattr(market, 'accepting_orders') and market.accepting_orders is False:
                    continue

                # Skip markets about to resolve (resolution risk)
                if market.end_date:
                    try:
                        if isinstance(market.end_date, str):
                            end_dt = datetime.fromisoformat(
                                market.end_date.replace("Z", "+00:00")
                            )
                        else:
                            end_dt = market.end_date

                        if end_dt < min_end_date:
                            logger.debug(
                                f"Skipping {market.question[:30]}...: resolves in "
                                f"{(end_dt - now).days} days (min: {min_days_to_resolution})"
                            )
                            continue
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Failed to parse end_date: {e}")

                # Skip markets without bid/ask
                if not market.best_bid or not market.best_ask:
                    continue

                try:
                    bid = float(market.best_bid)
                    ask = float(market.best_ask)

                    # Validate bid/ask
                    if bid <= 0 or ask <= 0 or ask <= bid:
                        continue

                    spread = ask - bid

                    # Price range filter (avoid near-certainty)
                    if bid < min_price or ask > max_price:
                        continue

                    # Spread filter
                    if spread < min_spread or spread > max_spread:
                        continue

                    # Must have token for trading
                    if not market.tokens:
                        continue

                    # Update spread field
                    market.spread = str(spread)
                    tradeable.append(market)

                except (ValueError, TypeError):
                    continue

        # Sort by spread descending (widest = most profit potential)
        tradeable.sort(key=lambda m: float(m.spread or 0), reverse=True)

        logger.info(
            f"Extracted {len(tradeable)} tradeable markets "
            f"(spread ${min_spread:.2f}-${max_spread:.2f}, "
            f"price {min_price:.0%}-{max_price:.0%})"
        )
        return tradeable

    async def get_tags(self) -> List[dict]:
        """
        Get available tags.

        Returns:
            List of tags

        Raises:
            MarketDataError: If request fails
        """
        try:
            response = await self.get(
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

    async def search_markets(self, query: str, limit: int = 20) -> List[Market]:
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

            response = await self.get(
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
    async def get_all_current_markets(self, limit: int = 100) -> List[Market]:
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
            batch = await self.get_markets(
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

    async def get_clob_tradable_markets(self, limit: int = 100) -> List[Market]:
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
        markets = await self.get_markets(active=True, closed=False, limit=limit)

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

    async def get_all_tradeable_events(self, limit: int = 100) -> List[Event]:
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
        all_events = await self.get_events(limit=limit)
        return self.filter_events_for_trading(all_events)

    # ========== 15-Minute Crypto Markets (BTC, ETH, SOL, XRP) ==========

    # Supported 15-min crypto assets
    CRYPTO_15MIN_ASSETS = ["btc", "eth", "sol", "xrp"]

    async def get_15min_crypto_markets(
        self,
        assets: Optional[List[str]] = None,
        slots_ahead: int = 8,
        slots_behind: int = 1,
    ) -> List[Event]:
        """
        Discover 15-minute crypto Up/Down markets.

        These markets resolve every 15 minutes and are NOT included in
        the standard /events API. They must be fetched by constructing
        slugs from timestamps.

        Slug pattern: {asset}-updown-15m-{unix_timestamp}
        Timestamp is rounded to 15-minute boundaries (unix // 900 * 900).

        Available assets: BTC, ETH, SOL, XRP

        Args:
            assets: List of assets to fetch (default: all supported)
            slots_ahead: Number of future 15-min slots to fetch (default: 8 = 2 hours)
            slots_behind: Number of past slots to fetch for active markets (default: 1)

        Returns:
            List of Event objects with nested Market objects

        Example:
            >>> gamma = GammaAPI(settings)
            >>> events = await gamma.get_15min_crypto_markets(assets=["btc", "eth"])
            >>> for event in events:
            ...     print(f"{event.title} ends at {event.end_date}")
        """
        import time

        if assets is None:
            assets = self.CRYPTO_15MIN_ASSETS

        # Normalize asset names to lowercase
        assets = [a.lower() for a in assets]

        # Calculate 15-min slot boundaries
        now = int(time.time())
        current_slot = (now // 900) * 900

        # Generate slots to fetch
        slots = []
        for i in range(-slots_behind, slots_ahead + 1):
            slots.append(current_slot + (i * 900))

        logger.info(
            f"Fetching 15-min crypto markets for {assets}, "
            f"{len(slots)} slots ({slots_behind} past, {slots_ahead} future)"
        )

        events = []
        for asset in assets:
            for slot_ts in slots:
                slug = f"{asset}-updown-15m-{slot_ts}"
                try:
                    # Fetch event by slug
                    response = await self.get(
                        "/events",
                        params={"slug": slug},
                        rate_limit_key="GET:/events"
                    )

                    if not response or len(response) == 0:
                        continue

                    data = response[0]

                    # Parse nested markets
                    nested_markets = []
                    for m_data in data.get("markets", []):
                        try:
                            market = Market(
                                id=m_data.get("id", ""),
                                question=m_data.get("question", ""),
                                slug=m_data.get("slug", ""),
                                condition_id=m_data.get("conditionId", ""),
                                category="Crypto",
                                outcomes=m_data.get("outcomes", []),
                                outcome_prices=m_data.get("outcomePrices", []),
                                tokens=m_data.get("clobTokenIds") or m_data.get("tokens"),
                                volume=float(m_data.get("volumeNum", 0) or 0),
                                liquidity=float(m_data.get("liquidityNum", 0) or 0),
                                active=m_data.get("active", False),
                                closed=m_data.get("closed", False),
                                start_date=m_data.get("startDate"),
                                end_date=m_data.get("endDate"),
                            )
                            nested_markets.append(market)
                        except Exception as e:
                            logger.debug(f"Failed to parse 15-min market: {e}")
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
                        markets=nested_markets,
                        neg_risk=data.get("negRisk")
                    )
                    events.append(event)

                except Exception as e:
                    logger.debug(f"No market for slug {slug}: {e}")
                    continue

        logger.info(f"Found {len(events)} 15-min crypto markets")
        return events

    async def get_15min_markets_expiring_soon(
        self,
        within_seconds: int = 120,
        assets: Optional[List[str]] = None,
    ) -> List[Event]:
        """
        Get 15-min crypto markets expiring within specified time window.

        Optimized for Resolution Sniper strategy which needs markets
        close to resolution.

        Args:
            within_seconds: Time window in seconds (default: 120 = 2 minutes)
            assets: List of assets (default: all supported)

        Returns:
            List of Event objects expiring soon, sorted by end_date

        Example:
            >>> events = await gamma.get_15min_markets_expiring_soon(within_seconds=60)
            >>> # Returns markets resolving in next 60 seconds
        """
        from datetime import datetime, timezone

        # Fetch markets (only need current and next slot)
        events = await self.get_15min_crypto_markets(
            assets=assets,
            slots_ahead=2,  # Current + next slot
            slots_behind=0   # No past slots needed
        )

        now = datetime.now(timezone.utc)
        expiring_soon = []

        for event in events:
            if not event.end_date:
                continue

            try:
                if isinstance(event.end_date, str):
                    end_dt = datetime.fromisoformat(
                        event.end_date.replace("Z", "+00:00")
                    )
                else:
                    end_dt = event.end_date

                seconds_until = (end_dt - now).total_seconds()

                # Check if within window and not already expired
                if 0 < seconds_until <= within_seconds:
                    expiring_soon.append(event)

            except (ValueError, TypeError) as e:
                logger.debug(f"Failed to parse end_date for {event.slug}: {e}")
                continue

        # Sort by end_date (soonest first)
        expiring_soon.sort(
            key=lambda e: e.end_date if e.end_date else "",
        )

        logger.info(
            f"Found {len(expiring_soon)} 15-min markets expiring within {within_seconds}s"
        )
        return expiring_soon

    async def get_public_profile(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Get public profile for a wallet address.

        Returns profile metadata including name, bio, profile image,
        account creation date, and verification status.

        Args:
            address: Wallet address (proxy wallet)

        Returns:
            Profile dict or None if not found

        Raises:
            MarketDataError: If request fails with non-404 error
        """
        if not address:
            return None

        try:
            response = await self.get(
                "/public-profile",
                params={"address": address.lower()},
                rate_limit_key="GET:/public-profile",
                retry=False
            )

            return self._parse_public_profile_response(response)

        except APIError as e:
            if e.status_code == 404:
                return None
            raise MarketDataError(f"Failed to fetch public profile: {e}")
        except MarketDataError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch public profile for {address}: {e}")
            raise MarketDataError(f"Failed to fetch public profile: {e}")

    @staticmethod
    def _parse_public_profile_response(response: Any) -> Optional[Dict[str, Any]]:
        """Normalize Gamma public-profile response into a single dict."""
        if isinstance(response, dict):
            return response
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict):
                    return item
        return None
