"""
CLOB API client for trading operations.

Handles order placement, cancellation, and account queries.
Adapted from py-clob-client (MIT License).
"""

import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..auth.authenticator import Authenticator
from ..config import PolymarketSettings
from ..exceptions import (
    APIError,
    AuthenticationError,
    FOKNotFilledError,
    InsufficientAllowanceError,
    InsufficientBalanceError,
    InvalidOrderError,
    MarketNotReadyError,
    OrderDelayedError,
    OrderExpiredError,
    OrderRejectedError,
    PriceUnavailableError,
    TickSizeError,
    TradingError,
)
from ..models import Balance, ClobTrade, FeeSchedule, Order
from ..models import OrderBook as OrderBookType
from ..models import OrderResponse, OrderStatus
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


def _parse_book_tick_size(payload: Dict[str, Any]) -> Decimal:
    """Parse the required tick field carried by ``/book`` and ``/books``."""
    if "tick_size" not in payload:
        raise TradingError("Order-book response is missing tick_size")
    tick_size = Decimal(str(payload["tick_size"]))
    if tick_size not in ALLOWED_TICK_SIZES:
        raise TradingError(f"Unsupported order-book tick_size: {tick_size}")
    return tick_size


def _parse_book_levels(
    payload: Dict[str, Any],
    side: str,
    *,
    token_id: str,
) -> List[tuple[Decimal, Decimal]]:
    """Parse every order-book level or reject the whole snapshot as incomplete."""
    raw_levels = payload.get(side)
    if not isinstance(raw_levels, list):
        raise TradingError(f"Order book for {token_id} is missing {side}")

    levels: List[tuple[Decimal, Decimal]] = []
    for index, raw_level in enumerate(raw_levels):
        if not isinstance(raw_level, dict):
            raise TradingError(
                f"Order book for {token_id} has malformed {side}[{index}]"
            )
        if "price" not in raw_level or "size" not in raw_level:
            raise TradingError(
                f"Order book for {token_id} has incomplete {side}[{index}]"
            )
        try:
            price = Decimal(str(raw_level["price"]))
            size = Decimal(str(raw_level["size"]))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise TradingError(
                f"Order book for {token_id} has invalid {side}[{index}]"
            ) from exc

        if (
            not price.is_finite()
            or not size.is_finite()
            or price <= 0
            or price >= 1
            or size <= 0
        ):
            raise TradingError(f"Order book for {token_id} has invalid {side}[{index}]")
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


