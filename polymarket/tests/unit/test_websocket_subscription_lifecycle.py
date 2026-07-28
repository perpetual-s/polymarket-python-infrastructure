"""Offline regressions for CLOB WebSocket subscription lifecycle."""

import json
import threading
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import pytest

from polymarket.api.websocket import ChannelType, WebSocketClient
from polymarket.api.websocket_models import LastTradePriceMessage


def _authenticated_websocket() -> WebSocketClient:
    return WebSocketClient(
        api_key="key",
        api_secret="secret",
        api_passphrase="passphrase",
        enable_metrics=False,
        enable_queue=False,
    )


def test_transport_rejects_market_then_user_endpoint_mixing():
    client = _authenticated_websocket()
    with patch.object(client, "connect"):
        client.subscribe_market("asset-1", Mock())

        with pytest.raises(
            RuntimeError,
            match="require separate Polymarket WebSocket transports",
        ):
            client.subscribe_user(Mock())

    assert client._channel_type is ChannelType.MARKET
    assert list(client._subscriptions) == ["market:asset-1"]


def test_transport_rejects_user_then_market_endpoint_mixing():
    client = _authenticated_websocket()
    with patch.object(client, "connect"):
        client.subscribe_user(Mock())

        with pytest.raises(
            RuntimeError,
            match="require separate Polymarket WebSocket transports",
        ):
            client.subscribe_market("asset-1", Mock())

    assert client._channel_type is ChannelType.USER
    assert list(client._subscriptions) == ["user"]


def test_initial_open_and_reconnect_restore_market_subscription():
    """Initial open and later reconnect replay the same normalized registry entry."""
    client = WebSocketClient(
        enable_metrics=False,
        enable_queue=False,
    )

    with patch.object(client, "connect") as connect:
        client.subscribe_market("asset-1", Mock())

    connect.assert_called_once_with()
    assert list(client._subscriptions) == ["market:asset-1"]
    client._subscriptions["market:asset-2"] = Mock()

    socket = Mock()
    client._ws = socket
    client._running = True

    client._on_open(socket)
    socket.send.assert_called_once()
    assert json.loads(socket.send.call_args.args[0]) == {
        "type": "market",
        "assets_ids": ["asset-1", "asset-2"],
        "custom_feature_enabled": False,
    }

    socket.send.reset_mock()
    client._reconnect_count = 1
    client._on_open(socket)
    socket.send.assert_called_once()
    assert json.loads(socket.send.call_args.args[0]) == {
        "type": "market",
        "assets_ids": ["asset-1", "asset-2"],
        "custom_feature_enabled": False,
    }
    assert client._total_reconnections == 1


def test_dynamic_market_subscribe_and_unsubscribe_use_official_frames():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    socket = Mock()
    client._ws = socket
    client._running = True
    client._connected = True

    first = Mock()
    replacement = Mock()
    client.subscribe_market("asset-1", first)
    client.subscribe_market("asset-1", replacement)
    client.unsubscribe("market:asset-1")

    assert client._subscriptions == {}
    assert [json.loads(call.args[0]) for call in socket.send.call_args_list] == [
        {
            "operation": "subscribe",
            "assets_ids": ["asset-1"],
            "custom_feature_enabled": False,
        },
        {"operation": "unsubscribe", "assets_ids": ["asset-1"]},
    ]


def test_market_subscription_waits_for_open_before_initial_frame():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    socket = Mock()
    client._ws = socket
    client._running = True

    client.subscribe_market("asset-1", Mock())
    socket.send.assert_not_called()

    client._on_open(socket)
    assert json.loads(socket.send.call_args.args[0]) == {
        "type": "market",
        "assets_ids": ["asset-1"],
        "custom_feature_enabled": False,
    }


def test_market_subscription_dispatches_typed_last_trade_price_message():
    """A public MARKET subscription receives the existing typed last-trade model."""
    client = WebSocketClient(
        enable_metrics=False,
        enable_queue=False,
    )
    callback = Mock()

    with patch.object(client, "connect"):
        client.subscribe_market("asset-1", callback)

    client._on_message(
        None,
        json.dumps(
            {
                "event_type": "last_trade_price",
                "asset_id": "asset-1",
                "market": "condition-1",
                "price": "0.55",
                "side": "BUY",
                "size": "10",
                "fee_rate_bps": "0",
                "timestamp": "1234567890000",
            }
        ),
    )

    callback.assert_called_once()
    message = callback.call_args.args[0]
    assert isinstance(message, LastTradePriceMessage)
    assert message.asset_id == "asset-1"
    assert message.market == "condition-1"
    assert message.price == "0.55"
    assert message.size == "10"
    assert message.timestamp == "1234567890000"


