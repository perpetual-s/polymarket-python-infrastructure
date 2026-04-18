"""
Tests for async BaseAPIClient refactor.

RGR Phase: RED - Write failing tests first.

Tests verify:
- Async HTTP methods (GET, POST, PUT, DELETE)
- aiohttp.ClientSession usage
- Connection pooling
- Timeout handling
- Rate limiting integration
- Circuit breaker integration
- Request deduplication
- Error handling
"""

import asyncio
from typing import Dict, Any
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import aiohttp
from aiohttp import ClientSession, ClientTimeout
import pytest
import pytest_asyncio

from polymarket.api.base import BaseAPIClient
from polymarket.config import PolymarketSettings
from polymarket.exceptions import (
    APIError,
    TimeoutError as PolymarketTimeoutError,
    RateLimitError,
    AuthenticationError
)
from polymarket.utils.rate_limiter import RateLimiter
from polymarket.utils.retry import CircuitBreaker


@pytest.fixture
def settings():
    """Create test settings."""
    return PolymarketSettings(
        chain_id=137,
        clob_url="https://clob.polymarket.com",
        gamma_url="https://gamma-api.polymarket.com",
        data_url="https://data-api.polymarket.com",
        connect_timeout=5.0,
        request_timeout=30.0,
        max_retries=3,
        retry_backoff_base=2.0,
        retry_backoff_max=60.0,
        enable_rate_limiting=True,
        enable_circuit_breaker=True,
    )


@pytest.fixture
def rate_limiter():
    """Create test rate limiter."""
    return RateLimiter(
        enabled=True,
        margin=0.8
    )


@pytest.fixture
def circuit_breaker():
    """Create test circuit breaker."""
    return CircuitBreaker(
        failure_threshold=5,
        timeout=60.0,
        name="test_circuit"
    )


@pytest_asyncio.fixture
async def base_client(settings, rate_limiter, circuit_breaker):
    """Create async BaseAPIClient for testing."""
    client = BaseAPIClient(
        base_url="https://clob.polymarket.com",
        settings=settings,
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker
    )
    yield client
    # Cleanup
    await client.close()


class TestAsyncClientSession:
    """Test that BaseAPIClient uses aiohttp.ClientSession."""

    @pytest.mark.asyncio
    async def test_client_has_async_session(self, base_client):
        """Verify BaseAPIClient has aiohttp.ClientSession."""
        assert hasattr(base_client, 'session'), "Client should have session attribute"
        assert isinstance(base_client.session, ClientSession), \
            f"Session should be aiohttp.ClientSession, got {type(base_client.session)}"

    @pytest.mark.asyncio
    async def test_session_timeout_configured(self, base_client, settings):
        """Verify session timeout is configured correctly."""
        assert base_client.session.timeout.total is not None, "Timeout should be configured"
        # aiohttp uses ClientTimeout object
        expected_total = settings.connect_timeout + settings.request_timeout
        assert base_client.session.timeout.total == expected_total, \
            f"Total timeout should be {expected_total}s"

    @pytest.mark.asyncio
    async def test_session_connection_limit_configured(self, base_client):
        """Verify connection pooling limits are set."""
        # aiohttp.TCPConnector has limit
        connector = base_client.session.connector
        assert connector is not None, "Session should have connector"
        assert connector.limit > 0, "Connector should have connection limit"
        assert connector.limit >= 100, "Should support at least 100 concurrent connections"


class TestAsyncHTTPMethods:
    """Test async HTTP methods (GET, POST, PUT, DELETE)."""

    @pytest.mark.asyncio
    async def test_get_request_is_async(self, base_client):
        """Verify _make_request() method is async and returns awaitable."""
        # Create async context manager mock
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b'{"test": "data"}')

        # Patch session.request to return an async context manager
        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should be awaitable (async method)
            result = base_client._make_request("GET", "/test")
            assert asyncio.iscoroutine(result), "_make_request should return coroutine"

            # Should return JSON data
            data = await result
            assert data == {"test": "data"}

            # Verify request was called
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_request_is_async(self, base_client):
        """Verify POST requests work with async."""
        mock_response = AsyncMock()
        mock_response.status = 201
        mock_response.read = AsyncMock(return_value=b'{"created": true}')

        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            # POST with JSON body
            result = await base_client._make_request("POST", "/orders", json_data={"size": 100})
            assert result == {"created": True}

            # Verify request was called with json parameter
            mock_request.assert_called_once()
            call_kwargs = mock_request.call_args.kwargs
            assert 'json' in call_kwargs, "POST should include json parameter"

    @pytest.mark.asyncio
    async def test_delete_request_is_async(self, base_client):
        """Verify DELETE requests work with async."""
        mock_response = AsyncMock()
        mock_response.status = 204
        mock_response.read = AsyncMock(return_value=b'{}')

        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await base_client._make_request("DELETE", "/orders/123")
            assert result == {}

            mock_request.assert_called_once()


