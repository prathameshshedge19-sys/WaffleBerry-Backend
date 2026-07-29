"""Small retry policy for transient provider-generation failures."""

import asyncio
import random
from collections.abc import Awaitable, Callable

from app.services.ai.exceptions import (
    AIConnectionError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)


RETRYABLE_AI_EXCEPTIONS = (
    AIConnectionError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)


class AIRetryPolicy:
    """Calculate and apply bounded exponential retry delays."""

    def __init__(
        self,
        *,
        max_retries: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
        jitter_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.max_delay_seconds = max(0.0, max_delay_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self._sleep = sleep

    @classmethod
    def no_retries(cls) -> "AIRetryPolicy":
        """Return a safe fallback for explicitly unconfigured callers."""
        return cls(
            max_retries=0,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_seconds=0,
        )

    def should_retry(self, exc: Exception, retry_number: int) -> bool:
        return (
            isinstance(exc, RETRYABLE_AI_EXCEPTIONS)
            and retry_number <= self.max_retries
        )

    async def wait(self, exc: Exception, retry_number: int) -> None:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after >= 0:
            delay = min(float(retry_after), self.max_delay_seconds)
        else:
            exponential = (
                self.base_delay_seconds
                * (2 ** (retry_number - 1))
            )
            delay = min(exponential, self.max_delay_seconds)
            delay += random.uniform(0, self.jitter_seconds)
            delay = min(delay, self.max_delay_seconds)

        await self._sleep(delay)
