from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.runtime_state import RuntimeState
from app.schemas.messages import ChatMessage, ChatRole, IncomingSMS
from app.services.gigachat import (
    GigaChatError,
    GigaChatModel,
    GigaChatTransientError,
)
from app.services.message_router import (
    AI_FAILURE_MESSAGE,
    AUTH_SUCCESS_MESSAGE,
    CLEAR_CONFIRMATION_MESSAGE,
    CONTINUATION_INSTRUCTION,
    CONTINUATION_UNAVAILABLE_MESSAGE,
    LATIN_INSTRUCTION,
    LATIN_OFF_MESSAGE,
    LATIN_ON_MESSAGE,
    MODEL_CHANGE_MESSAGE_TEMPLATE,
    MODEL_NOT_FOUND_MESSAGE,
    MODELS_UNAVAILABLE_MESSAGE,
    SMS_SYSTEM_PROMPT,
    STAT_UNAVAILABLE_MESSAGE,
    AdminCommandProcessor,
    AnswerDelivery,
    AuthCommandProcessor,
    ClearCommandProcessor,
    ContinueCommandProcessor,
    IncomingSMSProcessor,
    LatinCommandProcessor,
    MessageRouter,
    ModelsCommandProcessor,
    StatCommandProcessor,
    is_clear_command,
    is_continue_command,
    is_models_command,
    is_stat_command,
    parse_latin_command,
)
from app.services.plusofon import PlusofonError, SendResult, SMSMessage


TIMEZONE = ZoneInfo("Asia/Yakutsk")


class FakeChatClient:
    def __init__(
        self,
        *,
        result: str | None = None,
        error: Exception | None = None,
        models: list[GigaChatModel] | None = None,
        models_error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.messages: list[ChatMessage] | None = None
        self.model: str | None = None
        self.call_count = 0
        self.models = models or []
        self.models_error = models_error
        self.list_models_call_count = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
    ) -> str:
        self.call_count += 1
        self.messages = messages
        self.model = model

        if self.error is not None:
            raise self.error

        assert self.result is not None
        return self.result

    async def list_models(
        self,
    ) -> list[GigaChatModel]:
        self.list_models_call_count += 1

        if self.models_error is not None:
            raise self.models_error

        return self.models


class FakeSMSProvider:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        dialog: list[SMSMessage] | None = None,
        dialog_error: Exception | None = None,
        history: list[SMSMessage] | None = None,
        history_error: Exception | None = None,
    ) -> None:
        self.error = error
        self.sent: list[tuple[str, str]] = []
        self.dialog = dialog or []
        self.dialog_error = dialog_error
        self.history = history or []
        self.history_error = history_error
        self.list_messages_calls: list[dict] = []

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

    async def get_dialog(
        self,
        phone: str,
        limit: int,
    ) -> list[SMSMessage]:
        if self.dialog_error is not None:
            raise self.dialog_error

        if limit <= 0:
            return []

        return self.dialog[-limit:]

    async def list_messages(
        self,
        **kwargs: object,
    ) -> list[SMSMessage]:
        self.list_messages_calls.append(kwargs)

        if self.history_error is not None:
            raise self.history_error

        return self.history


