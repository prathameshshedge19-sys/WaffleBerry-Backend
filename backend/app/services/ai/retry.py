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


AI_MAX_RETRIES = 2
AI_RETRY_BASE_DELAY_SECONDS = 0.25
AI_RETRY_MAX_DELAY_SECONDS = 2.0
AI_RETRY_JITTER_SECONDS = 0.15

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
        max_retries: int = AI_MAX_RETRIES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self._sleep = sleep

    def should_retry(self, exc: Exception, retry_number: int) -> bool:
        return (
            isinstance(exc, RETRYABLE_AI_EXCEPTIONS)
            and retry_number <= self.max_retries
        )

    async def wait(self, exc: Exception, retry_number: int) -> None:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after >= 0:
            delay = min(float(retry_after), AI_RETRY_MAX_DELAY_SECONDS)
        else:
            exponential = (
                AI_RETRY_BASE_DELAY_SECONDS
                * (2 ** (retry_number - 1))
            )
            delay = min(exponential, AI_RETRY_MAX_DELAY_SECONDS)
            delay += random.uniform(0, AI_RETRY_JITTER_SECONDS)

        await self._sleep(delay)
