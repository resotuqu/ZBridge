from __future__ import annotations

import asyncio
from collections.abc import (
    Awaitable,
    Callable,
    Sequence,
)
from typing import TypeVar


ResultT = TypeVar("ResultT")

RetryCallback = Callable[
    [BaseException, int, float],
    None,
]


async def call_with_retries(
    operation: Callable[
        [],
        Awaitable[ResultT],
    ],
    *,
    retry_exceptions: tuple[
        type[BaseException],
        ...,
    ],
    delays: Sequence[float],
    on_retry: RetryCallback | None = None,
) -> ResultT:
    for attempt in range(len(delays) + 1):
        try:
            return await operation()

        except retry_exceptions as exc:
            if attempt >= len(delays):
                raise

            delay = max(
                0.0,
                float(delays[attempt]),
            )

            if on_retry is not None:
                on_retry(
                    exc,
                    attempt + 1,
                    delay,
                )

            if delay:
                await asyncio.sleep(delay)

    raise RuntimeError(
        "Unreachable retry state."
    )