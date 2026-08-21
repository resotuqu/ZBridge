from __future__ import annotations

import logging
from time import monotonic
from typing import Protocol

from app.core.runtime_state import RuntimeState
from app.schemas.messages import (
    ChatMessage,
    ChatRole,
    IncomingSMS,
)
from app.services.gigachat import GigaChatError
from app.services.plusofon import (
    PlusofonError,
    SendResult,
)


logger = logging.getLogger(__name__)

AI_FAILURE_MESSAGE = "ИИ поломался :("

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


class MessageRouter:
    def __init__(
        self,
        chat_client: ChatClient,
        *,
        model: str,
    ) -> None:
        self._chat_client = chat_client
        self._model = model

    async def answer(
        self,
        message: IncomingSMS,
        request_id: str,
    ) -> str:
        messages = [
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=SMS_SYSTEM_PROMPT,
            ),
            ChatMessage(
                role=ChatRole.USER,
                content=message.content.strip(),
            ),
        ]

        logger.info(
            "GigaChat request started",
            extra={
                "event": "gigachat_request",
                "request_id": request_id,
                "phone": message.sender,
                "model": self._model,
            },
        )

        try:
            answer = await self._chat_client.chat(
                messages,
                model=self._model,
            )
        except GigaChatError as exc:
            logger.error(
                "GigaChat request failed",
                extra={
                    "event": "gigachat_error",
                    "request_id": request_id,
                    "phone": message.sender,
                    "model": self._model,
                    "error_type": type(exc).__name__,
                },
            )
            return AI_FAILURE_MESSAGE

        logger.info(
            "GigaChat request completed",
            extra={
                "event": "gigachat_response",
                "request_id": request_id,
                "phone": message.sender,
                "model": self._model,
            },
        )

        return answer


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

        async with phone_lock:
            answer = await self._message_router.answer(
                message,
                request_id,
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