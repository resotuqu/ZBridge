from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from time import monotonic
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.runtime_state import RuntimeState
from app.core.security import (
    parse_auth_command,
    parse_model_command,
)
from app.schemas.messages import (
    ChatMessage,
    ChatRole,
    IncomingSMS,
)
from app.services.gigachat import GigaChatError
from app.services.plusofon import (
    PlusofonError,
    SendResult,
    SMSMessage,
)


logger = logging.getLogger(__name__)

AI_FAILURE_MESSAGE = "ИИ поломался :("
AUTH_SUCCESS_MESSAGE = "Доступ разрешён до перезапуска."
MODEL_CHANGE_MESSAGE_TEMPLATE = "Модель: {model}"
CLEAR_CONFIRMATION_MESSAGE = "Новый диалог."
CONTINUATION_UNAVAILABLE_MESSAGE = "Продолжения нет."
CONTINUATION_INSTRUCTION = (
    "Продолжи предыдущий ответ. "
    "Не повторяй уже отправленный текст."
)
STAT_UNAVAILABLE_MESSAGE = "Статистика недоступна :("

SMS_SYSTEM_PROMPT = """Ты отвечаешь пользователю через обычные SMS.

Правила:
- отвечай прямо;
- не повторяй вопрос;
- не используй Markdown и таблицы;
- пиши информационно плотно;
- по умолчанию давай короткий ответ;
- учитывай, что пользователь может запросить продолжение символом "+";
- не выдумывай текущие новости, погоду, курсы, цены и расписания;
- для актуальных данных используй только данные, переданные сервером."""

_STAT_KEYWORDS = {"stat", "stats", "стат"}
_CLEAR_KEYWORDS = {"clear", "сброс"}
_CONTINUE_KEYWORDS = {"+"}

_SERVICE_COMMAND_KEYWORDS = (
    _STAT_KEYWORDS
    | _CLEAR_KEYWORDS
    | _CONTINUE_KEYWORDS
    | {"models"}
)

_SERVICE_NOTIFICATION_PREFIXES = (
    "Модель: ",
    "SMS: ",
)


def is_stat_command(text: str) -> bool:
    return text.strip().lower() in _STAT_KEYWORDS


def is_clear_command(text: str) -> bool:
    return text.strip().lower() in _CLEAR_KEYWORDS


def is_continue_command(text: str) -> bool:
    return text.strip() in _CONTINUE_KEYWORDS


def _service_notification_texts() -> set[str]:
    return {
        AI_FAILURE_MESSAGE,
        AUTH_SUCCESS_MESSAGE,
        CLEAR_CONFIRMATION_MESSAGE,
        CONTINUATION_UNAVAILABLE_MESSAGE,
        STAT_UNAVAILABLE_MESSAGE,
    }


def _is_service_message(text: str) -> bool:
    normalized = text.strip()

    if normalized.lower() in _SERVICE_COMMAND_KEYWORDS:
        return True

    if normalized in _service_notification_texts():
        return True

    if normalized.startswith(
        _SERVICE_NOTIFICATION_PREFIXES
    ):
        return True

    if parse_auth_command(normalized) is not None:
        return True

    if parse_model_command(normalized) is not None:
        return True

    return False


def _find_last_exchange(
    conversation: list[SMSMessage],
) -> tuple[str, str] | None:
    last_user: SMSMessage | None = None
    last_assistant: SMSMessage | None = None

    for entry in conversation:
        if entry.incoming:
            last_user = entry
        else:
            last_assistant = entry

    if last_user is None or last_assistant is None:
        return None

    if last_assistant.sent_at <= last_user.sent_at:
        return None

    return (
        last_user.text.strip(),
        last_assistant.text.strip(),
    )


class ChatClient(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
    ) -> str: ...


class SMSProvider(Protocol):
    async def send(
        self,
        to: str,
        text: str,
    ) -> SendResult: ...

    async def get_dialog(
        self,
        phone: str,
        limit: int,
    ) -> list[SMSMessage]: ...

    async def list_messages(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        incoming: bool | None = None,
        receiver: str | None = None,
        sender: str | None = None,
        limit: int | None = None,
    ) -> list[SMSMessage]: ...


