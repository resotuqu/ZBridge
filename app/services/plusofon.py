from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Generic, TypeVar
from urllib.parse import urljoin, urlsplit
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

    success: bool
    data: list[_ItemT]
    next_page_url: str | None = None


class _SMSHistoryItem(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    created_datetime: datetime
    sent_datetime: datetime | None
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
    sent_datetime: datetime | None
    created_datetime: datetime
    msg: str
    incoming: bool | None = None


class _SMSDialogResponse(BaseModel):
    """
    GET /api/v1/sms/dialog/{number} has its own, simpler
    contract: an object with a required `success` flag and a
    `data` array, no `pdu` on its items, and no pagination --
    it must never be treated as a `_Page` (which additionally
    offers `next_page_url`). `success` is required, matching
    the list endpoint: a response missing it, or carrying
    `success: false`, is invalid/rejected, not silently
    accepted.
    """

    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    success: bool
    data: list[_SMSDialogItem]


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
        self._api_origin = self._parse_origin(
            self._api_base_url
        )
        self._api_path_prefix = (
            urlsplit(self._api_base_url)
            .path.rstrip("/")
            + "/"
        )
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
                "reject_long": True,
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
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        incoming: bool | None = None,
        receiver: str | None = None,
        sender: str | None = None,
        limit: int | None = None,
    ) -> list[SMSMessage]:
        body: dict[str, object] = {}

        if date_from is not None:
            body["date_from"] = (
                self._format_filter_date(date_from)
            )

        if date_to is not None:
            body["date_to"] = (
                self._format_filter_date(date_to)
            )

        if incoming is not None:
            body["incoming"] = 1 if incoming else 0

        if receiver is not None:
            body["receiver"] = normalize_phone_number(
                receiver
            )

        if sender is not None:
            body["sender"] = normalize_phone_number(
                sender
            )

        if limit is not None:
            body["limit"] = limit

        items = await self._paginate(
            f"{self._api_base_url}/sms",
            body,
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

        try:
            payload = await call_with_retries(
                partial(
                    self._fetch_dialog, normalized_phone
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
                "Plusofon dialog request failed."
            ) from exc

        messages = sorted(
            (
                self._dialog_item_to_message(item)
                for item in payload.data
            ),
            key=lambda message: message.sent_at,
        )

        if limit <= 0:
            return []

        return messages[-limit:]

    async def _fetch_dialog(
        self,
        phone: str,
    ) -> _SMSDialogResponse:
        response = await self._http.get(
            f"{self._api_base_url}/sms/dialog/{phone}",
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
            payload = _SMSDialogResponse.model_validate(
                response.json()
            )
        except (ValueError, ValidationError) as exc:
            raise PlusofonError(
                "Invalid Plusofon dialog response."
            ) from exc

        if not payload.success:
            raise PlusofonError(
                "Plusofon dialog request was rejected."
            )

        return payload

    def _format_filter_date(
        self,
        value: datetime,
    ) -> str:
        """
        The confirmed Plusofon v1 docs (help.plusofon.ru/api/v1/sms)
        type date_from/date_to as "date", not "datetime" -- so only
        the calendar date is sent, as "YYYY-MM-DD". The caller still
        passes a timezone-aware datetime (not a bare date) so *which*
        calendar day it resolves to is unambiguous across timezones;
        it's converted to this client's own timezone before the time
        portion is discarded.
        """
        if value.tzinfo is None:
            raise ValueError(
                "Plusofon date filters must be "
                "timezone-aware datetimes."
            )

        localized = value.astimezone(
            self._default_timezone
        )

        return localized.strftime("%Y-%m-%d")

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
                or item.created_datetime
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
                or item.created_datetime
            ),
            sender=item.sender,
            receiver=item.receiver,
            text=item.msg,
            incoming=(
                item.incoming
                if item.incoming is not None
                else (
                    normalize_phone_number(
                        item.receiver
                    )
                    == self._own_number
                )
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
        json_body: dict[str, object],
        page_model: type[_Page[_ItemT]],
    ) -> list[_ItemT]:
        items: list[_ItemT] = []
        next_url: str | None = url
        seen_urls: set[str] = set()

        for _ in range(_MAX_HISTORY_PAGES):
            if next_url is None:
                return items

            if next_url in seen_urls:
                raise PlusofonError(
                    "Plusofon pagination loop detected."
                )

            seen_urls.add(next_url)

            try:
                page = await call_with_retries(
                    partial(
                        self._fetch_page,
                        next_url,
                        json_body,
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

            if page.next_page_url is None:
                return items

            # next_page_url (e.g. "...?page=2") is only a
            # continuation cursor -- it carries no date/sender/
            # receiver/etc. filters of its own, so the exact
            # same JSON filter body is resent on every page,
            # not just the first.
            next_url = self._validated_pagination_url(
                page.next_page_url,
                current_url=next_url,
            )

        raise PlusofonError(
            "Plusofon pagination limit exceeded."
        )

    async def _fetch_page(
        self,
        url: str,
        json_body: dict[str, object] | None,
        page_model: type[_Page[_ItemT]],
    ) -> _Page[_ItemT]:
        # Plusofon's confirmed GET /api/v1/sms contract takes
        # its filters as a JSON request body, not query
        # params -- hence the explicit .request("GET", ...)
        # rather than .get(...), which can't send a body.
        response = await self._http.request(
            "GET",
            url,
            json=json_body,
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
            page = page_model.model_validate(
                response.json()
            )
        except (ValueError, ValidationError) as exc:
            raise PlusofonError(
                "Invalid Plusofon history response."
            ) from exc

        if not page.success:
            raise PlusofonError(
                "Plusofon history request was rejected."
            )

        return page

    @staticmethod
    def _parse_origin(
        url: str,
    ) -> tuple[str, str, int]:
        parsed = urlsplit(url)

        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "PLUSOFON_API_BASE_URL must be an "
                "absolute HTTP(S) URL without credentials."
            )

        port = parsed.port

        if port is None:
            port = 443 if parsed.scheme == "https" else 80

        return (
            parsed.scheme,
            parsed.hostname.lower(),
            port,
        )

    def _validated_pagination_url(
        self,
        url: str,
        *,
        current_url: str,
    ) -> str:
        candidate = urljoin(current_url, url)
        parsed = urlsplit(candidate)

        try:
            origin = self._parse_origin(candidate)
        except ValueError as exc:
            raise PlusofonError(
                "Invalid Plusofon pagination URL."
            ) from exc

        if (
            origin != self._api_origin
            or not parsed.path.startswith(
                self._api_path_prefix
            )
            or parsed.fragment
        ):
            raise PlusofonError(
                "Unsafe Plusofon pagination URL."
            )

        return candidate

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
