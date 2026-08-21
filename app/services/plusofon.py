from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.security import normalize_phone_number
from app.services.retry import call_with_retries


logger = logging.getLogger(__name__)

_SAFE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


class PlusofonError(RuntimeError):
    pass


class PlusofonAuthError(PlusofonError):
    pass


class PlusofonTransientError(PlusofonError):
    pass


class PlusofonDeliveryUnknownError(PlusofonError):
    pass


class _SendData(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    id: str | int
    pdu: int | None = None


class _SendResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    success: bool
    data: _SendData | None = None
    message: str | None = None
    pdu: int | None = None


@dataclass(frozen=True, slots=True)
class SendResult:
    message_id: str
    pdu_count: int


class PlusofonClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        token: str,
        client_id: int,
        number_id: int,
        api_base_url: str,
        retry_delays: tuple[float, ...] = (2.0, 10.0, 30.0),
    ) -> None:
        self._http = http_client
        self._token = token
        self._client_id = client_id
        self._number_id = number_id
        self._api_base_url = api_base_url.rstrip("/")
        self._retry_delays = retry_delays

    async def send(
        self,
        to: str,
        text: str,
    ) -> SendResult:
        recipient = normalize_phone_number(to)
        normalized_text = text.strip()

        if not normalized_text:
            raise ValueError("SMS text must not be blank.")

        async def operation() -> SendResult:
            return await self._send_once(
                recipient,
                normalized_text,
            )

        try:
            result = await call_with_retries(
                operation,
                retry_exceptions=(
                    *_SAFE_TRANSPORT_ERRORS,
                    PlusofonTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except _SAFE_TRANSPORT_ERRORS as exc:
            raise PlusofonTransientError(
                "Could not connect to Plusofon."
            ) from exc
        except httpx.TransportError as exc:
            raise PlusofonDeliveryUnknownError(
                "Could not confirm whether SMS was accepted."
            ) from exc

        logger.info(
            "SMS accepted by Plusofon",
            extra={
                "event": "plusofon_send",
                "phone": recipient,
                "message_id": result.message_id,
                "segments_sent": result.pdu_count,
            },
        )

        return result

    async def _send_once(
        self,
        recipient: str,
        text: str,
    ) -> SendResult:
        response = await self._http.post(
            f"{self._api_base_url}/sms",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Client": str(self._client_id),
            },
            json={
                "text": text,
                "number_id": self._number_id,
                "to": int(recipient),
                "reject_long": False,
                "count_pdu": True,
            },
        )

        if response.status_code in {401, 403}:
            raise PlusofonAuthError(
                f"Plusofon rejected credentials: "
                f"{response.status_code}"
            )

        if response.status_code == 429:
            raise PlusofonTransientError(
                "Plusofon rate limit reached."
            )

        if response.status_code >= 500:
            raise PlusofonDeliveryUnknownError(
                f"Plusofon server error: "
                f"{response.status_code}"
            )

        if not 200 <= response.status_code < 300:
            raise PlusofonError(
                f"Unexpected Plusofon status: "
                f"{response.status_code}"
            )

        try:
            payload = _SendResponse.model_validate(
                response.json()
            )
        except (ValueError, ValidationError) as exc:
            raise PlusofonDeliveryUnknownError(
                "Invalid Plusofon send response."
            ) from exc

        if not payload.success:
            raise PlusofonError(
                "Plusofon rejected SMS."
            )

        if payload.data is None:
            raise PlusofonDeliveryUnknownError(
                "Plusofon response contains no message data."
            )

        pdu_count = (
            payload.data.pdu
            or payload.pdu
            or 1
        )

        return SendResult(
            message_id=str(payload.data.id),
            pdu_count=pdu_count,
        )

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "Plusofon request retry",
            extra={
                "event": "plusofon_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
            },
        )