class MessageRouter:
    def __init__(
        self,
        chat_client: ChatClient,
        sms_provider: SMSProvider,
        runtime_state: RuntimeState,
        *,
        model: str,
        max_context_messages: int,
    ) -> None:
        self._chat_client = chat_client
        self._sms_provider = sms_provider
        self._runtime_state = runtime_state
        self._model = model
        self._max_context_messages = max_context_messages

    async def answer(
        self,
        message: IncomingSMS,
        request_id: str,
        *,
        model: str | None = None,
    ) -> str:
        effective_model = model or self._model

        context = await self._fetch_conversation(
            message.sender,
            request_id,
            exclude_text=message.content.strip(),
        )

        messages = [
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=SMS_SYSTEM_PROMPT,
            ),
            *self._to_chat_messages(context),
            ChatMessage(
                role=ChatRole.USER,
                content=message.content.strip(),
            ),
        ]

        return await self._call_chat(
            messages,
            effective_model,
            request_id,
            message.sender,
        )

    async def continue_answer(
        self,
        phone: str,
        request_id: str,
        *,
        model: str | None = None,
    ) -> str:
        effective_model = model or self._model

        context = await self._fetch_conversation(
            phone,
            request_id,
        )

        exchange = _find_last_exchange(context)

        if exchange is None:
            return CONTINUATION_UNAVAILABLE_MESSAGE

        last_question, last_answer = exchange

        messages = [
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=SMS_SYSTEM_PROMPT,
            ),
            ChatMessage(
                role=ChatRole.USER,
                content=last_question,
            ),
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content=last_answer,
            ),
            ChatMessage(
                role=ChatRole.USER,
                content=CONTINUATION_INSTRUCTION,
            ),
        ]

        return await self._call_chat(
            messages,
            effective_model,
            request_id,
            phone,
        )

    async def _call_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        request_id: str,
        phone: str,
    ) -> str:
        self._runtime_state.record_gigachat_request(
            model
        )

        logger.info(
            "GigaChat request started",
            extra={
                "event": "gigachat_request",
                "request_id": request_id,
                "phone": phone,
                "model": model,
            },
        )

        try:
            answer = await self._chat_client.chat(
                messages,
                model=model,
            )
        except GigaChatError as exc:
            logger.error(
                "GigaChat request failed",
                extra={
                    "event": "gigachat_error",
                    "request_id": request_id,
                    "phone": phone,
                    "model": model,
                    "error_type": type(exc).__name__,
                },
            )
            return AI_FAILURE_MESSAGE

        logger.info(
            "GigaChat request completed",
            extra={
                "event": "gigachat_response",
                "request_id": request_id,
                "phone": phone,
                "model": model,
            },
        )

        return answer

    async def _fetch_conversation(
        self,
        phone: str,
        request_id: str,
        *,
        exclude_text: str | None = None,
    ) -> list[SMSMessage]:
        if self._max_context_messages <= 0:
            return []

        try:
            dialog = await self._sms_provider.get_dialog(
                phone,
                self._max_context_messages,
            )
        except PlusofonError as exc:
            logger.warning(
                "Dialog history unavailable, "
                "continuing without context",
                extra={
                    "event": "context_unavailable",
                    "request_id": request_id,
                    "phone": phone,
                    "error_type": type(exc).__name__,
                },
            )
            return []

        boundary = (
            self._runtime_state.get_context_boundary(
                phone
            )
        )

        filtered = [
            entry
            for entry in dialog
            if entry.text.strip()
            and (
                boundary is None
                or entry.sent_at > boundary
            )
            and not _is_service_message(
                entry.text.strip()
            )
        ]

        filtered.sort(key=lambda entry: entry.sent_at)

        if (
            exclude_text is not None
            and filtered
            and filtered[-1].incoming
            and filtered[-1].text.strip()
            == exclude_text
        ):
            filtered = filtered[:-1]

        return filtered[-self._max_context_messages:]

    @staticmethod
    def _to_chat_messages(
        conversation: list[SMSMessage],
    ) -> list[ChatMessage]:
        return [
            ChatMessage(
                role=(
                    ChatRole.USER
                    if entry.incoming
                    else ChatRole.ASSISTANT
                ),
                content=entry.text.strip(),
            )
            for entry in conversation
        ]


class AuthCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
    ) -> None:
        self._sms_provider = sms_provider

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        try:
            await self._sms_provider.send(
                message.sender,
                AUTH_SUCCESS_MESSAGE,
            )
        except PlusofonError as exc:
            logger.error(
                "Auth confirmation SMS failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )


class AdminCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
    ) -> None:
        self._sms_provider = sms_provider

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
        model: str,
    ) -> None:
        try:
            await self._sms_provider.send(
                message.sender,
                MODEL_CHANGE_MESSAGE_TEMPLATE.format(
                    model=model
                ),
            )
        except PlusofonError as exc:
            logger.error(
                "Admin confirmation SMS failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )


class ClearCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
    ) -> None:
        self._sms_provider = sms_provider

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        try:
            await self._sms_provider.send(
                message.sender,
                CLEAR_CONFIRMATION_MESSAGE,
            )
        except PlusofonError as exc:
            logger.error(
                "Clear confirmation SMS failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )


class StatCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
        runtime_state: RuntimeState,
        *,
        default_model: str,
        sms_price_rub: Decimal,
        timezone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sms_provider = sms_provider
        self._runtime_state = runtime_state
        self._default_model = default_model
        self._sms_price_rub = sms_price_rub
        self._timezone = timezone
        self._clock = clock or (
            lambda: datetime.now(timezone)
        )

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        try:
            text = await self._build_stat_text(
                message.sender
            )
        except PlusofonError as exc:
            logger.error(
                "Stat lookup failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )
            text = STAT_UNAVAILABLE_MESSAGE

        try:
            await self._sms_provider.send(
                message.sender,
                text,
            )
        except PlusofonError as exc:
            logger.error(
                "Stat confirmation SMS failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )

    async def _build_stat_text(
        self,
        phone: str,
    ) -> str:
        now = self._clock()
        today = now.date()
        month_start = today.replace(day=1)

        month_messages = (
            await self._sms_provider.list_messages(
                date_from=month_start,
                date_to=today,
                incoming=False,
                receiver=phone,
            )
        )

        month_pdu = sum(
            entry.pdu or 0 for entry in month_messages
        )

        today_pdu = sum(
            entry.pdu or 0
            for entry in month_messages
            if entry.sent_at.astimezone(
                self._timezone
            ).date()
            == today
        )

        cost_rub = (
            Decimal(month_pdu) * self._sms_price_rub
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )

        model = (
            self._runtime_state.get_selected_model(
                phone
            )
            or self._default_model
        )

        ai_total = (
            self._runtime_state.gigachat_requests_total
        )

        return (
            f"SMS: {today_pdu} сегодня / "
            f"{month_pdu} месяц. "
            f"AI: {ai_total} с запуска. "
            f"Модель: {model}. "
            f"~{cost_rub} ₽"
        )


class ContinueCommandProcessor:
    def __init__(
        self,
        message_router: MessageRouter,
        sms_provider: SMSProvider,
        runtime_state: RuntimeState,
    ) -> None:
        self._message_router = message_router
        self._sms_provider = sms_provider
        self._runtime_state = runtime_state

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        started_at = monotonic()
        phone_lock = self._runtime_state.get_phone_lock(
            message.sender
        )

        effective_model = (
            self._runtime_state.get_selected_model(
                message.sender
            )
        )

        async with phone_lock:
            answer = (
                await self
                ._message_router
                .continue_answer(
                    message.sender,
                    request_id,
                    model=effective_model,
                )
            )

            try:
                result = await self._sms_provider.send(
                    message.sender,
                    answer,
                )
            except PlusofonError as exc:
                logger.error(
                    "Outgoing SMS failed",
                    extra={
                        "event": "plusofon_error",
                        "request_id": request_id,
                        "phone": message.sender,
                        "error_type": type(exc).__name__,
                    },
                )
                return

        duration_ms = round(
            (monotonic() - started_at) * 1000
        )

        logger.info(
            "Continuation processed",
            extra={
                "event": "sms_processed",
                "request_id": request_id,
                "phone": message.sender,
                "route": "continue",
                "segments_sent": result.pdu_count,
                "duration_ms": duration_ms,
            },
        )


class IncomingSMSProcessor:
    def __init__(
        self,
        message_router: MessageRouter,
        sms_provider: SMSProvider,
        runtime_state: RuntimeState,
    ) -> None:
        self._message_router = message_router
        self._sms_provider = sms_provider
        self._runtime_state = runtime_state

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        started_at = monotonic()
        phone_lock = self._runtime_state.get_phone_lock(
            message.sender
        )

        effective_model = (
            self._runtime_state.get_selected_model(
                message.sender
            )
        )

        async with phone_lock:
            answer = await self._message_router.answer(
                message,
                request_id,
                model=effective_model,
            )

            try:
                result = await self._sms_provider.send(
                    message.sender,
                    answer,
                )
            except PlusofonError as exc:
                logger.error(
                    "Outgoing SMS failed",
                    extra={
                        "event": "plusofon_error",
                        "request_id": request_id,
                        "phone": message.sender,
                        "error_type": type(exc).__name__,
                    },
                )
                return

        duration_ms = round(
            (monotonic() - started_at) * 1000
        )

        logger.info(
            "Incoming SMS processed",
            extra={
                "event": "sms_processed",
                "request_id": request_id,
                "phone": message.sender,
                "route": "ai",
                "segments_sent": result.pdu_count,
                "duration_ms": duration_ms,
            },
        )