def incoming_sms(content: str = "  Что такое VLAN?  ") -> IncomingSMS:
    return IncomingSMS(
        sender="71111111111",
        recipient="70000000000",
        content=content,
        received_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def make_message(
    *,
    text: str,
    incoming: bool,
    sent_at: datetime,
    created_at: datetime | None = None,
    pdu: int | None = 1,
) -> SMSMessage:
    return SMSMessage(
        created_at=created_at or sent_at,
        sent_at=sent_at,
        sender=(
            "71111111111" if incoming else "70000000000"
        ),
        receiver=(
            "70000000000" if incoming else "71111111111"
        ),
        text=text,
        incoming=incoming,
        pdu=pdu,
    )


def gigachat_model(model_id: str) -> GigaChatModel:
    return GigaChatModel(
        id=model_id,
        object="model",
        owned_by="provider",
    )


def build_router(
    chat_client: FakeChatClient,
    sms_provider: FakeSMSProvider | None = None,
    runtime_state: RuntimeState | None = None,
    *,
    model: str = "GigaChat-3-Ultra",
    max_context_messages: int = 8,
) -> MessageRouter:
    return MessageRouter(
        chat_client,
        sms_provider or FakeSMSProvider(),
        runtime_state or RuntimeState(),
        model=model,
        max_context_messages=max_context_messages,
    )


def build_answer_delivery(
    sms_provider: FakeSMSProvider | None = None,
    *,
    daily_warning_threshold: int = 1000,
) -> AnswerDelivery:
    return AnswerDelivery(
        sms_provider or FakeSMSProvider(),
        daily_warning_threshold=daily_warning_threshold,
        timezone=TIMEZONE,
        clock=lambda: datetime(
            2026, 8, 21, 12, 0, tzinfo=TIMEZONE
        ),
    )


@pytest.mark.asyncio
async def test_ordinary_message_is_sent_to_gigachat() -> None:
    client = FakeChatClient(
        result="Виртуальная локальная сеть."
    )
    router = build_router(client)

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
    router = build_router(client)

    result = await router.answer(
        incoming_sms(),
        "request-2",
    )

    assert result == AI_FAILURE_MESSAGE
    assert result == "ИИ поломался :("


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


@pytest.mark.asyncio
async def test_router_uses_model_override_when_given() -> None:
    client = FakeChatClient(result="ответ")
    router = build_router(client)

    await router.answer(
        incoming_sms(),
        "request-5",
        model="GigaChat-2-Pro",
    )

    assert client.model == "GigaChat-2-Pro"


@pytest.mark.asyncio
async def test_admin_command_processor_switches_to_valid_model() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models=[
            gigachat_model("GigaChat-2"),
            gigachat_model("GigaChat-2-Pro"),
        ]
    )
    runtime_state = RuntimeState()
    processor = AdminCommandProcessor(
        provider, chat_client, runtime_state
    )

    await processor(
        incoming_sms(),
        "request-6",
        "GigaChat-2-Pro",
    )

    assert provider.sent == [
        ("71111111111", "Модель: GigaChat-2-Pro")
    ]
    assert (
        runtime_state.get_selected_model("71111111111")
        == "GigaChat-2-Pro"
    )


@pytest.mark.asyncio
async def test_admin_command_processor_swallows_send_failure() -> None:
    provider = FakeSMSProvider(
        error=PlusofonError("send failed")
    )
    chat_client = FakeChatClient(
        models=[gigachat_model("GigaChat-2-Pro")]
    )
    runtime_state = RuntimeState()
    processor = AdminCommandProcessor(
        provider, chat_client, runtime_state
    )

    await processor(
        incoming_sms(),
        "request-7",
        "GigaChat-2-Pro",
    )


@pytest.mark.asyncio
async def test_admin_command_processor_rejects_unknown_model_without_state_change() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models=[gigachat_model("GigaChat-2")]
    )
    runtime_state = RuntimeState()

    processor = AdminCommandProcessor(
        provider, chat_client, runtime_state
    )

    await processor(
        incoming_sms(),
        "request-6b",
        "GigaChat-Nonexistent",
    )

    assert provider.sent == [
        ("71111111111", MODEL_NOT_FOUND_MESSAGE)
    ]
    assert (
        runtime_state.get_selected_model("71111111111")
        is None
    )


@pytest.mark.asyncio
async def test_admin_command_processor_falls_back_when_models_api_unavailable() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models_error=GigaChatError("models down")
    )
    runtime_state = RuntimeState()
    runtime_state.set_selected_model(
        "71111111111", "GigaChat-2"
    )

    processor = AdminCommandProcessor(
        provider, chat_client, runtime_state
    )

    await processor(
        incoming_sms(),
        "request-6c",
        "GigaChat-2-Pro",
    )

    assert provider.sent == [
        ("71111111111", MODELS_UNAVAILABLE_MESSAGE)
    ]
    assert (
        runtime_state.get_selected_model("71111111111")
        == "GigaChat-2"
    )


