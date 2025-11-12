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

from typing import Optional, List, Dict, Any, Tuple
import logging
from decimal import Decimal
import hashlib
import json

from .base import BaseAPIClient
from ..utils.numeric import to_decimal
from ..config import PolymarketSettings
from ..models import OrderBook as OrderBookType
from ..exceptions import (
    PriceUnavailableError,
    OrderBookError,
    MarketNotFoundError
)
from ..utils.rate_limiter import RateLimiter
from ..utils.retry import CircuitBreaker

logger = logging.getLogger(__name__)


class PublicCLOBAPI(BaseAPIClient):
    """
    Public CLOB API client for market data (no authentication required).

    All methods in this class access public endpoints and don't require
    API credentials or wallet signatures.

    Usage:
        >>> from shared.polymarket.api.clob_public import PublicCLOBAPI
        >>> from shared.polymarket.config import PolymarketSettings
        >>>
        >>> settings = PolymarketSettings()
        >>> client = PublicCLOBAPI(settings)
        >>>
        >>> # Get orderbook
        >>> orderbook = client.get_orderbook(token_id)
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
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize Public CLOB API client.

        Args:
            settings: Client settings
            rate_limiter: Optional rate limiter
            circuit_breaker: Optional circuit breaker
        """
        super().__init__(
            base_url=settings.clob_url,
            settings=settings,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker
        )

    # ========== Health & System ==========

    def get_ok(self) -> bool:
        """
        Health check endpoint.

        Rate limit: 50 req/10s

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            # get() method will raise exception on error, so reaching here means success
            self.get("/", retry=False)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def get_server_time(self) -> int:
        """
        Get current server timestamp.

        Rate limit: 5,000 req/10s (general)

        Returns:
            Server timestamp in milliseconds
        """
        response = self.get("/time")
        return int(response.get("timestamp", 0))

    # ========== Pricing & Spreads ==========

    def get_midpoint(self, token_id: str) -> Optional[Decimal]:
        """
        Get midpoint price for a token.

        Rate limit: 200 req/10s

        Args:
            token_id: Token ID

        Returns:
            Midpoint price, or None if unavailable
        """
        try:
            response = self.get(
                "/midpoint",
                params={"token_id": token_id}
            )

            mid = response.get("mid")
            if mid is None:
                logger.warning(f"No midpoint data for token {token_id}")
                return None

            return to_decimal(mid)

        except Exception as e:
            logger.error(f"Error fetching midpoint for {token_id}: {e}")
            raise PriceUnavailableError(f"Midpoint unavailable: {e}")

    def get_midpoints(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
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

            response = self.post(
                "/midpoints",
                json_data=params
            )

            # Parse response into dict
            result = {}
            for item in response:
                token_id = item.get("token_id")
                mid = item.get("mid")
                result[token_id] = to_decimal(mid) if mid is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch midpoints: {e}")
            return {tid: None for tid in token_ids}

    def get_price(self, token_id: str, side: str) -> Optional[Decimal]:
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
            response = self.get(
                "/price",
                params={"token_id": token_id, "side": side}
            )

            price = response.get("price")
            if price is None:
                logger.warning(f"No price data for token {token_id}, side={side}")
                return None

            return to_decimal(price)

        except Exception as e:
            logger.error(f"Error fetching price for {token_id}: {e}")
            raise PriceUnavailableError(f"Price unavailable: {e}")

    def get_prices(self, params: List[Dict[str, str]]) -> Dict[str, Optional[Decimal]]:
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
            response = self.post(
                "/prices",
                json_data=params
            )

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

    def get_spread(self, token_id: str) -> Optional[Decimal]:
        """
        Get bid-ask spread for a token.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            Spread (ask - bid), or None if unavailable
        """
        try:
            response = self.get(
                "/spread",
                params={"token_id": token_id}
            )

            spread = response.get("spread")
            if spread is None:
                logger.warning(f"No spread data for token {token_id}")
                return None

            return to_decimal(spread)

        except Exception as e:
            logger.error(f"Error fetching spread for {token_id}: {e}")
            return None

    def get_spreads(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
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

            response = self.post(
                "/spreads",
                json_data=params
            )

            result = {}
            for item in response:
                token_id = item.get("token_id")
                spread = item.get("spread")
                result[token_id] = to_decimal(spread) if spread is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch spreads: {e}")
            return {tid: None for tid in token_ids}

    # ========== Order Books ==========

    def get_orderbook(self, token_id: str) -> OrderBookType:
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
            response = self.get(
                "/book",
                params={"token_id": token_id}
            )

            # Parse bids and asks - MUST be tuples (price, size) per OrderBookType model
            bids = []
            for level in response.get("bids", []):
                price = to_decimal(level.get("price", 0))
                size = to_decimal(level.get("size", 0))
                if price and price > 0 and size and size > 0:
                    bids.append((price, size))

            asks = []
            for level in response.get("asks", []):
                price = to_decimal(level.get("price", 0))
                size = to_decimal(level.get("size", 0))
                if price and price > 0 and size and size > 0:
                    asks.append((price, size))

            return OrderBookType(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market=response.get("market"),
                tick_size=to_decimal(response.get("tick_size", "0.01")),
                neg_risk=response.get("neg_risk", False),
                timestamp=response.get("timestamp")
            )

        except Exception as e:
            logger.error(f"Error fetching orderbook for {token_id}: {e}")
            raise OrderBookError(f"Orderbook unavailable: {e}")

    def get_orderbooks_batch(
        self,
        token_ids: List[str]
    ) -> List[OrderBookType]:
        """
        Get orderbooks for multiple tokens (batch operation).

        Rate limit: 80 req/10s (much more efficient than individual calls)

        Args:
            token_ids: List of token IDs

        Returns:
            List of OrderBook objects
        """
        if not token_ids:
            return []

        try:
            params = [{"token_id": tid} for tid in token_ids]

            response = self.post(
                "/books",
                json_data=params
            )

            orderbooks = []
            for book_data in response:
                try:
                    # Parse bids and asks - MUST be tuples (price, size)
                    bids = []
                    for level in book_data.get("bids", []):
                        price = to_decimal(level.get("price", 0))
                        size = to_decimal(level.get("size", 0))
                        if price and price > 0 and size and size > 0:
                            bids.append((price, size))

                    asks = []
                    for level in book_data.get("asks", []):
                        price = to_decimal(level.get("price", 0))
                        size = to_decimal(level.get("size", 0))
                        if price and price > 0 and size and size > 0:
                            asks.append((price, size))

                    # Get token_id from book_data (should be in response)
                    token_id = book_data.get("asset_id", "")

                    orderbooks.append(OrderBookType(
                        token_id=token_id,
                        bids=bids,
                        asks=asks,
                        market=book_data.get("market"),
                        tick_size=to_decimal(book_data.get("tick_size", "0.01")),
                        neg_risk=book_data.get("neg_risk", False),
                        timestamp=book_data.get("timestamp")
                    ))
                except Exception as e:
                    logger.warning(f"Error parsing orderbook in batch: {e}")
                    continue

            return orderbooks

        except Exception as e:
            logger.error(f"Error fetching batch orderbooks: {e}")
            return []

    def get_order_book_hash(self, orderbook: OrderBookType) -> str:
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
        book_str = json.dumps({
            "market": orderbook.market,
            "token_id": orderbook.token_id,
            "bids": [[str(b[0]), str(b[1])] for b in orderbook.bids],  # b[0]=price, b[1]=size
            "asks": [[str(a[0]), str(a[1])] for a in orderbook.asks],  # a[0]=price, a[1]=size
            "timestamp": str(orderbook.timestamp) if orderbook.timestamp else ""
        }, sort_keys=True)

        return hashlib.sha256(book_str.encode()).hexdigest()

    # ========== Market Metadata ==========

    def get_tick_size(self, token_id: str) -> Decimal:
        """
        Get minimum tick size for a token.

        Rate limit: 50 req/10s

        Args:
            token_id: Token ID

        Returns:
            Tick size (usually Decimal("0.01"))
        """
        try:
            response = self.get(
                "/tick_size",
                params={"token_id": token_id}
            )

            tick_size = response.get("minimum_tick_size", "0.01")
            return to_decimal(tick_size)

        except Exception as e:
            logger.warning(f"Error fetching tick size for {token_id}: {e}, using default 0.01")
            return Decimal("0.01")

    def get_neg_risk(self, token_id: str) -> bool:
        """
        Check if token is in a neg-risk market.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            True if neg-risk enabled, False otherwise
        """
        try:
            response = self.get(
                "/neg_risk",
                params={"token_id": token_id}
            )

            return bool(response.get("neg_risk", False))

        except Exception as e:
            logger.warning(f"Error fetching neg_risk for {token_id}: {e}, assuming False")
            return False

    def get_fee_rate_bps(self, token_id: str) -> int:
        """
        Get fee rate in basis points for a token.

        Rate limit: 5,000 req/10s (general)

        Note: Polymarket currently has 0 trading fees.

        Args:
            token_id: Token ID

        Returns:
            Fee rate in basis points (0 for Polymarket)
        """
        try:
            response = self.get(
                "/fee_rate",
                params={"token_id": token_id}
            )

            return int(response.get("fee_rate_bps", 0))

        except Exception as e:
            logger.warning(f"Error fetching fee rate for {token_id}: {e}, assuming 0")
            return 0

    # ========== Market Listings ==========

    def get_simplified_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
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
            response = self.get(
                "/simplified_markets",
                params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching simplified markets: {e}")
            return {"data": [], "next_cursor": ""}

    def get_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
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
            response = self.get(
                "/markets",
                params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return {"data": [], "next_cursor": ""}

    def get_sampling_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get sampling market list.

        Rate limit: 5,000 req/10s (general)

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            Market data with pagination
        """
        try:
            response = self.get(
                "/sampling_markets",
                params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching sampling markets: {e}")
            return {"data": [], "next_cursor": ""}

    def get_sampling_simplified_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get sampling simplified market list.

        Rate limit: 5,000 req/10s (general)

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            Simplified market data with pagination
        """
        try:
            response = self.get(
                "/sampling_simplified_markets",
                params={"next_cursor": next_cursor}
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching sampling simplified markets: {e}")
            return {"data": [], "next_cursor": ""}

    def get_market(self, condition_id: str) -> Dict[str, Any]:
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
            response = self.get(
                f"/markets/{condition_id}"
            )

            return response

        except Exception as e:
            logger.error(f"Error fetching market {condition_id}: {e}")
            raise MarketNotFoundError(f"Market not found: {condition_id}")

    def get_market_trades_events(self, condition_id: str) -> List[Dict[str, Any]]:
        """
        Get trade events for a market.

        Rate limit: 5,000 req/10s (general)

        Args:
            condition_id: Market condition ID

        Returns:
            List of trade event dictionaries
        """
        try:
            response = self.get(
                f"/market_trades_events/{condition_id}"
            )

            # Response is a list of trade events
            return response if isinstance(response, list) else []

        except Exception as e:
            logger.error(f"Error fetching market trades events: {e}")
            return []

    # ========== Trade History ==========

    def get_last_trade_price(self, token_id: str) -> Optional[Decimal]:
        """
        Get last trade price for a token.

        Rate limit: 5,000 req/10s (general)

        Args:
            token_id: Token ID

        Returns:
            Last trade price, or None if no trades
        """
        try:
            response = self.get(
                "/last_trade_price",
                params={"token_id": token_id}
            )

            price = response.get("price")
            if price is None:
                return None

            return to_decimal(price)

        except Exception as e:
            logger.error(f"Error fetching last trade price for {token_id}: {e}")
            return None

    def get_last_trades_prices(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
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

            response = self.post(
                "/last_trades_prices",
                json_data=params
            )

            result = {}
            for item in response:
                token_id = item.get("token_id")
                price = item.get("price")
                result[token_id] = to_decimal(price) if price is not None else None

            return result

        except Exception as e:
            logger.error(f"Error fetching batch last trade prices: {e}")
            return {tid: None for tid in token_ids}

    # ========== Derived Methods (Convenience) ==========

    def get_best_bid_ask(self, token_id: str) -> Optional[Tuple[Decimal, Decimal]]:
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
            orderbook = self.get_orderbook(token_id)

            if not orderbook.bids or not orderbook.asks:
                logger.warning(f"Empty orderbook for token {token_id}")
                return None

            # Bids and asks are tuples (price, size)
            best_bid = orderbook.bids[0][0]  # First element is price
            best_ask = orderbook.asks[0][0]  # First element is price

            return (best_bid, best_ask)

        except Exception as e:
            logger.error(f"Error getting best bid/ask for {token_id}: {e}")
            return None

    def get_liquidity_depth(
        self,
        token_id: str,
        price_range: Decimal = Decimal("0.05")
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
            orderbook = self.get_orderbook(token_id)

            if not orderbook.bids or not orderbook.asks:
                return {
                    "bid_depth": Decimal("0"),
                    "ask_depth": Decimal("0"),
                    "bid_levels": 0,
                    "ask_levels": 0,
                    "total_depth": Decimal("0")
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
                "total_depth": bid_depth + ask_depth
            }

        except Exception as e:
            logger.error(f"Error calculating liquidity depth for {token_id}: {e}")
            return {
                "bid_depth": Decimal("0"),
                "ask_depth": Decimal("0"),
                "bid_levels": 0,
                "ask_levels": 0,
                "total_depth": Decimal("0")
            }
