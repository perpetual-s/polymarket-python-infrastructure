"""
CLOB API client for trading operations.

Handles order placement, cancellation, and account queries.
Adapted from py-clob-client (MIT License).
"""

from typing import Optional, List, Dict, Any
import json
import logging
import time
from decimal import Decimal

from .base import BaseAPIClient
from ..utils.numeric import to_decimal
from ..config import PolymarketSettings
from ..models import (
    Order,
    OrderResponse,
    OrderStatus,
    Balance,
    OrderBook as OrderBookType
)
from ..exceptions import (
    TradingError,
    OrderRejectedError,
    PriceUnavailableError,
    InsufficientBalanceError,
    TickSizeError,
    InsufficientAllowanceError,
    OrderDelayedError,
    OrderExpiredError,
    FOKNotFilledError,
    InvalidOrderError,
    MarketNotReadyError,
    AuthenticationError
)
from ..auth.authenticator import Authenticator
from ..utils.rate_limiter import RateLimiter
from ..utils.retry import CircuitBreaker

logger = logging.getLogger(__name__)


class CLOBAPI(BaseAPIClient):
    """
    CLOB API client for trading operations.

    Requires L2 authentication for all operations.
    """

    def __init__(
        self,
        settings: PolymarketSettings,
        authenticator: Authenticator,
        rate_limiter: Optional[RateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize CLOB API client.

        Args:
            settings: Client settings
            authenticator: Authenticator for L2 headers
            rate_limiter: Optional rate limiter
            circuit_breaker: Optional circuit breaker
        """
        super().__init__(
            base_url=settings.clob_url,
            settings=settings,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker
        )
        self.authenticator = authenticator

    def _create_l2_headers(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        method: str,
        path: str,
        body: str = ""
    ) -> Dict[str, str]:
        """Create L2 authentication headers."""
        return self.authenticator.create_l2_headers(
            address=address,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            method=method,
            path=path,
            body=body
        )

    # ========== Health & System (Read-Only) ==========

    async def get_ok(self) -> bool:
        """
        Health check endpoint.

        Confirms CLOB server is operational.
        Does not require authentication.

        Returns:
            True if server is up

        Raises:
            TradingError: If server unreachable

        Example:
            >>> if clob.get_ok():
            ...     print("CLOB server operational")
        """
        try:
            response = await self.get(
                "/",
                rate_limit_key="GET:/",
                retry=False  # Don't retry health checks
            )
            # API returns "OK" as JSON string, not {"ok": true}
            if isinstance(response, str):
                return response.upper() == "OK"
            return response.get("ok", False) or True
        except Exception as e:
            logger.error(f"CLOB health check failed: {e}")
            raise TradingError(f"CLOB server unavailable: {e}")

    async def get_server_time(self) -> int:
        """
        Get current server timestamp.

        Use for GTD order validation and clock synchronization.
        Does not require authentication.

        Returns:
            UNIX timestamp in milliseconds

        Raises:
            TradingError: If request fails

        Example:
            >>> server_time = clob.get_server_time()
            >>> import time
            >>> local_time = int(time.time() * 1000)
            >>> drift_ms = abs(server_time - local_time)
            >>> if drift_ms > 5000:
            ...     print(f"Clock drift: {drift_ms}ms")
        """
        try:
            response = await self.get(
                "/time",
                rate_limit_key="GET:/time",
                retry=True
            )

            # CRITICAL FIX (Bug #53): Handle both response formats and convert to milliseconds
            # Polymarket API returns timestamp directly as int, not as {"timestamp": int}
            # API returns seconds, but nonces must be in milliseconds
            if isinstance(response, int):
                timestamp = response
            elif isinstance(response, dict):
                timestamp = response.get("timestamp")
                if timestamp is None:
                    raise TradingError("Server time response missing timestamp")
                timestamp = int(timestamp)
            else:
                raise TradingError(f"Unexpected server time response type: {type(response)}")

            # Convert to milliseconds if needed (check if it's in seconds)
            # Timestamp in seconds is ~1.7B (10 digits), in milliseconds is ~1.7T (13 digits)
            if timestamp < 10_000_000_000:  # Less than 10 billion = seconds
                timestamp = timestamp * 1000

            return timestamp

        except Exception as e:
            logger.error(f"Failed to get server time: {e}")
            raise TradingError(f"Server time fetch failed: {e}")

    # ========== Market Data (Read-Only) ==========

    async def get_simplified_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get simplified market data with pagination.

        Lightweight market list without full details.
        Does not require authentication.

        Args:
            next_cursor: Pagination cursor (default: "MA==")

        Returns:
            Dict with 'data' (list of markets) and 'next_cursor' fields

        Raises:
            TradingError: If request fails

        Example:
            >>> # Get first page
            >>> response = clob.get_simplified_markets()
            >>> markets = response['data']
            >>> next_cursor = response.get('next_cursor')
            >>>
            >>> # Get next page if available
            >>> if next_cursor and next_cursor != "LTE=":
            ...     more_markets = clob.get_simplified_markets(next_cursor)
        """
        try:
            response = await self.get(
                "/simplified-markets",
                params={"next_cursor": next_cursor},
                rate_limit_key="GET:/simplified-markets",
                retry=True
            )

            logger.debug(f"Fetched simplified markets (cursor: {next_cursor})")
            return response

        except Exception as e:
            logger.error(f"Failed to get simplified markets: {e}")
            raise TradingError(f"Simplified markets fetch failed: {e}")

    async def get_midpoint(self, token_id: str) -> Optional[Decimal]:
        """
        Get midpoint price for token.

        Args:
            token_id: Token ID

        Returns:
            Midpoint price (Decimal) or None if unavailable

        Raises:
            PriceUnavailableError: If price cannot be fetched
        """
        try:
            response = await self.get(
                "/midpoint",
                params={"token_id": token_id},
                rate_limit_key="GET:/midpoint",
                retry=True
            )

            mid = response.get("mid")
            if mid is None:
                logger.warning(f"No midpoint for token {token_id}")
                return None

            price = to_decimal(mid)
            logger.debug(f"Midpoint for {token_id}: {price}")
            return price

        except Exception as e:
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get midpoint: {e}",
                token_id=token_id
            )

    async def get_price(self, token_id: str, side: str) -> Optional[Decimal]:
        """
        Get price for token on specific side.

        Args:
            token_id: Token ID
            side: BUY or SELL

        Returns:
            Price (Decimal) or None if unavailable

        Raises:
            PriceUnavailableError: If price cannot be fetched
        """
        try:
            response = await self.get(
                "/price",
                params={"token_id": token_id, "side": side},
                rate_limit_key="GET:/price",
                retry=True
            )

            price_str = response.get("price")
            if price_str is None:
                logger.warning(f"No {side} price for token {token_id}")
                return None

            price = to_decimal(price_str)
            logger.debug(f"{side} price for {token_id}: {price}")
            return price

        except Exception as e:
            logger.error(f"Failed to get {side} price for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get price: {e}",
                token_id=token_id
            )

    async def get_last_trade_price(self, token_id: str) -> Optional[Decimal]:
        """
        Get last trade price for token.

        Faster than fetching full orderbook when you only need the last price.
        Does not require authentication.

        Args:
            token_id: Token ID

        Returns:
            Last trade price (Decimal) or None if no recent trades

        Raises:
            PriceUnavailableError: If request fails

        Example:
            >>> # Fast price check (no orderbook overhead)
            >>> price = clob.get_last_trade_price("123456")
            >>> if price and price > Decimal("0.50"):
            ...     print(f"Above threshold: {price}")
        """
        try:
            response = await self.get(
                "/last-trade-price",
                params={"token_id": token_id},
                rate_limit_key="GET:/last-trade-price",
                retry=True
            )

            price_str = response.get("price")
            if price_str is None:
                logger.warning(f"No last trade price for token {token_id}")
                return None

            price = to_decimal(price_str)
            logger.debug(f"Last trade price for {token_id}: {price}")
            return price

        except Exception as e:
            logger.error(f"Failed to get last trade price for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get last trade price: {e}",
                token_id=token_id
            )

    async def get_last_trades_prices(self, token_ids: List[str]) -> Dict[str, Optional[Decimal]]:
        """
        Get last trade prices for multiple tokens (batch endpoint).

        More efficient than calling get_last_trade_price() individually.
        Does not require authentication.

        Args:
            token_ids: List of token IDs

        Returns:
            Dict mapping token_id to last trade price

        Raises:
            TradingError: If request fails

        Example:
            >>> token_ids = ["123", "456", "789"]
            >>> prices = clob.get_last_trades_prices(token_ids)
            >>> for tid, price in prices.items():
            ...     print(f"{tid}: ${price}")
        """
        if not token_ids:
            return {}

        try:
            body = [{"token_id": tid} for tid in token_ids]

            response = await self.post(
                "/last-trades-prices",
                json_data=body,
                rate_limit_key="POST:/last-trades-prices",
                retry=True
            )

            results = {}
            for item in response:
                token_id = item.get("token_id")
                price_str = item.get("price")

                if token_id:
                    price = to_decimal(price_str) if price_str is not None else None
                    results[token_id] = price

            logger.info(f"Fetched {len(results)}/{len(token_ids)} last trade prices")
            return results

        except Exception as e:
            logger.error(f"Failed to fetch batch last trade prices: {e}")
            raise TradingError(f"Batch last trade price fetch failed: {e}")

    async def get_orderbook(self, token_id: str) -> OrderBookType:
        """
        Get order book for token.

        Args:
            token_id: Token ID

        Returns:
            Order book

        Raises:
            TradingError: If request fails
        """
        try:
            response = await self.get(
                "/book",
                params={"token_id": token_id},
                rate_limit_key="GET:/book",
                retry=True
            )

            # Parse bids and asks (convert to Decimal for precision)
            # CRITICAL: Polymarket API returns bids LOW→HIGH and asks HIGH→LOW
            # We need: bids HIGH→LOW (best bid first), asks LOW→HIGH (best ask first)
            bids = []
            for bid in response.get("bids", []):
                price_str = bid.get("price", "0")
                size_str = bid.get("size", "0")
                price = Decimal(str(price_str))
                size = Decimal(str(size_str))
                bids.append((price, size))

            asks = []
            for ask in response.get("asks", []):
                price_str = ask.get("price", "0")
                size_str = ask.get("size", "0")
                price = Decimal(str(price_str))
                size = Decimal(str(size_str))
                asks.append((price, size))

            # Sort: bids descending (best=highest first), asks ascending (best=lowest first)
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            orderbook = OrderBookType(
                token_id=token_id,
                bids=bids,
                asks=asks
            )

            logger.debug(
                f"Order book for {token_id}: "
                f"best_bid={orderbook.best_bid}, best_ask={orderbook.best_ask}"
            )
            return orderbook

        except Exception as e:
            logger.error(f"Failed to get order book for {token_id}: {e}")
            raise TradingError(f"Failed to get order book: {e}")

    # ========== Trading Operations (Authenticated) ==========

    async def post_order(
        self,
        signed_order: Dict[str, Any],
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        order_type: str = "GTC"
    ) -> OrderResponse:
        """
        Post signed order to exchange.

        Args:
            signed_order: Signed order dict
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            order_type: Order type (GTC, FOK, etc.)

        Returns:
            Order response

        Raises:
            OrderRejectedError: If order is rejected
            InsufficientBalanceError: If insufficient balance
        """
        try:
            path = "/order"

            # CRITICAL: owner field must be API key, not wallet address (per py-clob-client)
            # CRITICAL FIX (Bug #49): Use official Python client format (3 fields only)
            # Official py-clob-client uses {"order", "owner", "orderType"} - NO deferExec field
            # TypeScript client includes deferExec, but Python client omits it (verified in utilities.py:37-38)
            body = {
                "order": signed_order,
                "owner": api_key,
                "orderType": order_type,
            }

            # CRITICAL FIX (Bug #49): Use stdlib json.dumps() for order payloads
            # The custom orjson serializer converts large ints to STRINGS, but the API expects them as INTEGERS
            # py_order_utils.SignedOrder.dict() keeps salt as int, which is correct per official py-clob-client
            #
            # We serialize manually with stdlib json to keep integers as integers,
            # then pass as data= with explicit Content-Type header (like official py-clob-client does with httpx json=)
            body_str = json.dumps(body)

            # DEBUG: Log the order payload for troubleshooting
            logger.error(f"🔍 POST /order payload: {body_str[:1000]}")  # First 1000 chars
            logger.error(f"🔍 Signed order keys: {list(signed_order.keys())}")
            logger.error(f"🔍 Order type: {order_type}, Owner: {api_key}, Address: {address}")

            # Create L2 headers with HMAC signature
            l2_headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="POST",
                path=path,
                body=body_str
            )

            # CRITICAL FIX: Add Content-Type header explicitly (like httpx does with json= parameter)
            # When using data= instead of json_data=, aiohttp doesn't auto-add Content-Type
            l2_headers["Content-Type"] = "application/json"

            response = await self.post(
                path,
                data=body_str,  # Use raw JSON string (not json_data which triggers custom serializer)
                headers=l2_headers,
                rate_limit_key="POST:/order",
                retry=False  # Don't auto-retry order submissions
            )

            # CRITICAL FIX: Validate response type before accessing
            if not isinstance(response, dict):
                raise TradingError(
                    f"Invalid order response format: expected dict, got {type(response).__name__}: {response}"
                )

            # Parse response
            success = response.get("success", False)
            error_msg = response.get("errorMsg")
            order_id = response.get("orderID")  # NOTE: Polymarket API uses 'orderID' not 'orderId'
            status = response.get("status")

            # Check for Polymarket-specific error codes
            if error_msg:
                error_upper = error_msg.upper()

                # Tick size violation
                if "MIN_TICK_SIZE" in error_upper or "TICK_SIZE" in error_upper:
                    raise TickSizeError(
                        f"Order price violates minimum tick size: {error_msg}"
                    )

                # Insufficient balance or allowance
                if "NOT_ENOUGH_BALANCE" in error_upper or "INSUFFICIENT" in error_upper:
                    # Check if it's an allowance issue
                    if "ALLOWANCE" in error_upper:
                        raise InsufficientAllowanceError(
                            f"Insufficient token allowance: {error_msg}"
                        )
                    else:
                        raise InsufficientBalanceError(
                            f"Insufficient balance: {error_msg}"
                        )

                # Order expiration issues
                if "EXPIRATION" in error_upper or "EXPIRED" in error_upper:
                    raise OrderExpiredError(
                        f"Order expiration invalid: {error_msg}"
                    )

                # FOK order not filled
                if "FOK" in error_upper and "NOT_FILLED" in error_upper:
                    raise FOKNotFilledError(
                        f"Fill-or-Kill order could not be filled: {error_msg}"
                    )

                # Order delayed
                if "ORDER_DELAYED" in error_upper or "DELAYED" in error_upper:
                    raise OrderDelayedError(
                        f"Order is delayed: {error_msg}",
                        order_id=order_id
                    )

                # CRITICAL: Additional production error codes
                if "SIZE_TOO_SMALL" in error_upper or "MINIMUM_SIZE" in error_upper:
                    raise InvalidOrderError(
                        f"Order size below minimum: {error_msg}"
                    )

                if "PRICE_OUT_OF_RANGE" in error_upper or "INVALID_PRICE" in error_upper:
                    raise InvalidOrderError(
                        f"Price out of valid range: {error_msg}"
                    )

                if "MARKET_CLOSED" in error_upper or "MARKET_NOT_ACTIVE" in error_upper:
                    raise MarketNotReadyError(
                        f"Market not accepting orders: {error_msg}"
                    )

                if "INVALID_SIGNATURE" in error_upper or "SIGNATURE_FAILED" in error_upper:
                    raise AuthenticationError(
                        f"Order signature invalid: {error_msg}"
                    )

                if "NONCE_TOO_LOW" in error_upper or "INVALID_NONCE" in error_upper:
                    raise OrderRejectedError(
                        f"Nonce conflict detected: {error_msg}",
                        order_id=order_id,
                        reason="NONCE_CONFLICT"
                    )

                if "ORDER_ALREADY_EXISTS" in error_upper or "DUPLICATE_ORDER" in error_upper:
                    raise OrderRejectedError(
                        f"Duplicate order detected: {error_msg}",
                        order_id=order_id,
                        reason="DUPLICATE"
                    )

                # Generic rejection for other errors
                if not success:
                    raise OrderRejectedError(
                        f"Order rejected: {error_msg}",
                        order_id=order_id,
                        reason=error_msg
                    )

            order_response = OrderResponse(
                success=success,
                order_id=order_id,
                status=OrderStatus(status) if status else None,
                error_msg=error_msg,
                order_hashes=response.get("orderHashes")
            )

            if success:
                logger.info(f"Order placed successfully: {order_id} ({status})")
            else:
                logger.warning(f"Order placement failed: {error_msg}")

            return order_response

        except (
            OrderRejectedError,
            InsufficientBalanceError,
            TickSizeError,
            InsufficientAllowanceError,
            OrderDelayedError,
            OrderExpiredError,
            FOKNotFilledError
        ):
            # Re-raise specific errors
            raise
        except Exception as e:
            logger.error(f"Failed to post order: {e}")
            raise TradingError(f"Failed to post order: {e}")

    async def cancel_order(
        self,
        order_id: str,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str
    ) -> bool:
        """
        Cancel single order.

        Args:
            order_id: Order ID to cancel
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase

        Returns:
            True if cancelled (or already gone/filled)

        Raises:
            TradingError: If cancellation fails (non-404 errors)

        Note:
            NOT_FOUND errors are treated as success since the order
            is already gone (cancelled/filled), which is the desired outcome.
        """
        try:
            # CRITICAL FIX: Official py-clob-client uses "/order" with body, NOT path param
            # See: py-clob-client/py_clob_client/client.py line 544-554
            path = "/order"
            body = {"orderID": order_id}  # NOTE: camelCase "orderID" per official API

            # Serialize body for HMAC signature
            body_str = json.dumps(body)

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="DELETE",
                path=path,
                body=body_str
            )

            # Add Content-Type header
            headers["Content-Type"] = "application/json"

            response = await self.delete(
                path,
                data=body_str,  # Use exact string that was signed
                headers=headers,
                rate_limit_key="DELETE:/order",
                retry=False
            )

            # API returns: {"canceled": ["order_id"], "not_canceled": {"order_id": "reason"}}
            canceled = response.get("canceled", [])
            not_canceled = response.get("not_canceled", {})

            if order_id in canceled:
                logger.info(f"Order cancelled: {order_id}")
                return True

            if order_id in not_canceled:
                error_msg = not_canceled[order_id]
                if "NOT_FOUND" in str(error_msg).upper():
                    # Order already gone = successful cancellation
                    logger.info(f"Order {order_id} already cancelled/filled (NOT_FOUND)")
                    return True
                raise TradingError(f"Cancel failed: {error_msg}")

            # Fallback: check legacy format or empty response
            if response.get("success", False):
                logger.info(f"Order cancelled: {order_id}")
                return True

            # If we get here with empty response but 200 status, assume success
            if not canceled and not not_canceled:
                logger.warning(f"Empty cancel response for {order_id}, assuming success")
                return True

            raise TradingError(f"Cancel failed: unexpected response {response}")

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise TradingError(f"Failed to cancel order: {e}")

    async def post_orders_batch(
        self,
        signed_orders: List[Dict[str, Any]],
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str
    ) -> List[OrderResponse]:
        """
        Post multiple signed orders in a single request.

        CRITICAL for Strategy-3: 10x faster than sequential orders.

        Args:
            signed_orders: List of signed order dicts
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase

        Returns:
            List of order responses (one per order)

        Raises:
            OrderRejectedError: If any order is rejected
            TradingError: If request fails

        Example:
            >>> orders = [builder.build_order(order1), builder.build_order(order2)]
            >>> responses = clob.post_orders_batch(orders, address, key, secret, passphrase)
            >>> successful = [r for r in responses if r.success]
        """
        try:
            path = "/orders"
            body = {
                "orders": signed_orders,
                "owner": address
            }

            # CRITICAL: Use the SAME JSON serializer for HMAC and request body
            # The session uses orjson with large int conversion, so we must too
            body_str = self.session._json_serialize(body)

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="POST",
                path=path,
                body=body_str
            )

            # FIX (Issue #9): Use data=body_str to ensure HMAC matches request body
            # json_data=body would re-serialize and potentially produce different JSON
            response = await self.post(
                path,
                data=body_str,
                headers=headers,
                rate_limit_key="POST:/orders",
                retry=False
            )

            # CRITICAL FIX: Validate response type before accessing
            if not isinstance(response, dict):
                raise TradingError(
                    f"Invalid batch order response format: expected dict, got {type(response).__name__}: {response}"
                )

            # Parse responses
            results = []
            for idx, order_response in enumerate(response.get("orders", [])):
                success = order_response.get("success", False)
                error_msg = order_response.get("errorMsg")
                order_id = order_response.get("orderId")
                status = order_response.get("status")

                results.append(OrderResponse(
                    success=success,
                    order_id=order_id,
                    status=OrderStatus(status) if status else None,
                    error_msg=error_msg
                ))

            logger.info(f"Batch order placement: {len(results)} orders, {sum(1 for r in results if r.success)} successful")
            return results

        except Exception as e:
            logger.error(f"Failed to post batch orders: {e}")
            raise TradingError(f"Batch order placement failed: {e}")

    async def get_orderbooks_batch(
        self,
        token_ids: List[str]
    ) -> Dict[str, OrderBookType]:
        """
        Get orderbooks for multiple tokens using native batch endpoint.

        Uses POST /books for 10x performance vs concurrent individual fetches.
        CRITICAL for Strategy-1 (spread farming) and Strategy-3 (copy trading).

        Args:
            token_ids: List of token IDs

        Returns:
            Dict mapping token_id to OrderBook

        Raises:
            TradingError: If request fails

        Example:
            >>> token_ids = ["123", "456", "789"]
            >>> books = clob.get_orderbooks_batch(token_ids)
            >>> best_ask = books["123"].best_ask
            >>> # 10x faster than individual fetches!
        """
        if not token_ids:
            return {}

        # Warn on very large batches (potential timeout)
        if len(token_ids) > 100:
            logger.warning(
                f"Large batch size ({len(token_ids)} tokens). "
                f"Consider splitting into smaller batches to avoid timeouts."
            )

        try:
            # Use native POST /books endpoint (official Polymarket batch API)
            body = [{"token_id": tid} for tid in token_ids]

            response = await self.post(
                "/books",
                json_data=body,
                rate_limit_key="POST:/books",
                retry=True
            )

            results = {}
            for book_data in response:
                token_id = book_data.get("asset_id")
                if not token_id:
                    logger.warning(f"Missing asset_id in book response: {book_data}")
                    continue

                # Parse orderbook using same logic as get_orderbook()
                # CRITICAL: Polymarket API returns bids LOW→HIGH and asks HIGH→LOW
                # We need: bids HIGH→LOW (best bid first), asks LOW→HIGH (best ask first)
                bids = []
                for bid in book_data.get("bids", []):
                    price = to_decimal(bid.get("price", 0))
                    size = to_decimal(bid.get("size", 0))
                    if price and price > 0 and size and size > 0:
                        bids.append((price, size))

                asks = []
                for ask in book_data.get("asks", []):
                    price = to_decimal(ask.get("price", 0))
                    size = to_decimal(ask.get("size", 0))
                    if price and price > 0 and size and size > 0:
                        asks.append((price, size))

                # Sort: bids descending (best=highest first), asks ascending (best=lowest first)
                bids.sort(key=lambda x: x[0], reverse=True)
                asks.sort(key=lambda x: x[0])

                # Extract metadata
                market_slug = book_data.get("market", "")
                tick_size = to_decimal(book_data.get("tick_size", "0.01"))
                neg_risk = book_data.get("neg_risk", False)

                # Create OrderBook instance
                orderbook = OrderBookType(
                    token_id=token_id,
                    bids=bids,
                    asks=asks,
                    market=market_slug,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                    timestamp=book_data.get("timestamp", int(time.time()))
                )

                results[token_id] = orderbook

            logger.info(f"Fetched {len(results)}/{len(token_ids)} orderbooks via batch endpoint")
            return results

        except Exception as e:
            logger.error(f"Failed to fetch batch orderbooks: {e}")
            raise TradingError(f"Batch orderbook fetch failed: {e}")

    async def get_tick_size(self, token_id: str) -> Decimal:
        """
        Get official tick size for token.

        More reliable than hardcoded defaults.

        Args:
            token_id: Token ID

        Returns:
            Tick size (Decimal, e.g., Decimal("0.01"))

        Raises:
            TradingError: If request fails

        Example:
            >>> tick_size = clob.get_tick_size("123456")
            >>> print(f"Tick size: {tick_size}")  # 0.01
        """
        try:
            response = await self.get(
                "/tick-size",
                params={"token_id": token_id},
                rate_limit_key="GET:/tick-size",
                retry=True
            )

            tick_size = to_decimal(response.get("tick_size", "0.01"), default=Decimal("0.01"))
            logger.debug(f"Tick size for {token_id}: {tick_size}")
            return tick_size

        except Exception as e:
            logger.warning(f"Failed to get tick size for {token_id}: {e}, using default 0.01")
            return Decimal("0.01")  # Fallback to default

    async def get_neg_risk(self, token_id: str) -> bool:
        """
        Get negative risk flag for token.

        Important for correct order amount calculations.

        Args:
            token_id: Token ID

        Returns:
            True if negative risk market

        Raises:
            TradingError: If request fails

        Example:
            >>> neg_risk = clob.get_neg_risk("123456")
            >>> print(f"Negative risk: {neg_risk}")
        """
        try:
            response = await self.get(
                "/neg-risk",
                params={"token_id": token_id},
                rate_limit_key="GET:/neg-risk",
                retry=True
            )

            neg_risk = response.get("neg_risk", False)
            logger.debug(f"Neg risk for {token_id}: {neg_risk}")
            return neg_risk

        except Exception as e:
            logger.warning(f"Failed to get neg_risk for {token_id}: {e}, using default False")
            return False  # Fallback to default

    async def get_fee_rate_bps(self, token_id: str) -> int:
        """
        Get fee rate in basis points for token.

        Polymarket currently has 0% taker fees on all markets.
        This method exists for future compatibility if fees are introduced.

        Args:
            token_id: Token ID

        Returns:
            Fee rate in basis points (0 = 0%, 100 = 1%)

        Example:
            >>> fee_bps = clob.get_fee_rate_bps("123456")
            >>> print(f"Fee: {fee_bps / 100}%")
        """
        # Polymarket currently has 0 bps (0%) taker fees
        # If they introduce fees in the future, this could query an API endpoint
        return 0

    async def is_order_scoring(self, order_id: str) -> bool:
        """
        Check if order earns maker rebates (2% on Polymarket).

        CRITICAL for Strategy-4 (Liquidity Mining): Know which orders earn rewards.

        Args:
            order_id: Order ID to check

        Returns:
            True if order is scoring (earning maker rebates)

        Raises:
            TradingError: If request fails

        Example:
            >>> # Check if your order earns 2% maker rebate
            >>> is_scoring = clob.is_order_scoring("0x123...")
            >>> if is_scoring:
            ...     print("✅ Order earning 2% rebate!")
        """
        try:
            response = await self.get(
                "/order-scoring",
                params={"order_id": order_id},
                rate_limit_key="GET:/order-scoring",
                retry=True
            )

            is_scoring = response.get("scoring", False)
            logger.debug(f"Order {order_id} scoring: {is_scoring}")
            return is_scoring

        except Exception as e:
            logger.error(f"Failed to check order scoring for {order_id}: {e}")
            raise TradingError(f"Order scoring check failed: {e}")

    async def are_orders_scoring(self, order_ids: List[str]) -> Dict[str, bool]:
        """
        Check if multiple orders earn maker rebates (batch endpoint).

        CRITICAL for Strategy-4: Batch check which orders earn 2% rewards.

        Args:
            order_ids: List of order IDs to check

        Returns:
            Dict mapping order_id to scoring status (True/False)

        Raises:
            TradingError: If request fails

        Example:
            >>> order_ids = ["0x123...", "0x456...", "0x789..."]
            >>> scoring = clob.are_orders_scoring(order_ids)
            >>> earning_count = sum(scoring.values())
            >>> print(f"{earning_count}/{len(order_ids)} orders earning rebates")
        """
        if not order_ids:
            return {}

        try:
            body = [{"order_id": oid} for oid in order_ids]

            response = await self.post(
                "/orders-scoring",
                json_data=body,
                rate_limit_key="POST:/orders-scoring",
                retry=True
            )

            results = {}
            for item in response:
                order_id = item.get("order_id")
                is_scoring = item.get("scoring", False)
                if order_id:
                    results[order_id] = is_scoring

            logger.info(f"Checked {len(results)}/{len(order_ids)} orders for scoring")
            return results

        except Exception as e:
            logger.error(f"Failed to check batch order scoring: {e}")
            raise TradingError(f"Batch order scoring check failed: {e}")

    async def cancel_market_orders(
        self,
        market_id: str,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str
    ) -> int:
        """
        Cancel all orders for a specific market.

        Convenient for market exit scenarios.

        Args:
            market_id: Market condition ID
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase

        Returns:
            Number of orders cancelled

        Raises:
            TradingError: If cancellation fails

        Example:
            >>> # Exit all positions on a market
            >>> cancelled = clob.cancel_market_orders(
            ...     market_id="0x123...",
            ...     address=addr,
            ...     api_key=key,
            ...     api_secret=secret,
            ...     api_passphrase=passphrase
            ... )
            >>> print(f"Cancelled {cancelled} orders")
        """
        try:
            path = "/cancel-market-orders"
            body = {
                "market": market_id,
                "address": address
            }

            # CRITICAL: Use the SAME JSON serializer for HMAC and request body
            # The session uses orjson with large int conversion, so we must too
            body_str = self.session._json_serialize(body)

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="DELETE",
                path=path,
                body=body_str
            )

            # FIX: Use data=body_str to ensure HMAC matches request body
            # (same fix as Issue #9 for batch orders)
            response = await self.delete(
                path,
                data=body_str,
                headers=headers,
                rate_limit_key="DELETE:/cancel-market-orders",
                retry=False
            )

            cancelled_count = len(response.get("cancelled", []))
            logger.info(f"Cancelled {cancelled_count} orders for market {market_id}")
            return cancelled_count

        except Exception as e:
            logger.error(f"Failed to cancel market orders for {market_id}: {e}")
            raise TradingError(f"Market order cancellation failed: {e}")

    async def cancel_all_orders(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        market_id: Optional[str] = None
    ) -> int:
        """
        Cancel all open orders.

        Args:
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            market_id: Optional market ID filter

        Returns:
            Number of orders cancelled

        Raises:
            TradingError: If cancellation fails
        """
        try:
            path = "/cancel-all"
            body = {"address": address}
            if market_id:
                body["market"] = market_id

            # CRITICAL: Use the SAME JSON serializer for HMAC and request body
            # The session uses orjson with large int conversion, so we must too
            body_str = self.session._json_serialize(body)

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="POST",
                path=path,
                body=body_str
            )

            # FIX: Use data=body_str to ensure HMAC matches request body
            # (same fix as Issue #9 for batch orders)
            response = await self.post(
                path,
                data=body_str,
                headers=headers,
                rate_limit_key="DELETE:/cancel-all",
                retry=False
            )

            cancelled = response.get("cancelled", 0)
            logger.info(f"Cancelled {cancelled} orders")
            return cancelled

        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            raise TradingError(f"Failed to cancel all orders: {e}")

    async def get_orders(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        market: Optional[str] = None
    ) -> List[Order]:
        """
        Get open orders with pagination support.

        Args:
            address: Wallet address
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            market: Optional market filter

        Returns:
            List of open orders

        Raises:
            TradingError: If request fails
        """
        from datetime import datetime

        try:
            path = "/data/orders"

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="GET",
                path=path
            )

            # CRITICAL FIX: Implement pagination per official py-clob-client
            # See: py-clob-client/py_clob_client/client.py lines 598-617
            # Response format: {"data": [...], "next_cursor": "..."}
            # END_CURSOR = "LTE=" signals no more pages
            END_CURSOR = "LTE="
            all_orders_data = []
            next_cursor = "MA=="  # Default start cursor

            while next_cursor != END_CURSOR:
                params = {"next_cursor": next_cursor}
                if market:
                    params["market"] = market

                response = await self.get(
                    path,
                    params=params,
                    headers=headers,
                    rate_limit_key="GET:/data/orders",
                    retry=True
                )

                # Handle response - could be dict (paginated) or list (legacy)
                if isinstance(response, dict):
                    # Paginated response: {"data": [...], "next_cursor": "..."}
                    next_cursor = response.get("next_cursor", END_CURSOR)
                    data_list = response.get("data", [])
                    all_orders_data.extend(data_list)
                elif isinstance(response, list):
                    # Legacy list response (shouldn't happen but handle gracefully)
                    all_orders_data.extend(response)
                    break  # No pagination for legacy format
                else:
                    logger.warning(f"Unexpected response type: {type(response)}")
                    break

            # Parse order objects
            orders = []
            for data in all_orders_data:
                try:
                    # Parse created_at - can be timestamp (int/float), ISO string, or None
                    created_at_raw = data.get("created_at")
                    if created_at_raw is None:
                        created_at = datetime.now()
                    elif isinstance(created_at_raw, (int, float)):
                        # Unix timestamp (seconds or milliseconds)
                        # - Seconds: ~1.7B (10 digits, year ~2024)
                        # - Milliseconds: ~1.7T (13 digits)
                        # Threshold: 10_000_000_000 (10B) - anything above is milliseconds
                        ts = created_at_raw if created_at_raw < 10_000_000_000 else created_at_raw / 1000
                        created_at = datetime.fromtimestamp(ts)
                    elif isinstance(created_at_raw, str):
                        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                    else:
                        created_at = datetime.now()

                    # Normalize status to lowercase (API returns "LIVE", enum expects "live")
                    status_raw = data.get("status", "live")
                    status = status_raw.lower() if isinstance(status_raw, str) else "live"

                    order = Order(
                        id=data.get("id", ""),
                        market=data.get("market", ""),
                        asset_id=data.get("asset_id", ""),
                        token_id=data.get("token_id", ""),
                        price=data.get("price", 0),
                        size=data.get("size", 0),
                        side=data.get("side", "BUY"),
                        status=status,
                        created_at=created_at
                    )
                    orders.append(order)
                except Exception as e:
                    logger.warning(f"Failed to parse order {data.get('id')}: {e}")
                    continue

            logger.info(f"Fetched {len(orders)} open orders")
            return orders

        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            raise TradingError(f"Failed to get orders: {e}")

    async def get_balances(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        signature_type: int = 0,
        funder: Optional[str] = None,
        asset_type: str = "COLLATERAL",
        token_id: Optional[str] = None
    ) -> Balance:
        """
        Get wallet balances.

        Args:
            address: Wallet address (EOA for signing)
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            signature_type: Wallet signature type (0=EOA, 1=MAGIC, 2=PROXY)
            funder: Funder address for proxy wallets (where USDC is actually held)
            asset_type: Asset type ("COLLATERAL" for USDC, "CONDITIONAL" for CTF tokens)
            token_id: Token ID (required when asset_type="CONDITIONAL")

        Returns:
            Balance information

        Raises:
            TradingError: If request fails
        """
        try:
            path = "/balance-allowance"

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="GET",
                path=path
            )

            # Build params dict
            params = {
                "address": address,
                "asset_type": asset_type,  # COLLATERAL = USDC, CONDITIONAL = CTF tokens
                "signature_type": signature_type  # 0=EOA, 1=MAGIC, 2=PROXY
            }

            # Add token_id for CONDITIONAL queries
            if token_id:
                params["token_id"] = token_id

            # Add funder for proxy wallets
            if funder:
                params["funder"] = funder

            response = await self.get(
                path,
                params=params,
                headers=headers,
                rate_limit_key="GET:/balance-allowance",
                retry=True
            )

            # Parse balance from API response
            # API returns balance in 6-decimal format (e.g., "13060149" = $13.06)
            balance_str = response.get("balance", "0")
            collateral = Decimal(balance_str) / Decimal("1000000")  # Convert to USD

            # Tokens field (conditional tokens)
            tokens = response.get("tokens", {})

            balance = Balance(collateral=collateral, tokens=tokens)
            logger.debug(f"Balances: {collateral} USDC, {len(tokens)} tokens")
            return balance

        except Exception as e:
            logger.error(f"Failed to get balances: {e}")
            raise TradingError(f"Failed to get balances: {e}")

    async def update_balance_allowance(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        signature_type: int = 0,
        asset_type: str = "COLLATERAL",
        token_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update balance & allowance from on-chain state.

        This syncs Polymarket's API balance with the actual on-chain USDC balance.
        Call this after depositing USDC to make funds visible to the API.

        Args:
            address: Wallet address (EOA for signing)
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            signature_type: Wallet signature type (0=EOA, 1=MAGIC, 2=PROXY)
            asset_type: "COLLATERAL" for USDC, "CONDITIONAL" for CTF tokens
            token_id: Required if asset_type="CONDITIONAL"

        Returns:
            Updated balance information

        Raises:
            TradingError: If request fails
        """
        try:
            path = "/balance-allowance/update"

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="GET",
                path=path
            )

            # Build params dict
            params = {
                "asset_type": asset_type,
                "signature_type": signature_type
            }

            # Add token_id for CONDITIONAL assets
            if token_id:
                params["token_id"] = token_id

            logger.info(f"Updating balance allowance for {address} (type={signature_type}, asset={asset_type})")

            response = await self.get(
                path,
                params=params,
                headers=headers,
                rate_limit_key="GET:/balance-allowance/update",
                retry=True
            )

            logger.info(f"Balance update response: {response}")
            return response

        except Exception as e:
            logger.error(f"Failed to update balance allowance: {e}")
            raise TradingError(f"Failed to update balance allowance: {e}")
