"""Offline API-A coverage for the top-level public-flow facade."""

import json
import threading
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from polymarket import PolymarketClient
from polymarket.api.websocket import WebSocketClient, WebSocketTelemetrySnapshotV1
from polymarket.api.websocket_models import (
    LastTradePriceMessage,
    OrderLevel,
    OrderbookMessage,
)
from polymarket.exceptions import AuthenticationError
from polymarket.models import Side


@pytest.mark.asyncio
async def test_truthful_result_facades_preserve_query_arguments():
    client = object.__new__(PolymarketClient)
    client.data = Mock()
    client.public_clob = Mock()
    client.data.get_trades_result_v1 = AsyncMock(return_value=object())
    client.public_clob.get_market_trades_events_result_v1 = AsyncMock(
        return_value=object()
    )
    client.public_clob.get_prices_history_result_v1 = AsyncMock(return_value=object())

    await client.get_market_trades_result_v1(
        "0xcondition",
        user="0xwallet",
        event_id="123",
        start=100,
        end=200,
        limit=10_000,
        offset=25,
        taker_only=False,
        filter_type="CASH",
        filter_amount=500.25,
        side=Side.BUY,
    )
    client.data.get_trades_result_v1.assert_awaited_once_with(
        user="0xwallet",
        market="0xcondition",
        event_id="123",
        start=100,
        end=200,
        limit=10_000,
        offset=25,
        taker_only=False,
        filter_type="CASH",
        filter_amount=500.25,
        side=Side.BUY,
    )

    await client.get_market_trades_events_result_v1("0xcondition")
    client.public_clob.get_market_trades_events_result_v1.assert_awaited_once_with(
        "0xcondition"
    )

    await client.get_prices_history_result_v1(
        "asset-1", start_ts=100, end_ts=200, fidelity=1
    )
    client.public_clob.get_prices_history_result_v1.assert_awaited_once_with(
        "asset-1", interval=None, start_ts=100, end_ts=200, fidelity=1
    )


def test_official_last_trade_and_orderbook_share_one_market_subscription():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws._lock = threading.RLock()
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()

    books = []
    trades = []
    client.subscribe_orderbook("asset-1", books.append)
    client.subscribe_clob_market_last_trade_price("asset-1", trades.append)

    client._ensure_websocket.assert_has_calls(
        [
            call(None, channel_type="MARKET"),
            call(None, channel_type="MARKET"),
        ]
    )
    client._ws.subscribe_market.assert_called_once()
    token_id, dispatch = client._ws.subscribe_market.call_args.args
    assert token_id == "asset-1"

    dispatch(
        OrderbookMessage(
            event_type="book",
            asset_id="asset-1",
            market="0xcondition",
            timestamp="1000",
            hash="0xhash",
            buys=[OrderLevel(price="0.49", size="10")],
            sells=[OrderLevel(price="0.51", size="12")],
        )
    )
    dispatch(
        LastTradePriceMessage(
            event_type="last_trade_price",
            asset_id="asset-1",
            market="0xcondition",
            price="0.505",
            side="BUY",
            size="3.25",
            fee_rate_bps="0",
            timestamp="1001",
        )
    )

    assert len(books) == 1
    assert books[0].token_id == "asset-1"
    assert books[0].best_bid == Decimal("0.49")
    assert books[0].best_ask == Decimal("0.51")
    assert len(trades) == 1
    assert trades[0].price == "0.505"


def test_official_last_trade_rejects_non_callable_callback():
    client = object.__new__(PolymarketClient)

    with pytest.raises(TypeError, match="callback must be callable"):
        client.subscribe_clob_market_last_trade_price("asset-1", None)


def test_public_websocket_telemetry_never_requires_private_transport_access():
    client = object.__new__(PolymarketClient)
    client._ws = None
    client._ws_lock = threading.Lock()

    detached = client.get_clob_websocket_telemetry_v1()

    assert isinstance(detached, WebSocketTelemetrySnapshotV1)
    assert detached.running is False
    assert detached.connected is False
    assert detached.observed_reconnect_silences == ()
    with pytest.raises(FrozenInstanceError):
        detached.connected = True

    attached = replace(detached, running=True, connected=True, connection_generation=1)
    transport = Mock()
    transport.telemetry_snapshot_v1.return_value = attached
    client._ws = transport

    assert client.get_clob_websocket_telemetry_v1() is attached
    assert client.is_websocket_connected() is True
    assert transport.telemetry_snapshot_v1.call_count == 2


