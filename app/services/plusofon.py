from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

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

_MAX_HISTORY_PAGES = 200

_ItemT = TypeVar("_ItemT", bound=BaseModel)


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


class _Page(BaseModel, Generic[_ItemT]):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    data: list[_ItemT]
    next_page_url: str | None = None


class _SMSHistoryItem(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    created_datetime: datetime
    sent_datetime: datetime
    sender: str
    receiver: str
    msg: str
    incoming: bool
    pdu: int


class _SMSDialogItem(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    sender: str
    receiver: str
    sent_datetime: datetime
    created_datetime: datetime
    msg: str


@dataclass(frozen=True, slots=True)
class SMSMessage:
    created_at: datetime
    sent_at: datetime
    sender: str
    receiver: str
    text: str
    incoming: bool
    pdu: int | None = None


class PlusofonClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        token: str,
        client_id: int,
        number_id: int,
        api_base_url: str,
        own_number: str,
        default_timezone: ZoneInfo,
        retry_delays: tuple[float, ...] = (2.0, 10.0, 30.0),
    ) -> None:
        self._http = http_client
        self._token = token
        self._client_id = client_id
        self._number_id = number_id
        self._api_base_url = api_base_url.rstrip("/")
        self._own_number = normalize_phone_number(
            own_number
        )
        self._default_timezone = default_timezone
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

    async def list_messages(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        incoming: bool | None = None,
        receiver: str | None = None,
        sender: str | None = None,
        limit: int | None = None,
    ) -> list[SMSMessage]:
        params: dict[str, str] = {}

        if date_from is not None:
            params["date_from"] = date_from.isoformat()

        if date_to is not None:
            params["date_to"] = date_to.isoformat()

        if incoming is not None:
            params["incoming"] = (
                "1" if incoming else "0"
            )

        if receiver is not None:
            params["receiver"] = normalize_phone_number(
                receiver
            )

        if sender is not None:
            params["sender"] = normalize_phone_number(
                sender
            )

        if limit is not None:
            params["limit"] = str(limit)

        items = await self._paginate(
            f"{self._api_base_url}/sms",
            params,
            _Page[_SMSHistoryItem],
        )

        return [
            self._history_item_to_message(item)
            for item in items
        ]

    async def get_dialog(
        self,
        phone: str,
        limit: int,
    ) -> list[SMSMessage]:
        normalized_phone = normalize_phone_number(phone)

        items = await self._paginate(
            (
                f"{self._api_base_url}"
                f"/sms/dialog/{normalized_phone}"
            ),
            {},
            _Page[_SMSDialogItem],
        )

        messages = sorted(
            (
                self._dialog_item_to_message(item)
                for item in items
            ),
            key=lambda message: message.sent_at,
        )

        if limit <= 0:
            return []

        return messages[-limit:]

    def _history_item_to_message(
        self,
        item: _SMSHistoryItem,
    ) -> SMSMessage:
        return SMSMessage(
            created_at=self._with_default_timezone(
                item.created_datetime
            ),
            sent_at=self._with_default_timezone(
                item.sent_datetime
            ),
            sender=item.sender,
            receiver=item.receiver,
            text=item.msg,
            incoming=item.incoming,
            pdu=item.pdu,
        )

    def _dialog_item_to_message(
        self,
        item: _SMSDialogItem,
    ) -> SMSMessage:
        return SMSMessage(
            created_at=self._with_default_timezone(
                item.created_datetime
            ),
            sent_at=self._with_default_timezone(
                item.sent_datetime
            ),
            sender=item.sender,
            receiver=item.receiver,
            text=item.msg,
            incoming=(
                normalize_phone_number(item.receiver)
                == self._own_number
            ),
            pdu=None,
        )

    def _with_default_timezone(
        self,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            return value.replace(
                tzinfo=self._default_timezone
            )

        return value

    async def _paginate(
        self,
        url: str,
        params: dict[str, str],
        page_model: type[_Page[_ItemT]],
    ) -> list[_ItemT]:
        items: list[_ItemT] = []
        next_url: str | None = url
        next_params: dict[str, str] | None = params

        for _ in range(_MAX_HISTORY_PAGES):
            if next_url is None:
                break

            try:
                page = await call_with_retries(
                    partial(
                        self._fetch_page,
                        next_url,
                        next_params,
                        page_model,
                    ),
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
                raise PlusofonTransientError(
                    "Plusofon history request failed."
                ) from exc

            items.extend(page.data)
            next_url = page.next_page_url
            next_params = None

        return items

    async def _fetch_page(
        self,
        url: str,
        params: dict[str, str] | None,
        page_model: type[_Page[_ItemT]],
    ) -> _Page[_ItemT]:
        response = await self._http.get(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Client": str(self._client_id),
            },
        )

        if response.status_code in {401, 403}:
            raise PlusofonAuthError(
                f"Plusofon rejected credentials: "
                f"{response.status_code}"
            )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise PlusofonTransientError(
                f"Plusofon temporarily unavailable: "
                f"{response.status_code}"
            )

        if not 200 <= response.status_code < 300:
            raise PlusofonError(
                f"Unexpected Plusofon status: "
                f"{response.status_code}"
            )

        try:
            return page_model.model_validate(
                response.json()
            )
        except (ValueError, ValidationError) as exc:
            raise PlusofonError(
                "Invalid Plusofon history response."
            ) from exc

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