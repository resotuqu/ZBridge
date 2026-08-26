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
from app.services.gigachat import (
    GigaChatError,
    GigaChatModel,
)
from app.services.plusofon import (
    PlusofonError,
    SendResult,
    SMSMessage,
)
from app.services.sms_formatter import format_sms_answer


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
MODELS_UNAVAILABLE_MESSAGE = "Модели сейчас недоступны :("
MODEL_NOT_FOUND_MESSAGE = "Модель недоступна."
LATIN_ON_MESSAGE = "Latin: ON"
LATIN_OFF_MESSAGE = "Latin: OFF"

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

LATIN_INSTRUCTION = (
    "Пиши русский текст транслитом латиницей.\n"
    "Не переводи ответ на английский, если "
    "пользователь не просил перевод."
)

_STAT_KEYWORDS = {"stat", "stats", "стат"}
_CLEAR_KEYWORDS = {"clear", "сброс"}
_CONTINUE_KEYWORDS = {"+"}
_MODELS_KEYWORDS = {"models"}

_SERVICE_COMMAND_KEYWORDS = (
    _STAT_KEYWORDS
    | _CLEAR_KEYWORDS
    | _CONTINUE_KEYWORDS
    | _MODELS_KEYWORDS
)

_SERVICE_NOTIFICATION_PREFIXES = (
    "Модель: ",
    "SMS: ",
)

_MODELS_LIST_MARKER = "Текущая: "


def is_stat_command(text: str) -> bool:
    return text.strip().lower() in _STAT_KEYWORDS


def is_clear_command(text: str) -> bool:
    return text.strip().lower() in _CLEAR_KEYWORDS


def is_continue_command(text: str) -> bool:
    return text.strip() in _CONTINUE_KEYWORDS


def is_models_command(text: str) -> bool:
    return text.strip().lower() in _MODELS_KEYWORDS


def parse_latin_command(text: str) -> bool | None:
    normalized = text.strip().lower()

    if normalized == "latin on":
        return True

    if normalized == "latin off":
        return False

    return None


def _generation_model_ids(
    models: list[GigaChatModel],
) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []

    for model in models:
        model_id = model.id

        if not model_id.startswith("GigaChat"):
            continue

        if model_id.startswith("Embeddings"):
            continue

        if model_id in seen:
            continue

        seen.add(model_id)
        ids.append(model_id)

    return ids


def _service_notification_texts() -> set[str]:
    return {
        AI_FAILURE_MESSAGE,
        AUTH_SUCCESS_MESSAGE,
        CLEAR_CONFIRMATION_MESSAGE,
        CONTINUATION_UNAVAILABLE_MESSAGE,
        STAT_UNAVAILABLE_MESSAGE,
        MODELS_UNAVAILABLE_MESSAGE,
        MODEL_NOT_FOUND_MESSAGE,
        LATIN_ON_MESSAGE,
        LATIN_OFF_MESSAGE,
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

    if _MODELS_LIST_MARKER in normalized:
        return True

    if parse_auth_command(normalized) is not None:
        return True

    if parse_model_command(normalized) is not None:
        return True

    if parse_latin_command(normalized) is not None:
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

    async def list_models(
        self,
    ) -> list[GigaChatModel]: ...


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
        default_latin_enabled: bool = False,
    ) -> None:
        self._chat_client = chat_client
        self._sms_provider = sms_provider
        self._runtime_state = runtime_state
        self._model = model
        self._max_context_messages = max_context_messages
        self._default_latin_enabled = (
            default_latin_enabled
        )

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
                content=self._build_system_prompt(
                    message.sender
                ),
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
                content=self._build_system_prompt(
                    phone
                ),
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

    def _build_system_prompt(self, phone: str) -> str:
        latin_enabled = (
            self._runtime_state.get_latin_mode(
                phone, self._default_latin_enabled
            )
        )

        if latin_enabled:
            return (
                f"{SMS_SYSTEM_PROMPT}\n\n"
                f"{LATIN_INSTRUCTION}"
            )

        return SMS_SYSTEM_PROMPT

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
                or entry.created_at > boundary
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


async def _send_segments(
    sms_provider: SMSProvider,
    phone: str,
    segments: list[str],
) -> SendResult:
    total_pdu = 0
    last_result: SendResult | None = None

    for segment in segments:
        last_result = await sms_provider.send(
            phone, segment
        )
        total_pdu += last_result.pdu_count

    assert last_result is not None

    return SendResult(
        message_id=last_result.message_id,
        pdu_count=total_pdu,
    )


