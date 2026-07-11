"""Regression tests for async PolymarketClient behavior and trading safety."""

import asyncio
import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from polymarket.client import PolymarketClient
from polymarket.config import PolymarketSettings
from polymarket.exceptions import InsufficientBalanceError, TradingError
from polymarket.models import (
    Balance,
    MarketOrderRequest,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Side,
)


def build_test_client() -> PolymarketClient:
    """Construct a lightweight client with patched process-level handlers."""
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
    )

    with patch("polymarket.client.signal.signal"), patch("polymarket.client.atexit.register"):
        client = PolymarketClient(
            settings=settings,
            enable_rate_limiting=False,
            enable_circuit_breaker=False,
        )

    wallet = SimpleNamespace(
        address="0x1234567890abcdef1234567890abcdef12345678",
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        private_key="0x" + "1" * 64,
        signature_type=0,
        funder=None,
    )

    client.key_manager.get_wallet = Mock(return_value=wallet)
    client.key_manager.has_api_credentials = Mock(return_value=True)
    client.metrics.track_order = Mock()
    client.metrics.track_order_latency = Mock()
    client.metrics.set_balance = Mock()
    client._build_signed_order = AsyncMock(return_value={"order": "signed"})
    return client


def make_order(
    *,
    token_id: str = "12345",
    price: str = "0.55",
    size: str = "10",
    side: Side = Side.BUY,
) -> OrderRequest:
    """Build a valid order for tests."""
    return OrderRequest(
        token_id=token_id,
        price=Decimal(price),
        size=Decimal(size),
        side=side,
        order_type=OrderType.GTC,
    )


