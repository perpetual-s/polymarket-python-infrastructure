"""
Thread-safe rate limiter with per-endpoint limits.

Supports burst allowances and sliding time windows.
"""

import asyncio
import time
import threading
from collections import deque
from typing import Optional
from ..config import get_rate_limit
from ..exceptions import RateLimitError
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Thread-safe rate limiter for API endpoints.

    Tracks requests per endpoint with sliding time windows.
    Supports burst and sustained rate limits.
    """

    def __init__(
        self,
        enabled: bool = True,
        margin: float = 0.8,
        cleanup_interval: float = 300.0,  # 5 minutes
        endpoint_ttl: float = 3600.0  # 1 hour
    ):
        """
        Initialize rate limiter.

        Args:
            enabled: Whether rate limiting is enabled
            margin: Use only this fraction of limits (e.g., 0.8 = 80%)
            cleanup_interval: Seconds between cleanup runs (default: 5 min)
            endpoint_ttl: Seconds before unused endpoint is removed (default: 1 hour)
        """
        self.enabled = enabled
        self.margin = margin
        self.cleanup_interval = cleanup_interval
        self.endpoint_ttl = endpoint_ttl

        self._locks: dict[str, threading.RLock] = {}
        self._requests: dict[str, deque] = {}  # endpoint -> timestamps
        self._last_access: dict[str, float] = {}  # endpoint -> last access time
        self._lock = threading.RLock()  # Reentrant lock for locks dict
        self._last_cleanup = time.time()
        self._request_count = 0

    def _get_lock(self, endpoint: str) -> threading.RLock:
        """Get or create reentrant lock for endpoint."""
        with self._lock:
            if endpoint not in self._locks:
                self._locks[endpoint] = threading.RLock()
            return self._locks[endpoint]

    def _get_requests(self, endpoint: str) -> deque:
        """Get or create request queue for endpoint."""
        lock = self._get_lock(endpoint)
        with lock:
            if endpoint not in self._requests:
                self._requests[endpoint] = deque()
            return self._requests[endpoint]

    def _clean_old_requests(self, endpoint: str, window: float) -> None:
        """Remove requests outside time window."""
        now = time.time()
        cutoff = now - window
        requests = self._get_requests(endpoint)

        while requests and requests[0] < cutoff:
            requests.popleft()

    def acquire(self, endpoint: str, timeout: Optional[float] = None) -> None:
        """
        Acquire permission to make request (blocking).

        OPTIMIZED: Lock held only during state check, not during sleep.
        This prevents lock contention under high load.

        MEMORY OPTIMIZATION: Periodically cleans up unused endpoints to prevent
        unbounded dictionary growth.

        Args:
            endpoint: API endpoint pattern (e.g., "POST:/order")
            timeout: Max wait time in seconds (None = wait forever)

        Raises:
            RateLimitError: If timeout exceeded while waiting or configuration error
        """
        if not self.enabled:
            return

        try:
            lock = self._get_lock(endpoint)
            config = get_rate_limit(endpoint)
            window = config.get("window", 10)
            limit = config.get("limit")
            burst = config.get("burst")

            # Use burst limit if available, otherwise regular limit
            effective_limit = burst if burst else limit
            if effective_limit:
                effective_limit = int(effective_limit * self.margin)

            if not effective_limit:
                return

        except Exception as e:
            # Configuration error - raise RateLimitError with context
            logger.error(f"Rate limiter configuration error for {endpoint}: {e}")
            raise RateLimitError(
                f"Rate limiter configuration error: {e}",
                endpoint=endpoint,
                retry_after=0
            ) from e

        start_time = time.time()

        while True:
            try:
                # Lock only for state check and mutation
                with lock:
                    self._clean_old_requests(endpoint, window)
                    requests = self._get_requests(endpoint)

                    if len(requests) < effective_limit:
                        # Have capacity, record request
                        requests.append(time.time())
                        return  # Success!

                    # Calculate wait time
                    oldest_request = requests[0]
                    wait_time = window - (time.time() - oldest_request)

            except Exception as e:
                # Internal error (queue corruption, etc) - raise RateLimitError
                logger.error(f"Rate limiter internal error for {endpoint}: {e}")
                raise RateLimitError(
                    f"Rate limiter internal error: {e}",
                    endpoint=endpoint,
                    retry_after=0
                ) from e

            # Check timeout OUTSIDE lock
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    raise RateLimitError(
                        f"Rate limit timeout for {endpoint}",
                        endpoint=endpoint,
                        retry_after=wait_time
                    )

            # Sleep OUTSIDE lock to prevent blocking other endpoints
            if wait_time > 0:
                logger.debug(
                    f"Rate limit reached for {endpoint}, waiting {wait_time:.2f}s"
                )
                time.sleep(min(wait_time, 1.0))  # Sleep max 1s at a time
            else:
                # Busy wait with small delay
                time.sleep(0.001)  # 1ms

    async def acquire_async(
        self,
        endpoint: str,
        timeout: Optional[float] = None
    ) -> None:
        """
        Acquire permission to make request (async).

        Args:
            endpoint: API endpoint pattern
            timeout: Max wait time in seconds

        Raises:
            RateLimitError: If timeout exceeded
        """
        if not self.enabled:
            return

        # Use thread-safe sync version in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.acquire, endpoint, timeout)

    def get_remaining(self, endpoint: str) -> int:
        """
        Get remaining requests in current window.

        Args:
            endpoint: API endpoint pattern

        Returns:
            Number of requests available
        """
        if not self.enabled:
            return 99999

        lock = self._get_lock(endpoint)
        with lock:
            config = get_rate_limit(endpoint)
            window = config.get("window", 10)
            limit = config.get("limit")
            burst = config.get("burst")

            effective_limit = burst if burst else limit
            if effective_limit:
                effective_limit = int(effective_limit * self.margin)

            if not effective_limit:
                return 99999

            self._clean_old_requests(endpoint, window)
            requests = self._get_requests(endpoint)

            return max(0, effective_limit - len(requests))

    def cleanup_stale_endpoints(self) -> int:
        """
        Manually cleanup endpoints not accessed in endpoint_ttl seconds.

        MEMORY OPTIMIZATION: Call periodically in long-running processes
        to prevent unbounded dict growth.

        Returns:
            Number of endpoints cleaned up
        """
        now = time.time()
        cutoff = now - self.endpoint_ttl

        with self._lock:
            # Find stale endpoints
            stale = [
                endpoint for endpoint, last_access in self._last_access.items()
                if last_access < cutoff
            ]

            # Remove stale endpoints
            for endpoint in stale:
                self._requests.pop(endpoint, None)
                self._locks.pop(endpoint, None)
                self._last_access.pop(endpoint, None)

            if stale:
                logger.info(f"Cleaned up {len(stale)} stale rate limit endpoints")

            return len(stale)

    def reset(self, endpoint: Optional[str] = None) -> None:
        """
        Reset rate limiter state.

        Args:
            endpoint: Specific endpoint to reset, or None for all
        """
        with self._lock:
            if endpoint:
                self._requests[endpoint] = deque()
            else:
                self._requests.clear()

    def get_stats(self) -> dict[str, dict]:
        """
        Get rate limiter statistics.

        Returns:
            Dict of endpoint -> stats
        """
        stats = {}
        for endpoint in self._requests:
            config = get_rate_limit(endpoint)
            remaining = self.get_remaining(endpoint)
            limit = config.get("burst") or config.get("limit", 0)
            effective = int(limit * self.margin) if limit else 0

            stats[endpoint] = {
                "limit": effective,
                "used": effective - remaining,
                "remaining": remaining,
                "window": config.get("window", 10)
            }

        return stats