@pytest.mark.asyncio
async def test_incoming_sms_processor_uses_selected_model() -> None:
    client = FakeChatClient(result="ответ")
    runtime_state = RuntimeState()
    runtime_state.set_selected_model(
        "71111111111",
        "GigaChat-2-Pro",
    )
    router = build_router(
        client, runtime_state=runtime_state
    )

    processor = IncomingSMSProcessor(
        router,
        build_answer_delivery(),
        runtime_state,
    )

    await processor(
        incoming_sms(),
        "request-8",
    )

    assert client.model == "GigaChat-2-Pro"


@pytest.mark.asyncio
async def test_incoming_sms_processor_uses_default_model_without_selection() -> None:
    client = FakeChatClient(result="ответ")
    runtime_state = RuntimeState()
    router = build_router(
        client, runtime_state=runtime_state
    )

    processor = IncomingSMSProcessor(
        router,
        build_answer_delivery(),
        runtime_state,
    )

    await processor(
        incoming_sms(),
        "request-9",
    )

    assert client.model == "GigaChat-3-Ultra"


@pytest.mark.asyncio
async def test_answer_includes_filtered_dialog_context() -> None:
    dialog = [
        make_message(
            text="А что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="stat",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT преобразует IP-адреса.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 2, tzinfo=UTC),
        ),
        make_message(
            text="Что такое VLAN?",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-10")

    assert client.messages == [
        ChatMessage(
            role=ChatRole.SYSTEM,
            content=SMS_SYSTEM_PROMPT,
        ),
        ChatMessage(
            role=ChatRole.USER,
            content="А что такое NAT?",
        ),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="NAT преобразует IP-адреса.",
        ),
        ChatMessage(
            role=ChatRole.USER,
            content="Что такое VLAN?",
        ),
    ]


@pytest.mark.asyncio
async def test_answer_falls_back_when_dialog_unavailable() -> None:
    sms_provider = FakeSMSProvider(
        dialog_error=PlusofonError("history down")
    )
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    result = await router.answer(
        incoming_sms(), "request-11"
    )

    assert result == "ответ"
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
async def test_answer_respects_clear_boundary() -> None:
    boundary = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    dialog = [
        make_message(
            text="Старый вопрос",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="Старый ответ",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    runtime_state = RuntimeState()
    runtime_state.set_context_boundary(
        "71111111111", boundary
    )
    client = FakeChatClient(result="ответ")
    router = build_router(
        client, sms_provider, runtime_state
    )

    await router.answer(incoming_sms(), "request-12")

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
async def test_continue_answer_uses_last_exchange() -> None:
    dialog = [
        make_message(
            text="Что такое VLAN?",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="VLAN — виртуальная сеть.",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 0, 5, tzinfo=UTC),
        ),
        make_message(
            text="+",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 1, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="Продолжение.")
    router = build_router(client, sms_provider)

    result = await router.continue_answer(
        "71111111111", "request-13"
    )

    assert result == "Продолжение."
    assert client.messages == [
        ChatMessage(
            role=ChatRole.SYSTEM,
            content=SMS_SYSTEM_PROMPT,
        ),
        ChatMessage(
            role=ChatRole.USER,
            content="Что такое VLAN?",
        ),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="VLAN — виртуальная сеть.",
        ),
        ChatMessage(
            role=ChatRole.USER,
            content=CONTINUATION_INSTRUCTION,
        ),
    ]


@pytest.mark.asyncio
async def test_continue_answer_without_exchange_is_unavailable() -> None:
    sms_provider = FakeSMSProvider(dialog=[])
    client = FakeChatClient(result="unused")
    router = build_router(client, sms_provider)

    result = await router.continue_answer(
        "71111111111", "request-14"
    )

    assert result == CONTINUATION_UNAVAILABLE_MESSAGE
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_continue_answer_when_unanswered_is_unavailable() -> None:
    dialog = [
        make_message(
            text="Старый вопрос",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="Старый ответ",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Новый вопрос без ответа",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 1, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="unused")
    router = build_router(client, sms_provider)

    result = await router.continue_answer(
        "71111111111", "request-15"
    )

    assert result == CONTINUATION_UNAVAILABLE_MESSAGE
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_clear_command_processor_sends_confirmation() -> None:
    provider = FakeSMSProvider()
    processor = ClearCommandProcessor(provider)

    await processor(incoming_sms(), "request-16")

    assert provider.sent == [
        ("71111111111", CLEAR_CONFIRMATION_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_stat_command_processor_builds_expected_text() -> None:
    today = datetime(2026, 8, 21, 15, 0, tzinfo=TIMEZONE)
    history = [
        make_message(
            text="ответ 1",
            incoming=False,
            sent_at=today,
            pdu=2,
        ),
        make_message(
            text="ответ 2",
            incoming=False,
            sent_at=datetime(
                2026, 8, 5, 12, 0, tzinfo=TIMEZONE
            ),
            pdu=3,
        ),
    ]
    provider = FakeSMSProvider(history=history)
    runtime_state = RuntimeState()
    runtime_state.record_gigachat_request(
        "GigaChat-3-Ultra"
    )
    processor = StatCommandProcessor(
        provider,
        runtime_state,
        default_model="GigaChat-3-Ultra",
        sms_price_rub=Decimal("2.00"),
        timezone=TIMEZONE,
        clock=lambda: today,
    )

    await processor(
        incoming_sms("stat"), "request-17"
    )

    assert len(provider.sent) == 1
    to, text = provider.sent[0]
    assert to == "71111111111"
    assert text == (
        "SMS: 2 сегодня / 5 месяц. "
        "AI: 1 с запуска. "
        "Модель: GigaChat-3-Ultra. "
        "~10 ₽"
    )


@pytest.mark.asyncio
async def test_stat_command_processor_falls_back_on_history_error() -> None:
    provider = FakeSMSProvider(
        history_error=PlusofonError("history down")
    )
    runtime_state = RuntimeState()
    processor = StatCommandProcessor(
        provider,
        runtime_state,
        default_model="GigaChat-3-Ultra",
        sms_price_rub=Decimal("2.00"),
        timezone=TIMEZONE,
    )

    await processor(incoming_sms("stat"), "request-18")

    assert provider.sent == [
        ("71111111111", STAT_UNAVAILABLE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_continue_command_processor_sends_answer() -> None:
    dialog = [
        make_message(
            text="Что такое VLAN?",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="VLAN — виртуальная сеть.",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 0, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(result="Продолжение.")
    router = build_router(
        chat_client, sms_provider, runtime_state
    )
    processor = ContinueCommandProcessor(
        router,
        build_answer_delivery(sms_provider),
        runtime_state,
    )

    await processor(incoming_sms("+"), "request-19")

    assert sms_provider.sent == [
        ("71111111111", "Продолжение.")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("stat", True),
        ("STAT", True),
        ("стат", True),
        ("stats", True),
        ("  stat  ", True),
        ("statistics", False),
        ("что такое stat", False),
    ],
)
def test_is_stat_command(text: str, expected: bool) -> None:
    assert is_stat_command(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("clear", True),
        ("CLEAR", True),
        ("сброс", True),
        ("  clear  ", True),
        ("clearance", False),
    ],
)
def test_is_clear_command(text: str, expected: bool) -> None:
    assert is_clear_command(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+", True),
        ("  +  ", True),
        ("++", False),
        ("+7999", False),
    ],
)
def test_is_continue_command(text: str, expected: bool) -> None:
    assert is_continue_command(text) is expected


@pytest.mark.asyncio
async def test_answer_delivery_sends_single_segment_below_threshold() -> None:
    provider = FakeSMSProvider(
        history=[
            make_message(
                text="ответ",
                incoming=False,
                sent_at=datetime(
                    2026, 8, 21, 9, 0, tzinfo=TIMEZONE
                ),
                pdu=2,
            ),
        ]
    )
    delivery = AnswerDelivery(
        provider,
        daily_warning_threshold=5,
        timezone=TIMEZONE,
        clock=lambda: datetime(
            2026, 8, 21, 12, 0, tzinfo=TIMEZONE
        ),
    )

    result = await delivery.send_answer(
        "71111111111", "Короткий ответ."
    )

    assert provider.sent == [
        ("71111111111", "Короткий ответ.")
    ]
    assert result.pdu_count == 1


@pytest.mark.asyncio
async def test_answer_delivery_adds_warning_at_threshold() -> None:
    provider = FakeSMSProvider(
        history=[
            make_message(
                text="ответ",
                incoming=False,
                sent_at=datetime(
                    2026, 8, 21, 9, 0, tzinfo=TIMEZONE
                ),
                pdu=5,
            ),
        ]
    )
    delivery = AnswerDelivery(
        provider,
        daily_warning_threshold=5,
        timezone=TIMEZONE,
        clock=lambda: datetime(
            2026, 8, 21, 12, 0, tzinfo=TIMEZONE
        ),
    )

    await delivery.send_answer(
        "71111111111", "Короткий ответ."
    )

    assert provider.sent == [
        ("71111111111", "[!] Короткий ответ.")
    ]


@pytest.mark.asyncio
async def test_answer_delivery_fails_open_when_history_unavailable() -> None:
    provider = FakeSMSProvider(
        history_error=PlusofonError("history down")
    )
    delivery = AnswerDelivery(
        provider,
        daily_warning_threshold=5,
        timezone=TIMEZONE,
        clock=lambda: datetime(
            2026, 8, 21, 12, 0, tzinfo=TIMEZONE
        ),
    )

    await delivery.send_answer(
        "71111111111", "Короткий ответ."
    )

    assert provider.sent == [
        ("71111111111", "Короткий ответ.")
    ]


@pytest.mark.asyncio
async def test_answer_delivery_sends_multiple_segments_and_sums_pdu() -> None:
    provider = FakeSMSProvider()
    delivery = AnswerDelivery(
        provider,
        daily_warning_threshold=1000,
        timezone=TIMEZONE,
        clock=lambda: datetime(
            2026, 8, 21, 12, 0, tzinfo=TIMEZONE
        ),
    )

    long_text = " ".join(["word"] * 60)

    result = await delivery.send_answer(
        "71111111111", long_text
    )

    assert len(provider.sent) > 1
    assert result.pdu_count == len(provider.sent)
    for to, text in provider.sent:
        assert to == "71111111111"
        assert text.startswith("[")


@pytest.mark.asyncio
async def test_answer_includes_latin_instruction_when_enabled() -> None:
    runtime_state = RuntimeState()
    runtime_state.set_latin_mode("71111111111", True)
    client = FakeChatClient(result="ответ")
    router = build_router(
        client, runtime_state=runtime_state
    )

    await router.answer(
        incoming_sms(), "request-latin-1"
    )

    system_message = client.messages[0]
    assert system_message.role == ChatRole.SYSTEM
    assert LATIN_INSTRUCTION in system_message.content


@pytest.mark.asyncio
async def test_answer_excludes_latin_instruction_when_disabled() -> None:
    runtime_state = RuntimeState()
    client = FakeChatClient(result="ответ")
    router = build_router(
        client, runtime_state=runtime_state
    )

    await router.answer(
        incoming_sms(), "request-latin-2"
    )

    system_message = client.messages[0]
    assert (
        LATIN_INSTRUCTION
        not in system_message.content
    )
    assert system_message.content == SMS_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_continue_answer_includes_latin_instruction_when_enabled() -> None:
    dialog = [
        make_message(
            text="Что такое VLAN?",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="VLAN — виртуальная сеть.",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 0, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    runtime_state = RuntimeState()
    runtime_state.set_latin_mode("71111111111", True)
    client = FakeChatClient(result="Продолжение.")
    router = build_router(
        client, sms_provider, runtime_state
    )

    await router.continue_answer(
        "71111111111", "request-latin-3"
    )

    system_message = client.messages[0]
    assert LATIN_INSTRUCTION in system_message.content


@pytest.mark.asyncio
async def test_router_uses_default_latin_enabled_setting() -> None:
    runtime_state = RuntimeState()
    client = FakeChatClient(result="ответ")
    router = MessageRouter(
        client,
        FakeSMSProvider(),
        runtime_state,
        model="GigaChat-3-Ultra",
        max_context_messages=8,
        default_latin_enabled=True,
    )

    await router.answer(
        incoming_sms(), "request-latin-4"
    )

    assert (
        LATIN_INSTRUCTION in client.messages[0].content
    )


@pytest.mark.asyncio
async def test_router_default_latin_enabled_overridden_by_explicit_off() -> None:
    runtime_state = RuntimeState()
    runtime_state.set_latin_mode("71111111111", False)
    client = FakeChatClient(result="ответ")
    router = MessageRouter(
        client,
        FakeSMSProvider(),
        runtime_state,
        model="GigaChat-3-Ultra",
        max_context_messages=8,
        default_latin_enabled=True,
    )

    await router.answer(
        incoming_sms(), "request-latin-5"
    )

    assert (
        LATIN_INSTRUCTION
        not in client.messages[0].content
    )


@pytest.mark.asyncio
async def test_latin_command_processor_sends_on_confirmation() -> None:
    provider = FakeSMSProvider()
    processor = LatinCommandProcessor(provider)

    await processor(
        incoming_sms("latin on"),
        "request-latin-6",
        True,
    )

    assert provider.sent == [
        ("71111111111", LATIN_ON_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_latin_command_processor_sends_off_confirmation() -> None:
    provider = FakeSMSProvider()
    processor = LatinCommandProcessor(provider)

    await processor(
        incoming_sms("latin off"),
        "request-latin-7",
        False,
    )

    assert provider.sent == [
        ("71111111111", LATIN_OFF_MESSAGE)
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("latin on", True),
        ("LATIN ON", True),
        ("  latin on  ", True),
        ("latin off", False),
        ("LATIN OFF", False),
        ("latin", None),
        ("latin maybe", None),
        ("+7999 latin on", None),
    ],
)
def test_parse_latin_command(
    text: str, expected: bool | None
) -> None:
    assert parse_latin_command(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("models", True),
        ("MODELS", True),
        ("  models  ", True),
        ("model", False),
        ("models list", False),
    ],
)
def test_is_models_command(
    text: str, expected: bool
) -> None:
    assert is_models_command(text) is expected


@pytest.mark.asyncio
async def test_models_command_processor_lists_models_and_current() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models=[
            gigachat_model("GigaChat-2"),
            gigachat_model("GigaChat-2-Pro"),
            gigachat_model("Embeddings"),
        ]
    )
    runtime_state = RuntimeState()
    runtime_state.set_selected_model(
        "71111111111", "GigaChat-2-Pro"
    )
    processor = ModelsCommandProcessor(
        provider,
        chat_client,
        runtime_state,
        default_model="GigaChat-3-Ultra",
    )

    await processor(
        incoming_sms("models"), "request-models-1"
    )

    assert len(provider.sent) == 1
    to, text = provider.sent[0]
    assert to == "71111111111"
    assert "GigaChat-2" in text
    assert "GigaChat-2-Pro" in text
    assert "Embeddings" not in text
    assert "Текущая: GigaChat-2-Pro" in text


@pytest.mark.asyncio
async def test_models_command_processor_uses_default_model_without_selection() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models=[gigachat_model("GigaChat-2")]
    )
    runtime_state = RuntimeState()
    processor = ModelsCommandProcessor(
        provider,
        chat_client,
        runtime_state,
        default_model="GigaChat-3-Ultra",
    )

    await processor(
        incoming_sms("models"), "request-models-2"
    )

    _, text = provider.sent[0]
    assert "Текущая: GigaChat-3-Ultra" in text


@pytest.mark.asyncio
async def test_models_command_processor_excludes_embedding_only_response() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models=[
            gigachat_model("Embeddings"),
            gigachat_model("Embeddings-2"),
        ]
    )
    runtime_state = RuntimeState()
    processor = ModelsCommandProcessor(
        provider,
        chat_client,
        runtime_state,
        default_model="GigaChat-3-Ultra",
    )

    await processor(
        incoming_sms("models"), "request-models-3"
    )

    assert provider.sent == [
        ("71111111111", MODELS_UNAVAILABLE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_models_command_processor_falls_back_on_api_error() -> None:
    provider = FakeSMSProvider()
    chat_client = FakeChatClient(
        models_error=GigaChatError("down")
    )
    runtime_state = RuntimeState()
    processor = ModelsCommandProcessor(
        provider,
        chat_client,
        runtime_state,
        default_model="GigaChat-3-Ultra",
    )

    await processor(
        incoming_sms("models"), "request-models-4"
    )

    assert provider.sent == [
        ("71111111111", MODELS_UNAVAILABLE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_context_excludes_pin_commands_and_their_replies() -> None:
    dialog = [
        make_message(
            text="9999 auth",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text=AUTH_SUCCESS_MESSAGE,
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="8241 model GigaChat-2-Pro",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text=MODEL_CHANGE_MESSAGE_TEMPLATE.format(
                model="GigaChat-2-Pro"
            ),
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 2, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 2, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-pin-1")

    joined = " ".join(
        message.content for message in client.messages
    )
    assert "9999" not in joined
    assert "8241" not in joined
    assert "auth" not in joined.lower()
    assert "NAT" in joined


@pytest.mark.asyncio
async def test_continue_answer_unavailable_immediately_after_clear() -> None:
    dialog = [
        make_message(
            text="Что такое VLAN?",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="VLAN — виртуальная сеть.",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 0, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    runtime_state = RuntimeState()
    runtime_state.set_context_boundary(
        "71111111111",
        datetime(2026, 8, 21, 9, 1, 0, tzinfo=UTC),
    )
    client = FakeChatClient(result="unused")
    router = build_router(
        client, sms_provider, runtime_state
    )

    result = await router.continue_answer(
        "71111111111", "request-clear-continue"
    )

    assert result == CONTINUATION_UNAVAILABLE_MESSAGE
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_clear_boundary_compares_created_at_not_sent_at() -> None:
    boundary = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    dialog = [
        # created before the boundary but delivered
        # (sent) after it -- must still be excluded.
        make_message(
            text="Старый вопрос",
            incoming=True,
            created_at=datetime(
                2026, 8, 20, 11, 0, tzinfo=UTC
            ),
            sent_at=datetime(
                2026, 8, 20, 13, 0, tzinfo=UTC
            ),
        ),
        # created after the boundary but with an
        # earlier sent_at timestamp -- must be kept.
        make_message(
            text="Новый вопрос",
            incoming=True,
            created_at=datetime(
                2026, 8, 20, 12, 30, tzinfo=UTC
            ),
            sent_at=datetime(
                2026, 8, 20, 12, 0, 1, tzinfo=UTC
            ),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    runtime_state = RuntimeState()
    runtime_state.set_context_boundary(
        "71111111111", boundary
    )
    client = FakeChatClient(result="ответ")
    router = build_router(
        client, sms_provider, runtime_state
    )

    await router.answer(
        incoming_sms(), "request-created-at"
    )

    contents = [
        message.content for message in client.messages
    ]
    assert "Старый вопрос" not in contents
    assert "Новый вопрос" in contents
