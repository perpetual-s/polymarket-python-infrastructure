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