@pytest.mark.asyncio
async def test_unsubscribe_market_price_changes_constructs_token_filter() -> None:
    client = build_test_client()
    try:
        # MagicMock: unsubscribe now inspects _subscriptions_lock/_active_subscriptions
        rtds = MagicMock()
        rtds._active_subscriptions = []
        client._rtds = rtds

        client.unsubscribe_market_price_changes(token_ids=["12345", "67890"])

        call_kwargs = rtds.unsubscribe.call_args.kwargs
        assert call_kwargs["topic"] == "clob_market"
        assert call_kwargs["type"] == "price_change"
        assert '"12345"' in call_kwargs["filters"]
        assert '"67890"' in call_kwargs["filters"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unsubscribe_market_price_changes_rejects_empty_tokens() -> None:
    client = build_test_client()
    try:
        with pytest.raises(ValueError, match="cannot be empty"):
            client.unsubscribe_market_price_changes(token_ids=[])
    finally:
        await client.close()


class _FakeRtds:
    """Minimal transport double: tracks subscription records under its own lock."""

    def __init__(self) -> None:
        self._subscriptions_lock = threading.RLock()
        self._active_subscriptions: list = []

    def subscribe(self, topic, type, filters=None, clob_auth=None) -> bool:
        with self._subscriptions_lock:
            self._active_subscriptions.append({"topic": topic, "type": type, "filters": filters})
        return True

    def unsubscribe(self, topic, type="*", filters=None) -> None:
        key = (topic, type, filters)
        with self._subscriptions_lock:
            self._active_subscriptions = [
                s
                for s in self._active_subscriptions
                if (s["topic"], s["type"], s["filters"]) != key
            ]

    def disconnect(self) -> None:
        pass


class _GatedLock:
    """Context-manager lock wrapper that parks one designated thread before acquiring."""

    def __init__(self, inner: threading.Lock) -> None:
        self._inner = inner
        self.gated_thread: threading.Thread | None = None
        self.gate_reached = threading.Event()
        self.proceed = threading.Event()

    def __enter__(self):
        if threading.current_thread() is self.gated_thread:
            self.gate_reached.set()
            self.proceed.wait(timeout=5.0)
        self._inner.acquire()
        return self._inner

    def __exit__(self, exc_type, exc, tb):
        self._inner.release()
        return False


@pytest.mark.asyncio
async def test_unsubscribe_price_changes_does_not_wipe_concurrent_subscriber() -> None:
    """
    A subscribe_market_price_changes() landing between unsubscribe's
    remaining-check and its handler-pop must keep its handler; otherwise its
    live wire subscription becomes a silent dead stream. The interleaving is
    forced with events (the unsubscriber is parked right before the pop), not
    scheduling luck.
    """
    client = build_test_client()
    try:
        fake = _FakeRtds()
        client._rtds = fake
        client._ensure_rtds = Mock(return_value=fake)

        def cb_a(message) -> None:
            pass

        def cb_b(message) -> None:
            pass

        client.subscribe_market_price_changes(cb_a, token_ids=["1"])

        gate = _GatedLock(client._rtds_handlers_lock)
        client._rtds_handlers_lock = gate

        unsub_thread = threading.Thread(
            target=client.unsubscribe_market_price_changes,
            kwargs={"token_ids": ["1"]},
            daemon=True,
        )
        gate.gated_thread = unsub_thread
        unsub_thread.start()

        # The unsubscriber passed its remaining-check (no subscriptions left)
        # and is parked right before popping the handler bucket.
        assert gate.gate_reached.wait(timeout=5.0), "unsubscriber never reached the handler pop"

        sub_done = threading.Event()

        def _subscribe_b() -> None:
            client.subscribe_market_price_changes(cb_b, token_ids=["2"])
            sub_done.set()

        sub_thread = threading.Thread(target=_subscribe_b, daemon=True)
        sub_thread.start()

        # Unfixed code: B registers its handler and records its subscription
        # while the unsubscriber is parked, so this wait returns quickly.
        # Fixed code: B blocks on the facade registration lock until the
        # unsubscriber finishes, so this times out — both paths continue.
        sub_done.wait(timeout=1.0)
        gate.proceed.set()

        unsub_thread.join(timeout=5.0)
        sub_thread.join(timeout=5.0)
        assert not unsub_thread.is_alive() and not sub_thread.is_alive()

        # B's wire subscription is live in both worlds...
        with fake._subscriptions_lock:
            assert any(
                s["topic"] == "clob_market" and s["type"] == "price_change" and "2" in s["filters"]
                for s in fake._active_subscriptions
            )
        # ...so its handler must still be registered (the bug wipes it).
        with client._rtds_handlers_lock:
            bucket = client._rtds_handlers.get(("clob_market", "price_change"), {})
            handlers = list(bucket.values())
        assert cb_b in handlers, "concurrent subscriber's handler was wiped -> silent dead stream"
        # A unsubscribed with nothing remaining at check time; it must not linger.
        assert cb_a not in handlers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_reserves_buy_notional() -> None:
    client = build_test_client()
    try:
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(success=True, order_id="order-1", status=OrderStatus.LIVE)
        )

        await client.place_order(
            make_order(price="0.55", size="10"),
            wallet_id="test-wallet",
            skip_balance_check=True,
        )

        assert await client.get_reserved_balance("test-wallet") == Decimal("5.50")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_fails_closed_when_balance_lookup_errors() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(side_effect=RuntimeError("boom"))
        client.clob.post_order = AsyncMock()

        with pytest.raises(TradingError, match="Balance preflight failed"):
            await client.place_order(make_order(), wallet_id="test-wallet")

        client.clob.post_order.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_orders_batch_checks_combined_buy_collateral() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(return_value=Balance(collateral=Decimal("6.00"), tokens={}))
        client.get_position_balance = AsyncMock(return_value=Decimal("0"))
        client.clob.post_orders_batch = AsyncMock()

        orders = [
            make_order(price="0.50", size="10"),
            make_order(token_id="67890", price="0.40", size="5"),
        ]

        with pytest.raises(InsufficientBalanceError):
            await client.place_orders_batch(orders, wallet_id="test-wallet")

        client.clob.post_orders_batch.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_orders_batch_fails_closed_when_balance_lookup_errors() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(side_effect=RuntimeError("boom"))
        client.clob.post_orders_batch = AsyncMock()

        orders = [
            make_order(price="0.50", size="10"),
            make_order(token_id="67890", price="0.25", size="4"),
        ]

        with pytest.raises(TradingError, match="Batch balance preflight failed"):
            await client.place_orders_batch(orders, wallet_id="test-wallet")

        client.clob.post_orders_batch.assert_not_called()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_orders_batch_reserves_only_successful_buy_notional() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(
            return_value=Balance(collateral=Decimal("100.00"), tokens={})
        )
        client.get_position_balance = AsyncMock(return_value=Decimal("0"))
        client.clob.post_orders_batch = AsyncMock(
            return_value=[
                OrderResponse(success=True, order_id="order-1", status=OrderStatus.LIVE),
                OrderResponse(success=False, error_msg="rejected"),
            ]
        )

        orders = [
            make_order(price="0.50", size="10"),
            make_order(token_id="67890", price="0.25", size="4"),
        ]

        responses = await client.place_orders_batch(orders, wallet_id="test-wallet")

        assert len(responses) == 2
        assert await client.get_reserved_balance("test-wallet") == Decimal("5.00")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_orders_batch_supports_mixed_buy_and_sell_preflight() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(
            return_value=Balance(collateral=Decimal("100.00"), tokens={})
        )
        client.get_position_balance = AsyncMock(return_value=Decimal("3.00"))
        client.clob.post_orders_batch = AsyncMock(
            return_value=[
                OrderResponse(success=True, order_id="buy-order", status=OrderStatus.LIVE),
                OrderResponse(success=True, order_id="sell-order", status=OrderStatus.LIVE),
            ]
        )

        orders = [
            make_order(price="0.50", size="10", side=Side.BUY),
            make_order(token_id="67890", price="0.60", size="2", side=Side.SELL),
        ]

        responses = await client.place_orders_batch(orders, wallet_id="test-wallet")

        assert [response.order_id for response in responses] == ["buy-order", "sell-order"]
        assert await client.get_reserved_balance("test-wallet") == Decimal("5.00")
        client.get_position_balance.assert_awaited_once_with(
            token_id="67890", wallet_id="test-wallet"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_orders_batch_returns_responses_when_reservation_update_fails() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(
            return_value=Balance(collateral=Decimal("100.00"), tokens={})
        )
        client.get_position_balance = AsyncMock(return_value=Decimal("0"))
        client.clob.post_orders_batch = AsyncMock(
            return_value=[
                OrderResponse(success=True, order_id="order-1", status=OrderStatus.LIVE),
                OrderResponse(success=True, order_id="order-2", status=OrderStatus.LIVE),
            ]
        )
        client._reserve_balance = AsyncMock(side_effect=[None, RuntimeError("reserve broke")])

        orders = [
            make_order(price="0.50", size="10"),
            make_order(token_id="67890", price="0.25", size="4"),
        ]

        responses = await client.place_orders_batch(orders, wallet_id="test-wallet")

        assert [response.order_id for response in responses] == ["order-1", "order-2"]
        assert client._reserve_balance.await_count == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_wrappers_await_underlying_clients() -> None:
    client = build_test_client()
    try:
        client.clob.get_server_time = AsyncMock(return_value=1234567890)
        client.public_clob.get_best_bid_ask = AsyncMock(
            return_value=(Decimal("0.45"), Decimal("0.47"))
        )

        assert await client.get_server_time() == 1234567890
        assert await client.get_best_bid_ask("12345") == (Decimal("0.45"), Decimal("0.47"))
        client.clob.get_server_time.assert_awaited_once()
        client.public_clob.get_best_bid_ask.assert_awaited_once_with("12345")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_keeps_reserved_balance_when_post_submission_work_fails() -> None:
    client = build_test_client()
    try:
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(success=True, order_id="order-1", status=OrderStatus.LIVE)
        )
        client.metrics.track_order.side_effect = RuntimeError("metrics broke")

        with pytest.raises(RuntimeError, match="metrics broke"):
            await client.place_order(
                make_order(price="0.55", size="10"),
                wallet_id="test-wallet",
                skip_balance_check=True,
            )

        assert await client.get_reserved_balance("test-wallet") == Decimal("5.50")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_buy_orders_fail_closed_after_first_tentative_reservation() -> None:
    client = build_test_client()
    first_order_posting = asyncio.Event()
    release_first_order = asyncio.Event()

    async def slow_post_order(**_: object) -> OrderResponse:
        first_order_posting.set()
        await release_first_order.wait()
        return OrderResponse(success=True, order_id="order-1", status=OrderStatus.LIVE)

    try:
        client.get_balances = AsyncMock(
            return_value=Balance(collateral=Decimal("10.00"), tokens={})
        )
        client.clob.post_order = AsyncMock(side_effect=slow_post_order)

        first_order = make_order(price="0.60", size="10")
        second_order = make_order(token_id="67890", price="0.60", size="10")

        task = asyncio.create_task(client.place_order(first_order, wallet_id="test-wallet"))
        await first_order_posting.wait()

        with pytest.raises(InsufficientBalanceError):
            await client.place_order(second_order, wallet_id="test-wallet")

        assert client.clob.post_order.await_count == 1

        release_first_order.set()
        await task

        assert await client.get_reserved_balance("test-wallet") == Decimal("6.00")
    finally:
        release_first_order.set()
        await client.close()