def test_user_order_subscription_routes_wallet_and_failure_callback_through_facade():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()
    callback = Mock()
    failure_callback = Mock()

    client.subscribe_user_orders(
        callback,
        wallet_id="primary",
        on_failure_callback=failure_callback,
    )

    client._ensure_websocket.assert_called_once_with(
        "primary",
        on_failure_callback=failure_callback,
        channel_type="USER",
    )
    client._ws.subscribe_user.assert_called_once()
    facade_dispatch = client._ws.subscribe_user.call_args.args[0]
    message = object()
    facade_dispatch(message)
    callback.assert_called_once_with(message)


def test_user_order_subscription_resolves_default_wallet_before_transport_auth():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()
    client.key_manager = Mock()
    client.key_manager.get_default_wallet.return_value = "default"

    client.subscribe_user_orders(Mock())

    client.key_manager.get_default_wallet.assert_called_once_with()
    client._ensure_websocket.assert_called_once_with(
        "default",
        on_failure_callback=None,
        channel_type="USER",
    )


def test_user_order_facade_constructs_transport_with_wallet_credentials_and_failure_callback():
    client = object.__new__(PolymarketClient)
    client._ws = None
    client._ws_lock = threading.Lock()
    client.settings = SimpleNamespace(
        ws_url="wss://example.invalid/ws",
        ws_reconnect_delay=2.0,
        ws_max_reconnects=7,
    )
    credentials = SimpleNamespace(
        api_key="api-key",
        api_secret="api-secret",
        api_passphrase="api-passphrase",
    )
    client.key_manager = Mock()
    client.key_manager.get_wallet.return_value = credentials
    callback = Mock()
    failure_callback = Mock()

    with patch("polymarket.client.WebSocketClient") as websocket_class:
        transport = websocket_class.return_value
        client.subscribe_user_orders(
            callback,
            wallet_id="primary",
            on_failure_callback=failure_callback,
        )

    websocket_class.assert_called_once_with(
        ws_url="wss://example.invalid/ws",
        api_key="api-key",
        api_secret="api-secret",
        api_passphrase="api-passphrase",
        reconnect_delay=2.0,
        max_reconnects=7,
        on_failure_callback=failure_callback,
    )
    transport.subscribe_user.assert_called_once()


def test_market_first_transport_rejects_user_endpoint_mixing():
    client = object.__new__(PolymarketClient)
    client._ws = None
    client._ws_wallet_id = None
    client._ws_channel_type = None
    client._ws_lock = threading.Lock()
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client.settings = SimpleNamespace(
        ws_url="wss://example.invalid/ws",
        ws_reconnect_delay=2.0,
        ws_max_reconnects=7,
    )
    credentials = SimpleNamespace(
        api_key="api-key",
        api_secret="api-secret",
        api_passphrase="api-passphrase",
    )
    client.key_manager = Mock()
    client.key_manager.get_default_wallet.return_value = "default"
    client.key_manager.get_wallet.return_value = credentials

    with patch("polymarket.client.WebSocketClient") as websocket_class:
        transport = websocket_class.return_value
        transport._lock = threading.RLock()

        client.subscribe_orderbook("asset-1", Mock())
        with pytest.raises(
            RuntimeError,
            match="require separate Polymarket WebSocket transports",
        ):
            client.subscribe_user_orders(Mock())

    websocket_class.assert_called_once_with(
        ws_url="wss://example.invalid/ws",
        api_key=None,
        api_secret=None,
        api_passphrase=None,
        reconnect_delay=2.0,
        max_reconnects=7,
        on_failure_callback=None,
    )
    assert client._ws_wallet_id is None
    assert client._ws_channel_type == "MARKET"
    transport.subscribe_market.assert_called_once()
    transport.subscribe_user.assert_not_called()


def test_unauthenticated_market_transport_cannot_silently_upgrade_to_user_channel():
    client = object.__new__(PolymarketClient)
    client._ws = None
    client._ws_wallet_id = None
    client._ws_channel_type = None
    client._ws_lock = threading.Lock()
    client.settings = SimpleNamespace(
        ws_url="wss://example.invalid/ws",
        ws_reconnect_delay=2.0,
        ws_max_reconnects=7,
    )
    client.key_manager = Mock()
    client.key_manager.get_default_wallet.return_value = None

    with patch("polymarket.client.WebSocketClient"):
        client._ensure_websocket()
        client.key_manager.get_default_wallet.return_value = "default"

        with pytest.raises(
            RuntimeError,
            match="require separate Polymarket WebSocket transports",
        ):
            client.subscribe_user_orders(Mock())


