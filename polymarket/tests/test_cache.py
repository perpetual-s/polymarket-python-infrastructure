"""Tests for cache module."""

import time
import pytest
from ..utils.cache import TTLCache, MarketMetadataCache


def test_ttl_cache_basic():
    """Test basic cache operations."""
    cache = TTLCache(default_ttl=1.0)

    # Set and get
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"

    # Get missing key
    assert cache.get("missing") is None

    # Expiration
    cache.set("key2", "value2", ttl=0.1)
    time.sleep(0.2)
    assert cache.get("key2") is None


def test_ttl_cache_get_or_fetch():
    """Test get_or_fetch prevents thundering herd."""
    cache = TTLCache()
    fetch_count = 0

    def fetch():
        nonlocal fetch_count
        fetch_count += 1
        return f"value_{fetch_count}"

    # First call fetches
    result1 = cache.get_or_fetch("key", fetch)
    assert result1 == "value_1"
    assert fetch_count == 1

    # Second call uses cache
    result2 = cache.get_or_fetch("key", fetch)
    assert result2 == "value_1"
    assert fetch_count == 1  # Not called again


def test_market_metadata_cache():
    """Test market metadata cache."""
    cache = MarketMetadataCache()

    # Tick size
    cache.set_tick_size("token1", 0.01)
    assert cache.get_tick_size("token1") == 0.01

    # Fee rate
    cache.set_fee_rate("token1", 100)
    assert cache.get_fee_rate("token1") == 100

    # Nonce increment (get-and-increment semantics)
    cache.set_nonce("address1", 10)
    assert cache.get_nonce("address1") == 10
    # increment_nonce() returns CURRENT nonce (10) and increments to 11
    current_nonce = cache.increment_nonce("address1")
    assert current_nonce == 10  # Returns nonce to USE (before increment)
    assert cache.get_nonce("address1") == 11  # Stored nonce incremented