def test_market_array_frame_isolates_bad_entries_and_dispatches_valid_siblings():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    callback = Mock()
    with patch.object(client, "connect"):
        client.subscribe_market("asset-1", callback)

    def trade(price: str) -> dict:
        return {
            "event_type": "last_trade_price",
            "asset_id": "asset-1",
            "market": "condition-1",
            "price": price,
            "side": "BUY",
            "size": "10",
            "fee_rate_bps": "0",
            "timestamp": "1234567890000",
        }

    client._on_message(None, json.dumps([trade("0.55"), None, trade("0.56")]))

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.messages_received == 1
    assert snapshot.messages_parsed == 2
    assert snapshot.parse_failures == 1
    assert snapshot.callbacks_invoked == 2
    assert [call.args[0].price for call in callback.call_args_list] == ["0.55", "0.56"]


def test_telemetry_counts_parse_unknown_and_message_callback_failures():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    callback = Mock(side_effect=RuntimeError("consumer failed"))
    with patch.object(client, "connect"):
        client.subscribe_market("asset-1", callback)

    client._on_message(
        None,
        json.dumps(
            {
                "event_type": "last_trade_price",
                "asset_id": "asset-1",
                "market": "condition-1",
                "price": "0.55",
                "side": "BUY",
                "size": "10",
                "fee_rate_bps": "0",
                "timestamp": "1234567890000",
            }
        ),
    )
    client._on_message(None, "not-json")
    client._on_message(None, json.dumps({"event_type": "future_event"}))

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.messages_received == 3
    assert snapshot.messages_parsed == 1
    assert snapshot.parse_failures == 1
    assert snapshot.unknown_messages == 1
    assert snapshot.callbacks_invoked == 1
    assert snapshot.callback_failures == 1
    assert snapshot.failure_callbacks_invoked == 0
    assert snapshot.failure_callback_failures == 0
    assert snapshot.last_receipt_time is not None
    assert snapshot.last_receipt_age_seconds is not None
    assert snapshot.last_receipt_age_seconds >= 0
    with pytest.raises(FrozenInstanceError):
        snapshot.messages_received = 0


def test_reconnect_silence_is_monotonic_bounded_and_transport_scoped():
    client = WebSocketClient(
        enable_metrics=False,
        enable_queue=False,
        reconnect_silence_history_size=1,
    )
    client._running = True
    socket = Mock()
    client._ws = socket

    with (
        patch("polymarket.api.websocket.time.time", return_value=100.0),
        patch("polymarket.api.websocket.time.monotonic", return_value=10.0),
    ):
        client._on_open(socket)
    with (
        patch("polymarket.api.websocket.time.time", return_value=110.0),
        patch("polymarket.api.websocket.time.monotonic", return_value=20.0),
    ):
        client._on_close(socket, 1001, "restart")
    client._reconnect_count = 1
    with (
        patch("polymarket.api.websocket.time.time", return_value=115.0),
        patch("polymarket.api.websocket.time.monotonic", return_value=25.0),
    ):
        client._on_open(socket)
    with (
        patch("polymarket.api.websocket.time.time", return_value=120.0),
        patch("polymarket.api.websocket.time.monotonic", return_value=30.0),
    ):
        client._on_close(socket, 1001, "restart")
    client._reconnect_count = 1
    with (
        patch("polymarket.api.websocket.time.time", return_value=122.0),
        patch("polymarket.api.websocket.time.monotonic", return_value=32.0),
    ):
        client._on_open(socket)

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.connected is True
    assert snapshot.running is True
    assert snapshot.connection_generation == 3
    assert snapshot.reconnect_completions == 2
    assert snapshot.disconnects == 2
    assert snapshot.reconnect_silence_threshold_seconds == 0.0
    assert snapshot.reconnect_silence_history_limit == 1
    assert snapshot.observed_reconnect_silence_count == 2
    assert snapshot.observed_reconnect_silences_discarded == 1
    assert len(snapshot.observed_reconnect_silences) == 1
    silence = snapshot.observed_reconnect_silences[0]
    assert silence.connection_generation == 2
    assert silence.started_at == 120.0
    assert silence.ended_at == 122.0
    assert silence.duration_seconds == 2.0