def test_user_websocket_rejects_different_wallet_credentials():
    client = object.__new__(PolymarketClient)
    client._ws = None
    client._ws_wallet_id = None
    client._ws_channel_type = None
    client._ws_lock = threading.Lock()
    client.settings = SimpleNamespace(
        ws_url="wss://example.invalid/ws",
        ws_reconnect_delay=2.0,
        ws_max_reconnects=7,
    )
    client.key_manager = Mock()
    client.key_manager.get_wallet.return_value = SimpleNamespace(
        api_key="api-key",
        api_secret="api-secret",
        api_passphrase="api-passphrase",
    )

    with patch("polymarket.client.WebSocketClient"):
        client._ensure_websocket("wallet-a")

        with pytest.raises(
            AuthenticationError,
            match="cannot subscribe USER channel",
        ):
            client._ensure_websocket("wallet-b")

    assert client._ws_wallet_id == "wallet-a"
    client.key_manager.get_wallet.assert_called_once_with("wallet-a")


def test_wait_until_websocket_connected_reports_only_current_facade_transport():
    client = object.__new__(PolymarketClient)
    client._ws_lock = threading.Lock()
    transport = Mock()
    transport.wait_until_connected.return_value = True
    transport.telemetry_snapshot_v1.return_value = replace(
        WebSocketTelemetrySnapshotV1.disconnected(),
        running=True,
        connected=True,
    )
    client._ws = transport

    assert client.wait_until_websocket_connected(1.25) is True
    transport.wait_until_connected.assert_called_once_with(1.25)

    client._ws = None
    assert client.wait_until_websocket_connected(1.25) is False


def test_unsubscribe_all_clears_market_fanout_registry():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws.telemetry_snapshot_v1.return_value = (
        WebSocketTelemetrySnapshotV1.disconnected()
    )
    client._ws_callbacks = {"asset-1": [Mock()]}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()

    client.unsubscribe_all()

    assert client._ws is None
    assert client._ws_callbacks == {}


def test_unsubscribe_all_retains_post_disconnect_queue_drain_snapshot():
    client = object.__new__(PolymarketClient)
    transport = WebSocketClient(
        enable_metrics=False,
        enable_queue=True,
        queue_maxsize=1,
        enable_deduplication=False,
    )
    transport._running = True
    transport._lifecycle_generation = 1
    transport._on_message(
        None,
        '{"event_type":"book","asset_id":"123","market":"0xabc",'
        '"timestamp":"1","hash":"0xsnapshot","buys":[],"sells":[]}',
    )
    client._ws = transport
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()

    result = client.unsubscribe_all()
    final = client.get_clob_websocket_telemetry_v1()

    assert result is None
    assert final.running is False
    assert final.queue_size == 0
    assert final.queue_drops == 1
    assert final.stale_lifecycle_messages_dropped == 1
    assert final.intentional_disconnects == 1


def test_older_detach_cannot_overwrite_newer_final_snapshot():
    client = object.__new__(PolymarketClient)
    old_transport = Mock()
    new_transport = Mock()
    old_final = replace(
        WebSocketTelemetrySnapshotV1.disconnected(),
        messages_received=1,
        intentional_disconnects=1,
    )
    new_final = replace(
        WebSocketTelemetrySnapshotV1.disconnected(),
        messages_received=2,
        intentional_disconnects=1,
    )
    old_transport.telemetry_snapshot_v1.return_value = old_final
    new_transport.telemetry_snapshot_v1.return_value = new_final
    old_disconnect_entered = threading.Event()
    release_old_disconnect = threading.Event()

    def block_old_disconnect():
        old_disconnect_entered.set()
        assert release_old_disconnect.wait(timeout=2)

    old_transport.disconnect.side_effect = block_old_disconnect
    client._ws = old_transport
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()

    old_teardown = threading.Thread(target=client.unsubscribe_all)
    old_teardown.start()
    assert old_disconnect_entered.wait(timeout=2)
    with client._ws_lock:
        client._ws = new_transport
    client.unsubscribe_all()
    release_old_disconnect.set()
    old_teardown.join(timeout=2)

    assert not old_teardown.is_alive()
    assert client.get_clob_websocket_telemetry_v1() is new_final