class AnswerDelivery:
    """
    Formats a raw AI answer into SMS-ready segments
    (Markdown stripped, GSM-7/UCS-2 aware, split on word
    boundaries, "[!]"/"[i/N]" prefixes) and sends every
    segment in order via SMSProvider.
    """

    def __init__(
        self,
        sms_provider: SMSProvider,
        *,
        daily_warning_threshold: int,
        timezone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sms_provider = sms_provider
        self._daily_warning_threshold = (
            daily_warning_threshold
        )
        self._timezone = timezone
        self._clock = clock or (
            lambda: datetime.now(timezone)
        )

    async def send_answer(
        self,
        phone: str,
        text: str,
    ) -> SendResult:
        add_warning = await self._should_warn(phone)
        segments = format_sms_answer(
            text, add_warning=add_warning
        )

        return await _send_segments(
            self._sms_provider, phone, segments
        )

    async def _should_warn(self, phone: str) -> bool:
        if self._daily_warning_threshold <= 0:
            return True

        today = self._clock().date()

        try:
            messages = (
                await self._sms_provider.list_messages(
                    date_from=today,
                    date_to=today,
                    incoming=False,
                    receiver=phone,
                )
            )
        except PlusofonError as exc:
            logger.warning(
                "Daily usage lookup unavailable, "
                "skipping [!] warning",
                extra={
                    "event": "usage_unavailable",
                    "phone": phone,
                    "error_type": type(exc).__name__,
                },
            )
            return False

        sent_today = sum(
            entry.pdu or 0 for entry in messages
        )

        return sent_today >= self._daily_warning_threshold


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
        chat_client: ChatClient,
        runtime_state: RuntimeState,
    ) -> None:
        self._sms_provider = sms_provider
        self._chat_client = chat_client
        self._runtime_state = runtime_state

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
        model_name: str,
    ) -> None:
        try:
            models = (
                await self._chat_client.list_models()
            )
        except GigaChatError as exc:
            logger.error(
                "Models lookup failed",
                extra={
                    "event": "gigachat_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )
            await self._reply(
                message,
                request_id,
                MODELS_UNAVAILABLE_MESSAGE,
            )
            return

        generation_ids = _generation_model_ids(models)
        canonical = next(
            (
                model_id
                for model_id in generation_ids
                if model_id == model_name
            ),
            None,
        )

        if canonical is None:
            logger.warning(
                "Admin model not found",
                extra={
                    "event": "admin_model_not_found",
                    "request_id": request_id,
                    "phone": message.sender,
                },
            )
            await self._reply(
                message,
                request_id,
                MODEL_NOT_FOUND_MESSAGE,
            )
            return

        self._runtime_state.set_selected_model(
            message.sender, canonical
        )

        logger.info(
            "Model changed by admin command",
            extra={
                "event": "admin_model_changed",
                "request_id": request_id,
                "phone": message.sender,
                "model": canonical,
            },
        )

        await self._reply(
            message,
            request_id,
            MODEL_CHANGE_MESSAGE_TEMPLATE.format(
                model=canonical
            ),
        )

    async def _reply(
        self,
        message: IncomingSMS,
        request_id: str,
        text: str,
    ) -> None:
        try:
            await self._sms_provider.send(
                message.sender, text
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


class ModelsCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
        chat_client: ChatClient,
        runtime_state: RuntimeState,
        *,
        default_model: str,
    ) -> None:
        self._sms_provider = sms_provider
        self._chat_client = chat_client
        self._runtime_state = runtime_state
        self._default_model = default_model

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        try:
            models = (
                await self._chat_client.list_models()
            )
        except GigaChatError as exc:
            logger.error(
                "Models lookup failed",
                extra={
                    "event": "gigachat_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "error_type": type(exc).__name__,
                },
            )
            await self._send(
                message.sender,
                request_id,
                MODELS_UNAVAILABLE_MESSAGE,
            )
            return

        generation_ids = _generation_model_ids(models)

        if not generation_ids:
            await self._send(
                message.sender,
                request_id,
                MODELS_UNAVAILABLE_MESSAGE,
            )
            return

        current_model = (
            self._runtime_state.get_selected_model(
                message.sender
            )
            or self._default_model
        )

        text = "\n".join(
            [
                *generation_ids,
                f"{_MODELS_LIST_MARKER}{current_model}",
            ]
        )

        await self._send(message.sender, request_id, text)

    async def _send(
        self,
        phone: str,
        request_id: str,
        text: str,
    ) -> None:
        segments = format_sms_answer(
            text, add_warning=False
        )

        try:
            await _send_segments(
                self._sms_provider, phone, segments
            )
        except PlusofonError as exc:
            logger.error(
                "Models confirmation SMS failed",
                extra={
                    "event": "plusofon_error",
                    "request_id": request_id,
                    "phone": phone,
                    "error_type": type(exc).__name__,
                },
            )


class LatinCommandProcessor:
    def __init__(
        self,
        sms_provider: SMSProvider,
    ) -> None:
        self._sms_provider = sms_provider

    async def __call__(
        self,
        message: IncomingSMS,
        request_id: str,
        enabled: bool,
    ) -> None:
        text = (
            LATIN_ON_MESSAGE
            if enabled
            else LATIN_OFF_MESSAGE
        )

        try:
            await self._sms_provider.send(
                message.sender, text
            )
        except PlusofonError as exc:
            logger.error(
                "Latin confirmation SMS failed",
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
        answer_delivery: AnswerDelivery,
        runtime_state: RuntimeState,
    ) -> None:
        self._message_router = message_router
        self._answer_delivery = answer_delivery
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
                result = (
                    await self
                    ._answer_delivery
                    .send_answer(
                        message.sender,
                        answer,
                    )
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
        answer_delivery: AnswerDelivery,
        runtime_state: RuntimeState,
    ) -> None:
        self._message_router = message_router
        self._answer_delivery = answer_delivery
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
                result = (
                    await self
                    ._answer_delivery
                    .send_answer(
                        message.sender,
                        answer,
                    )
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