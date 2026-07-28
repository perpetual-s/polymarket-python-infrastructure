"""Regression tests for async PolymarketClient behavior and trading safety."""

import asyncio
import signal
import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from polymarket.client import PolymarketClient
from polymarket.config import PolymarketSettings
from polymarket.exceptions import (
    InsufficientBalanceError,
    OrderRejectedError,
    TradingError,
)
from polymarket.models import (
    Balance,
    FeeInfo,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Side,
)


def build_test_client() -> PolymarketClient:
    """Construct a lightweight client with best-effort exit cleanup patched."""
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
    )

    with patch("polymarket.client.atexit.register"):
        client = PolymarketClient(
            settings=settings,
            enable_rate_limiting=False,
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
    client._build_signed_order = AsyncMock(
        return_value={"order": "signed", "_orderHash": "order-1"}
    )
    client._resolve_tick_size = AsyncMock(return_value=Decimal("0.01"))
    client.get_fee_info = AsyncMock(return_value=FeeInfo(base_fee_bps=0, rate_bps=0))
    return client


@pytest.mark.asyncio
async def test_constructor_preserves_application_signal_handler_identity() -> None:
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
    )
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def application_sigint(signum, frame) -> None:
        return None

    def application_sigterm(signum, frame) -> None:
        return None

    client = None
    try:
        signal.signal(signal.SIGINT, application_sigint)
        signal.signal(signal.SIGTERM, application_sigterm)
        with patch("polymarket.client.atexit.register") as register_exit_cleanup:
            client = PolymarketClient(
                settings=settings,
                enable_rate_limiting=False,
            )

        register_exit_cleanup.assert_called_once_with(client._close_sync)
        assert signal.getsignal(signal.SIGINT) is application_sigint
        assert signal.getsignal(signal.SIGTERM) is application_sigterm

        await client.close()

        assert signal.getsignal(signal.SIGINT) is application_sigint
        assert signal.getsignal(signal.SIGTERM) is application_sigterm
    finally:
        if client is not None:
            await client.close()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


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