def test_first_market_subscription_failure_rolls_back_callbacks():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws._lock = threading.RLock()
    client._ws.subscribe_market.side_effect = RuntimeError("wire failure")
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()

    with pytest.raises(RuntimeError, match="wire failure"):
        client.subscribe_clob_market_last_trade_price("asset-1", Mock())

    assert client._ws_callbacks == {}


def test_market_callback_failure_does_not_block_other_handlers():
    client = object.__new__(PolymarketClient)
    client._ws = Mock()
    client._ws._lock = threading.RLock()
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()
    bad = Mock(side_effect=RuntimeError("consumer failure"))
    good = Mock()

    client.subscribe_clob_market_last_trade_price("asset-1", bad)
    client.subscribe_clob_market_last_trade_price("asset-1", good)
    dispatch = client._ws.subscribe_market.call_args.args[1]
    message = LastTradePriceMessage(
        event_type="last_trade_price",
        asset_id="asset-1",
        market="0xcondition",
        price="0.5",
        side="BUY",
        size="1",
        fee_rate_bps="0",
        timestamp="1001",
    )

    with pytest.raises(RuntimeError, match="1 CLOB Market callback"):
        dispatch(message)

    bad.assert_called_once_with(message)
    good.assert_called_once_with(message)


def test_public_snapshot_accounts_failed_last_trade_consumer():
    client = object.__new__(PolymarketClient)
    transport = WebSocketClient(enable_metrics=False, enable_queue=False)
    client._ws = transport
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()
    callback = Mock(side_effect=RuntimeError("consumer failure"))

    with patch.object(transport, "connect"):
        client.subscribe_clob_market_last_trade_price("asset-1", callback)
    transport._on_message(
        None,
        json.dumps(
            {
                "event_type": "last_trade_price",
                "asset_id": "asset-1",
                "market": "condition-1",
                "price": "0.5",
                "side": "BUY",
                "size": "1",
                "fee_rate_bps": "0",
                "timestamp": "1001",
            }
        ),
    )

    snapshot = client.get_clob_websocket_telemetry_v1()
    assert snapshot.callbacks_invoked == 1
    assert snapshot.callback_failures == 1


def test_concurrent_unsubscribe_cannot_leave_detached_subscription():
    client = object.__new__(PolymarketClient)
    transport = Mock()
    transport._lock = threading.RLock()
    subscribe_entered = threading.Event()
    release_subscribe = threading.Event()

    def blocking_subscribe(*_args):
        subscribe_entered.set()
        assert release_subscribe.wait(timeout=2)

    transport.subscribe_market.side_effect = blocking_subscribe
    client._ws = transport
    client._ws_callbacks = {}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._ensure_websocket = Mock()

    register = threading.Thread(
        target=client.subscribe_clob_market_last_trade_price,
        args=("asset-1", Mock()),
    )
    teardown = threading.Thread(target=client.unsubscribe_all)
    register.start()
    assert subscribe_entered.wait(timeout=2)
    teardown.start()
    release_subscribe.set()
    register.join(timeout=2)
    teardown.join(timeout=2)

    assert not register.is_alive() and not teardown.is_alive()
    assert client._ws is None
    assert client._ws_callbacks == {}
    transport.disconnect.assert_called_once_with()


@pytest.mark.asyncio
async def test_close_detaches_websocket_and_clears_market_fanout_registry():
    client = object.__new__(PolymarketClient)
    transport = Mock()
    final = replace(
        WebSocketTelemetrySnapshotV1.disconnected(),
        intentional_disconnects=1,
    )
    transport.telemetry_snapshot_v1.return_value = final
    client._ws = transport
    client._ws_callbacks = {"asset-1": [Mock()]}
    client._ws_callbacks_lock = threading.RLock()
    client._ws_lock = threading.Lock()
    client._rtds = None
    client._rtds_lock = threading.Lock()
    client._rtds_handlers = {}
    client._rtds_handlers_lock = threading.Lock()
    client.gamma = Mock(close=AsyncMock())
    client.clob = Mock(close=AsyncMock())
    client.data = Mock(close=AsyncMock())
    client.public_clob = Mock(close=AsyncMock())

    await client.close()

    assert client._ws is None
    assert client._ws_callbacks == {}
    transport.disconnect.assert_called_once_with()
    assert client.get_clob_websocket_telemetry_v1() is final
