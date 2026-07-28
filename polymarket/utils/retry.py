"""Retry transient transport failures with exponential backoff."""

import asyncio
import logging
import random
import time
from functools import wraps
from typing import Callable, Optional, TypeVar

from ..exceptions import (
    APIError,
    PolymarketError,
    RateLimitError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryStrategy:
    """
    Configurable retry strategy with exponential backoff.

    Features:
    - Exponential backoff with jitter
    - Configurable retry conditions
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """
        Initialize retry strategy.

        Args:
            max_retries: Maximum retry attempts
            base_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Backoff multiplier
            jitter: Add random jitter to delays
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for attempt with exponential backoff + jitter."""
        delay = min(self.base_delay * (self.exponential_base**attempt), self.max_delay)

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

        if isinstance(exception, APIError):
            status_code = getattr(exception, "status_code", None)
            if status_code is not None and 400 <= status_code < 500:
                return False
            return True

        # Retry on timeouts and rate limits
        if isinstance(exception, (TimeoutError, RateLimitError)):
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
                result = func(*args, **kwargs)
                return result

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

    async def execute_async(self, func: Callable[..., T], *args, **kwargs) -> T:
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
                # Execute async function
                result = await func(*args, **kwargs)
                return result

            except Exception as e:
                last_exception = e

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
    max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0
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
            max_retries=max_retries, base_delay=base_delay, max_delay=max_delay
        )

        @wraps(func)
        def wrapper(*args, **kwargs):
            return strategy.execute(func, *args, **kwargs)

        return wrapper

    return decorator
