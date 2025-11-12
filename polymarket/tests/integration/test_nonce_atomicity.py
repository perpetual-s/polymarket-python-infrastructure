"""
Integration test for atomic nonce management.

Critical test to verify no race conditions under concurrent load.
"""

import pytest
import threading
from unittest.mock import Mock, patch
from shared.polymarket.utils.cache import AtomicNonceManager


class TestAtomicNonceManager:
    """Test atomic nonce manager."""

    def test_get_and_increment(self):
        """Test atomic get and increment."""
        manager = AtomicNonceManager()

        # Set initial nonce
        manager.set("0x123", 100)

        # Get and increment
        nonce1 = manager.get_and_increment("0x123")
        nonce2 = manager.get_and_increment("0x123")
        nonce3 = manager.get_and_increment("0x123")

        # Verify increments
        assert nonce1 == 100
        assert nonce2 == 101
        assert nonce3 == 102

    def test_concurrent_access_no_race_condition(self):
        """
        CRITICAL: Test no race conditions under concurrent access.

        This is the bug we fixed - multiple threads must get unique nonces.
        """
        manager = AtomicNonceManager()
        manager.set("0x123", 0)

        nonces = []
        nonces_lock = threading.Lock()

        def get_nonce():
            """Thread worker to get nonce."""
            nonce = manager.get_and_increment("0x123")
            with nonces_lock:
                nonces.append(nonce)

        # Create 100 threads
        threads = []
        for _ in range(100):
            t = threading.Thread(target=get_nonce)
            threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Verify: Should have 100 unique nonces (0-99)
        assert len(nonces) == 100
        assert len(set(nonces)) == 100  # All unique
        assert set(nonces) == set(range(100))  # Exactly 0-99

    def test_concurrent_multi_wallet(self):
        """Test concurrent access across multiple wallets."""
        manager = AtomicNonceManager()

        # Initialize wallets
        wallets = [f"0x{i:040x}" for i in range(10)]
        for wallet in wallets:
            manager.set(wallet, 0)

        nonces_by_wallet = {wallet: [] for wallet in wallets}
        lock = threading.Lock()

        def get_nonce_for_wallet(wallet):
            """Get nonce for specific wallet."""
            nonce = manager.get_and_increment(wallet)
            with lock:
                nonces_by_wallet[wallet].append(nonce)

        # Create 10 threads per wallet = 100 threads total
        threads = []
        for wallet in wallets:
            for _ in range(10):
                t = threading.Thread(target=get_nonce_for_wallet, args=(wallet,))
                threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Verify: Each wallet should have 10 unique nonces
        for wallet in wallets:
            nonces = nonces_by_wallet[wallet]
            assert len(nonces) == 10
            assert len(set(nonces)) == 10  # All unique
            assert set(nonces) == set(range(10))  # Exactly 0-9


class TestMarketMetadataCache:
    """Test market metadata cache with atomic nonce."""

    def test_nonce_increment_atomic(self):
        """Test nonce increment is atomic via cache."""
        from shared.polymarket.utils.cache import MarketMetadataCache

        cache = MarketMetadataCache()
        cache.set_nonce("0x123", 100)

        # Increment (get-and-increment semantics)
        current_nonce = cache.increment_nonce("0x123")

        # Verify: returns CURRENT (100), increments to 101
        assert current_nonce == 100  # Nonce to USE
        assert cache.get_nonce("0x123") == 101  # Stored nonce incremented

    def test_concurrent_nonce_increment(self):
        """Test concurrent nonce increments via cache."""
        from shared.polymarket.utils.cache import MarketMetadataCache

        cache = MarketMetadataCache()
        cache.set_nonce("0x123", 0)

        results = []
        lock = threading.Lock()

        def increment():
            """Increment nonce."""
            # Simulate get and increment pattern
            current = cache.nonce_manager.get_and_increment("0x123")
            with lock:
                results.append(current)

        # Run 50 concurrent increments
        threads = [threading.Thread(target=increment) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all unique
        assert len(results) == 50
        assert len(set(results)) == 50
        assert set(results) == set(range(50))
