"""Facade RTDS routing: concurrent subscriptions must not clobber each other."""
import time
from unittest.mock import MagicMock, patch

import pytest

from polymarket import PolymarketClient
from polymarket.api.real_time_data import ConnectionStatus, Message
from polymarket.config import PolymarketSettings


def _msg(topic, type_, payload):
    return Message(topic=topic, type=type_, timestamp=int(time.time() * 1000),
                   payload=payload, connection_id="test")


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
        client._dispatch_rtds_message(mock_rtds, _msg("clob_market", "price_change",
                                                      {"price_changes": []}))
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
