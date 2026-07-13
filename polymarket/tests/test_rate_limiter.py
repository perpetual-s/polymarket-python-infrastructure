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


def test_rate_limiter_blocks_over_limit(monkeypatch):
    """A full deterministic bucket fails immediately at a zero timeout."""
    monkeypatch.setattr(
        "polymarket.utils.rate_limiter.get_rate_limit",
        lambda _endpoint: {"window": 60, "limit": 1, "burst": None},
    )
    limiter = RateLimiter(enabled=True, margin=1.0)

    limiter.acquire("test", timeout=0)

    with pytest.raises(RateLimitError, match="Rate limit timeout") as exc_info:
        limiter.acquire("test", timeout=0)

    assert exc_info.value.endpoint == "test"
    assert exc_info.value.retry_after > 0


def test_rate_limiter_disabled():
    """Test disabled rate limiter allows all."""
    limiter = RateLimiter(enabled=False)

    # Should allow unlimited
    for _ in range(1000):
        limiter.acquire("test")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"margin": 0.0},
        {"margin": float("nan")},
        {"margin": 1.01},
        {"cleanup_interval": 0.0},
        {"endpoint_ttl": float("inf")},
    ],
)
def test_rate_limiter_rejects_invalid_configuration(kwargs):
    """Invalid bounds cannot silently disable throttling or cleanup."""
    with pytest.raises(ValueError):
        RateLimiter(**kwargs)


@pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf")])
def test_rate_limiter_rejects_invalid_timeout(timeout):
    """Malformed timeouts cannot enter an unbounded wait."""
    limiter = RateLimiter()

    with pytest.raises(ValueError, match="finite and non-negative"):
        limiter.acquire("test", timeout=timeout)


def test_rate_limiter_tracks_and_cleans_stale_endpoints(monkeypatch):
    """Endpoint access timestamps drive the advertised bounded cleanup."""
    monkeypatch.setattr(
        "polymarket.utils.rate_limiter.get_rate_limit",
        lambda _endpoint: {"window": 60, "limit": 10, "burst": None},
    )
    limiter = RateLimiter(margin=1.0, cleanup_interval=1.0, endpoint_ttl=5.0)
    limiter.acquire("stale")
    limiter.acquire("active")

    now = time.monotonic()
    limiter._last_access["stale"] = now - 10
    limiter._last_access["active"] = now

    assert limiter.cleanup_stale_endpoints(now=now) == 1
    assert "stale" not in limiter._requests
    assert "stale" not in limiter._locks
    assert "active" in limiter._requests


def test_small_positive_margin_still_enforces_one_request(monkeypatch):
    """Rounding a positive safety margin cannot turn throttling off."""
    monkeypatch.setattr(
        "polymarket.utils.rate_limiter.get_rate_limit",
        lambda _endpoint: {"window": 60, "limit": 2, "burst": None},
    )
    limiter = RateLimiter(margin=0.01)
    limiter.acquire("test", timeout=0)

    with pytest.raises(RateLimitError, match="Rate limit timeout"):
        limiter.acquire("test", timeout=0)


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
