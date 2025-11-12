"""
Retry logic with exponential backoff and circuit breaker.

Handles transient failures with configurable retry strategies.
"""

import asyncio
import random
import time
import threading
from typing import Callable, TypeVar, Optional, Any
from functools import wraps
import logging

from ..exceptions import (
    PolymarketError,
    APIError,
    TimeoutError,
    RateLimitError,
    CircuitBreakerError
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent cascading failures.

    States: CLOSED (normal), OPEN (failing), HALF_OPEN (testing recovery)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: float = 60.0,
        name: str = "default"
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Failures before opening circuit
            timeout: Seconds before attempting recovery
            name: Circuit breaker name for logging
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.name = name

        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._lock = threading.Lock()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Call function with circuit breaker protection.

        Args:
            func: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerError: If circuit is open
        """
        with self._lock:
            if self._state == "OPEN":
                # Check if timeout expired
                if time.time() - self._last_failure_time >= self.timeout:
                    logger.info(f"Circuit breaker {self.name}: OPEN -> HALF_OPEN")
                    self._state = "HALF_OPEN"
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker {self.name} is OPEN"
                    )

        # Try calling function
        try:
            result = func(*args, **kwargs)

            # Success - reset on half-open or keep closed
            with self._lock:
                if self._state == "HALF_OPEN":
                    logger.info(f"Circuit breaker {self.name}: HALF_OPEN -> CLOSED")
                    self._state = "CLOSED"
                    self._failures = 0

            return result

        except Exception as e:
            # Failure - increment counter
            with self._lock:
                self._failures += 1
                self._last_failure_time = time.time()

                if self._failures >= self.failure_threshold:
                    if self._state != "OPEN":
                        logger.warning(
                            f"Circuit breaker {self.name}: {self._state} -> OPEN "
                            f"({self._failures} failures)"
                        )
                        self._state = "OPEN"
                elif self._state == "HALF_OPEN":
                    # Failed during testing, back to open
                    logger.warning(f"Circuit breaker {self.name}: HALF_OPEN -> OPEN")
                    self._state = "OPEN"

            raise

    def reset(self) -> None:
        """Reset circuit breaker to closed state."""
        with self._lock:
            self._failures = 0
            self._last_failure_time = None
            self._state = "CLOSED"
            logger.info(f"Circuit breaker {self.name}: RESET -> CLOSED")

    @property
    def state(self) -> str:
        """Get current state."""
        return self._state

    @property
    def failures(self) -> int:
        """Get failure count."""
        return self._failures


class RetryStrategy:
    """
    Configurable retry strategy with exponential backoff.

    Features:
    - Exponential backoff with jitter
    - Configurable retry conditions
    - Circuit breaker integration
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize retry strategy.

        Args:
            max_retries: Maximum retry attempts
            base_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Backoff multiplier
            jitter: Add random jitter to delays
            circuit_breaker: Optional circuit breaker
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.circuit_breaker = circuit_breaker

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for attempt with exponential backoff + jitter."""
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )

        if self.jitter:
            # Add random jitter (±25%)
            jitter_amount = delay * 0.25
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0, delay)

    def _should_retry(self, exception: Exception, attempt: int) -> bool:
        """Determine if exception should trigger retry."""
        # Don't retry if max attempts reached
        if attempt >= self.max_retries:
            return False

        # Never retry validation errors or circuit breaker errors
        if isinstance(exception, (CircuitBreakerError,)):
            return False

        # Retry on API errors, timeouts, rate limits
        if isinstance(exception, (APIError, TimeoutError, RateLimitError)):
            return True

        # Retry on connection errors
        if isinstance(exception, (ConnectionError, OSError)):
            return True

        return False

    def execute(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute function with retry logic.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            Last exception if all retries exhausted
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                # Use circuit breaker if available
                if self.circuit_breaker:
                    return self.circuit_breaker.call(func, *args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if not self._should_retry(e, attempt):
                    logger.debug(
                        f"Not retrying {func.__name__} after attempt {attempt + 1}: "
                        f"{type(e).__name__}"
                    )
                    raise

                delay = self._calculate_delay(attempt)
                logger.warning(
                    f"Retry {attempt + 1}/{self.max_retries} for {func.__name__} "
                    f"after {type(e).__name__}: {e}. "
                    f"Waiting {delay:.2f}s"
                )

                time.sleep(delay)

        # All retries exhausted
        if last_exception:
            logger.error(
                f"All {self.max_retries} retries exhausted for {func.__name__}"
            )
            raise last_exception

        # Should never reach here
        raise PolymarketError("Retry logic error")

    async def execute_async(
        self,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Execute async function with retry logic.

        Args:
            func: Async function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                # Circuit breaker check with proper locking
                if self.circuit_breaker:
                    with self.circuit_breaker._lock:
                        if self.circuit_breaker._state == "OPEN":
                            # Check if timeout expired
                            if (self.circuit_breaker._last_failure_time and
                                time.time() - self.circuit_breaker._last_failure_time >=
                                self.circuit_breaker.timeout):
                                self.circuit_breaker._state = "HALF_OPEN"
                            else:
                                raise CircuitBreakerError(
                                    f"Circuit breaker {self.circuit_breaker.name} is OPEN"
                                )

                # Execute async function
                result = await func(*args, **kwargs)

                # Success - update circuit breaker with proper locking
                if self.circuit_breaker:
                    with self.circuit_breaker._lock:
                        if self.circuit_breaker._state == "HALF_OPEN":
                            self.circuit_breaker._state = "CLOSED"
                            self.circuit_breaker._failures = 0

                return result

            except Exception as e:
                last_exception = e

                # Update circuit breaker on failure with proper locking
                if self.circuit_breaker:
                    with self.circuit_breaker._lock:
                        self.circuit_breaker._failures += 1
                        self.circuit_breaker._last_failure_time = time.time()

                        if self.circuit_breaker._failures >= self.circuit_breaker.failure_threshold:
                            self.circuit_breaker._state = "OPEN"
                        elif self.circuit_breaker._state == "HALF_OPEN":
                            # Failed during testing, back to open
                            self.circuit_breaker._state = "OPEN"

                if not self._should_retry(e, attempt):
                    raise

                delay = self._calculate_delay(attempt)
                logger.warning(
                    f"Async retry {attempt + 1}/{self.max_retries} "
                    f"for {func.__name__}: {type(e).__name__}. "
                    f"Waiting {delay:.2f}s"
                )

                await asyncio.sleep(delay)

        if last_exception:
            raise last_exception

        raise PolymarketError("Retry logic error")


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0
) -> Callable:
    """
    Decorator to add retry logic to function.

    Args:
        max_retries: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        strategy = RetryStrategy(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay
        )

        @wraps(func)
        def wrapper(*args, **kwargs):
            return strategy.execute(func, *args, **kwargs)

        return wrapper

    return decorator
