"""RTDS transport: registry-when-disconnected, unconditional keepalive, staleness watchdog."""

import json
import threading
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


def test_send_failure_keeps_subscription_for_replay():
    # A connected send that raises must NOT drop the tracked record: the
    # subscription is registered before the send, so a failed write leaves it
    # in the registry to be replayed on the next (re)connect.
    client, mock_ws = _connected_client()
    mock_ws.send.side_effect = RuntimeError("socket write failed")

    sent_now = client.subscribe(topic="activity", type="trades", filters='{"m":"x"}')

    assert mock_ws.send.called  # the connected send path was exercised (not queued)
    assert sent_now is False  # send raised -> reported as not sent
    assert [(s["topic"], s["type"], s["filters"]) for s in client._active_subscriptions] == [
        ("activity", "trades", '{"m":"x"}')
    ]


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


def test_connect_ping_interval_zero_omits_ping_timeout():
    # Degenerate branch: ping_interval <= 0 disables protocol pings. It must
    # pass ping_interval=0 and NOT leave a dangling ping_timeout, which
    # websocket-client rejects unless 0 < ping_timeout < ping_interval.
    client = RealTimeDataClient(auto_reconnect=False, ping_interval=0)
    with (
        patch("polymarket.api.real_time_data.websocket.WebSocketApp"),
        patch("polymarket.api.real_time_data.threading.Thread") as thread_cls,
    ):
        client.connect()
    run_kwargs = thread_cls.call_args.kwargs["kwargs"]
    assert run_kwargs["ping_interval"] == 0
    assert "ping_timeout" not in run_kwargs


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


class _StartOnceTimer:
    """Stand-in for threading.Timer: enforces the real start-once contract, never fires.

    The slow `daemon` setter widens the window between assigning the timer
    attribute and starting it, so an unlocked _schedule_ping loses the race
    deterministically instead of by scheduling luck.
    """

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.cancelled = False
        self._started = False
        self._start_guard = threading.Lock()

    def __setattr__(self, name, value):
        if name == "daemon":
            time.sleep(0.0005)
        object.__setattr__(self, name, value)

    def start(self):
        with self._start_guard:
            if self._started:
                raise RuntimeError("threads can only be started once")
            self._started = True

    def cancel(self):
        self.cancelled = True


def test_schedule_ping_concurrent_callers_never_double_start():
    # Variant A: _on_open (ws thread) and _send_ping's finally (timer thread)
    # both call _schedule_ping; the loser must not start the winner's timer.
    client = RealTimeDataClient(auto_reconnect=False)
    errors, broken = [], []
    barrier = threading.Barrier(2)

    def hammer():
        for _ in range(200):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                broken.append(True)
                return
            try:
                client._schedule_ping()
            except Exception as exc:  # noqa: BLE001 - the race surfaces here
                errors.append(exc)

    with patch("polymarket.api.real_time_data.threading.Timer", _StartOnceTimer):
        threads = [threading.Thread(target=hammer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not broken
    assert errors == []
    assert isinstance(client._ping_timer, _StartOnceTimer)
    assert client._ping_timer._started  # exactly one armed, started-once timer survives


def test_schedule_ping_vs_on_close_cancel_never_hits_none():
    # Variant B: _check_staleness's ws.close() runs _on_close (cancel + None)
    # while the same keepalive tick's finally re-arms via _schedule_ping.
    client = RealTimeDataClient(auto_reconnect=False)
    errors, broken = [], []
    barrier = threading.Barrier(2)

    def run(fn):
        for _ in range(200):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                broken.append(True)
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - the race surfaces here
                errors.append(exc)

    with patch("polymarket.api.real_time_data.threading.Timer", _StartOnceTimer):
        arm = threading.Thread(target=run, args=(client._schedule_ping,))
        close = threading.Thread(
            target=run, args=(lambda: client._on_close(MagicMock(), 1000, "bye"),)
        )
        arm.start()
        close.start()
        arm.join()
        close.join()

    assert not broken
    assert errors == []


def test_schedule_ping_single_thread_semantics_pinned():
    client = RealTimeDataClient(auto_reconnect=False)
    with patch("polymarket.api.real_time_data.threading.Timer", _StartOnceTimer):
        client._schedule_ping()
        first = client._ping_timer
        client._schedule_ping()
        second = client._ping_timer
        assert first is not second
        assert first.cancelled and first._started  # old timer cancelled
        assert second._started and not second.cancelled  # exactly one live
        assert second.daemon is True

        client._shutdown_requested = True
        client._ping_timer = None
        client._schedule_ping()
        assert client._ping_timer is None  # shutdown stops the chain


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


def test_stats_exposes_desired_subscriptions_and_message_age():
    client, _ = _connected_client()
    client.subscribe(topic="activity", type="trades")
    client.subscribe(topic="clob_market", type="price_change", filters='["1"]')
    client._last_message_time = time.time() - 5.0

    stats = client.stats()

    # desired_subscriptions mirrors the tracked registry size
    assert stats["desired_subscriptions"] == 2
    assert stats["desired_subscriptions"] == stats["active_subscriptions"]
    # last_message_age_seconds is a finite, non-negative float reflecting elapsed time
    age = stats["last_message_age_seconds"]
    assert isinstance(age, float)
    assert 4.9 <= age <= 6.5
