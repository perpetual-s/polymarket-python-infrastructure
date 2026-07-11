"""RTDS transport: registry-when-disconnected, unconditional keepalive, staleness watchdog."""

import json
import time
from unittest.mock import MagicMock, patch

from polymarket.api.real_time_data import RealTimeDataClient


def _connected_client():
    client = RealTimeDataClient(auto_reconnect=False)
    mock_ws = MagicMock()
    mock_ws.sock = MagicMock()
    mock_ws.sock.connected = True
    client.ws = mock_ws
    return client, mock_ws


def test_subscribe_while_disconnected_is_recorded_and_replayed_on_open():
    client = RealTimeDataClient(auto_reconnect=False)  # no ws at all
    sent_now = client.subscribe(topic="activity", type="trades")
    assert sent_now is False
    assert [(s["topic"], s["type"]) for s in client._active_subscriptions] == [
        ("activity", "trades")
    ]

    mock_ws = MagicMock()
    mock_ws.sock = MagicMock()
    mock_ws.sock.connected = True
    client.ws = mock_ws
    with patch.object(client, "_schedule_ping"):
        client._on_open(mock_ws)  # replay
    replayed = json.loads(mock_ws.send.call_args_list[0][0][0])
    assert replayed["subscriptions"][0]["topic"] == "activity"


def test_duplicate_subscribe_not_double_tracked():
    client, _ = _connected_client()
    client.subscribe(topic="clob_market", type="price_change", filters='["1"]')
    client.subscribe(topic="clob_market", type="price_change", filters='["1"]')
    assert len(client._active_subscriptions) == 1


def test_ping_rearms_without_pong():
    client, mock_ws = _connected_client()
    with patch.object(client, "_schedule_ping") as re_arm:
        client._send_ping()
    mock_ws.send.assert_called_once_with("ping")
    re_arm.assert_called_once()  # unconditional re-arm, no pong needed


def test_connect_arms_protocol_ping_on_run_forever():
    client = RealTimeDataClient(auto_reconnect=False)
    with (
        patch("polymarket.api.real_time_data.websocket.WebSocketApp") as app_cls,
        patch("polymarket.api.real_time_data.threading.Thread") as thread_cls,
    ):
        client.connect()
    run_kwargs = thread_cls.call_args.kwargs["kwargs"]
    assert run_kwargs["ping_interval"] == client.ping_interval
    assert 0 < run_kwargs["ping_timeout"] < run_kwargs["ping_interval"]
    # on_pong must be wired on the app so protocol pongs stamp freshness
    assert app_cls.call_args.kwargs["on_pong"] == client._on_pong


def test_registered_on_pong_callback_stamps_last_pong():
    client = RealTimeDataClient(auto_reconnect=False)
    with (
        patch("polymarket.api.real_time_data.websocket.WebSocketApp") as app_cls,
        patch("polymarket.api.real_time_data.threading.Thread"),
    ):
        client.connect()
    on_pong = app_cls.call_args.kwargs["on_pong"]
    client._last_pong = 0.0
    on_pong(MagicMock(), b"")
    assert time.time() - client._last_pong < 1.0


def test_staleness_watchdog_forces_socket_close():
    client, mock_ws = _connected_client()
    client.max_staleness = 30.0
    client._last_pong = time.time() - 120
    client._last_message_time = time.time() - 120
    client._check_staleness()
    mock_ws.close.assert_called_once()


def test_fresh_connection_not_closed_by_watchdog():
    client, mock_ws = _connected_client()
    client.max_staleness = 30.0
    client._last_message_time = time.time()
    client._check_staleness()
    mock_ws.close.assert_not_called()


def test_pong_fresh_idle_connection_not_closed_by_watchdog():
    # Live-reproduced regression: a quiet stream has no messages for minutes,
    # but protocol pongs keep arriving — the watchdog must NOT churn it.
    client, mock_ws = _connected_client()
    client.max_staleness = 30.0
    client._last_pong = time.time()
    client._last_message_time = time.time() - 120
    client._check_staleness()
    mock_ws.close.assert_not_called()


def test_on_open_stamps_freshness_before_status_notify():
    # A stale armed timer must never observe pre-connect timestamps on a
    # fresh socket, so _on_open stamps freshness before anything else runs.
    client = RealTimeDataClient(auto_reconnect=False)
    client._last_pong = client._last_message_time = 0.0
    observed = []
    client.on_status_change_callback = lambda status: observed.append(
        (client._last_pong, client._last_message_time)
    )
    with patch.object(client, "_schedule_ping"):
        client._on_open(MagicMock())
    assert observed and observed[0][0] > 0 and observed[0][1] > 0
