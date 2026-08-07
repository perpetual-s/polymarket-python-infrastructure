"""Tests for rate limiter."""

import pytest
from ..config import get_rate_limit
from ..utils.rate_limiter import RateLimiter
from ..exceptions import RateLimitError


def test_rate_limiter_allows_within_limit():
    """Test requests within limit are allowed."""
    limiter = RateLimiter(enabled=True, margin=1.0)

    # Should allow first request
    limiter.acquire("test", timeout=1.0)
    assert limiter.get_remaining("test") >= 0


def test_rate_limiter_blocks_over_limit():
    """Test requests over limit are blocked.

    Uses a configured endpoint at full margin so the quota is exact, and a
    zero timeout so the assertion is about the limit rather than about wall
    clock behavior.
    """
    endpoint = "GET:/activity"
    quota = get_rate_limit(endpoint)["limit"]

    limiter = RateLimiter(enabled=True, margin=1.0)

    # Fill the whole window quota; every one of these must be admitted.
    for _ in range(quota):
        limiter.acquire(endpoint, timeout=0.0)

    assert limiter.get_remaining(endpoint) == 0

    # The next request is over the limit and cannot be admitted by waiting
    # zero seconds.
    with pytest.raises(RateLimitError):
        limiter.acquire(endpoint, timeout=0.0)


def test_rate_limiter_disabled():
    """Test disabled rate limiter allows all."""
    limiter = RateLimiter(enabled=False)

    # Should allow unlimited
    for _ in range(1000):
        limiter.acquire("test")


def test_activity_uses_live_measured_conservative_limit():
    assert get_rate_limit("GET:/activity") == {"limit": 8, "window": 10}
    assert RateLimiter(enabled=True).get_remaining("GET:/activity") == 6


def test_rate_limiter_handles_config_errors():
    """Test rate limiter gracefully handles configuration errors."""
    from unittest.mock import patch

    limiter = RateLimiter(enabled=True)

    # Mock get_rate_limit to raise exception
    with patch('polymarket.utils.rate_limiter.get_rate_limit') as mock_config:
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
