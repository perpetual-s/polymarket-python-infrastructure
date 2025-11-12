"""Tests for rate limiter."""

import time
import pytest
from ..utils.rate_limiter import RateLimiter
from ..exceptions import RateLimitError


def test_rate_limiter_allows_within_limit():
    """Test requests within limit are allowed."""
    limiter = RateLimiter(enabled=True, margin=1.0)

    # Should allow first request
    limiter.acquire("test", timeout=1.0)
    assert limiter.get_remaining("test") >= 0


def test_rate_limiter_blocks_over_limit():
    """Test requests over limit are blocked."""
    limiter = RateLimiter(enabled=True, margin=0.001)  # Very low limit

    # Fill up quota
    for _ in range(3):
        try:
            limiter.acquire("test", timeout=0.1)
        except RateLimitError:
            break

    # Next request should timeout
    with pytest.raises(RateLimitError):
        limiter.acquire("test", timeout=0.1)


def test_rate_limiter_disabled():
    """Test disabled rate limiter allows all."""
    limiter = RateLimiter(enabled=False)

    # Should allow unlimited
    for _ in range(1000):
        limiter.acquire("test")


def test_rate_limiter_handles_config_errors():
    """Test rate limiter gracefully handles configuration errors."""
    from unittest.mock import patch

    limiter = RateLimiter(enabled=True)

    # Mock get_rate_limit to raise exception
    with patch('shared.polymarket.utils.rate_limiter.get_rate_limit') as mock_config:
        mock_config.side_effect = Exception("Config fetch failed")

        # Should NOT raise - should gracefully allow request or raise RateLimitError
        try:
            limiter.acquire("test", timeout=1.0)
            # If it doesn't raise, it should allow the request
        except RateLimitError as e:
            # If it raises, should be RateLimitError with clear message
            assert "Config fetch failed" in str(e) or "configuration error" in str(e).lower()
        except Exception as e:
            # Should NOT raise raw Exception
            pytest.fail(f"Should handle config errors gracefully, got: {type(e).__name__}: {e}")


def test_rate_limiter_handles_queue_corruption():
    """Test rate limiter handles corrupted request queue."""
    limiter = RateLimiter(enabled=True)

    # Corrupt the requests queue (simulate data corruption)
    endpoint = "test"
    lock = limiter._get_lock(endpoint)
    with lock:
        # Put invalid data in queue
        limiter._requests[endpoint] = "corrupted_data"  # Should be deque, not string

    # Should handle corruption gracefully
    try:
        limiter.acquire(endpoint, timeout=1.0)
        # If it doesn't raise, it recovered gracefully
    except RateLimitError as e:
        # If it raises, should be RateLimitError with clear message
        assert "internal error" in str(e).lower() or "corrupted" in str(e).lower()
    except Exception as e:
        # Should NOT raise raw Exception
        pytest.fail(f"Should handle queue corruption gracefully, got: {type(e).__name__}: {e}")