class CLOBAPI(BaseAPIClient):
    """
    CLOB API client for trading operations.

    Requires L2 authentication for all operations.
    """

    @staticmethod
    def _parse_order_timestamp(value: Any, *, required: bool) -> Optional[datetime]:
        if value in (None, ""):
            if required:
                raise ValueError("order timestamp is required")
            return None
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value >= 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        raise ValueError(f"unsupported order timestamp {value!r}")

    @classmethod
    def _parse_order(cls, data: Dict[str, Any]) -> Order:
        """Parse the documented authenticated-order wire shape without loss."""
        return Order(
            id=data["id"],
            market=data["market"],
            asset_id=data["asset_id"],
            price=data["price"],
            original_size=data["original_size"],
            size_matched=data.get("size_matched", "0"),
            side=data["side"],
            status=data["status"],
            created_at=cls._parse_order_timestamp(
                data.get("created_at"), required=True
            ),
            expiration=cls._parse_order_timestamp(
                data.get("expiration"), required=False
            ),
            owner=data.get("owner"),
            maker_address=data.get("maker_address"),
            outcome=data.get("outcome"),
            order_type=data.get("order_type"),
            associate_trades=data.get("associate_trades") or [],
        )

    def __init__(
        self,
        settings: PolymarketSettings,
        authenticator: Authenticator,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        """
        Initialize CLOB API client.

        Args:
            settings: Client settings
            authenticator: Authenticator for L2 headers
            rate_limiter: Optional rate limiter
        """
        super().__init__(
            base_url=settings.clob_url,
            settings=settings,
            rate_limiter=rate_limiter,
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
        body: str = "",
    ) -> Dict[str, str]:
        """Create L2 authentication headers."""
        return self.authenticator.create_l2_headers(
            address=address,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            method=method,
            path=path,
            body=body,
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
                retry=False,  # Don't retry health checks
            )
            return _parse_health_response(response)
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
            response = await self.get("/time", rate_limit_key="GET:/time", retry=True)

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
                raise TradingError(
                    f"Unexpected server time response type: {type(response)}"
                )

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
                retry=True,
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
                retry=True,
            )

            mid = response.get("mid")
            if mid is None:
                logger.warning(f"No midpoint for token {token_id}")
                return None

            price = to_decimal(mid)
            logger.debug(f"Midpoint for {token_id}: {price}")
            return price

        except APIError as e:
            if e.status_code == 404:
                logger.warning(f"No midpoint/orderbook for token {token_id}")
                return None
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get midpoint: {e}", token_id=token_id
            )
        except Exception as e:
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            raise PriceUnavailableError(
                f"Failed to get midpoint: {e}", token_id=token_id
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
                retry=True,
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
            raise PriceUnavailableError(f"Failed to get price: {e}", token_id=token_id)

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
                retry=True,
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
                f"Failed to get last trade price: {e}", token_id=token_id
            )

    async def get_last_trades_prices(
        self, token_ids: List[str]
    ) -> Dict[str, Optional[Decimal]]:
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
                retry=True,
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
                retry=True,
            )

            if not isinstance(response, dict):
                raise TradingError("Order-book response is not an object")
            response_token_id = response.get("asset_id")
            if not isinstance(response_token_id, str) or response_token_id != token_id:
                raise TradingError(
                    "Order-book asset_id does not match requested token "
                    f"{token_id}: {response_token_id!r}"
                )

            # Polymarket returns bids LOW→HIGH and asks HIGH→LOW. Reject a
            # partially parsed snapshot; dropping one bad level changes price
            # and depth semantics.
            bids = _parse_book_levels(response, "bids", token_id=token_id)
            asks = _parse_book_levels(response, "asks", token_id=token_id)

            # Sort: bids descending (best=highest first), asks ascending (best=lowest first)
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            orderbook = OrderBookType(
                token_id=token_id,
                bids=bids,
                asks=asks,
                market=response.get("market"),
                tick_size=_parse_book_tick_size(response),
                neg_risk=response.get("neg_risk"),
                timestamp=response.get("timestamp", int(time.time())),
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
        order_type: str = "GTC",
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
            # V2 wire shape mirrors py-clob-client-v2's order_to_json_v2:
            # {"order", "owner", "orderType", "deferExec", "postOnly"}.
            # Private underscore keys (e.g. _orderHash, the pre-computable
            # exchange orderID) never reach the wire.
            wire_order = {
                k: v for k, v in signed_order.items() if not k.startswith("_")
            }
            body = {
                "order": wire_order,
                "owner": api_key,
                "orderType": order_type,
                "deferExec": False,
                "postOnly": False,
            }

            # CRITICAL FIX (Bug #49): Use stdlib json.dumps() for order payloads
            # The custom orjson serializer converts large ints to STRINGS, but the API expects them as INTEGERS
            # py_order_utils.SignedOrder.dict() keeps salt as int, which is correct per official py-clob-client
            #
            # We serialize manually with stdlib json to keep integers as integers,
            # then pass as data= with explicit Content-Type header (like official py-clob-client does with httpx json=)
            body_str = json.dumps(body)

            # Never log the payload/API key here: signed order material and
            # credentials must not reach persistent logs.
            logger.debug(
                f"POST /order: type={order_type}, token={signed_order.get('tokenId', '?')}"
            )

            # Create L2 headers with HMAC signature
            l2_headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="POST",
                path=path,
                body=body_str,
            )

            # CRITICAL FIX: Add Content-Type header explicitly (like httpx does with json= parameter)
            # When using data= instead of json_data=, aiohttp doesn't auto-add Content-Type
            l2_headers["Content-Type"] = "application/json"

            response = await self.post(
                path,
                data=body_str,  # Use raw JSON string (not json_data which triggers custom serializer)
                headers=l2_headers,
                rate_limit_key="POST:/order",
                retry=False,  # Don't auto-retry order submissions
            )

            # CRITICAL FIX: Validate response type before accessing
            if not isinstance(response, dict):
                raise TradingError(
                    f"Invalid order response format: expected dict, got {type(response).__name__}: {response}"
                )

            # Parse response. Missing/coerced success truth must never become a
            # definitive rejection: the caller may already have a durable
            # pre-submit identity and reservation for an accepted order.
            success = response.get("success")
            if not isinstance(success, bool):
                raise TradingError(
                    "Invalid order response format: success must be boolean"
                )
            error_msg = response.get("errorMsg")
            if error_msg is not None and not isinstance(error_msg, str):
                raise TradingError(
                    "Invalid order response format: errorMsg must be a string or null"
                )
            order_id = response.get(
                "orderID"
            )  # NOTE: Polymarket API uses 'orderID' not 'orderId'
            if order_id is not None and (not isinstance(order_id, str) or not order_id):
                raise TradingError(
                    "Invalid order response format: orderID must be a non-empty string or null"
                )
            status = response.get("status")
            if success:
                if not order_id:
                    raise TradingError(
                        "Invalid order response format: successful result is missing orderID"
                    )
                if error_msg and error_msg.strip():
                    raise TradingError(
                        "Invalid order response format: successful result includes errorMsg"
                    )
            elif not error_msg or not error_msg.strip():
                raise TradingError(
                    "Invalid order response format: unsuccessful result is missing errorMsg"
                )

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
                    raise OrderExpiredError(f"Order expiration invalid: {error_msg}")

                # FOK order not filled
                if "FOK" in error_upper and "NOT_FILLED" in error_upper:
                    raise FOKNotFilledError(
                        f"Fill-or-Kill order could not be filled: {error_msg}"
                    )

                # Order delayed
                if "ORDER_DELAYED" in error_upper or "DELAYED" in error_upper:
                    raise OrderDelayedError(
                        f"Order is delayed: {error_msg}", order_id=order_id
                    )

                # CRITICAL: Additional production error codes
                if "SIZE_TOO_SMALL" in error_upper or "MINIMUM_SIZE" in error_upper:
                    raise InvalidOrderError(f"Order size below minimum: {error_msg}")

                if (
                    "PRICE_OUT_OF_RANGE" in error_upper
                    or "INVALID_PRICE" in error_upper
                ):
                    raise InvalidOrderError(f"Price out of valid range: {error_msg}")

                if "MARKET_CLOSED" in error_upper or "MARKET_NOT_ACTIVE" in error_upper:
                    raise MarketNotReadyError(
                        f"Market not accepting orders: {error_msg}"
                    )

                if (
                    "INVALID_SIGNATURE" in error_upper
                    or "SIGNATURE_FAILED" in error_upper
                ):
                    raise AuthenticationError(f"Order signature invalid: {error_msg}")

                if "NONCE_TOO_LOW" in error_upper or "INVALID_NONCE" in error_upper:
                    raise OrderRejectedError(
                        f"Nonce conflict detected: {error_msg}",
                        order_id=order_id,
                        reason="NONCE_CONFLICT",
                    )

                if (
                    "ORDER_ALREADY_EXISTS" in error_upper
                    or "DUPLICATE_ORDER" in error_upper
                ):
                    raise OrderRejectedError(
                        f"Duplicate order detected: {error_msg}",
                        order_id=order_id,
                        reason="DUPLICATE",
                    )

                # Unknown response text is not proof that the deterministic
                # identity failed to land. Preserve it as an ambiguous transport
                # result so the facade retains cap until exact reconciliation.
                if not success:
                    raise TradingError(f"Unclassified order response: {error_msg}")

            # Successful FAK/FOK matches return tradeIDs (effective 2026-07-24);
            # transactionHashes is the legacy field (docs example also shows a
            # "transactionsHashes" spelling — accept all three).
            trade_ids = response.get("tradeIDs")
            legacy_hashes = (
                response.get("orderHashes")
                or response.get("transactionHashes")
                or response.get("transactionsHashes")
            )
            order_response = OrderResponse(
                success=success,
                order_id=order_id,
                status=OrderStatus.normalize(status) if status else None,
                error_msg=error_msg,
                order_hashes=legacy_hashes,
                trade_ids=trade_ids,
            )

            if success:
                logger.info(f"Order placed successfully: {order_id} ({status})")
            else:
                logger.warning(f"Order placement failed: {error_msg}")

            return order_response

        except (
            OrderRejectedError,
            AuthenticationError,
            InsufficientBalanceError,
            TickSizeError,
            InsufficientAllowanceError,
            InvalidOrderError,
            MarketNotReadyError,
            OrderDelayedError,
            OrderExpiredError,
            FOKNotFilledError,
            TradingError,
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
        api_passphrase: str,
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
                body=body_str,
            )

            # Add Content-Type header
            headers["Content-Type"] = "application/json"

            response = await self.delete(
                path,
                data=body_str,  # Use exact string that was signed
                headers=headers,
                rate_limit_key="DELETE:/order",
                retry=False,
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
                    logger.info(
                        f"Order {order_id} already cancelled/filled (NOT_FOUND)"
                    )
                    return True
                raise TradingError(f"Cancel failed: {error_msg}")

            # Fallback: check legacy format or empty response
            if response.get("success", False):
                logger.info(f"Order cancelled: {order_id}")
                return True

            # If we get here with empty response but 200 status, assume success
            if not canceled and not not_canceled:
                logger.warning(
                    f"Empty cancel response for {order_id}, assuming success"
                )
                return True

            raise TradingError(f"Cancel failed: unexpected response {response}")

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise TradingError(f"Failed to cancel order: {e}")

    @staticmethod
    def _is_definitive_batch_rejection(
        *,
        error_msg: Any,
        status: Any,
    ) -> bool:
        """Classify only batch item failures that prove no order was accepted."""
        error_upper = str(error_msg or "").strip().upper()
        status_upper = str(status or "").strip().upper()
        if (
            "ORDER_ALREADY_EXISTS" in error_upper
            or "DUPLICATE_ORDER" in error_upper
            or "ORDER_DELAYED" in error_upper
            or "DELAYED" in error_upper
            or status_upper == "DELAYED"
        ):
            return False

        definitive_patterns = (
            "MIN_TICK_SIZE",
            "TICK_SIZE",
            "NOT_ENOUGH_BALANCE",
            "INSUFFICIENT",
            "EXPIRATION",
            "EXPIRED",
            "FOK_NOT_FILLED",
            "SIZE_TOO_SMALL",
            "MINIMUM_SIZE",
            "PRICE_OUT_OF_RANGE",
            "INVALID_PRICE",
            "MARKET_CLOSED",
            "MARKET_NOT_ACTIVE",
            "INVALID_SIGNATURE",
            "SIGNATURE_FAILED",
            "NONCE_TOO_LOW",
            "INVALID_NONCE",
        )
        return any(pattern in error_upper for pattern in definitive_patterns)

    async def get_orderbooks_batch(
        self, token_ids: List[str]
    ) -> Dict[str, OrderBookType]:
        """
        Get orderbooks for multiple tokens using native batch endpoint.

        Uses POST /books for 10x performance vs concurrent individual fetches.

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
                "/books", json_data=body, rate_limit_key="POST:/books", retry=True
            )

            if not isinstance(response, list):
                raise TradingError("Batch order-book response is not a list")

            expected_token_ids = set(token_ids)
            results = {}
            for book_data in response:
                if not isinstance(book_data, dict):
                    raise TradingError("Batch order-book entry is not an object")
                token_id = book_data.get("asset_id")
                if not isinstance(token_id, str) or not token_id:
                    raise TradingError("Batch order-book entry is missing asset_id")
                if token_id not in expected_token_ids:
                    raise TradingError(
                        f"Batch order-book returned unrequested token {token_id}"
                    )
                if token_id in results:
                    raise TradingError(
                        f"Batch order-book returned duplicate token {token_id}"
                    )

                # Parse orderbook using same logic as get_orderbook()
                # CRITICAL: Polymarket API returns bids LOW→HIGH and asks HIGH→LOW
                # We need: bids HIGH→LOW (best bid first), asks LOW→HIGH (best ask first)
                bids = _parse_book_levels(book_data, "bids", token_id=token_id)
                asks = _parse_book_levels(book_data, "asks", token_id=token_id)

                # Sort: bids descending (best=highest first), asks ascending (best=lowest first)
                bids.sort(key=lambda x: x[0], reverse=True)
                asks.sort(key=lambda x: x[0])

                # Extract metadata
                market_slug = book_data.get("market", "")
                tick_size = _parse_book_tick_size(book_data)
                neg_risk = book_data.get("neg_risk", False)

                # Create OrderBook instance
                orderbook = OrderBookType(
                    token_id=token_id,
                    bids=bids,
                    asks=asks,
                    market=market_slug,
                    tick_size=tick_size,
                    neg_risk=neg_risk,
                    timestamp=book_data.get("timestamp", int(time.time())),
                )

                results[token_id] = orderbook

            missing_token_ids = expected_token_ids.difference(results)
            if missing_token_ids:
                missing = ", ".join(sorted(missing_token_ids))
                raise TradingError(
                    f"Batch order-book response is missing requested tokens: {missing}"
                )

            logger.info(
                f"Fetched {len(results)}/{len(token_ids)} orderbooks via batch endpoint"
            )
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
                retry=True,
            )
            if not isinstance(response, dict) or "minimum_tick_size" not in response:
                raise TradingError("Tick-size response is missing minimum_tick_size")
            tick_size = Decimal(str(response["minimum_tick_size"]))
            if tick_size not in ALLOWED_TICK_SIZES:
                raise TradingError(f"Unsupported minimum_tick_size: {tick_size}")
            logger.debug(f"Tick size for {token_id}: {tick_size}")
            return tick_size
        except TradingError:
            raise
        except Exception as e:
            raise TradingError(f"Failed to get tick size for {token_id}: {e}") from e

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
                retry=True,
            )

            if not isinstance(response, dict) or not isinstance(
                response.get("neg_risk"), bool
            ):
                raise TradingError("Neg-risk response is missing boolean neg_risk")
            neg_risk = response["neg_risk"]
            logger.debug(f"Neg risk for {token_id}: {neg_risk}")
            return neg_risk

        except TradingError:
            raise
        except Exception as e:
            raise TradingError(f"Failed to get neg_risk for {token_id}: {e}") from e

    async def get_fee_rate_bps(self, token_id: str) -> int:
        """
        Get the CLOB protocol base-fee value for a token.

        Args:
            token_id: Token ID

        Returns:
            Fee rate in basis points (0 = 0%, 100 = 1%)

        Example:
            >>> fee_bps = clob.get_fee_rate_bps("123456")
            >>> print(f"Fee: {fee_bps / 100}%")
        """
        try:
            response = await self.get(
                "/fee-rate",
                params={"token_id": token_id},
                rate_limit_key="GET:/fee-rate",
                retry=True,
            )
            if not isinstance(response, dict) or "base_fee" not in response:
                raise TradingError("Fee-rate response is missing base_fee")
            base_fee = Decimal(str(response["base_fee"]))
            if (
                not base_fee.is_finite()
                or base_fee < 0
                or base_fee != base_fee.to_integral_value()
            ):
                raise TradingError(f"Invalid base_fee: {base_fee}")
            return int(base_fee)
        except TradingError:
            raise
        except Exception as e:
            raise TradingError(f"Failed to get fee rate for {token_id}: {e}") from e

    async def get_fee_schedule(self, token_id: str) -> Optional[FeeSchedule]:
        """Return the CLOB V2 ``fd`` fee curve for a token.

        The official V2 SDK resolves token -> condition and then reads the
        compact ``fd`` object from ``/clob-markets/{condition_id}``.
        Absence of ``fd`` is the CLOB's explicit fee-free representation.
        """
        try:
            token_market = await self.get(
                f"/markets-by-token/{token_id}",
                rate_limit_key="GET:/markets-by-token/:token_id",
                retry=True,
            )
            if not isinstance(token_market, dict):
                raise TradingError("markets-by-token response must be an object")
            condition_id = token_market.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id:
                raise TradingError("markets-by-token response is missing condition_id")

            market = await self.get(
                f"/clob-markets/{condition_id}",
                rate_limit_key="GET:/clob-markets/:condition_id",
                retry=True,
            )
            if not isinstance(market, dict):
                raise TradingError("clob-markets response must be an object")
            raw_fee = market.get("fd")
            if raw_fee is None:
                return None
            if not isinstance(raw_fee, dict):
                raise TradingError("clob-markets fd must be an object")
            missing_fields = {"r", "e", "to"} - raw_fee.keys()
            if missing_fields:
                raise TradingError(
                    "clob-markets fd is missing fields: "
                    + ", ".join(sorted(missing_fields))
                )

            rate = Decimal(str(raw_fee["r"]))
            exponent = Decimal(str(raw_fee["e"]))
            taker_only = raw_fee["to"]
            if not rate.is_finite() or rate < 0 or rate >= 1:
                raise TradingError(f"Invalid clob-markets fd.r: {rate}")
            if not exponent.is_finite() or exponent < 0:
                raise TradingError(f"Invalid clob-markets fd.e: {exponent}")
            if not isinstance(taker_only, bool):
                raise TradingError("clob-markets fd.to must be a bool")
            if rate > 0 and exponent <= 0:
                raise TradingError(
                    "Fee-bearing clob-markets fd requires a positive exponent"
                )
            if rate == 0:
                return None
            return FeeSchedule(
                rate=rate,
                exponent=exponent,
                taker_only=taker_only,
                rebate_rate=Decimal("0"),
            )
        except TradingError:
            raise
        except Exception as e:
            raise TradingError(f"Failed to get fee schedule for {token_id}: {e}") from e

    async def get_minimum_order_size(self, token_id: str) -> Decimal:
        """Return the CLOB V2 per-market minimum order size in shares.

        Resolved like the ``fd`` fee curve: token -> condition, then the
        compact ``mos`` field on ``/clob-markets/{condition_id}``. The minimum
        is order-facing market truth, so a missing or invalid value is an
        error, never zero.
        """
        try:
            token_market = await self.get(
                f"/markets-by-token/{token_id}",
                rate_limit_key="GET:/markets-by-token/:token_id",
                retry=True,
            )
            if not isinstance(token_market, dict):
                raise TradingError("markets-by-token response must be an object")
            condition_id = token_market.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id:
                raise TradingError("markets-by-token response is missing condition_id")

            market = await self.get(
                f"/clob-markets/{condition_id}",
                rate_limit_key="GET:/clob-markets/:condition_id",
                retry=True,
            )
            if not isinstance(market, dict):
                raise TradingError("clob-markets response must be an object")
            raw_minimum = market.get("mos")
            if raw_minimum is None:
                raise TradingError("clob-markets response is missing mos")
            minimum = Decimal(str(raw_minimum))
            if not minimum.is_finite() or minimum <= 0:
                raise TradingError(f"Invalid clob-markets mos: {raw_minimum}")
            return minimum
        except TradingError:
            raise
        except Exception as e:
            raise TradingError(
                f"Failed to get minimum order size for {token_id}: {e}"
            ) from e

    async def is_order_scoring(self, order_id: str) -> bool:
        """
        Check if order earns maker rebates (2% on Polymarket).

        Scoring status determines whether an order is currently earning maker
        rewards.

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
                retry=True,
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

        Batch check which orders are currently earning maker rewards.

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
                retry=True,
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

    async def get_orders(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        market: Optional[str] = None,
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
        try:
            path = "/data/orders"

            headers = self._create_l2_headers(
                address=address,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                method="GET",
                path=path,
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
                    retry=True,
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
                    orders.append(self._parse_order(data))
                except Exception as e:
                    logger.warning(f"Failed to parse order {data.get('id')}: {e}")
                    continue

            logger.info(f"Fetched {len(orders)} open orders")
            return orders

        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            raise TradingError(f"Failed to get orders: {e}")

    async def get_order(
        self,
        order_id: str,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
    ) -> Optional[Order]:
        """Get one order by exchange order hash, including terminal state."""
        path = f"/data/order/{order_id}"
        headers = self._create_l2_headers(
            address=address,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            method="GET",
            path=path,
        )
        try:
            response = await self.get(
                path,
                headers=headers,
                rate_limit_key="GET:/data/order/{id}",
                retry=True,
            )
            if not isinstance(response, dict):
                raise TradingError(
                    f"Unexpected single-order response: {type(response).__name__}"
                )
            return self._parse_order(response)
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise TradingError(f"Failed to get order {order_id}: {exc}") from exc
        except (TradingError, AuthenticationError):
            raise
        except Exception as exc:
            raise TradingError(f"Failed to get order {order_id}: {exc}") from exc

    async def get_trades(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        *,
        trade_id: Optional[str] = None,
        maker_address: Optional[str] = None,
        market: Optional[str] = None,
        asset_id: Optional[str] = None,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> List[ClobTrade]:
        """Get authenticated CLOB executions, including maker contributions."""
        path = "/data/trades"
        headers = self._create_l2_headers(
            address=address,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            method="GET",
            path=path,
        )
        filters = {
            "id": trade_id,
            "maker_address": maker_address,
            "market": market,
            "asset_id": asset_id,
            "before": before,
            "after": after,
        }
        filters = {key: value for key, value in filters.items() if value is not None}
        cursor = "MA=="
        seen_cursors: set[str] = set()
        rows: list[dict[str, Any]] = []
        try:
            while cursor != "LTE=":
                if cursor in seen_cursors:
                    raise TradingError(
                        f"Authenticated trades pagination repeated cursor {cursor!r}"
                    )
                seen_cursors.add(cursor)
                response = await self.get(
                    path,
                    params={**filters, "next_cursor": cursor},
                    headers=headers,
                    rate_limit_key="GET:/data/trades",
                    retry=True,
                )
                if not isinstance(response, dict):
                    raise TradingError(
                        f"Unexpected trades response: {type(response).__name__}"
                    )
                page_rows = response.get("data")
                next_cursor = response.get("next_cursor")
                if not isinstance(page_rows, list):
                    raise TradingError(
                        "Authenticated trades response is missing list data"
                    )
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise TradingError(
                        "Authenticated trades response is missing next_cursor"
                    )
                rows.extend(page_rows)
                cursor = next_cursor
            return [ClobTrade.model_validate(row) for row in rows]
        except (TradingError, AuthenticationError):
            raise
        except Exception as exc:
            raise TradingError(f"Failed to get authenticated trades: {exc}") from exc

    async def get_balances(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        signature_type: int = 0,
        funder: Optional[str] = None,
        asset_type: str = "COLLATERAL",
        token_id: Optional[str] = None,
    ) -> Balance:
        """
        Get wallet balances.

        Args:
            address: Wallet address (EOA for signing)
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            signature_type: Wallet signature type (0=EOA, 1=POLY_PROXY,
                2=GNOSIS_SAFE, 3=POLY_1271)
            funder: Funder address for smart-contract wallets (where pUSD is held)
            asset_type: Asset type ("COLLATERAL" for pUSD, "CONDITIONAL" for CTF tokens)
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
                path=path,
            )

            # Build params dict
            params = {
                "address": address,
                "asset_type": asset_type,  # COLLATERAL = pUSD, CONDITIONAL = CTF tokens
                "signature_type": signature_type,
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
                retry=True,
            )

            if not isinstance(response, Mapping):
                raise TradingError("Balance-allowance response is not an object")
            if "balance" not in response:
                raise TradingError("Balance-allowance response is missing balance")

            # The API returns balance in 6-decimal base units (for example,
            # "13060149" = 13.060149 collateral or conditional-token shares).
            # Missing or malformed observation truth must not become zero.
            try:
                raw_balance = Decimal(str(response["balance"]))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise TradingError(
                    "Balance-allowance response has an invalid balance"
                ) from exc
            if not raw_balance.is_finite() or raw_balance < 0:
                raise TradingError("Balance-allowance response has an invalid balance")
            collateral = raw_balance / Decimal("1000000")

            # Tokens field (conditional tokens)
            tokens = response.get("tokens", {})

            balance = Balance(collateral=collateral, tokens=tokens)
            logger.debug(f"Balances: {collateral} pUSD, {len(tokens)} tokens")
            return balance

        except TradingError:
            raise
        except Exception as e:
            logger.error(f"Failed to get balances: {e}")
            raise TradingError(f"Failed to get balances: {e}") from e

    async def update_balance_allowance(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        signature_type: int = 0,
        asset_type: str = "COLLATERAL",
        token_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update balance & allowance from on-chain state.

        This syncs Polymarket's API balance with the actual on-chain pUSD balance.
        Call this after funding pUSD to make funds visible to the CLOB.

        Args:
            address: Wallet address (EOA for signing)
            api_key: API key
            api_secret: API secret
            api_passphrase: API passphrase
            signature_type: Wallet signature type (0=EOA, 1=POLY_PROXY,
                2=GNOSIS_SAFE, 3=POLY_1271)
            asset_type: "COLLATERAL" for pUSD, "CONDITIONAL" for CTF tokens
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
                path=path,
            )

            # Build params dict
            params = {"asset_type": asset_type, "signature_type": signature_type}

            # Add token_id for CONDITIONAL assets
            if token_id:
                params["token_id"] = token_id

            logger.info(
                f"Updating balance allowance for {address} (type={signature_type}, asset={asset_type})"
            )

            response = await self.get(
                path,
                params=params,
                headers=headers,
                rate_limit_key="GET:/balance-allowance/update",
                retry=True,
            )

            logger.info(f"Balance update response: {response}")
            return response

        except Exception as e:
            logger.error(f"Failed to update balance allowance: {e}")
            raise TradingError(f"Failed to update balance allowance: {e}")
