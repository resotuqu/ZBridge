from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
)

from app.schemas.messages import ChatMessage
from app.services.retry import call_with_retries


logger = logging.getLogger(__name__)


class GigaChatError(RuntimeError):
    pass


class GigaChatAuthError(GigaChatError):
    pass


class GigaChatTransientError(GigaChatError):
    pass


class _OAuthResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    access_token: SecretStr
    expires_at: int | float


class _AssistantMessage(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    content: str


class _Choice(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    message: _AssistantMessage


class _ChatResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    choices: list[_Choice]


@dataclass(frozen=True, slots=True)
class OAuthToken:
    access_token: str
    expires_at: datetime

    def is_valid(
        self,
        margin_seconds: int = 60,
    ) -> bool:
        return (
            datetime.now(UTC)
            + timedelta(
                seconds=margin_seconds
            )
            < self.expires_at
        )


class GigaChatClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        credentials: str,
        scope: str,
        api_base_url: str,
        oauth_url: str,
        retry_delays: tuple[
            float,
            ...,
        ] = (0.5, 2.0),
    ) -> None:
        self._http = http_client
        self._credentials = credentials
        self._scope = scope
        self._api_base_url = (
            api_base_url.rstrip("/")
        )
        self._oauth_url = oauth_url
        self._retry_delays = retry_delays

        self._token: OAuthToken | None = None
        self._token_lock = asyncio.Lock()

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
    ) -> str:
        if not messages:
            raise ValueError(
                "At least one chat message "
                "is required."
            )

        access_token = (
            await self._get_access_token()
        )

        try:
            return await self._chat_with_retries(
                messages=messages,
                model=model,
                access_token=access_token,
            )

        except GigaChatAuthError:
            refreshed_token = (
                await self._get_access_token(
                    force_refresh=True,
                    rejected_token=access_token,
                )
            )

            return await self._chat_with_retries(
                messages=messages,
                model=model,
                access_token=refreshed_token,
            )

    async def _get_access_token(
            self,
            *,
            force_refresh: bool = False,
            rejected_token: str | None = None,
    ) -> str:
        cached = self._token
        if not force_refresh and cached is not None and cached.is_valid():
            return cached.access_token

        async with self._token_lock:
            cached = self._token

            if (
                    force_refresh
                    and rejected_token is not None
                    and cached is not None
                    and cached.access_token != rejected_token
                    and cached.is_valid()
            ):
                return cached.access_token

            if not force_refresh and cached is not None and cached.is_valid():
                return cached.access_token

            try:
                token = await call_with_retries(
                    self._request_access_token_once,
                    retry_exceptions=(
                        httpx.TransportError,
                        GigaChatTransientError,
                    ),
                    delays=self._retry_delays,
                    on_retry=self._log_retry,
                )
            except httpx.TransportError as exc:
                raise GigaChatTransientError(
                    "OAuth transport failed."
                ) from exc

            self._token = token
            return token.access_token

    async def _request_access_token_once(
        self,
    ) -> OAuthToken:
        response = await self._http.post(
            self._oauth_url,
            headers={
                "Accept": "application/json",
                "Authorization": (
                    "Basic "
                    f"{self._credentials}"
                ),
                "RqUID": str(uuid4()),
            },
            data={
                "scope": self._scope,
            },
        )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise GigaChatTransientError(
                "OAuth temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code in {
            400,
            401,
            403,
        }:
            raise GigaChatAuthError(
                "OAuth rejected credentials: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise GigaChatError(
                "Unexpected OAuth status: "
                f"{response.status_code}"
            )

        try:
            payload = (
                _OAuthResponse.model_validate(
                    response.json()
                )
            )

        except (
            ValueError,
            ValidationError,
        ) as exc:
            raise GigaChatError(
                "Invalid OAuth response."
            ) from exc

        return OAuthToken(
            access_token=(
                payload
                .access_token
                .get_secret_value()
            ),
            expires_at=self._parse_expiration(
                payload.expires_at
            ),
        )

    async def _chat_with_retries(
            self,
            *,
            messages: list[ChatMessage],
            model: str,
            access_token: str,
    ) -> str:
        async def operation() -> str:
            return await self._chat_once(
                messages=messages,
                model=model,
                access_token=access_token,
            )

        try:
            return await call_with_retries(
                operation,
                retry_exceptions=(
                    httpx.TransportError,
                    GigaChatTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except httpx.TransportError as exc:
            raise GigaChatTransientError(
                "Chat transport failed."
            ) from exc

    async def _chat_once(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        access_token: str,
    ) -> str:
        response = await self._http.post(
            (
                f"{self._api_base_url}"
                "/chat/completions"
            ),
            headers={
                "Accept": "application/json",
                "Authorization": (
                    f"Bearer {access_token}"
                ),
            },
            json={
                "model": model,
                "messages": [
                    message.model_dump(
                        mode="json"
                    )
                    for message in messages
                ],
                "stream": False,
            },
        )

        if response.status_code == 401:
            raise GigaChatAuthError(
                "Access token rejected."
            )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise GigaChatTransientError(
                "Chat temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise GigaChatError(
                "Unexpected chat status: "
                f"{response.status_code}"
            )

        try:
            payload = (
                _ChatResponse.model_validate(
                    response.json()
                )
            )

        except (
            ValueError,
            ValidationError,
        ) as exc:
            raise GigaChatError(
                "Invalid chat response."
            ) from exc

        if not payload.choices:
            raise GigaChatError(
                "Chat response contains "
                "no choices."
            )

        text = (
            payload
            .choices[0]
            .message
            .content
            .strip()
        )

        if not text:
            raise GigaChatError(
                "Chat response is empty."
            )

        return text

    @staticmethod
    def _parse_expiration(
        value: int | float,
    ) -> datetime:
        timestamp = float(value)

        # Поддержка секунд и миллисекунд.
        if timestamp > 10_000_000_000:
            timestamp /= 1000

        return datetime.fromtimestamp(
            timestamp,
            tz=UTC,
        )

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "GigaChat request retry",
            extra={
                "event": "gigachat_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": (
                    type(error).__name__
                ),
            },
        )