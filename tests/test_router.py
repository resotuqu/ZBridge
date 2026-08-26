from datetime import UTC, datetime

import pytest

from app.schemas.messages import ChatMessage, ChatRole, IncomingSMS
from app.services.gigachat import GigaChatTransientError
from app.services.message_router import (
    AI_FAILURE_MESSAGE,
    AUTH_SUCCESS_MESSAGE,
    SMS_SYSTEM_PROMPT,
    AuthCommandProcessor,
    MessageRouter,
)
from app.services.plusofon import PlusofonError, SendResult


class FakeChatClient:
    def __init__(
        self,
        *,
        result: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.messages: list[ChatMessage] | None = None
        self.model: str | None = None

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
    ) -> str:
        self.messages = messages
        self.model = model

        if self.error is not None:
            raise self.error

        assert self.result is not None
        return self.result


def incoming_sms() -> IncomingSMS:
    return IncomingSMS(
        sender="71111111111",
        recipient="70000000000",
        content="  Что такое VLAN?  ",
        received_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_ordinary_message_is_sent_to_gigachat() -> None:
    client = FakeChatClient(
        result="Виртуальная локальная сеть."
    )
    router = MessageRouter(
        client,
        model="GigaChat-3-Ultra",
    )

    result = await router.answer(
        incoming_sms(),
        "request-1",
    )

    assert result == "Виртуальная локальная сеть."
    assert client.model == "GigaChat-3-Ultra"
    assert client.messages == [
        ChatMessage(
            role=ChatRole.SYSTEM,
            content=SMS_SYSTEM_PROMPT,
        ),
        ChatMessage(
            role=ChatRole.USER,
            content="Что такое VLAN?",
        ),
    ]


@pytest.mark.asyncio
async def test_gigachat_failure_returns_exact_fallback() -> None:
    client = FakeChatClient(
        error=GigaChatTransientError("temporary failure")
    )
    router = MessageRouter(
        client,
        model="GigaChat-3-Ultra",
    )

    result = await router.answer(
        incoming_sms(),
        "request-2",
    )

    assert result == AI_FAILURE_MESSAGE
    assert result == "ИИ поломался :("


class FakeSMSProvider:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self.error = error
        self.sent: list[tuple[str, str]] = []

    async def send(
        self,
        to: str,
        text: str,
    ) -> SendResult:
        if self.error is not None:
            raise self.error

        self.sent.append((to, text))
        return SendResult(
            message_id="sms-1",
            pdu_count=1,
        )


@pytest.mark.asyncio
async def test_auth_command_processor_sends_confirmation() -> None:
    provider = FakeSMSProvider()
    processor = AuthCommandProcessor(provider)

    await processor(
        incoming_sms(),
        "request-3",
    )

    assert provider.sent == [
        ("71111111111", AUTH_SUCCESS_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_auth_command_processor_swallows_send_failure() -> None:
    provider = FakeSMSProvider(
        error=PlusofonError("send failed")
    )
    processor = AuthCommandProcessor(provider)

    await processor(
        incoming_sms(),
        "request-4",
    )