@pytest.mark.asyncio
async def test_get_positions_batch_returns_materialized_results() -> None:
    client = build_test_client()
    try:
        client.data.get_positions = AsyncMock(side_effect=lambda user, **kwargs: [user])

        result = await client.get_positions_batch(["0x1", "0x2"])

        assert result == {"0x1": ["0x1"], "0x2": ["0x2"]}
        assert client.data.get_positions.await_count == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_market_order_buy_reserves_usd_amount() -> None:
    client = build_test_client()
    try:
        client.get_balances = AsyncMock(
            return_value=Balance(collateral=Decimal("100.00"), tokens={})
        )
        client.get_orderbook = AsyncMock(
            return_value=SimpleNamespace(
                asks=[SimpleNamespace(price=Decimal("0.40"), size=Decimal("100"))],
                bids=[],
            )
        )
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True, order_id="market-order", status=OrderStatus.LIVE
            )
        )

        response = await client.place_market_order(
            MarketOrderRequest(
                token_id="12345",
                amount=Decimal("20.00"),
                side=Side.BUY,
                order_type=OrderType.FOK,
            ),
            wallet_id="test-wallet",
        )

        assert response.order_id == "market-order"
        assert await client.get_reserved_balance("test-wallet") == Decimal("20.00")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_check_awaits_async_clob_probe() -> None:
    client = build_test_client()
    try:
        client.clob.health_check = AsyncMock(return_value={"status": "healthy"})

        health = await client.health_check()

        assert health["status"] == "healthy"
        assert health["clob"]["status"] == "healthy"
        client.clob.health_check.assert_awaited_once()
    finally:
        await client.close()


def test_unknown_settings_override_raises_type_error() -> None:
    settings = PolymarketSettings(
        enable_rate_limiting=False, enable_metrics=False, enable_rtds=False
    )

    with patch("polymarket.client.signal.signal"), patch("polymarket.client.atexit.register"):
        with pytest.raises(TypeError, match="Unknown PolymarketClient setting override"):
            PolymarketClient(settings=settings, not_a_setting=123)


@pytest.mark.asyncio
async def test_constructor_overrides_do_not_mutate_caller_settings() -> None:
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
        pool_connections=50,
    )

    with patch("polymarket.client.signal.signal"), patch("polymarket.client.atexit.register"):
        client = PolymarketClient(
            settings=settings,
            pool_connections=75,
            enable_circuit_breaker=False,
        )

    try:
        assert settings.pool_connections == 50
        assert client.settings.pool_connections == 75
    finally:
        await client.close()