class TestAsyncErrorHandling:
    """Test async error handling."""

    @pytest.mark.asyncio
    async def test_timeout_raises_polymarket_timeout_error(self, base_client):
        """Verify asyncio.TimeoutError is converted to PolymarketTimeoutError."""
        with patch.object(base_client.session, 'request') as mock_request:
            # Make the context manager itself raise the error
            async def raise_timeout(*args, **kwargs):
                raise asyncio.TimeoutError("Request timed out")
            mock_request.return_value.__aenter__ = raise_timeout
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(PolymarketTimeoutError) as exc_info:
                await base_client._make_request("GET", "/test")

            assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_aiohttp_client_error_raises_api_error(self, base_client):
        """Verify aiohttp.ClientError is converted to APIError."""
        with patch.object(base_client.session, 'request') as mock_request:
            # Make the context manager itself raise the error
            async def raise_client_error(*args, **kwargs):
                raise aiohttp.ClientError("Connection failed")
            mock_request.return_value.__aenter__ = raise_client_error
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(APIError) as exc_info:
                await base_client._make_request("GET", "/test")

            assert "connection" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_429_status_raises_rate_limit_error(self, base_client):
        """Verify 429 status code raises RateLimitError."""
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.read = AsyncMock(return_value=b"Rate limit exceeded")
        mock_response.headers = {"Retry-After": "60"}  # Regular dict, not AsyncMock

        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(RateLimitError):
                await base_client._make_request("GET", "/test")

    @pytest.mark.asyncio
    async def test_401_status_raises_authentication_error(self, base_client):
        """Verify 401 status code raises AuthenticationError."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.read = AsyncMock(return_value=b"Unauthorized")

        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(AuthenticationError):
                await base_client._make_request("GET", "/test")


class TestAsyncRateLimiting:
    """Test rate limiting integration with async."""

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_called_before_request(self, base_client):
        """Verify rate limiter is consulted before making request."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b'{}')

        with patch.object(base_client.rate_limiter, 'acquire_async', new_callable=AsyncMock) as mock_acquire:
            with patch.object(base_client.session, 'request') as mock_request:
                mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
                mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

                await base_client._make_request("GET", "/test", rate_limit_key="test")

                # Verify acquire was called
                mock_acquire.assert_called_once()


class TestAsyncCircuitBreaker:
    """Test circuit breaker integration with async."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_tracks_failures(self, base_client):
        """Verify circuit breaker tracks failures via retry strategy."""
        with patch.object(base_client.session, 'request') as mock_request:
            # Make the context manager itself raise the error
            async def raise_client_error(*args, **kwargs):
                raise aiohttp.ClientError("Simulated failure")
            mock_request.return_value.__aenter__ = raise_client_error
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            # First call through retry strategy should fail and increment circuit breaker
            try:
                await base_client.get("/test", retry=True)
            except APIError:
                pass

            # Circuit breaker should have tracked the failure
            assert base_client.circuit_breaker._failures > 0


class TestSessionLifecycle:
    """Test session lifecycle management."""

    @pytest.mark.asyncio
    async def test_session_cleanup_on_close(self, settings):
        """Verify session is properly closed."""
        client = BaseAPIClient(
            base_url="https://clob.polymarket.com",
            settings=settings
        )

        # Session should exist
        assert client.session is not None
        assert not client.session.closed

        # Close client
        await client.close()

        # Session should be closed
        assert client.session.closed

    @pytest.mark.asyncio
    async def test_multiple_close_calls_safe(self, settings):
        """Verify multiple close() calls don't raise errors."""
        client = BaseAPIClient(
            base_url="https://clob.polymarket.com",
            settings=settings
        )

        await client.close()
        # Should not raise
        await client.close()


class TestConnectionPooling:
    """Test connection pooling with aiohttp."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_use_connection_pool(self, base_client):
        """Verify concurrent requests reuse connections."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b'{"test": "data"}')

        with patch.object(base_client.session, 'request') as mock_request:
            mock_request.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_request.return_value.__aexit__ = AsyncMock(return_value=None)

            # Make 10 concurrent requests
            tasks = [base_client._make_request("GET", f"/test/{i}") for i in range(10)]
            results = await asyncio.gather(*tasks)

            # All should succeed
            assert len(results) == 10
            assert all(r == {"test": "data"} for r in results)

            # Verify all requests were made (connection pooling reuses connections)
            assert mock_request.call_count == 10