def test_intentional_disconnect_clears_open_reconnect_silence():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    client._running = True
    socket = Mock()
    client._ws = socket
    client._on_open(socket)
    client._on_close(socket, 1001, "restart")
    client._reconnect_count = 3

    assert (
        client.telemetry_snapshot_v1().current_reconnect_silence_started_at is not None
    )

    client.disconnect()
    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.running is False
    assert snapshot.connected is False
    assert snapshot.intentional_disconnects == 1
    assert snapshot.current_reconnect_attempts == 0
    assert snapshot.current_reconnect_silence_started_at is None
    assert snapshot.observed_reconnect_silence_count == 0


def test_local_send_failures_are_aggregate_and_operation_scoped():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    client._ws = Mock()
    client._ws.send.side_effect = RuntimeError("local send failed")

    client._send_initial_market_subscribe(["asset-1"])
    client._send_subscribe(ChannelType.MARKET, "asset-1")
    client._send_unsubscribe("market:asset-1")

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.local_send_failures == 3
    assert snapshot.local_subscribe_send_failures == 2
    assert snapshot.local_unsubscribe_send_failures == 1
    assert snapshot.last_local_send_failure_time is not None


def test_stale_socket_callbacks_cannot_reassert_or_dispatch_current_generation():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    callback = Mock()
    with patch.object(client, "connect"):
        client.subscribe_market("asset-1", callback)

    current = Mock()
    stale = Mock()
    client._running = True
    client._ws = current
    client._on_open(current)

    message = json.dumps(
        {
            "event_type": "last_trade_price",
            "asset_id": "asset-1",
            "market": "condition-1",
            "price": "0.55",
            "side": "BUY",
            "size": "10",
            "fee_rate_bps": "0",
            "timestamp": "1234567890000",
        }
    )
    client._on_message(stale, message)
    client._on_open(stale)

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.connection_generation == 1
    assert snapshot.messages_received == 0
    callback.assert_not_called()

    client._on_message(current, message)
    assert client.telemetry_snapshot_v1().messages_received == 1
    callback.assert_called_once()


def test_disconnect_between_open_transition_and_resubscribe_wins():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    client._metrics = Mock()
    client._subscriptions = {"market:asset-1": Mock()}
    socket = Mock()
    client._running = True
    client._ws = socket

    def stop_after_transition(message, *args, **kwargs):
        if message == "WebSocket connected":
            client.disconnect()

    with patch(
        "polymarket.api.websocket.logger.info", side_effect=stop_after_transition
    ):
        client._on_open(socket)

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.running is False
    assert snapshot.connected is False
    socket.send.assert_not_called()
    client._metrics.set_websocket_connection.assert_called_once_with(
        "clob", connected=False
    )


def test_explicit_restart_is_new_generation_not_reconnect_completion():
    client = WebSocketClient(enable_metrics=False, enable_queue=False)
    first = Mock()
    client._running = True
    client._ws = first
    client._on_open(first)
    client.disconnect()

    second = Mock()
    client._running = True
    client._ws = second
    client._on_open(second)

    snapshot = client.telemetry_snapshot_v1()
    assert snapshot.connection_generation == 2
    assert snapshot.reconnect_attempts == 0
    assert snapshot.reconnect_completions == 0
    assert snapshot.intentional_disconnects == 1


def test_timed_out_disconnect_retains_owner_and_rejects_restart(monkeypatch):
    import websocket

    entered = threading.Event()
    release = threading.Event()

    class BlockingWebSocketApp:
        def __init__(self, *args, **kwargs):
            pass

        def run_forever(self, **kwargs):
            entered.set()
            assert release.wait(timeout=2)

        def close(self):
            pass

    monkeypatch.setattr(websocket, "WebSocketApp", BlockingWebSocketApp)
    client = WebSocketClient(
        enable_metrics=False,
        enable_queue=False,
        reconnect_delay=0,
    )
    client._channel_type = ChannelType.MARKET
    client.connect()
    assert entered.wait(timeout=2)
    old_thread = client._thread
    real_join = old_thread.join
    old_thread.join = Mock()  # Simulate the five-second join timing out immediately.

    client.disconnect()

    assert client._thread is old_thread
    assert client._ws is not None
    with pytest.raises(RuntimeError, match="still stopping"):
        client.connect()

    release.set()
    real_join(timeout=2)
    assert not old_thread.is_alive()
    assert client._thread is None
    assert client._ws is None

    with patch("polymarket.api.websocket.threading.Thread") as new_thread:
        client.connect()
    assert client._thread is new_thread.return_value
    assert client.telemetry_snapshot_v1().running is True
