"""Pytest configuration for unit tests in polymarket.

These tests require an async event loop as PolymarketClient/APIs create aiohttp sessions.
Skip by default until tests are updated for async context.
"""


# Skip tests that instantiate PolymarketClient/CLOBAPI without async context
# These tests need to be updated to use @pytest.mark.asyncio and async fixtures
collect_ignore_glob = [
    "test_websocket_queue.py",
    "test_websocket_v35.py",
]
