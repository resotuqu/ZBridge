from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.retry import call_with_retries


logger = logging.getLogger(__name__)

_SAFE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadTimeout,
)


class WikipediaError(RuntimeError):
    pass


class WikipediaNotFoundError(WikipediaError):
    pass


class WikipediaTransientError(WikipediaError):
    pass


class _WikipediaSummaryResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    extract: str | None = None
    type: str | None = None


class WikipediaProvider:
    """
    Thin client for the public Wikipedia REST summary API
    (no API key required). Isolated from webhook/command
    handling so it stays independently unit-testable.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_base_url: str = (
            "https://ru.wikipedia.org/api/rest_v1"
        ),
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._http = http_client
        self._api_base_url = api_base_url.rstrip("/")
        self._retry_delays = retry_delays

    async def get_summary(self, topic: str) -> str:
        normalized_topic = topic.strip()

        if not normalized_topic:
            raise WikipediaNotFoundError(
                "Topic must not be blank."
            )

        encoded_topic = quote(
            normalized_topic.replace(" ", "_"),
            safe="",
        )
        url = (
            f"{self._api_base_url}"
            f"/page/summary/{encoded_topic}"
        )

        try:
            return await call_with_retries(
                lambda: self._fetch_once(url),
                retry_exceptions=(
                    *_SAFE_TRANSPORT_ERRORS,
                    WikipediaTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except _SAFE_TRANSPORT_ERRORS as exc:
            raise WikipediaTransientError(
                "Could not reach Wikipedia."
            ) from exc

    async def _fetch_once(self, url: str) -> str:
        response = await self._http.get(
            url,
            headers={"Accept": "application/json"},
        )

        if response.status_code == 404:
            raise WikipediaNotFoundError(
                "Article not found."
            )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise WikipediaTransientError(
                "Wikipedia temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise WikipediaError(
                "Unexpected Wikipedia status: "
                f"{response.status_code}"
            )

        try:
            payload = (
                _WikipediaSummaryResponse.model_validate(
                    response.json()
                )
            )
        except (ValueError, ValidationError) as exc:
            raise WikipediaError(
                "Invalid Wikipedia response."
            ) from exc

        extract = (payload.extract or "").strip()

        if not extract:
            raise WikipediaNotFoundError(
                "No summary available."
            )

        return extract

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "Wikipedia request retry",
            extra={
                "event": "wikipedia_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
            },
        )