def set_batch_order_hashes(
    client: PolymarketClient,
    *order_ids: str,
) -> None:
    client._build_signed_order = AsyncMock(
        side_effect=[
            {"order": f"signed-{index}", "_orderHash": order_id}
            for index, order_id in enumerate(order_ids)
        ]
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
            self._active_subscriptions.append(
                {"topic": topic, "type": type, "filters": filters}
            )
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
        assert gate.gate_reached.wait(timeout=5.0), (
            "unsubscriber never reached the handler pop"
        )

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
                s["topic"] == "clob_market"
                and s["type"] == "price_change"
                and "2" in s["filters"]
                for s in fake._active_subscriptions
            )
        # ...so its handler must still be registered (the bug wipes it).
        with client._rtds_handlers_lock:
            bucket = client._rtds_handlers.get(("clob_market", "price_change"), {})
            handlers = list(bucket.values())
        assert cb_b in handlers, (
            "concurrent subscriber's handler was wiped -> silent dead stream"
        )
        # A unsubscribed with nothing remaining at check time; it must not linger.
        assert cb_a not in handlers
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_reserves_buy_notional() -> None:
    client = build_test_client()
    try:
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True, order_id="order-1", status=OrderStatus.LIVE
            )
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
async def test_cancelled_place_order_retains_reservation_after_transport_starts() -> (
    None
):
    client = build_test_client()
    try:
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.place_order(make_order(), wallet_id="test-wallet")

        client.release_reserved_balance.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancelled_place_order_retains_durable_presubmit_reservation() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "0xorder"}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(side_effect=asyncio.CancelledError)
        persisted = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=persisted,
            )

        persisted.assert_awaited_once_with("0xorder", Decimal("5.50"))
        client.release_reserved_balance.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unclassified_response_retains_durable_presubmit_reservation() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "0xorder"}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(success=False, error_msg="rejected")
        )
        persisted = AsyncMock()

        with pytest.raises(TradingError, match="not a definitive rejection"):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=persisted,
            )

        persisted.assert_awaited_once_with("0xorder", Decimal("5.50"))
        client.release_reserved_balance.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_typed_exchange_rejection_releases_durable_reservation() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "0xorder"}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(
            side_effect=OrderRejectedError(
                "exchange rejected order",
                reason="INVALID_ORDER",
            )
        )
        persisted = AsyncMock()

        with pytest.raises(OrderRejectedError):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=persisted,
            )

        persisted.assert_awaited_once_with("0xorder", Decimal("5.50"))
        client.release_reserved_balance.assert_awaited_once_with(
            Decimal("5.50"),
            "test-wallet",
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_duplicate_exchange_identity_retains_durable_reservation() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "0xorder"}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(
            side_effect=OrderRejectedError(
                "duplicate order",
                reason="DUPLICATE",
            )
        )

        with pytest.raises(OrderRejectedError):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=AsyncMock(),
            )

        client.release_reserved_balance.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_presubmit_requires_deterministic_order_hash() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(return_value={"order": "signed"})
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock()
        persisted = AsyncMock()

        with pytest.raises(TradingError, match="order hash"):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=persisted,
            )

        persisted.assert_not_awaited()
        client.clob.post_order.assert_not_awaited()
        client.release_reserved_balance.assert_awaited_once_with(
            Decimal("5.50"), "test-wallet"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_presubmit_receives_tick_aligned_fee_inclusive_reservation() -> None:
    client = build_test_client()
    try:
        client._resolve_tick_size = AsyncMock(return_value=Decimal("0.05"))
        client.get_fee_info = AsyncMock(
            return_value=FeeInfo(
                base_fee_bps=100,
                rate_bps=100,
                exponent=Decimal("1"),
            )
        )
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "0xorder"}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.05"))
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True,
                order_id="0xorder",
                status=OrderStatus.LIVE,
            )
        )
        persisted = AsyncMock()

        await client.place_order(
            make_order(price="0.57", size="10"),
            wallet_id="test-wallet",
            pre_submit=persisted,
        )

        persisted.assert_awaited_once_with("0xorder", Decimal("5.05"))
        normalized = client._build_signed_order.await_args.args[0]
        assert normalized.price == Decimal("0.55")
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        OrderResponse(success=True, order_id=None, status=OrderStatus.LIVE),
        OrderResponse(
            success=True,
            order_id="0x" + "b" * 64,
            status=OrderStatus.LIVE,
        ),
    ],
)
async def test_success_response_must_match_durable_presubmit_identity(
    response: OrderResponse,
) -> None:
    client = build_test_client()
    order_hash = "0x" + "a" * 64
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": order_hash}
        )
        client._check_and_reserve_buy_balance = AsyncMock(return_value=Decimal("5.50"))
        client.release_reserved_balance = AsyncMock()
        client.clob.post_order = AsyncMock(return_value=response)
        persisted = AsyncMock()

        with pytest.raises(TradingError, match="deterministic"):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                pre_submit=persisted,
            )

        persisted.assert_awaited_once_with(order_hash, Decimal("5.50"))
        client.release_reserved_balance.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        OrderResponse(success=True, order_id=None, status=OrderStatus.LIVE),
        OrderResponse(
            success=True,
            order_id="different-order",
            status=OrderStatus.LIVE,
        ),
    ],
)
async def test_success_response_must_match_local_identity_without_callback(
    response: OrderResponse,
) -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "expected-order"}
        )
        client.clob.post_order = AsyncMock(return_value=response)

        with pytest.raises(TradingError, match="deterministic|local order hash"):
            await client.place_order(
                make_order(),
                wallet_id="test-wallet",
                skip_balance_check=True,
            )

        assert await client.get_reserved_balance("test-wallet") == Decimal("5.50")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_threads_fixed_timestamp_into_signing() -> None:
    client = build_test_client()
    try:
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True, order_id="order-1", status=OrderStatus.LIVE
            )
        )

        await client.place_order(
            make_order(side=Side.SELL),
            wallet_id="test-wallet",
            skip_balance_check=True,
            timestamp_ms=1_700_000_000_123,
        )

        assert (
            client._build_signed_order.await_args.kwargs["timestamp_ms"]
            == 1_700_000_000_123
        )
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
async def test_sell_uses_authenticated_conditional_balance_without_data_api() -> None:
    client = build_test_client()
    try:
        client._build_signed_order = AsyncMock(
            return_value={"order": "signed", "_orderHash": "sell-order"}
        )
        client.get_balances = AsyncMock(
            side_effect=AssertionError("collateral read used")
        )
        client.get_position_balance = AsyncMock(
            side_effect=RuntimeError("Data API unavailable")
        )
        client.get_token_balance = AsyncMock(return_value=Decimal("0.50"))
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True,
                order_id="sell-order",
                status=OrderStatus.LIVE,
            )
        )

        response = await client.place_order(
            make_order(price="0.55", size="0.50", side=Side.SELL),
            wallet_id="test-wallet",
        )

        assert response.success is True
        client.get_token_balance.assert_awaited_once_with(
            token_id="12345", wallet_id="test-wallet"
        )
        client.get_position_balance.assert_not_awaited()
        client.get_balances.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sell_balance_error_is_unknown_and_never_submitted() -> None:
    client = build_test_client()
    try:
        client.get_token_balance = AsyncMock(
            side_effect=TimeoutError("balance timeout")
        )
        client.clob.post_order = AsyncMock()

        with pytest.raises(TradingError, match="Balance preflight failed"):
            await client.place_order(
                make_order(price="0.55", size="0.50", side=Side.SELL),
                wallet_id="test-wallet",
            )

        client.clob.post_order.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sell_requires_exact_authenticated_share_balance() -> None:
    client = build_test_client()
    try:
        client.get_token_balance = AsyncMock(return_value=Decimal("0.99"))
        client.clob.post_order = AsyncMock()

        with pytest.raises(InsufficientBalanceError):
            await client.place_order(
                make_order(price="0.55", size="1.00", side=Side.SELL),
                wallet_id="test-wallet",
            )

        client.clob.post_order.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_wrappers_await_underlying_clients() -> None:
    client = build_test_client()
    try:
        client.market_clob.get_server_time = AsyncMock(return_value=1234567890)
        client.public_clob.get_best_bid_ask = AsyncMock(
            return_value=(Decimal("0.45"), Decimal("0.47"))
        )

        assert await client.get_server_time() == 1234567890
        assert await client.get_best_bid_ask("12345") == (
            Decimal("0.45"),
            Decimal("0.47"),
        )
        client.market_clob.get_server_time.assert_awaited_once()
        client.public_clob.get_best_bid_ask.assert_awaited_once_with("12345")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_place_order_keeps_reserved_balance_when_post_submission_work_fails() -> (
    None
):
    client = build_test_client()
    try:
        client.clob.post_order = AsyncMock(
            return_value=OrderResponse(
                success=True, order_id="order-1", status=OrderStatus.LIVE
            )
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
async def test_concurrent_buy_orders_fail_closed_after_first_tentative_reservation() -> (
    None
):
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

        task = asyncio.create_task(
            client.place_order(first_order, wallet_id="test-wallet")
        )
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
async def test_health_check_awaits_async_clob_probe() -> None:
    client = build_test_client()
    try:
        client.market_clob.health_check = AsyncMock(return_value={"status": "healthy"})

        health = await client.health_check()

        assert health["status"] == "healthy"
        assert health["clob"]["status"] == "healthy"
        client.market_clob.health_check.assert_awaited_once()
    finally:
        await client.close()


def test_unknown_settings_override_raises_type_error() -> None:
    settings = PolymarketSettings(
        enable_rate_limiting=False, enable_metrics=False, enable_rtds=False
    )

    with patch("polymarket.client.atexit.register"):
        with pytest.raises(
            TypeError, match="Unknown PolymarketClient setting override"
        ):
            PolymarketClient(settings=settings, not_a_setting=123)


@pytest.mark.asyncio
async def test_constructor_overrides_do_not_mutate_caller_settings() -> None:
    settings = PolymarketSettings(
        enable_rate_limiting=False,
        enable_metrics=False,
        enable_rtds=False,
        pool_connections=50,
    )

    with patch("polymarket.client.atexit.register"):
        client = PolymarketClient(
            settings=settings,
            pool_connections=75,
        )

    try:
        assert settings.pool_connections == 50
        assert client.settings.pool_connections == 75
    finally:
        await client.close()
