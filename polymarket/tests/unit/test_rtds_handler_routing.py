"""Facade RTDS routing: concurrent subscriptions must not clobber each other."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from polymarket import PolymarketClient
from polymarket.api.real_time_data import ConnectionStatus, Message
from polymarket.config import PolymarketSettings


def _msg(topic, type_, payload):
    return Message(
        topic=topic,
        type=type_,
        timestamp=int(time.time() * 1000),
        payload=payload,
        connection_id="test",
    )


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_two_topics_route_to_their_own_callbacks(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            trades, created = [], []
            client.subscribe_activity_trades(trades.append)
            client.subscribe_market_created(created.append)
        client._dispatch_rtds_message(mock_rtds, _msg("activity", "trades", {"a": 1}))
        client._dispatch_rtds_message(mock_rtds, _msg("clob_market", "market_created", {"b": 2}))
        assert len(trades) == 1 and trades[0].payload == {"a": 1}
        assert len(created) == 1 and created[0].payload == {"b": 2}


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_same_callback_multi_token_delivers_once(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            got = []
            client.subscribe_market_price_changes(got.append, token_ids=["1"])
            client.subscribe_market_price_changes(got.append, token_ids=["2"])
        client._dispatch_rtds_message(
            mock_rtds, _msg("clob_market", "price_change", {"price_changes": []})
        )
        assert len(got) == 1  # id(callback)-keyed registry: one delivery, no duplicate


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_callback_exception_does_not_break_other_handlers(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):

            def boom(_msg):
                raise RuntimeError("boom")

            got = []
            client.subscribe_activity_trades(boom)
            client.subscribe_activity_orders_matched(got.append)
        client._dispatch_rtds_message(mock_rtds, _msg("activity", "trades", {}))
        client._dispatch_rtds_message(mock_rtds, _msg("activity", "orders_matched", {}))
        assert len(got) == 1


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_unsubscribe_price_changes_clears_handlers_when_no_subs_remain(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds._active_subscriptions = []
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            got = []
            client.subscribe_market_price_changes(got.append, token_ids=["1"])
        client.unsubscribe_market_price_changes(token_ids=["1"])
        client._dispatch_rtds_message(mock_rtds, _msg("clob_market", "price_change", {}))
        assert got == []  # handler removed once no price_change subscriptions remain


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_prefix_wildcard_types_route_concrete_messages(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            reactions, requests, quotes = [], [], []
            client.subscribe_reactions(reactions.append, parent_entity_id=123)
            client.subscribe_rfq_requests(requests.append)
            client.subscribe_rfq_quotes(quotes.append)
        client._dispatch_rtds_message(mock_rtds, _msg("comments", "reaction_added", {"r": 1}))
        client._dispatch_rtds_message(mock_rtds, _msg("rfq", "request_created", {"q": 2}))
        client._dispatch_rtds_message(mock_rtds, _msg("rfq", "quote_created", {"u": 3}))
        # Each prefix-registered handler gets exactly its own concrete-typed message
        assert len(reactions) == 1 and reactions[0].payload == {"r": 1}
        assert len(requests) == 1 and requests[0].payload == {"q": 2}
        assert len(quotes) == 1 and quotes[0].payload == {"u": 3}


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_exact_registration_does_not_prefix_match(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            got = []
            client.subscribe_market_price_changes(got.append, token_ids=["1"])
        client._dispatch_rtds_message(mock_rtds, _msg("clob_market", "price_changes_x", {}))
        assert got == []  # non-wildcard key "price_change" must not prefix-match


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_same_callback_in_wildcard_and_prefix_buckets_delivers_once(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            got = []

            def on_comment_event(msg):
                got.append(msg)

            client.subscribe_comments(on_comment_event)  # ("comments", "*")
            client.subscribe_reactions(on_comment_event)  # ("comments", "reaction_*")
        client._dispatch_rtds_message(mock_rtds, _msg("comments", "reaction_added", {}))
        assert len(got) == 1  # identity-deduped: one delivery despite two matching buckets


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_ensure_rtds_timeout_raises_runtime_error(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=False, create=True):
            with pytest.raises(RuntimeError):
                client.subscribe_activity_trades(lambda m: None)
        assert client._rtds is None  # cleared on failed init so a later retry is possible


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_ensure_rtds_uses_configured_timeout_and_staleness(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    settings = PolymarketSettings(
        enable_rtds=True, rtds_connection_timeout=7.0, rtds_max_staleness=45.0
    )
    async with PolymarketClient(settings=settings) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True) as wait:
            client.subscribe_activity_trades(lambda m: None)
        assert wait.call_args.kwargs["timeout"] == 7.0
        assert mock_rtds_class.call_args.kwargs["max_staleness"] == 45.0


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_close_clears_rtds_handlers(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
        client.subscribe_activity_trades(lambda m: None)
    assert client._rtds_handlers
    await client.close()
    assert client._rtds_handlers == {}  # stale callbacks must not survive a close


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_close_nulls_rtds_even_when_disconnect_raises(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds.disconnect.side_effect = RuntimeError("socket already dead")
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
        client.subscribe_activity_trades(lambda m: None)
    assert client._rtds is not None
    await client.close()
    assert (
        client._rtds is None
    )  # broken handle must not survive close(), or _ensure_rtds skips reinit


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_unsubscribe_rtds_all_nulls_rtds_even_when_disconnect_raises(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds.disconnect.side_effect = RuntimeError("socket already dead")
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
        client.subscribe_activity_trades(lambda m: None)
    assert client._rtds is not None
    client.unsubscribe_rtds_all()
    # Broken handle must not survive (mirrors close()); handlers must be cleared
    assert client._rtds is None
    assert client._rtds_handlers == {}


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_subscribe_survives_concurrent_close_nulling_rtds(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
        real_register = client._register_rtds_handler

        def register_then_null(topic, type, callback):
            real_register(topic, type, callback)
            # Deterministic stand-in for a concurrent close() landing between
            # _ensure_rtds() and the transport call inside the subscribe method
            client._rtds = None

        with patch.object(client, "_register_rtds_handler", side_effect=register_then_null):
            client.subscribe_market_created(lambda m: None)  # must not raise AttributeError
    # The captured strong reference still receives the subscribe (benign on teardown)
    mock_rtds.subscribe.assert_called_once_with(topic="clob_market", type="market_created")


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_unsubscribe_price_changes_survives_concurrent_close_nulling_rtds(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds._active_subscriptions = []
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
        client.subscribe_market_price_changes(lambda m: None, token_ids=["1"])

    def null_handle(**kwargs):
        client._rtds = None  # concurrent close() lands mid-unsubscribe

    mock_rtds.unsubscribe.side_effect = null_handle
    client.unsubscribe_market_price_changes(token_ids=["1"])  # must not raise AttributeError
    assert ("clob_market", "price_change") not in client._rtds_handlers


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_concurrent_unsubscribe_all_and_subscribe_never_raises(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
    errors = []
    with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):

        def subscriber():
            for _ in range(200):
                try:
                    client.subscribe_market_created(lambda m: None)
                except Exception as e:  # noqa: BLE001 - hammer collects everything
                    errors.append(e)

        def closer():
            for _ in range(200):
                try:
                    client.unsubscribe_rtds_all()
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        threads = [threading.Thread(target=subscriber), threading.Thread(target=closer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_ensure_rtds_returns_the_installed_transport(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            rtds = client._ensure_rtds()
        assert rtds is client._rtds
        assert rtds is mock_rtds


@pytest.mark.asyncio
@patch("polymarket.client.RealTimeDataClient")
async def test_transport_constructed_with_dispatcher_as_on_message(mock_rtds_class):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    mock_rtds_class.return_value = mock_rtds
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch.object(client, "_rtds_wait_connected", return_value=True, create=True):
            client.subscribe_activity_trades(lambda m: None)
        assert mock_rtds_class.call_count == 1
        on_message = mock_rtds_class.call_args.kwargs["on_message"]
        assert on_message == client._dispatch_rtds_message  # bound-method equality


@pytest.mark.asyncio
async def test_rtds_disabled_rejects_subscription_before_transport_creation():
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=False)) as client:
        with patch("polymarket.client.RealTimeDataClient") as rtds_class:
            with pytest.raises(RuntimeError, match="RTDS is disabled"):
                client.subscribe_crypto_prices(lambda _message: None)
        rtds_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filter_kwargs", "expected_filter"),
    [
        ({"market_slug": "market-a"}, '{"market_slug": "market-a"}'),
        ({"event_slug": "event-a"}, '{"event_slug": "event-a"}'),
    ],
)
async def test_activity_trade_subscriptions_build_slug_filters(filter_kwargs, expected_filter):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED

    with patch("polymarket.client.RealTimeDataClient", return_value=mock_rtds):
        async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
            with patch.object(client, "_rtds_wait_connected", return_value=True):
                client.subscribe_activity_trades(lambda _message: None, **filter_kwargs)

    mock_rtds.subscribe.assert_called_once_with(
        topic="activity",
        type="trades",
        filters=expected_filter,
    )


@pytest.mark.asyncio
async def test_activity_trade_subscription_rejects_ambiguous_slug_filters():
    async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
        with patch("polymarket.client.RealTimeDataClient") as rtds_class:
            with pytest.raises(ValueError, match="Cannot specify both"):
                client.subscribe_activity_trades(
                    lambda _message: None,
                    market_slug="market-a",
                    event_slug="event-a",
                )
        rtds_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_topic"),
    [
        ("subscribe_crypto_prices", "crypto_prices"),
        ("subscribe_crypto_prices_chainlink", "crypto_prices_chainlink"),
    ],
)
async def test_crypto_subscriptions_validate_and_normalize_symbols(method_name, expected_topic):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED

    with patch("polymarket.client.RealTimeDataClient", return_value=mock_rtds):
        async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
            method = getattr(client, method_name)
            with pytest.raises(ValueError, match="Invalid symbol"):
                method(lambda _message: None, symbol="dogeusdt")

            with patch.object(client, "_rtds_wait_connected", return_value=True):
                method(lambda _message: None, symbol="BTCUSDT")

    mock_rtds.subscribe.assert_called_once_with(
        topic=expected_topic,
        type="update",
        filters='{"symbol": "btcusdt"}',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "expected_type"),
    [
        ("subscribe_market_price_changes", "price_change"),
        ("subscribe_market_orderbook_rtds", "agg_orderbook"),
        ("subscribe_market_last_trade_price", "last_trade_price"),
        ("subscribe_market_tick_size_change", "tick_size_change"),
    ],
)
async def test_token_scoped_subscriptions_validate_and_build_filters(method_name, expected_type):
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED

    with patch("polymarket.client.RealTimeDataClient", return_value=mock_rtds):
        async with PolymarketClient(settings=PolymarketSettings(enable_rtds=True)) as client:
            method = getattr(client, method_name)
            with pytest.raises(ValueError, match="cannot be empty"):
                method(lambda _message: None, token_ids=[])

            with patch.object(client, "_rtds_wait_connected", return_value=True):
                method(lambda _message: None, token_ids=["token-1", "token-2"])

    mock_rtds.subscribe.assert_called_once_with(
        topic="clob_market",
        type=expected_type,
        filters='["token-1", "token-2"]',
    )


@pytest.mark.asyncio
async def test_concurrent_subscriptions_initialize_one_rtds_transport():
    mock_rtds = MagicMock()
    mock_rtds.status = ConnectionStatus.CONNECTED
    errors = []

    with patch("polymarket.client.RealTimeDataClient", return_value=mock_rtds) as rtds_class:
        client = PolymarketClient(settings=PolymarketSettings(enable_rtds=True))
        with patch.object(client, "_rtds_wait_connected", return_value=True):

            def subscribe():
                try:
                    client.subscribe_crypto_prices(lambda _message: None)
                except Exception as error:  # noqa: BLE001 - collect thread failures
                    errors.append(error)

            threads = [threading.Thread(target=subscribe) for _ in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        await client.close()

    assert errors == []
    rtds_class.assert_called_once()
    assert mock_rtds.subscribe.call_count == 10
