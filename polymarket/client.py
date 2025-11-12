"""
Main Polymarket client.

Unified interface for all Polymarket operations across strategies.
Thread-safe, multi-wallet, production-ready.
"""

from typing import Optional, List, Callable, Dict, Any
import asyncio
import logging
import atexit
import time
import signal
import sys
import threading
import secrets  # For cryptographically secure random nonces
from decimal import Decimal

from .config import get_settings, PolymarketSettings
from .models import (
    WalletConfig,
    OrderRequest,
    MarketOrderRequest,
    OrderResponse,
    Order,
    Market,
    Event,
    Balance,
    Position,
    OrderBook,
    Side,
    Trade,
    Activity,
    ActivityType,
    Holder
)
from .auth.key_manager import KeyManager, WalletCredentials
from .auth.authenticator import Authenticator
from .api.gamma import GammaAPI
from .api.clob import CLOBAPI
from .api.clob_public import PublicCLOBAPI
from .api.data_api import DataAPI
from .api.websocket import WebSocketClient
from .api.real_time_data import RealTimeDataClient, Message, ConnectionStatus
from .utils.rate_limiter import RateLimiter
from .utils.retry import CircuitBreaker
from .utils.cache import MarketMetadataCache, AtomicNonceManager
from .trading.order_builder import OrderBuilder
from .exceptions import (
    AuthenticationError,
    ValidationError,
    InsufficientBalanceError,
    BalanceTrackingError,
    APIError,
    TimeoutError,
    TradingError
)
from .metrics import get_metrics

logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    Main client for Polymarket operations.

    Features:
    - Multi-wallet support (thread-safe)
    - Automatic rate limiting
    - Retry logic with circuit breaker
    - Typed exceptions
    - Production-ready

    Usage:
        client = PolymarketClient()
        client.add_wallet(wallet_config, wallet_id="strategy1")
        markets = client.get_markets(active=True)
        response = client.place_order(order, wallet_id="strategy1")
    """

    def __init__(
        self,
        settings: Optional[PolymarketSettings] = None,
        enable_rate_limiting: Optional[bool] = None,
        enable_circuit_breaker: Optional[bool] = None
    ):
        """
        Initialize Polymarket client.

        Args:
            settings: Optional settings (loads from env if not provided)
            enable_rate_limiting: Override rate limiting setting
            enable_circuit_breaker: Override circuit breaker setting
        """
        # Load settings
        self.settings = settings or get_settings()

        # Override settings if provided
        if enable_rate_limiting is not None:
            self.settings.enable_rate_limiting = enable_rate_limiting

        # Initialize components
        self.key_manager = KeyManager()
        self.authenticator = Authenticator(chain_id=self.settings.chain_id)
        self.metadata_cache = MarketMetadataCache(ttl=300.0)  # 5 min TTL
        self.order_builder = OrderBuilder(
            chain_id=self.settings.chain_id,
            metadata_cache=self.metadata_cache
        )

        # Rate limiter
        self.rate_limiter = None
        if self.settings.enable_rate_limiting:
            self.rate_limiter = RateLimiter(
                enabled=True,
                margin=self.settings.rate_limit_margin
            )

        # Circuit breaker
        self.circuit_breaker = None
        if enable_circuit_breaker is not False:
            self.circuit_breaker = CircuitBreaker(
                failure_threshold=self.settings.circuit_breaker_threshold,
                timeout=self.settings.circuit_breaker_timeout,
                name="polymarket"
            )

        # CRITICAL FIX: Track reserved balance to prevent over-ordering
        # Maps wallet_id -> reserved USD amount for pending orders
        self._reserved_balances: Dict[str, float] = {}
        self._balance_lock = asyncio.Lock()  # Async-safe balance updates

        if self.circuit_breaker:
            logger.info("Circuit breaker enabled")

        # Initialize API clients
        self.gamma = GammaAPI(
            settings=self.settings,
            rate_limiter=self.rate_limiter,
            circuit_breaker=self.circuit_breaker
        )

        self.clob = CLOBAPI(
            settings=self.settings,
            authenticator=self.authenticator,
            rate_limiter=self.rate_limiter,
            circuit_breaker=self.circuit_breaker
        )

        self.data = DataAPI(
            settings=self.settings,
            rate_limiter=self.rate_limiter,
            circuit_breaker=self.circuit_breaker
        )

        self.public_clob = PublicCLOBAPI(
            settings=self.settings,
            rate_limiter=self.rate_limiter,
            circuit_breaker=self.circuit_breaker
        )

        # Initialize metrics
        self.metrics = get_metrics(
            enabled=self.settings.enable_metrics,
            port=self.settings.metrics_port
        )

        # Balance monitoring
        self._min_balance_warning = 10.0  # USDC

        # Track inflight orders for graceful shutdown
        # MEMORY OPTIMIZATION: Bounded deque prevents unbounded growth
        from collections import deque
        self._inflight_orders: deque = deque(maxlen=10000)  # Max 10K recent orders
        self._shutdown_requested = False

        # Nonce management (thread-safe atomic counter)
        self._nonce_manager = AtomicNonceManager()

        # WebSocket client (lazy initialized)
        self._ws: Optional[WebSocketClient] = None
        self._ws_callbacks: Dict[str, List[Callable]] = {}

        # Real-Time Data Service client (lazy initialized)
        self._rtds: Optional[RealTimeDataClient] = None
        self._rtds_lock = threading.Lock()  # Thread-safe RTDS initialization (used in property)

        # Register cleanup handlers
        atexit.register(self.close)
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

        logger.info("Polymarket client initialized")

    # ========== Wallet Management ==========

    def add_wallet(
        self,
        wallet_config: WalletConfig,
        wallet_id: Optional[str] = None,
        set_default: bool = False
    ) -> str:
        """
        Add wallet for trading.

        Args:
            wallet_config: Wallet configuration
            wallet_id: Unique identifier (uses address if not provided)
            set_default: Set as default wallet

        Returns:
            Wallet ID

        Raises:
            ValidationError: If config is invalid
            AuthenticationError: If wallet already exists
        """
        wallet_id = self.key_manager.add_wallet(
            wallet_config,
            wallet_id=wallet_id,
            set_default=set_default
        )

        # Create/derive API credentials
        self._initialize_api_credentials(wallet_id)

        return wallet_id

    def _initialize_api_credentials(self, wallet_id: str) -> None:
        """
        Initialize API credentials for wallet.

        Args:
            wallet_id: Wallet identifier
        """
        try:
            credentials = self.key_manager.get_wallet(wallet_id)

            # ALWAYS authenticate with EOA address (signer address)
            # Even for proxy wallets - EOA is in credentials.address
            headers = self.authenticator.create_l1_headers(
                address=credentials.address,
                private_key=credentials.private_key
            )

            # Try to derive existing key first
            try:
                path = "/auth/derive-api-key"
                response = self.clob.get(
                    path,
                    headers=headers,
                    rate_limit_key="GET:/auth/derive-api-key",
                    retry=False
                )

                api_key = response.get("apiKey")
                api_secret = response.get("secret")
                api_passphrase = response.get("passphrase")

                logger.info(f"Derived API credentials for wallet {wallet_id}")

            except Exception as e:
                # Create new API key if derivation fails
                logger.info(f"Creating new API key for wallet {wallet_id}")

                path = "/auth/api-key"
                response = self.clob.post(
                    path,
                    json_data={},
                    headers=headers,
                    rate_limit_key="POST:/auth/api-key",
                    retry=False
                )

                api_key = response.get("apiKey")
                api_secret = response.get("secret")
                api_passphrase = response.get("passphrase")

            if not all([api_key, api_secret, api_passphrase]):
                raise AuthenticationError("Failed to get API credentials")

            # Store credentials
            self.key_manager.set_api_credentials(
                wallet_id=wallet_id,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase
            )

            logger.info(f"API credentials initialized for wallet {wallet_id}")

        except Exception as e:
            logger.error(f"Failed to initialize API credentials for {wallet_id}: {e}")
            raise

    def remove_wallet(self, wallet_id: str) -> None:
        """Remove wallet."""
        self.key_manager.remove_wallet(wallet_id)

    def list_wallets(self) -> List[str]:
        """List all wallet IDs."""
        return self.key_manager.list_wallets()

    def get_default_wallet(self) -> Optional[str]:
        """Get default wallet ID."""
        return self.key_manager.get_default_wallet()

    # ========== Market Data Operations ==========

    def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        **kwargs
    ) -> List[Market]:
        """
        Get markets with filters.

        Args:
            limit: Max results
            offset: Pagination offset
            active: Filter by active status
            closed: Filter by closed status
            **kwargs: Additional filters

        Returns:
            List of markets
        """
        return self.gamma.get_markets(
            limit=limit,
            offset=offset,
            active=active,
            closed=closed,
            **kwargs
        )

    def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """Get market by slug."""
        return self.gamma.get_market_by_slug(slug)

    def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """Get market by ID."""
        return self.gamma.get_market_by_id(market_id)

    def search_markets(self, query: str, limit: int = 20) -> List[Market]:
        """Search markets by query."""
        return self.gamma.search_markets(query, limit)

    # New helper methods from official Polymarket agents repo
    def get_all_current_markets(self, limit: int = 100) -> List[Market]:
        """
        Auto-paginate through all active, non-closed, non-archived markets.

        From official Polymarket agents repository.

        Args:
            limit: Batch size for pagination (default: 100)

        Returns:
            List of all current markets
        """
        return self.gamma.get_all_current_markets(limit=limit)

    def get_clob_tradable_markets(self, limit: int = 100) -> List[Market]:
        """
        Get markets with order book enabled (CLOB tradable).

        From official Polymarket agents repository.

        Args:
            limit: Max results (default: 100)

        Returns:
            List of tradable markets
        """
        return self.gamma.get_clob_tradable_markets(limit=limit)

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        archived: Optional[bool] = None
    ) -> List[Event]:
        """
        Get events (collections of related markets).

        Args:
            limit: Max results
            offset: Pagination offset
            active: Filter by active status
            closed: Filter by closed status
            archived: Filter by archived status

        Returns:
            List of events
        """
        return self.gamma.get_events(
            limit=limit,
            offset=offset,
            active=active,
            closed=closed,
            archived=archived
        )

    def filter_events_for_trading(self, events: List[Event]) -> List[Event]:
        """
        Filter events for active trading (no restrictions, not archived/closed).

        From official Polymarket agents repository.

        Args:
            events: List of events to filter

        Returns:
            Filtered list of tradable events
        """
        return self.gamma.filter_events_for_trading(events)

    def get_all_tradeable_events(self, limit: int = 100) -> List[Event]:
        """
        Get all tradeable events in one call.

        From official Polymarket agents repository.

        Args:
            limit: Max results (default: 100)

        Returns:
            List of tradeable events
        """
        return self.gamma.get_all_tradeable_events(limit=limit)

    def get_orderbook(self, token_id: str) -> OrderBook:
        """Get order book for token."""
        return self.clob.get_orderbook(token_id)

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """
        Get midpoint price for token.

        Runs in thread pool to avoid blocking the event loop.

        Args:
            token_id: Token ID to get midpoint for

        Returns:
            Midpoint price or None if unavailable
        """
        return await asyncio.to_thread(self.clob.get_midpoint, token_id)

    def get_price(self, token_id: str, side: Side) -> Optional[float]:
        """Get price for token on specific side."""
        return self.clob.get_price(token_id, side.value)

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """
        Get last trade price for token (Phase 5 enhancement).

        Faster than fetching full orderbook when you only need last price.
        """
        return self.clob.get_last_trade_price(token_id)

    def get_last_trades_prices(self, token_ids: List[str]) -> Dict[str, Optional[float]]:
        """
        Get last trade prices for multiple tokens (Phase 5 enhancement).

        Batch endpoint - more efficient than individual calls.
        """
        return self.clob.get_last_trades_prices(token_ids)

    def get_server_time(self) -> int:
        """
        Get Polymarket server timestamp in milliseconds (Phase 5 enhancement).

        Use for GTD order validation and clock synchronization.
        """
        return self.clob.get_server_time()

    def get_ok(self) -> bool:
        """
        CLOB health check (Phase 5 enhancement).

        Returns True if CLOB server is operational.
        """
        return self.clob.get_ok()

    def get_simplified_markets(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get simplified market data with pagination (Phase 5 enhancement).

        Lightweight alternative to full market queries.
        """
        return self.clob.get_simplified_markets(next_cursor)

    # ========== Public CLOB API (New Methods) ==========

    def get_spread(self, token_id: str) -> Optional[float]:
        """
        Get bid-ask spread for a token.

        Public endpoint - no authentication required, doesn't consume
        trading rate limits.

        Args:
            token_id: Token ID

        Returns:
            Spread (ask - bid), or None if unavailable
        """
        result = self.public_clob.get_spread(token_id)
        return float(result) if result is not None else None

    def get_spreads(self, token_ids: List[str]) -> Dict[str, Optional[float]]:
        """
        Get bid-ask spreads for multiple tokens (batch operation).

        More efficient than individual calls. Public endpoint.

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id -> spread
        """
        result = self.public_clob.get_spreads(token_ids)
        return {k: (float(v) if v is not None else None) for k, v in result.items()}

    def get_midpoints(self, token_ids: List[str]) -> Dict[str, Optional[float]]:
        """
        Get midpoint prices for multiple tokens (batch operation).

        More efficient than individual calls. Public endpoint.

        Args:
            token_ids: List of token IDs

        Returns:
            Dictionary mapping token_id -> midpoint price
        """
        result = self.public_clob.get_midpoints(token_ids)
        return {k: (float(v) if v is not None else None) for k, v in result.items()}

    def get_prices(self, params: List[Dict[str, str]]) -> Dict[str, Optional[float]]:
        """
        Get prices for multiple tokens and sides (batch operation).

        Public endpoint - useful for bulk price queries.

        Args:
            params: List of dicts with {"token_id": str, "side": str}

        Returns:
            Dictionary with results (key format: "{token_id}_{side}")
        """
        result = self.public_clob.get_prices(params)
        return {k: (float(v) if v is not None else None) for k, v in result.items()}

    def get_best_bid_ask(self, token_id: str) -> Optional[tuple[float, float]]:
        """
        Get best bid and ask prices (top of book).

        More efficient than fetching full orderbook when you only need
        best prices.

        Args:
            token_id: Token ID

        Returns:
            (best_bid, best_ask) tuple, or None if unavailable
        """
        result = self.public_clob.get_best_bid_ask(token_id)
        if result is None:
            return None
        return (float(result[0]), float(result[1]))

    def get_liquidity_depth(
        self,
        token_id: str,
        price_range: float = 0.05
    ) -> Dict[str, Any]:
        """
        Calculate liquidity depth within price range.

        Analyzes orderbook to determine available liquidity within a
        percentage of the best bid/ask.

        Args:
            token_id: Token ID
            price_range: Price range (e.g., 0.05 for ±5%)

        Returns:
            {
                "bid_depth": float,
                "ask_depth": float,
                "bid_levels": int,
                "ask_levels": int,
                "total_depth": float
            }
        """
        from decimal import Decimal
        result = self.public_clob.get_liquidity_depth(token_id, Decimal(str(price_range)))
        return {
            "bid_depth": float(result["bid_depth"]),
            "ask_depth": float(result["ask_depth"]),
            "bid_levels": result["bid_levels"],
            "ask_levels": result["ask_levels"],
            "total_depth": float(result["total_depth"])
        }

    def get_markets_full(self, next_cursor: str = "MA==") -> Dict[str, Any]:
        """
        Get complete market list (full data).

        More comprehensive than get_simplified_markets but slower.
        Public endpoint.

        Args:
            next_cursor: Pagination cursor

        Returns:
            Market data with pagination
        """
        return self.public_clob.get_markets(next_cursor)

    def get_market_by_condition(self, condition_id: str) -> Dict[str, Any]:
        """
        Get single market details by condition ID.

        Public endpoint.

        Args:
            condition_id: Market condition ID (0x...)

        Returns:
            Market data dictionary
        """
        return self.public_clob.get_market(condition_id)

    def get_market_trades_events(self, condition_id: str) -> List[Dict[str, Any]]:
        """
        Get trade events for a market.

        Public endpoint.

        Args:
            condition_id: Market condition ID

        Returns:
            List of trade event dictionaries
        """
        return self.public_clob.get_market_trades_events(condition_id)

    def is_order_scoring(self, order_id: str) -> bool:
        """
        Check if order earns maker rebates (Strategy-4 enhancement).

        Returns True if order is earning 2% maker rebates on Polymarket.
        """
        return self.clob.is_order_scoring(order_id)

    def are_orders_scoring(self, order_ids: List[str]) -> Dict[str, bool]:
        """
        Check if multiple orders earn maker rebates (Strategy-4 enhancement).

        Batch version of is_order_scoring().
        Returns dict mapping order_id to scoring status.
        """
        return self.clob.are_orders_scoring(order_ids)

    # ========== Trading Operations ==========

    async def place_order(
        self,
        order: OrderRequest,
        wallet_id: Optional[str] = None,
        skip_balance_check: bool = False,
        idempotency_key: Optional[str] = None
    ) -> OrderResponse:
        """
        Place limit order with balance monitoring.

        Runs in thread pool to avoid blocking the event loop.

        Args:
            order: Order request
            wallet_id: Wallet to use (uses default if not specified)
            skip_balance_check: Skip pre-flight balance check
            idempotency_key: Optional key for deterministic order hash
                           (prevents duplicate orders on retry)

        Returns:
            Order response

        Raises:
            ValidationError: If order is invalid
            InsufficientBalanceError: If insufficient balance
            OrderRejectedError: If order is rejected
        """
        start_time = time.time()

        # Track reserved balance for cleanup on failure (defensive programming)
        reserved_for_cleanup = Decimal("0")  # Use Decimal instead of float
        wallet_key = wallet_id or "default"

        try:
            credentials = self.key_manager.get_wallet(wallet_id)

            if not self.key_manager.has_api_credentials(wallet_id):
                raise AuthenticationError(
                    f"Wallet {wallet_id} has no API credentials"
                )

            # Validate order
            from .utils.validators import validate_order
            validate_order(
                order.token_id,
                order.price,
                order.size,
                order.side.value,
                min_size=self.settings.min_order_size
            )

            # Pre-flight balance check
            if not skip_balance_check:
                await self._check_balance(order, wallet_id)  # CRITICAL FIX: Pass wallet_id, not address

            # Build and sign order
            signed_order = self._build_signed_order(order, credentials, idempotency_key)

            # Submit order (run in thread pool to avoid blocking)
            response = await asyncio.to_thread(
                self.clob.post_order,
                signed_order=signed_order,
                address=credentials.address,
                api_key=credentials.api_key,
                api_secret=credentials.api_secret,
                api_passphrase=credentials.api_passphrase,
                order_type=order.order_type.value
            )

            # CRITICAL FIX: Reserve balance after successful order placement
            # Use Decimal throughout for precision
            if order.side == Side.BUY and response.order_id:
                reserved_amount = Decimal(str(order.size))  # Keep as Decimal
                async with self._balance_lock:
                    current = self._reserved_balances.get(wallet_key, Decimal("0"))
                    self._reserved_balances[wallet_key] = current + reserved_amount
                    reserved_for_cleanup = reserved_amount  # Track for exception cleanup
                    logger.debug(
                        f"Reserved ${reserved_amount:.2f} for order {response.order_id} "
                        f"(total reserved: ${self._reserved_balances[wallet_key]:.2f})"
                    )

            # Track metrics
            self.metrics.track_order(
                wallet=wallet_id or "default",
                side=order.side.value,
                status=response.status.value if response.status else "unknown"
            )
            self.metrics.track_order_latency(
                wallet=wallet_id or "default",
                duration=time.time() - start_time
            )

            return response

        except Exception as e:
            # CRITICAL FIX: Release reserved balance if we reserved it before the error
            # This prevents balance leaks if code after reservation fails
            if reserved_for_cleanup > Decimal("0"):
                await self.release_reserved_balance(reserved_for_cleanup, wallet_id)
                logger.warning(
                    f"Released ${reserved_for_cleanup:.2f} reserved balance due to exception: {e}"
                )

            self.metrics.track_order(
                wallet=wallet_id or "default",
                side=order.side.value,
                status="error"
            )
            raise

    async def release_reserved_balance(
        self,
        amount: Decimal,  # Changed to Decimal only (no float)
        wallet_id: Optional[str] = None,
        order_id: Optional[str] = None
    ) -> None:
        """
        Release reserved balance when an order is cancelled or filled.

        CRITICAL: Call this when orders complete to free up balance for new orders.

        Args:
            amount: USD amount to release (Decimal for precision)
            wallet_id: Wallet identifier
            order_id: Optional order ID for logging

        Raises:
            BalanceTrackingError: If trying to release more than reserved
        """
        async with self._balance_lock:
            wallet_key = wallet_id or "default"
            current = self._reserved_balances.get(wallet_key, Decimal("0"))

            # Ensure amount is Decimal
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))

            # Check for over-release (raise instead of silent clamping)
            if amount > current:
                raise BalanceTrackingError(
                    f"Over-release detected: trying to release ${amount} "
                    f"but only ${current} reserved for wallet {wallet_key}, order {order_id}"
                )

            # Release balance (no clamping - error raised above)
            self._reserved_balances[wallet_key] = current - amount

            logger.debug(
                f"Released ${amount:.2f} for order {order_id or 'unknown'} "
                f"(remaining reserved: ${self._reserved_balances[wallet_key]:.2f})"
            )

    async def get_reserved_balance(self, wallet_id: Optional[str] = None) -> Decimal:
        """
        Get currently reserved balance for a wallet.

        Args:
            wallet_id: Wallet identifier

        Returns:
            Reserved USD amount (Decimal for precision)
        """
        async with self._balance_lock:
            return self._reserved_balances.get(wallet_id or "default", Decimal("0"))

    async def _check_balance(self, order: OrderRequest, wallet_id: Optional[str]) -> None:
        """
        Pre-flight balance check with reserved balance tracking.

        CRITICAL FIX: Accounts for reserved balance (pending orders) to prevent over-ordering.
        """
        try:
            balance = self.get_balances(wallet_id=wallet_id)

            # Get reserved balance for this wallet (now returns Decimal)
            reserved = await self.get_reserved_balance(wallet_id)

            if order.side == Side.BUY:
                # BUY: Need size USDC (size is already in USD)
                required = order.size
                available = Decimal(str(balance.collateral)) - reserved

                if available < required:
                    raise InsufficientBalanceError(
                        f"Insufficient available USDC: need {required:.2f}, "
                        f"have {available:.2f} (total: {balance.collateral:.2f}, reserved: {reserved:.2f})"
                    )
            else:
                # SELL: Check token balance (use balance.tokens, not positions)
                # CRITICAL FIX: Use actual token balance, not stale position data
                token_balance = balance.tokens.get(order.token_id, 0.0)

                # Validate price before division (CRITICAL: prevent ZeroDivisionError)
                if order.price <= 0:
                    raise ValidationError(f"Invalid order price: {order.price} (must be > 0)")

                # size is in USD, convert to token quantity
                tokens_needed = order.size / order.price

                if token_balance < tokens_needed:
                    raise InsufficientBalanceError(
                        f"Insufficient token balance: need {tokens_needed:.2f}, have {token_balance:.2f} "
                        f"(selling ${order.size:.2f} worth at ${order.price:.2f}/token)"
                    )

            if balance.collateral < self._min_balance_warning:
                logger.warning(f"Low balance: {balance.collateral:.2f} USDC")

            credentials = self.key_manager.get_wallet(wallet_id)
            self.metrics.set_balance(credentials.address, balance.collateral)
        except InsufficientBalanceError:
            raise
        except Exception as e:
            logger.warning(f"Balance check failed (continuing): {e}")

    def _build_signed_order(
        self,
        order: OrderRequest,
        credentials: WalletCredentials,
        idempotency_key: Optional[str] = None
    ) -> dict:
        """
        Build and sign order.

        Fetches market metadata (tick size, fee rate, neg risk) from CLOB API
        for accurate order validation before signing.

        Args:
            order: Order request
            credentials: Wallet credentials
            idempotency_key: Optional key for deterministic salt generation
                           (prevents duplicate orders on retry)

        Returns:
            Signed order dict

        Raises:
            TradingError: If building/signing fails
        """
        # Get or fetch nonce
        nonce = self._get_nonce(credentials.address)

        # Resolve tick size from API (Phase 6 enhancement)
        tick_size = self._resolve_tick_size(order.token_id)

        # Resolve fee rate from API (Phase 6 enhancement)
        fee_rate_bps = self._resolve_fee_rate(order.token_id)

        # Resolve neg risk flag from API (Phase 6 enhancement)
        neg_risk = self._resolve_neg_risk(order.token_id)

        # Build and sign order with resolved metadata
        signed_order = self.order_builder.build_order(
            order=order,
            private_key=credentials.private_key,
            address=credentials.address,
            nonce=nonce,
            tick_size=tick_size,
            fee_rate_bps=fee_rate_bps,
            neg_risk=neg_risk,
            idempotency_key=idempotency_key
        )

        # CRITICAL FIX: Don't increment nonce here - already incremented by get_and_increment()
        # Double increment was causing nonce gaps and order rejections
        # self.metadata_cache.increment_nonce(credentials.address)  # REMOVED

        return signed_order

    def _get_nonce(self, address: str) -> int:
        """
        Get current nonce for address (thread-safe, race-condition free).

        Uses AtomicNonceManager for all nonce operations.
        Fetches from API on first use, then manages atomically.

        Args:
            address: Wallet address

        Returns:
            Current nonce

        Raises:
            TradingError: If nonce fetch fails
        """
        # Try to get and increment from atomic manager
        nonce = self._nonce_manager.get_and_increment(address)

        if nonce is not None:
            # Already initialized, use atomic counter
            logger.debug(f"Using atomic nonce counter {nonce} for {address}")
            return nonce

        # First use for this address - try to fetch from API
        try:
            logger.debug(f"Fetching initial nonce from API for {address}")

            try:
                response = self.clob.get(
                    f"/nonce",
                    params={"address": address},
                    rate_limit_key="GET:/nonce",
                    retry=True
                )
                api_nonce = int(response.get("nonce", 0))

                # Initialize atomic counter with API nonce
                self._nonce_manager.set(address, api_nonce)

                logger.info(f"Initialized nonce from API for {address}: {api_nonce}")
                return api_nonce

            except (APIError, TimeoutError, KeyError, ValueError, TypeError) as e:
                logger.debug(f"Failed to fetch nonce from API for {address}: {e}")
                # Fallback: Initialize with timestamp + cryptographic randomness
                # SECURITY FIX (SEC-005): Add randomness to prevent nonce prediction attacks
                base_nonce = int(time.time() * 1000)
                random_offset = secrets.randbelow(100000)  # 0-99,999 random offset
                timestamp_nonce = base_nonce + random_offset
                self._nonce_manager.set(address, timestamp_nonce)

                logger.info(f"Initialized nonce with secure timestamp for {address}: {timestamp_nonce}")
                return timestamp_nonce

        except Exception as e:
            logger.error(f"Failed to initialize nonce for {address}: {e}")
            # Ultimate fallback: timestamp-based nonce with crypto randomness
            # SECURITY FIX (SEC-005): Add randomness to prevent nonce prediction attacks
            base_nonce = int(time.time() * 1000)
            random_offset = secrets.randbelow(100000)  # 0-99,999 random offset
            timestamp_nonce = base_nonce + random_offset
            self._nonce_manager.set(address, timestamp_nonce)

            logger.warning(f"Using secure timestamp nonce (after error) for {address}: {timestamp_nonce}")
            return timestamp_nonce

    def _resolve_tick_size(self, token_id: str) -> float:
        """
        Resolve tick size for token from CLOB API.

        Phase 6 enhancement: Always fetches from API (like official client).

        Args:
            token_id: Token ID

        Returns:
            Minimum tick size for market

        Raises:
            TradingError: If fetch fails
        """
        # Check cache first
        cached = self.metadata_cache.get_tick_size(token_id)
        if cached is not None:
            logger.debug(f"Using cached tick size {cached} for {token_id}")
            return cached

        # Fetch from CLOB API
        try:
            tick_size = self.clob.get_tick_size(token_id)

            # Cache it
            self.metadata_cache.set_tick_size(token_id, tick_size)

            logger.debug(f"Fetched tick size {tick_size} for {token_id}")
            return tick_size

        except Exception as e:
            # Fallback to default if API fails
            logger.warning(f"Failed to fetch tick size for {token_id}, using default: {e}")
            default_tick_size = 0.01
            self.metadata_cache.set_tick_size(token_id, default_tick_size)
            return default_tick_size

    def _resolve_fee_rate(self, token_id: str) -> int:
        """
        Resolve fee rate for token from CLOB API.

        NOTE: Polymarket has NO trading fees (https://docs.polymarket.com/polymarket-learn/trading/fees).
        This function exists for order signing compatibility with the CLOB protocol.
        Always returns 0.

        Args:
            token_id: Token ID

        Returns:
            Fee rate in basis points (always 0)
        """
        # Check cache first
        cached = self.metadata_cache.get_fee_rate(token_id)
        if cached is not None:
            logger.debug(f"Using cached fee rate {cached}bps for {token_id}")
            return cached

        # Fetch from CLOB API (always returns 0 for Polymarket)
        try:
            fee_rate_bps = self.clob.get_fee_rate_bps(token_id)

            # Cache it
            self.metadata_cache.set_fee_rate(token_id, fee_rate_bps)

            logger.debug(f"Fetched fee rate {fee_rate_bps}bps for {token_id}")
            return fee_rate_bps

        except Exception as e:
            # Fallback to default if API fails
            logger.debug(f"Failed to fetch fee rate for {token_id}, using default 0: {e}")
            default_fee_rate = 0  # Polymarket has no trading fees
            self.metadata_cache.set_fee_rate(token_id, default_fee_rate)
            return default_fee_rate

    def _resolve_neg_risk(self, token_id: str) -> bool:
        """
        Resolve neg risk flag for token from CLOB API.

        Phase 6 enhancement: Fetches from API when available.

        Args:
            token_id: Token ID

        Returns:
            True if neg risk market

        Raises:
            TradingError: If fetch fails
        """
        # Check cache first
        cached = self.metadata_cache.get_neg_risk(token_id)
        if cached is not None:
            logger.debug(f"Using cached neg risk {cached} for {token_id}")
            return cached

        # Fetch from CLOB API
        try:
            neg_risk = self.clob.get_neg_risk(token_id)

            # Cache it
            self.metadata_cache.set_neg_risk(token_id, neg_risk)

            logger.debug(f"Fetched neg risk {neg_risk} for {token_id}")
            return neg_risk

        except Exception as e:
            # Fallback to default if API fails
            logger.warning(f"Failed to fetch neg risk for {token_id}, using default: {e}")
            default_neg_risk = False
            self.metadata_cache.set_neg_risk(token_id, default_neg_risk)
            return default_neg_risk

    def place_orders_batch(
        self,
        orders: List[OrderRequest],
        wallet_id: Optional[str] = None,
        skip_balance_check: bool = False
    ) -> List[OrderResponse]:
        """
        Place multiple orders in a single batch request.

        CRITICAL for Strategy-3: 10x faster than sequential orders.

        Args:
            orders: List of order requests
            wallet_id: Wallet to use (uses default if None)
            skip_balance_check: Skip pre-flight balance validation

        Returns:
            List of order responses (one per order)

        Raises:
            AuthenticationError: If wallet has no API credentials
            ValidationError: If any order is invalid
            TradingError: If batch submission fails

        Example:
            >>> # Place 10 orders simultaneously
            >>> orders = [
            ...     OrderRequest(token_id="123", price=0.50, size=10.0, side=Side.BUY),
            ...     OrderRequest(token_id="456", price=0.60, size=20.0, side=Side.BUY),
            ... ]
            >>> responses = client.place_orders_batch(orders, wallet_id="strategy3")
            >>> successful = [r for r in responses if r.success]
            >>> print(f"Placed {len(successful)}/{len(orders)} orders")
        """
        if not orders:
            return []

        credentials = self.key_manager.get_wallet(wallet_id)
        if not self.key_manager.has_api_credentials(wallet_id):
            raise AuthenticationError(f"Wallet {wallet_id} has no API credentials")

        # Validate all orders first
        from .utils.validators import validate_order
        for idx, order in enumerate(orders):
            try:
                validate_order(
                    order.token_id,
                    order.price,
                    order.size,
                    order.side.value,
                    min_size=self.settings.min_order_size
                )
            except Exception as e:
                raise ValidationError(f"Order {idx} invalid: {e}")

        # Build and sign all orders
        signed_orders = []
        for order in orders:
            # Note: Batch orders use random salts (no idempotency key)
            # Each order in a batch is treated as independent
            signed_order = self._build_signed_order(order, credentials, idempotency_key=None)
            signed_orders.append(signed_order)

        # Submit batch
        responses = self.clob.post_orders_batch(
            signed_orders=signed_orders,
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase
        )

        # Track metrics
        successful = sum(1 for r in responses if r.success)
        logger.info(f"Batch order placement: {successful}/{len(orders)} successful")

        return responses

    def get_orderbooks_batch(
        self,
        token_ids: List[str]
    ) -> Dict[str, OrderBook]:
        """
        Get orderbooks for multiple tokens simultaneously.

        CRITICAL for Strategy-3: 10x faster than sequential fetches.

        Args:
            token_ids: List of token IDs

        Returns:
            Dict mapping token_id to OrderBook

        Raises:
            TradingError: If request fails

        Example:
            >>> # Get orderbooks for 10 markets
            >>> token_ids = ["123", "456", "789"]
            >>> books = client.get_orderbooks_batch(token_ids)
            >>> for token_id, book in books.items():
            ...     print(f"{token_id}: bid={book.best_bid}, ask={book.best_ask}")
        """
        return self.clob.get_orderbooks_batch(token_ids)

    async def cancel_order(
        self,
        order_id: str,
        wallet_id: Optional[str] = None
    ) -> bool:
        """
        Cancel single order.

        Runs in thread pool to avoid blocking the event loop.

        Args:
            order_id: Order ID
            wallet_id: Wallet to use

        Returns:
            True if cancelled
        """
        credentials = self.key_manager.get_wallet(wallet_id)

        return await asyncio.to_thread(
            self.clob.cancel_order,
            order_id=order_id,
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase
        )

    def cancel_all_orders(
        self,
        wallet_id: Optional[str] = None,
        market_id: Optional[str] = None
    ) -> int:
        """
        Cancel all open orders.

        Args:
            wallet_id: Wallet to use
            market_id: Optional market filter

        Returns:
            Number of orders cancelled
        """
        credentials = self.key_manager.get_wallet(wallet_id)

        return self.clob.cancel_all_orders(
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase,
            market_id=market_id
        )

    def cancel_market_orders(
        self,
        market_id: str,
        wallet_id: Optional[str] = None
    ) -> int:
        """
        Cancel all orders for a specific market (convenient for market exit).

        Args:
            market_id: Market condition ID to cancel orders for
            wallet_id: Wallet to use (uses default if None)

        Returns:
            Number of orders cancelled

        Raises:
            TradingError: If cancellation fails
        """
        credentials = self.key_manager.get_wallet(wallet_id)

        return self.clob.cancel_market_orders(
            market_id=market_id,
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase
        )

    def get_orders(
        self,
        wallet_id: Optional[str] = None,
        market: Optional[str] = None
    ) -> List[Order]:
        """
        Get open orders.

        Args:
            wallet_id: Wallet to query
            market: Optional market filter

        Returns:
            List of orders
        """
        credentials = self.key_manager.get_wallet(wallet_id)

        return self.clob.get_orders(
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase,
            market=market
        )

    def get_balances(
        self,
        wallet_id: Optional[str] = None
    ) -> Balance:
        """
        Get wallet balances.

        Args:
            wallet_id: Wallet to query

        Returns:
            Balance information
        """
        credentials = self.key_manager.get_wallet(wallet_id)

        return self.clob.get_balances(
            address=credentials.address,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            api_passphrase=credentials.api_passphrase
        )

    # ========== Utility Methods ==========

    def get_rate_limiter_stats(self) -> dict:
        """Get rate limiter statistics."""
        if self.rate_limiter:
            return self.rate_limiter.get_stats()
        return {}

    def get_circuit_breaker_state(self) -> Optional[str]:
        """Get circuit breaker state."""
        if self.circuit_breaker:
            return self.circuit_breaker.state
        return None

    def reset_circuit_breaker(self) -> None:
        """Reset circuit breaker."""
        if self.circuit_breaker:
            self.circuit_breaker.reset()

    # ========== Dashboard Operations ==========

    async def get_positions(
        self,
        wallet_id: Optional[str] = None,
        **kwargs
    ) -> List[Position]:
        """
        Get current positions with P&L tracking.

        Runs in thread pool to avoid blocking the event loop.

        Args:
            wallet_id: Wallet to query (uses default if not specified)
            **kwargs: Additional filters (market, size_threshold, etc.)

        Returns:
            List of positions with comprehensive P&L metrics
        """
        credentials = self.key_manager.get_wallet(wallet_id)
        return await asyncio.to_thread(
            self.data.get_positions,
            user=credentials.address,
            **kwargs
        )

    def get_trades(
        self,
        wallet_id: Optional[str] = None,
        **kwargs
    ) -> List[Trade]:
        """
        Get trade history for wallet.

        Args:
            wallet_id: Wallet to query
            **kwargs: Additional filters (limit, market, side, etc.)

        Returns:
            List of trades ordered by most recent first
        """
        credentials = self.key_manager.get_wallet(wallet_id)
        return self.data.get_trades(user=credentials.address, **kwargs)

    def get_activity(
        self,
        wallet_id: Optional[str] = None,
        **kwargs
    ) -> List[Activity]:
        """
        Get onchain activity for wallet.

        Args:
            wallet_id: Wallet to query
            **kwargs: Additional filters (type, market, etc.)

        Returns:
            List of activity records (trades, splits, merges, redemptions)
        """
        credentials = self.key_manager.get_wallet(wallet_id)
        return self.data.get_activity(user=credentials.address, **kwargs)

    def get_portfolio_value(
        self,
        wallet_id: Optional[str] = None,
        market: Optional[str] = None
    ) -> "PortfolioValue":
        """
        Get total USD value of portfolio with detailed breakdown.

        Args:
            wallet_id: Wallet to query
            market: Optional market filter

        Returns:
            PortfolioValue with detailed metrics:
            - value: Total portfolio value (legacy)
            - bets: Total bet value
            - cash: Available USDC
            - equity_total: Total portfolio value (bets + cash)

        Example:
            portfolio = client.get_portfolio_value(wallet_id="strategy1")
            print(f"Total value: ${portfolio.equity_total}")
            print(f"Bets: ${portfolio.bets}, Cash: ${portfolio.cash}")
        """
        credentials = self.key_manager.get_wallet(wallet_id)
        return self.data.get_portfolio_value(user=credentials.address, market=market)

    def get_market_holders(
        self,
        market: str,
        limit: int = 100,
        min_balance: int = 1
    ) -> List[Holder]:
        """
        Get top holders in a market.

        Useful for whale discovery and tracking large position holders.

        Args:
            market: Market conditionId
            limit: Max holders (default: 100, max: 500)
            min_balance: Minimum position size to include (default: 1)

        Returns:
            List of holders grouped by token, sorted by position size

        Example:
            # Find whales with positions > $5000
            whales = client.get_market_holders(
                market="0x123...",
                limit=500,
                min_balance=5000
            )
            for whale in whales:
                print(f"{whale.pseudonym}: {whale.amount} @ {whale.proxy_wallet}")
        """
        return self.data.get_holders(market=market, limit=limit, min_balance=min_balance)

    def get_leaderboard(
        self,
        limit: int = 100,
        min_pnl: Optional[float] = None
    ) -> List["LeaderboardTrader"]:
        """
        Get leaderboard of top traders.

        Args:
            limit: Max traders to return (default: 100)
            min_pnl: Minimum PnL filter (optional)

        Returns:
            List of leaderboard traders ordered by rank
        """
        return self.data.get_leaderboard(limit=limit, min_pnl=min_pnl)

    # ========== Multi-Wallet Batch Operations (Strategy-3 Optimized) ==========

    def get_positions_batch(
        self,
        wallet_addresses: List[str],
        **kwargs
    ) -> Dict[str, List[Position]]:
        """
        Get positions for multiple wallets efficiently.

        Optimized for Strategy-3's 100+ wallet tracking with concurrent requests.

        Args:
            wallet_addresses: List of wallet addresses
            **kwargs: Filters applied to all wallets

        Returns:
            Dict mapping wallet address to positions
        """
        import concurrent.futures

        results = {}

        def fetch_positions(address: str) -> tuple[str, List[Position]]:
            try:
                positions = self.data.get_positions(user=address, **kwargs)
                return (address, positions)
            except Exception as e:
                logger.warning(f"Failed to get positions for {address}: {e}")
                return (address, [])

        # Parallel fetch with thread pool (IO-bound operations)
        max_workers = self.settings.batch_max_workers
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch_positions, addr) for addr in wallet_addresses]

            for future in concurrent.futures.as_completed(futures):
                try:
                    address, positions = future.result()
                    results[address] = positions
                except Exception as e:
                    logger.error(f"Batch fetch error: {e}")

        logger.info(f"Fetched positions for {len(results)}/{len(wallet_addresses)} wallets")
        return results

    def get_trades_batch(
        self,
        wallet_addresses: List[str],
        **kwargs
    ) -> Dict[str, List[Trade]]:
        """
        Get trades for multiple wallets efficiently with concurrent requests.

        Args:
            wallet_addresses: List of wallet addresses
            **kwargs: Filters applied to all wallets

        Returns:
            Dict mapping wallet address to trades
        """
        import concurrent.futures

        results = {}

        def fetch_trades(address: str) -> tuple[str, List[Trade]]:
            try:
                trades = self.data.get_trades(user=address, **kwargs)
                return (address, trades)
            except Exception as e:
                logger.warning(f"Failed to get trades for {address}: {e}")
                return (address, [])

        max_workers = self.settings.batch_max_workers
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch_trades, addr) for addr in wallet_addresses]

            for future in concurrent.futures.as_completed(futures):
                try:
                    address, trades = future.result()
                    results[address] = trades
                except Exception as e:
                    logger.error(f"Batch fetch error: {e}")

        return results

    def get_activity_batch(
        self,
        wallet_addresses: List[str],
        **kwargs
    ) -> Dict[str, List[Activity]]:
        """
        Get activity for multiple wallets efficiently with concurrent requests.

        Args:
            wallet_addresses: List of wallet addresses
            **kwargs: Filters applied to all wallets

        Returns:
            Dict mapping wallet address to activities
        """
        import concurrent.futures

        results = {}

        def fetch_activity(address: str) -> tuple[str, List[Activity]]:
            try:
                activities = self.data.get_activity(user=address, **kwargs)
                return (address, activities)
            except Exception as e:
                logger.warning(f"Failed to get activity for {address}: {e}")
                return (address, [])

        max_workers = self.settings.batch_max_workers
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch_activity, addr) for addr in wallet_addresses]

            for future in concurrent.futures.as_completed(futures):
                try:
                    address, activities = future.result()
                    results[address] = activities
                except Exception as e:
                    logger.error(f"Batch fetch error: {e}")

        return results

    def aggregate_multi_wallet_metrics(
        self,
        wallet_addresses: List[str],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get aggregated metrics across multiple wallets.

        Convenience method combining position fetch + aggregation.

        Args:
            wallet_addresses: List of wallet addresses
            **kwargs: Position filters

        Returns:
            Aggregated metrics (total P&L, top performers, etc.)
        """
        from .utils.dashboard_helpers import aggregate_multi_wallet_positions

        # Fetch positions for all wallets
        wallet_positions = self.get_positions_batch(wallet_addresses, **kwargs)

        # Aggregate metrics
        return aggregate_multi_wallet_positions(wallet_positions)

    def detect_signals(
        self,
        wallet_addresses: List[str],
        min_wallets: int = 5,
        min_agreement: float = 0.6,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Detect consensus signals from multiple wallets.

        Strategy-3 specific: Find markets where N+ wallets agree.

        Args:
            wallet_addresses: List of wallet addresses to track
            min_wallets: Minimum wallets for consensus (default: 5)
            min_agreement: Minimum agreement ratio (default: 60%)
            **kwargs: Position filters

        Returns:
            List of consensus signals sorted by strength
        """
        from .utils.dashboard_helpers import detect_consensus_signals

        # Fetch positions for all wallets
        wallet_positions = self.get_positions_batch(wallet_addresses, **kwargs)

        # Detect consensus
        return detect_consensus_signals(wallet_positions, min_wallets, min_agreement)

    # ========== Production Operations ==========

    def _shutdown_handler(self, signum, frame):
        """
        Graceful shutdown handler for SIGTERM/SIGINT.

        Cancels all inflight orders before exiting.
        """
        logger.warning(f"Shutdown signal {signum} received")
        self._shutdown_requested = True

        # Cancel all inflight orders
        if self._inflight_orders:
            logger.info(f"Cancelling {len(self._inflight_orders)} inflight orders")
            for order_id in self._inflight_orders:
                try:
                    self.cancel_order(order_id)
                    logger.info(f"Cancelled order {order_id}")
                except Exception as e:
                    logger.error(f"Failed to cancel order {order_id}: {e}")

        self.close()
        sys.exit(0)

    def health_check(self) -> Dict[str, Any]:
        """
        Health check for Docker/K8s probes.

        Returns:
            Dict with status, circuit breaker state, and connectivity
        """
        try:
            # Check CLOB connectivity
            clob_health = self.clob.health_check()

            # Check circuit breaker
            cb_state = self.get_circuit_breaker_state() or "disabled"

            # Check rate limiter
            rate_stats = self.get_rate_limiter_stats()

            return {
                "status": "healthy" if clob_health["status"] == "healthy" else "degraded",
                "clob": clob_health,
                "circuit_breaker": cb_state,
                "rate_limiter": rate_stats,
                "inflight_orders": len(self._inflight_orders),
                "timestamp": time.time()
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": time.time()
            }

    # ========== Real-Time WebSocket ==========

    def subscribe_orderbook(
        self,
        token_id: str,
        callback: Callable[[OrderBook], None],
        wallet_id: Optional[str] = None
    ) -> None:
        """
        Subscribe to real-time orderbook updates via WebSocket.

        CRITICAL for HFT: 100ms updates vs 1s polling.

        Args:
            token_id: Token ID to track
            callback: Function called on each update with OrderBook
            wallet_id: Wallet for authenticated feed (optional)

        Example:
            >>> def on_update(book):
            ...     print(f"Best bid: {book.best_bid}, Best ask: {book.best_ask}")
            >>> client.subscribe_orderbook("123456", on_update)
        """
        self._ensure_websocket(wallet_id)

        def handle_market_update(data: Dict[str, Any]):
            try:
                # Parse market data to orderbook
                bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
                asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

                book = OrderBook(
                    token_id=token_id,
                    bids=bids,
                    asks=asks
                )
                callback(book)
            except Exception as e:
                logger.error(f"Error processing orderbook update: {e}")

        self._ws.subscribe_market(token_id, handle_market_update)
        logger.info(f"Subscribed to orderbook updates for {token_id}")

    def subscribe_user_orders(
        self,
        callback: Callable[[Dict[str, Any]], None],
        wallet_id: Optional[str] = None
    ) -> None:
        """
        Subscribe to real-time order fill notifications via WebSocket.

        CRITICAL for order management: Instant fill notifications.

        Args:
            callback: Function called on order updates
            wallet_id: Wallet to track

        Example:
            >>> def on_fill(order_data):
            ...     print(f"Order filled: {order_data['orderId']}")
            >>> client.subscribe_user_orders(on_fill, wallet_id="strategy1")
        """
        self._ensure_websocket(wallet_id)

        def handle_user_update(data: Dict[str, Any]):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error processing user update: {e}")

        self._ws.subscribe_user(handle_user_update)
        logger.info("Subscribed to user order updates")

    def unsubscribe_all(self) -> None:
        """
        Unsubscribe from all WebSocket feeds.

        Example:
            >>> client.unsubscribe_all()
        """
        if self._ws:
            self._ws.disconnect()
            self._ws = None
            logger.info("Unsubscribed from all WebSocket feeds")

    def is_websocket_connected(self) -> bool:
        """
        Check if WebSocket is currently connected and running.

        Returns:
            True if WebSocket is connected and active, False otherwise

        Example:
            >>> if client.is_websocket_connected():
            >>>     print("WebSocket is active")
        """
        return self._ws is not None and self._ws._running

    def _ensure_websocket(self, wallet_id: Optional[str] = None) -> None:
        """Ensure WebSocket is initialized."""
        if self._ws is None:
            # Get API key if wallet provided
            api_key = None
            if wallet_id:
                credentials = self.key_manager.get_wallet(wallet_id)
                api_key = credentials.api_key

            self._ws = WebSocketClient(
                ws_url=self.settings.ws_url,
                api_key=api_key,
                reconnect_delay=self.settings.ws_reconnect_delay,
                max_reconnects=self.settings.ws_max_reconnects
            )
            self._ws.connect()
            logger.info("WebSocket connected")

    def _ensure_rtds(self) -> None:
        """
        Ensure RTDS client is initialized (thread-safe).

        ZERO ASSUMPTIONS:
        - Checks if RTDS is enabled before init
        - Thread-safe initialization with lock
        - Handles connection failures gracefully
        - Logs all state transitions

        Raises:
            RuntimeError: If RTDS is disabled in settings
        """
        # Check if RTDS is enabled
        if not self.settings.enable_rtds:
            raise RuntimeError(
                "RTDS is disabled in settings. Set POLYMARKET_ENABLE_RTDS=true to enable."
            )

        # Thread-safe lazy initialization
        with self._rtds_lock:
            if self._rtds is None:
                try:
                    logger.info("Initializing RTDS client...")

                    self._rtds = RealTimeDataClient(
                        host=self.settings.rtds_url,
                        on_connect=self._on_rtds_connect,
                        on_message=None,  # Set per-subscription
                        on_status_change=self._on_rtds_status_change,
                        auto_reconnect=self.settings.rtds_auto_reconnect,
                        ping_interval=self.settings.rtds_ping_interval
                    )

                    # Establish connection
                    self._rtds.connect()

                    # Wait for connection to establish (up to 10 seconds)
                    import time
                    connection_timeout = 10.0
                    poll_interval = 0.1
                    elapsed = 0.0

                    while elapsed < connection_timeout:
                        if self._rtds._is_connected():
                            logger.info("RTDS connection established")
                            break
                        time.sleep(poll_interval)
                        elapsed += poll_interval
                    else:
                        # Timeout reached without connection
                        logger.warning(
                            f"RTDS connection not established after {connection_timeout}s, "
                            "but client is initialized. Connection may establish asynchronously."
                        )

                    logger.info(
                        f"RTDS client initialized: {self.settings.rtds_url}",
                        extra={
                            "auto_reconnect": self.settings.rtds_auto_reconnect,
                            "ping_interval": self.settings.rtds_ping_interval,
                            "connected": self._rtds._is_connected()
                        }
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to initialize RTDS client: {e}",
                        exc_info=True,
                        extra={"rtds_url": self.settings.rtds_url}
                    )
                    # Set to None so retry is possible
                    self._rtds = None
                    raise RuntimeError(f"RTDS initialization failed: {e}") from e

    def _on_rtds_connect(self, client: RealTimeDataClient) -> None:
        """
        Callback when RTDS connects.

        ZERO ASSUMPTIONS:
        - May be called multiple times (reconnects)
        - Client may not have any active subscriptions
        - Exceptions won't crash RTDS thread
        """
        try:
            logger.info(
                "RTDS connected successfully",
                extra={"host": client.host, "status": "connected"}
            )
            # Future: Re-subscribe to streams after reconnect
        except Exception as e:
            logger.error(
                f"Error in RTDS connect callback: {e}",
                exc_info=True
            )

    def _on_rtds_status_change(self, status: ConnectionStatus) -> None:
        """
        Callback when RTDS connection status changes.

        ZERO ASSUMPTIONS:
        - Status may transition rapidly
        - May be called from any thread
        - Exceptions won't crash RTDS thread

        Args:
            status: New connection status
        """
        try:
            logger.info(
                f"RTDS status changed: {status.value}",
                extra={"status": status.value}
            )

            # Emit metrics if enabled
            if self.metrics:
                status_map = {
                    ConnectionStatus.CONNECTING: 0,
                    ConnectionStatus.CONNECTED: 1,
                    ConnectionStatus.DISCONNECTED: 2
                }
                # Record status change event
                # Note: metrics.record_rtds_status() would go here if implemented

        except Exception as e:
            logger.error(
                f"Error in RTDS status change callback: {e}",
                exc_info=True
            )

    # ========== Real-Time Data Service (RTDS) ==========

    def subscribe_activity_trades(
        self,
        callback: Callable[[Message], None],
        market_slug: Optional[str] = None,
        event_slug: Optional[str] = None
    ) -> None:
        """
        Subscribe to real-time trade activity.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type or exceptions will be logged
        - Either market_slug or event_slug can be specified (not both)
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called for each trade message (receives Message object)
            market_slug: Filter by specific market (optional)
            event_slug: Filter by event (all markets in event) (optional)

        Raises:
            ValueError: If both market_slug and event_slug provided
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_trade(msg: Message):
            ...     print(f"Trade: {msg.payload}")
            >>> client.subscribe_activity_trades(on_trade, market_slug="trump-2024")
        """
        import json

        # Input validation
        if market_slug and event_slug:
            raise ValueError("Cannot specify both market_slug and event_slug")

        # Ensure RTDS initialized
        self._ensure_rtds()

        # Build filters
        filters = None
        if market_slug:
            filters = json.dumps({"market_slug": market_slug})
        elif event_slug:
            filters = json.dumps({"event_slug": event_slug})

        # Wrap callback for error handling
        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in activity_trades callback: {e}",
                    exc_info=True,
                    extra={"market_slug": market_slug, "event_slug": event_slug}
                )

        # Set callback and subscribe
        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="activity",
            type="trades",
            filters=filters
        )

        logger.info(
            "Subscribed to activity_trades",
            extra={"market_slug": market_slug, "event_slug": event_slug}
        )

    def subscribe_activity_orders_matched(
        self,
        callback: Callable[[Message], None],
        market_slug: Optional[str] = None
    ) -> None:
        """
        Subscribe to order matching events.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called for each order match (receives Message object)
            market_slug: Filter by specific market (optional)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_match(msg: Message):
            ...     print(f"Orders matched: {msg.payload}")
            >>> client.subscribe_activity_orders_matched(on_match)
        """
        import json
        self._ensure_rtds()

        filters = json.dumps({"market_slug": market_slug}) if market_slug else None

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in orders_matched callback: {e}",
                    exc_info=True,
                    extra={"market_slug": market_slug}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="activity",
            type="orders_matched",
            filters=filters
        )

        logger.info("Subscribed to orders_matched", extra={"market_slug": market_slug})

    def subscribe_market_created(
        self,
        callback: Callable[[Message], None]
    ) -> None:
        """
        Subscribe to new market creation events.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called when new market created (receives Message object)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_market_created(msg: Message):
            ...     print(f"New market: {msg.payload.get('title')}")
            >>> client.subscribe_market_created(on_market_created)
        """
        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in market_created callback: {e}",
                    exc_info=True
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="market_created"
        )

        logger.info("Subscribed to market_created")

    def subscribe_market_resolved(
        self,
        callback: Callable[[Message], None]
    ) -> None:
        """
        Subscribe to market resolution events.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called when market resolves (receives Message object)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_resolved(msg: Message):
            ...     print(f"Market resolved: {msg.payload}")
            >>> client.subscribe_market_resolved(on_resolved)
        """
        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in market_resolved callback: {e}",
                    exc_info=True
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="market_resolved"
        )

        logger.info("Subscribed to market_resolved")

    def subscribe_market_price_changes(
        self,
        callback: Callable[[Message], None],
        token_ids: List[str]
    ) -> None:
        """
        Subscribe to price change events for specific tokens.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - token_ids must be valid token IDs
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on price changes (receives Message object)
            token_ids: List of token IDs to monitor

        Raises:
            ValueError: If token_ids is empty
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_price_change(msg: Message):
            ...     print(f"Price changed: {msg.payload}")
            >>> client.subscribe_market_price_changes(
            ...     on_price_change,
            ...     token_ids=["12345", "67890"]
            ... )
        """
        import json

        if not token_ids:
            raise ValueError("token_ids cannot be empty")

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in price_changes callback: {e}",
                    exc_info=True,
                    extra={"token_count": len(token_ids)}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="price_change",
            filters=json.dumps(token_ids)
        )

        logger.info("Subscribed to price_changes", extra={"token_count": len(token_ids)})

    def subscribe_market_orderbook_rtds(
        self,
        callback: Callable[[Message], None],
        token_ids: List[str]
    ) -> None:
        """
        Subscribe to aggregated orderbook updates via RTDS.

        NOTE: This is different from subscribe_orderbook() which uses CLOB WebSocket.
        RTDS orderbook provides aggregated data across multiple tokens.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - token_ids must be valid token IDs
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on orderbook updates (receives Message object)
            token_ids: List of token IDs to monitor

        Raises:
            ValueError: If token_ids is empty
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_book_update(msg: Message):
            ...     print(f"Orderbook: {msg.payload}")
            >>> client.subscribe_market_orderbook_rtds(
            ...     on_book_update,
            ...     token_ids=["12345"]
            ... )
        """
        import json

        if not token_ids:
            raise ValueError("token_ids cannot be empty")

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in orderbook_rtds callback: {e}",
                    exc_info=True,
                    extra={"token_count": len(token_ids)}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="agg_orderbook",
            filters=json.dumps(token_ids)
        )

        logger.info("Subscribed to orderbook_rtds", extra={"token_count": len(token_ids)})

    def subscribe_comments(
        self,
        callback: Callable[[Message], None],
        parent_entity_id: Optional[int] = None,
        parent_entity_type: str = "Event"
    ) -> None:
        """
        Subscribe to comment events (creation/removal).

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - parent_entity_type must be valid ("Event", "Market", etc.)
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on comment events (receives Message object)
            parent_entity_id: Filter by parent entity ID (optional)
            parent_entity_type: Parent type ("Event", "Market", etc.)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_comment(msg: Message):
            ...     print(f"Comment: {msg.payload}")
            >>> client.subscribe_comments(on_comment, parent_entity_id=123)
        """
        import json
        self._ensure_rtds()

        filters = None
        if parent_entity_id is not None:
            filters = json.dumps({
                "parentEntityID": parent_entity_id,
                "parentEntityType": parent_entity_type
            })

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in comments callback: {e}",
                    exc_info=True,
                    extra={"parent_entity_id": parent_entity_id}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="comments",
            type="*",  # All comment events
            filters=filters
        )

        logger.info("Subscribed to comments", extra={"parent_entity_id": parent_entity_id})

    def subscribe_reactions(
        self,
        callback: Callable[[Message], None],
        parent_entity_id: Optional[int] = None
    ) -> None:
        """
        Subscribe to comment reaction events.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on reaction events (receives Message object)
            parent_entity_id: Filter by parent entity ID (optional)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_reaction(msg: Message):
            ...     print(f"Reaction: {msg.payload}")
            >>> client.subscribe_reactions(on_reaction)
        """
        import json
        self._ensure_rtds()

        filters = json.dumps({"parentEntityID": parent_entity_id}) if parent_entity_id else None

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in reactions callback: {e}",
                    exc_info=True,
                    extra={"parent_entity_id": parent_entity_id}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="comments",
            type="reaction_*",  # All reaction events
            filters=filters
        )

        logger.info("Subscribed to reactions", extra={"parent_entity_id": parent_entity_id})

    def subscribe_rfq_requests(
        self,
        callback: Callable[[Message], None],
        market: Optional[str] = None
    ) -> None:
        """
        Subscribe to RFQ (Request for Quote) request events for OTC trading.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on RFQ requests (receives Message object)
            market: Filter by market condition ID (optional)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_rfq_request(msg: Message):
            ...     print(f"RFQ request: {msg.payload}")
            >>> client.subscribe_rfq_requests(on_rfq_request)
        """
        import json
        self._ensure_rtds()

        filters = json.dumps({"market": market}) if market else None

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in rfq_requests callback: {e}",
                    exc_info=True,
                    extra={"market": market}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="rfq",
            type="request_*",  # All request events
            filters=filters
        )

        logger.info("Subscribed to rfq_requests", extra={"market": market})

    def subscribe_rfq_quotes(
        self,
        callback: Callable[[Message], None],
        request_id: Optional[str] = None
    ) -> None:
        """
        Subscribe to RFQ quote events.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on RFQ quotes (receives Message object)
            request_id: Filter by specific request ID (optional)

        Raises:
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_rfq_quote(msg: Message):
            ...     print(f"RFQ quote: {msg.payload}")
            >>> client.subscribe_rfq_quotes(on_rfq_quote)
        """
        import json
        self._ensure_rtds()

        filters = json.dumps({"requestId": request_id}) if request_id else None

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in rfq_quotes callback: {e}",
                    exc_info=True,
                    extra={"request_id": request_id}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="rfq",
            type="quote_*",  # All quote events
            filters=filters
        )

        logger.info("Subscribed to rfq_quotes", extra={"request_id": request_id})

    def subscribe_crypto_prices(
        self,
        callback: Callable[[Message], None],
        symbol: str = "btcusdt"
    ) -> None:
        """
        Subscribe to real-time crypto price updates.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - symbol must be valid (btcusdt, ethusdt, solusdt, xrpusdt)
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on price updates (receives Message object)
            symbol: Crypto symbol ("btcusdt", "ethusdt", "solusdt", "xrpusdt")

        Raises:
            ValueError: If symbol is invalid
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_crypto_price(msg: Message):
            ...     print(f"BTC: ${msg.payload.get('price')}")
            >>> client.subscribe_crypto_prices(on_crypto_price, symbol="btcusdt")
        """
        import json

        valid_symbols = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
        symbol_lower = symbol.lower()

        if symbol_lower not in valid_symbols:
            raise ValueError(
                f"Invalid symbol: {symbol}. Must be one of {valid_symbols}"
            )

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in crypto_prices callback: {e}",
                    exc_info=True,
                    extra={"symbol": symbol}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="crypto_prices",
            type="update",
            filters=json.dumps({"symbol": symbol_lower})
        )

        logger.info("Subscribed to crypto_prices", extra={"symbol": symbol})

    def subscribe_crypto_prices_chainlink(
        self,
        callback: Callable[[Message], None],
        symbol: str = "btcusdt"
    ) -> None:
        """
        Subscribe to real-time Chainlink-based crypto price updates.

        Uses Chainlink oracles for price data (alternative to regular crypto_prices).

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - symbol must be valid (btcusdt, ethusdt, solusdt, xrpusdt)
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on price updates (receives Message object)
            symbol: Crypto symbol ("btcusdt", "ethusdt", "solusdt", "xrpusdt")

        Raises:
            ValueError: If symbol is invalid
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_chainlink_price(msg: Message):
            ...     print(f"BTC (Chainlink): ${msg.payload.get('price')}")
            >>> client.subscribe_crypto_prices_chainlink(on_chainlink_price, symbol="btcusdt")
        """
        import json

        valid_symbols = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
        symbol_lower = symbol.lower()

        if symbol_lower not in valid_symbols:
            raise ValueError(
                f"Invalid symbol: {symbol}. Must be one of {valid_symbols}"
            )

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in crypto_prices_chainlink callback: {e}",
                    exc_info=True,
                    extra={"symbol": symbol}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="crypto_prices_chainlink",
            type="update",
            filters=json.dumps({"symbol": symbol_lower})
        )

        logger.info("Subscribed to crypto_prices_chainlink", extra={"symbol": symbol})

    def subscribe_market_last_trade_price(
        self,
        callback: Callable[[Message], None],
        token_ids: List[str]
    ) -> None:
        """
        Subscribe to last trade price updates for specific tokens.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - token_ids must be valid token IDs
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on last trade price updates (receives Message object)
            token_ids: List of token IDs to monitor

        Raises:
            ValueError: If token_ids is empty
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_last_price(msg: Message):
            ...     print(f"Last trade price: {msg.payload}")
            >>> client.subscribe_market_last_trade_price(
            ...     on_last_price,
            ...     token_ids=["12345", "67890"]
            ... )
        """
        import json

        if not token_ids:
            raise ValueError("token_ids cannot be empty")

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in last_trade_price callback: {e}",
                    exc_info=True,
                    extra={"token_count": len(token_ids)}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="last_trade_price",
            filters=json.dumps(token_ids)
        )

        logger.info("Subscribed to last_trade_price", extra={"token_count": len(token_ids)})

    def subscribe_market_tick_size_change(
        self,
        callback: Callable[[Message], None],
        token_ids: List[str]
    ) -> None:
        """
        Subscribe to tick size change events for specific tokens.

        Tick size changes are rare but important for order placement validation.

        ZERO ASSUMPTIONS:
        - Callback must handle Message type
        - token_ids must be valid token IDs
        - Connection auto-initializes if needed
        - Auto-reconnects on disconnect

        Args:
            callback: Function called on tick size changes (receives Message object)
            token_ids: List of token IDs to monitor

        Raises:
            ValueError: If token_ids is empty
            RuntimeError: If RTDS disabled in settings

        Example:
            >>> def on_tick_change(msg: Message):
            ...     print(f"Tick size changed: {msg.payload}")
            >>> client.subscribe_market_tick_size_change(
            ...     on_tick_change,
            ...     token_ids=["12345"]
            ... )
        """
        import json

        if not token_ids:
            raise ValueError("token_ids cannot be empty")

        self._ensure_rtds()

        def safe_callback(client, message: Message):
            try:
                callback(message)
            except Exception as e:
                logger.error(
                    f"Error in tick_size_change callback: {e}",
                    exc_info=True,
                    extra={"token_count": len(token_ids)}
                )

        self._rtds.on_custom_message = safe_callback
        self._rtds.subscribe(
            topic="clob_market",
            type="tick_size_change",
            filters=json.dumps(token_ids)
        )

        logger.info("Subscribed to tick_size_change", extra={"token_count": len(token_ids)})

    def unsubscribe_rtds_all(self) -> None:
        """
        Disconnect from all RTDS streams and close connection.

        ZERO ASSUMPTIONS:
        - Safe to call even if RTDS not initialized
        - Handles cleanup errors gracefully
        - Thread-safe

        Example:
            >>> client.unsubscribe_rtds_all()
        """
        if self._rtds:
            try:
                self._rtds.disconnect()
                self._rtds = None
                logger.info("Unsubscribed from all RTDS streams")
            except Exception as e:
                logger.error(f"Error unsubscribing from RTDS: {e}", exc_info=True)

    # ========== Utility Methods ==========

    def close(self) -> None:
        """
        Close client and cleanup resources.

        ZERO ASSUMPTIONS:
        - Disconnects all WebSocket/RTDS connections safely
        - Handles partial cleanup if some resources fail
        - Logs all cleanup steps
        - Thread-safe
        """
        try:
            logger.info("Closing Polymarket client...")

            # Disconnect WebSocket
            if self._ws:
                try:
                    self._ws.disconnect()
                    logger.info("WebSocket disconnected")
                except Exception as e:
                    logger.error(f"Error disconnecting WebSocket: {e}")

            # Disconnect RTDS
            if self._rtds:
                try:
                    self._rtds.disconnect()
                    self._rtds = None
                    logger.info("RTDS disconnected")
                except Exception as e:
                    logger.error(f"Error disconnecting RTDS: {e}")

            # Close API clients
            self.gamma.close()
            self.clob.close()
            self.data.close()

            logger.info("Polymarket client closed gracefully")
        except Exception as e:
            logger.error(f"Error closing client: {e}", exc_info=True)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
