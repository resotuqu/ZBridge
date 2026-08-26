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
from app.services.currency import (
    CurrencyError,
    CurrencyRate,
)
from app.services.message_router import (
    AI_FAILURE_MESSAGE,
    AUTH_SUCCESS_MESSAGE,
    CALC_DIVISION_BY_ZERO_MESSAGE,
    CALC_EMPTY_EXPRESSION_MESSAGE,
    CALC_INVALID_EXPRESSION_MESSAGE,
    CLEAR_CONFIRMATION_MESSAGE,
    CONTINUATION_INSTRUCTION,
    CONTINUATION_UNAVAILABLE_MESSAGE,
    CURRENCY_INVALID_CODE_MESSAGE,
    CURRENCY_MISSING_ARGS_MESSAGE,
    CURRENCY_UNAVAILABLE_MESSAGE,
    HELP_TEXT,
    LATIN_INSTRUCTION,
    LATIN_OFF_MESSAGE,
    LATIN_ON_MESSAGE,
    MODEL_CHANGE_MESSAGE_TEMPLATE,
    MODEL_NOT_FOUND_MESSAGE,
    MODELS_UNAVAILABLE_MESSAGE,
    NEWS_SUMMARY_SYSTEM_PROMPT,
    NEWS_UNAVAILABLE_MESSAGE,
    SMS_SYSTEM_PROMPT,
    STAT_UNAVAILABLE_MESSAGE,
    TRANSLATE_EMPTY_TEXT_MESSAGE,
    WEATHER_EMPTY_CITY_MESSAGE,
    WEATHER_NOT_FOUND_MESSAGE,
    WEATHER_UNAVAILABLE_MESSAGE,
    WIKI_EMPTY_TOPIC_MESSAGE,
    WIKI_NOT_FOUND_MESSAGE,
    WIKI_UNAVAILABLE_MESSAGE,
    AdminCommandProcessor,
    AnswerDelivery,
    AuthCommandProcessor,
    CalcCommandProcessor,
    ClearCommandProcessor,
    ContinueCommandProcessor,
    CurrencyCommandProcessor,
    HelpCommandProcessor,
    IncomingSMSProcessor,
    LatinCommandProcessor,
    MessageRouter,
    ModelsCommandProcessor,
    NewsCommandProcessor,
    StatCommandProcessor,
    TranslateCommandProcessor,
    WeatherCommandProcessor,
    WikiCommandProcessor,
    is_clear_command,
    is_continue_command,
    is_help_command,
    is_models_command,
    is_stat_command,
    parse_calc_command,
    parse_currency_command,
    parse_latin_command,
    parse_news_command,
    parse_translate_command,
    parse_weather_command,
    parse_wiki_command,
)
from app.services.news import NewsError, NewsItem
from app.services.plusofon import PlusofonError, SendResult, SMSMessage
from app.services.sms_formatter import format_sms_answer
from app.services.weather import (
    WeatherError,
    WeatherInfo,
    WeatherNotFoundError,
)
from app.services.wikipedia import (
    WikipediaError,
    WikipediaNotFoundError,
)


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


class FakeWikipediaProvider:
    def __init__(
        self,
        *,
        summary: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.summary = summary
        self.error = error
        self.requested_topics: list[str] = []

    async def get_summary(self, topic: str) -> str:
        self.requested_topics.append(topic)

        if self.error is not None:
            raise self.error

        assert self.summary is not None
        return self.summary


class FakeWeatherProvider:
    def __init__(
        self,
        *,
        weather: WeatherInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self.weather = weather
        self.error = error
        self.requested_cities: list[str] = []

    async def get_weather(
        self, city: str
    ) -> WeatherInfo:
        self.requested_cities.append(city)

        if self.error is not None:
            raise self.error

        assert self.weather is not None
        return self.weather


class FakeCurrencyProvider:
    def __init__(
        self,
        *,
        rate: CurrencyRate | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rate = rate
        self.error = error
        self.requested_pairs: list[
            tuple[str, str]
        ] = []

    async def convert(
        self, base: str, quote: str
    ) -> CurrencyRate:
        self.requested_pairs.append((base, quote))

        if self.error is not None:
            raise self.error

        assert self.rate is not None
        return self.rate


class FakeNewsProvider:
    def __init__(
        self,
        *,
        items: list[NewsItem] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = items
        self.error = error
        self.requested: list[
            tuple[str | None, int]
        ] = []

    async def get_news(
        self, topic: str | None, limit: int
    ) -> list[NewsItem]:
        self.requested.append((topic, limit))

        if self.error is not None:
            raise self.error

        assert self.items is not None
        return self.items


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
async def test_continue_answer_skips_trailing_tool_exchange() -> None:
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
            text="calc 2+2",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="4",
            incoming=False,
            sent_at=datetime(2026, 8, 21, 9, 1, 1, tzinfo=UTC),
        ),
        make_message(
            text="+",
            incoming=True,
            sent_at=datetime(2026, 8, 21, 9, 2, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="Продолжение.")
    router = build_router(client, sms_provider)

    result = await router.continue_answer(
        "71111111111", "request-continue-skip-tool"
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


# --- help ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("help", True),
        ("HELP", True),
        ("  help  ", True),
        ("помощь", True),
        ("ПОМОЩЬ", True),
        ("helpme", False),
        ("help me", False),
    ],
)
def test_is_help_command(
    text: str, expected: bool
) -> None:
    assert is_help_command(text) is expected


@pytest.mark.asyncio
async def test_help_command_processor_sends_full_text() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    processor = HelpCommandProcessor(
        provider, runtime_state
    )

    await processor(incoming_sms("help"), "request-help-1")

    expected_segments = format_sms_answer(
        HELP_TEXT, add_warning=False
    )
    assert provider.sent == [
        ("71111111111", segment)
        for segment in expected_segments
    ]


# --- calc -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("calc 1+1", "1+1"),
        ("CALC 1+1", "1+1"),
        ("  calc   (10+5)*2  ", "(10+5)*2"),
        ("calc", ""),
        ("calculate 1+1", None),
        ("что такое calc", None),
    ],
)
def test_parse_calc_command(
    text: str, expected: str | None
) -> None:
    assert parse_calc_command(text) == expected


@pytest.mark.asyncio
async def test_calc_command_processor_sends_result() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    processor = CalcCommandProcessor(
        provider, runtime_state
    )

    await processor(
        incoming_sms("calc 1250*1.2"),
        "request-calc-1",
        "1250*1.2",
    )

    assert provider.sent == [("71111111111", "1500")]


@pytest.mark.asyncio
async def test_calc_command_processor_reports_empty_expression() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    processor = CalcCommandProcessor(
        provider, runtime_state
    )

    await processor(
        incoming_sms("calc"), "request-calc-2", ""
    )

    assert provider.sent == [
        ("71111111111", CALC_EMPTY_EXPRESSION_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_calc_command_processor_reports_invalid_expression() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    processor = CalcCommandProcessor(
        provider, runtime_state
    )

    await processor(
        incoming_sms("calc abc"),
        "request-calc-3",
        "abc",
    )

    assert provider.sent == [
        ("71111111111", CALC_INVALID_EXPRESSION_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_calc_command_processor_reports_division_by_zero() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    processor = CalcCommandProcessor(
        provider, runtime_state
    )

    await processor(
        incoming_sms("calc 10/0"),
        "request-calc-4",
        "10/0",
    )

    assert provider.sent == [
        ("71111111111", CALC_DIVISION_BY_ZERO_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_calc_command_processor_serializes_per_phone() -> None:
    import asyncio

    provider = FakeSMSProvider()
    order: list[str] = []

    original_send = provider.send

    async def slow_send(to: str, text: str) -> SendResult:
        if text == "1":
            await asyncio.sleep(0.02)
        order.append(text)
        return await original_send(to, text)

    provider.send = slow_send  # type: ignore[method-assign]

    runtime_state = RuntimeState()
    processor = CalcCommandProcessor(
        provider, runtime_state
    )

    await asyncio.gather(
        processor(
            incoming_sms("calc 0+1"),
            "request-calc-5a",
            "0+1",
        ),
        processor(
            incoming_sms("calc 1+1"),
            "request-calc-5b",
            "1+1",
        ),
    )

    # The slower first call must still finish (and send)
    # before the second call's send happens, because both
    # share the same per-phone lock.
    assert order == ["1", "2"]


# --- translate --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("translate Hello", "Hello"),
        ("TRANSLATE Hello", "Hello"),
        ("translate en Привет", "en Привет"),
        ("translate", ""),
        ("translated text", None),
        ("не translate", None),
    ],
)
def test_parse_translate_command(
    text: str, expected: str | None
) -> None:
    assert parse_translate_command(text) == expected


@pytest.mark.asyncio
async def test_translate_command_processor_uses_explicit_target_language() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(result="Hello!")
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate en Привет"),
        "request-translate-1",
        "en Привет",
    )

    assert chat_client.messages is not None
    system_message = chat_client.messages[0]
    assert system_message.role == ChatRole.SYSTEM
    assert "en" in system_message.content
    assert chat_client.messages[1] == ChatMessage(
        role=ChatRole.USER, content="Привет"
    )
    assert provider.sent == [("71111111111", "Hello!")]


@pytest.mark.asyncio
async def test_translate_command_processor_auto_detects_russian_to_english() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(result="Hello!")
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate Привет, как дела?"),
        "request-translate-2",
        "Привет, как дела?",
    )

    system_message = chat_client.messages[0]
    assert "en" in system_message.content


@pytest.mark.asyncio
async def test_translate_command_processor_auto_detects_other_to_russian() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(result="Привет!")
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate Hello, how are you?"),
        "request-translate-3",
        "Hello, how are you?",
    )

    system_message = chat_client.messages[0]
    assert "ru" in system_message.content


@pytest.mark.asyncio
async def test_translate_command_processor_reports_empty_text() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(result="unused")
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate"),
        "request-translate-4",
        "",
    )

    assert provider.sent == [
        ("71111111111", TRANSLATE_EMPTY_TEXT_MESSAGE)
    ]
    assert chat_client.call_count == 0


@pytest.mark.asyncio
async def test_translate_command_processor_falls_back_on_gigachat_error() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    chat_client = FakeChatClient(
        error=GigaChatError("down")
    )
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate Hello"),
        "request-translate-5",
        "Hello",
    )

    assert provider.sent == [
        ("71111111111", AI_FAILURE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_translate_command_processor_uses_selected_model() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    runtime_state.set_selected_model(
        "71111111111", "GigaChat-2-Pro"
    )
    chat_client = FakeChatClient(result="Hello!")
    router = build_router(
        chat_client, provider, runtime_state
    )
    delivery = build_answer_delivery(provider)
    processor = TranslateCommandProcessor(
        router, delivery, runtime_state
    )

    await processor(
        incoming_sms("translate en Привет"),
        "request-translate-6",
        "en Привет",
    )

    assert chat_client.model == "GigaChat-2-Pro"


# --- wiki ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("wiki DHCP", "DHCP"),
        ("WIKI DHCP", "DHCP"),
        ("  wiki   виртуальная машина  ", "виртуальная машина"),
        ("wiki", ""),
        ("wikipedia DHCP", None),
        ("моя wiki страница", None),
    ],
)
def test_parse_wiki_command(
    text: str, expected: str | None
) -> None:
    assert parse_wiki_command(text) == expected


@pytest.mark.asyncio
async def test_wiki_command_processor_sends_summary() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    wikipedia = FakeWikipediaProvider(
        summary="DHCP — протокол динамической настройки узла."
    )
    delivery = build_answer_delivery(provider)
    processor = WikiCommandProcessor(
        wikipedia, delivery, runtime_state
    )

    await processor(
        incoming_sms("wiki DHCP"),
        "request-wiki-1",
        "DHCP",
    )

    assert wikipedia.requested_topics == ["DHCP"]
    assert provider.sent == [
        (
            "71111111111",
            "DHCP — протокол динамической настройки узла.",
        )
    ]


@pytest.mark.asyncio
async def test_wiki_command_processor_truncates_long_summary() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    long_summary = (
        "Первое предложение с деталями. "
        + "Слово " * 200
        + "Последнее предложение."
    )
    wikipedia = FakeWikipediaProvider(
        summary=long_summary
    )
    delivery = build_answer_delivery(provider)
    processor = WikiCommandProcessor(
        wikipedia, delivery, runtime_state
    )

    await processor(
        incoming_sms("wiki тема"),
        "request-wiki-2",
        "тема",
    )

    assert provider.sent
    sent_text_total = "".join(
        text for _, text in provider.sent
    )
    assert len(sent_text_total) < len(long_summary)


@pytest.mark.asyncio
async def test_wiki_command_processor_reports_empty_topic() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    wikipedia = FakeWikipediaProvider(summary="unused")
    delivery = build_answer_delivery(provider)
    processor = WikiCommandProcessor(
        wikipedia, delivery, runtime_state
    )

    await processor(
        incoming_sms("wiki"), "request-wiki-3", ""
    )

    assert provider.sent == [
        ("71111111111", WIKI_EMPTY_TOPIC_MESSAGE)
    ]
    assert wikipedia.requested_topics == []


@pytest.mark.asyncio
async def test_wiki_command_processor_reports_not_found() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    wikipedia = FakeWikipediaProvider(
        error=WikipediaNotFoundError("missing")
    )
    delivery = build_answer_delivery(provider)
    processor = WikiCommandProcessor(
        wikipedia, delivery, runtime_state
    )

    await processor(
        incoming_sms("wiki абракадабра"),
        "request-wiki-4",
        "абракадабра",
    )

    assert provider.sent == [
        ("71111111111", WIKI_NOT_FOUND_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_wiki_command_processor_reports_unavailable_on_timeout() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    wikipedia = FakeWikipediaProvider(
        error=WikipediaError("timeout")
    )
    delivery = build_answer_delivery(provider)
    processor = WikiCommandProcessor(
        wikipedia, delivery, runtime_state
    )

    await processor(
        incoming_sms("wiki DHCP"),
        "request-wiki-5",
        "DHCP",
    )

    assert provider.sent == [
        ("71111111111", WIKI_UNAVAILABLE_MESSAGE)
    ]


# --- weather -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("weather Якутск", "Якутск"),
        ("WEATHER Москва", "Москва"),
        ("  weather   London  ", "London"),
        ("weather", ""),
        ("weathervane Якутск", None),
        ("моя weather погода", None),
    ],
)
def test_parse_weather_command(
    text: str, expected: str | None
) -> None:
    assert parse_weather_command(text) == expected


@pytest.mark.asyncio
async def test_weather_command_processor_sends_forecast() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    weather_provider = FakeWeatherProvider(
        weather=WeatherInfo(
            city="Якутск",
            temperature_c=-18,
            night_min_temperature_c=-23,
            wind_speed_ms=3,
            condition="облачно",
        )
    )
    delivery = build_answer_delivery(provider)
    processor = WeatherCommandProcessor(
        weather_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("weather Якутск"),
        "request-weather-1",
        "Якутск",
    )

    assert weather_provider.requested_cities == [
        "Якутск"
    ]
    assert provider.sent == [
        (
            "71111111111",
            "Якутск: -18°, облачно; ветер 3 м/с; "
            "ночью -23°.",
        )
    ]


@pytest.mark.asyncio
async def test_weather_command_processor_reports_empty_city() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    weather_provider = FakeWeatherProvider(
        weather=WeatherInfo(
            city="unused",
            temperature_c=0,
            night_min_temperature_c=0,
            wind_speed_ms=0,
            condition="ясно",
        )
    )
    delivery = build_answer_delivery(provider)
    processor = WeatherCommandProcessor(
        weather_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("weather"),
        "request-weather-2",
        "",
    )

    assert provider.sent == [
        ("71111111111", WEATHER_EMPTY_CITY_MESSAGE)
    ]
    assert weather_provider.requested_cities == []


@pytest.mark.asyncio
async def test_weather_command_processor_reports_not_found() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    weather_provider = FakeWeatherProvider(
        error=WeatherNotFoundError("missing")
    )
    delivery = build_answer_delivery(provider)
    processor = WeatherCommandProcessor(
        weather_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("weather Абракадаброград"),
        "request-weather-3",
        "Абракадаброград",
    )

    assert provider.sent == [
        ("71111111111", WEATHER_NOT_FOUND_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_weather_command_processor_reports_unavailable_on_timeout() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    weather_provider = FakeWeatherProvider(
        error=WeatherError("timeout")
    )
    delivery = build_answer_delivery(provider)
    processor = WeatherCommandProcessor(
        weather_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("weather Якутск"),
        "request-weather-4",
        "Якутск",
    )

    assert provider.sent == [
        ("71111111111", WEATHER_UNAVAILABLE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_weather_command_processor_serializes_per_phone() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    weather_provider = FakeWeatherProvider(
        weather=WeatherInfo(
            city="Якутск",
            temperature_c=-18,
            night_min_temperature_c=-23,
            wind_speed_ms=3,
            condition="облачно",
        )
    )
    delivery = build_answer_delivery(provider)
    processor = WeatherCommandProcessor(
        weather_provider, delivery, runtime_state
    )

    import asyncio

    await asyncio.gather(
        processor(
            incoming_sms("weather Якутск"),
            "request-weather-5a",
            "Якутск",
        ),
        processor(
            incoming_sms("weather Якутск"),
            "request-weather-5b",
            "Якутск",
        ),
    )

    assert len(provider.sent) == 2


# --- currency --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("currency USD RUB", "USD RUB"),
        ("CURRENCY eur rub", "eur rub"),
        ("  currency   CNY   RUB  ", "CNY   RUB"),
        ("currency", ""),
        ("currencyxyz USD RUB", None),
        ("моя currency USD", None),
    ],
)
def test_parse_currency_command(
    text: str, expected: str | None
) -> None:
    assert parse_currency_command(text) == expected


@pytest.mark.asyncio
async def test_currency_command_processor_sends_rate() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider(
        rate=CurrencyRate(
            base="USD",
            quote="RUB",
            rate=Decimal("92.34"),
        )
    )
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency USD RUB"),
        "request-currency-1",
        "USD RUB",
    )

    assert currency_provider.requested_pairs == [
        ("USD", "RUB")
    ]
    assert provider.sent == [
        ("71111111111", "1 USD ≈ 92.34 RUB")
    ]


@pytest.mark.asyncio
async def test_currency_command_processor_normalizes_lowercase_input() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider(
        rate=CurrencyRate(
            base="EUR",
            quote="RUB",
            rate=Decimal("100.5"),
        )
    )
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency eur rub"),
        "request-currency-2",
        "eur rub",
    )

    assert currency_provider.requested_pairs == [
        ("EUR", "RUB")
    ]
    assert provider.sent == [
        ("71111111111", "1 EUR ≈ 100.50 RUB")
    ]


@pytest.mark.asyncio
async def test_currency_command_processor_reports_missing_arguments() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider(
        rate=CurrencyRate(
            base="USD",
            quote="RUB",
            rate=Decimal("92.34"),
        )
    )
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency USD"),
        "request-currency-3",
        "USD",
    )

    assert provider.sent == [
        ("71111111111", CURRENCY_MISSING_ARGS_MESSAGE)
    ]
    assert currency_provider.requested_pairs == []


@pytest.mark.asyncio
async def test_currency_command_processor_reports_too_many_arguments() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider()
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency USD RUB extra"),
        "request-currency-4",
        "USD RUB extra",
    )

    assert provider.sent == [
        ("71111111111", CURRENCY_MISSING_ARGS_MESSAGE)
    ]
    assert currency_provider.requested_pairs == []


@pytest.mark.asyncio
async def test_currency_command_processor_reports_invalid_code() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider()
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency US RUB"),
        "request-currency-5",
        "US RUB",
    )

    assert provider.sent == [
        ("71111111111", CURRENCY_INVALID_CODE_MESSAGE)
    ]
    assert currency_provider.requested_pairs == []


@pytest.mark.asyncio
async def test_currency_command_processor_reports_unavailable_on_error() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    currency_provider = FakeCurrencyProvider(
        error=CurrencyError("unavailable")
    )
    delivery = build_answer_delivery(provider)
    processor = CurrencyCommandProcessor(
        currency_provider, delivery, runtime_state
    )

    await processor(
        incoming_sms("currency USD RUB"),
        "request-currency-6",
        "USD RUB",
    )

    assert provider.sent == [
        ("71111111111", CURRENCY_UNAVAILABLE_MESSAGE)
    ]


# --- news ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("news", ""),
        ("NEWS", ""),
        ("news ИИ", "ИИ"),
        ("news космос", "космос"),
        ("  news   Якутия  ", "Якутия"),
        ("newsroom ИИ", None),
        ("моя news подписка", None),
    ],
)
def test_parse_news_command(
    text: str, expected: str | None
) -> None:
    assert parse_news_command(text) == expected


def _news_item(
    *,
    title: str = "Заголовок",
    source: str = "ТАСС",
    snippet: str = "Описание.",
) -> NewsItem:
    return NewsItem(
        title=title,
        source=source,
        url="https://example.com/a",
        published_at=None,
        snippet=snippet,
    )


@pytest.mark.asyncio
async def test_news_command_processor_sends_summary_without_topic() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    news_provider = FakeNewsProvider(
        items=[_news_item()]
    )
    client = FakeChatClient(result="1. Главное событие.")
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news"), "request-news-1", ""
    )

    assert news_provider.requested == [(None, 5)]
    assert provider.sent == [
        ("71111111111", "1. Главное событие.")
    ]


@pytest.mark.asyncio
async def test_news_command_processor_passes_topic_to_provider() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    news_provider = FakeNewsProvider(
        items=[_news_item(title="Про космос")]
    )
    client = FakeChatClient(result="1. Про космос.")
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news космос"),
        "request-news-2",
        "космос",
    )

    assert news_provider.requested == [("космос", 5)]


@pytest.mark.asyncio
async def test_news_command_processor_sends_only_found_items_to_gigachat() -> None:
    """
    GigaChat must receive exactly the NewsItem objects the
    provider found -- a strict system prompt and a single
    user turn built only from those items -- never the usual
    dialog context or SMS_SYSTEM_PROMPT.
    """
    provider = FakeSMSProvider(
        dialog=[
            make_message(
                text="Какой-то старый вопрос",
                incoming=True,
                sent_at=datetime(
                    2026, 8, 20, 9, 0, tzinfo=UTC
                ),
            ),
            make_message(
                text="Какой-то старый ответ",
                incoming=False,
                sent_at=datetime(
                    2026, 8, 20, 9, 0, 5, tzinfo=UTC
                ),
            ),
        ]
    )
    runtime_state = RuntimeState()
    news_provider = FakeNewsProvider(
        items=[
            _news_item(
                title="Заголовок A",
                source="ТАСС",
                snippet="Снипет A.",
            ),
            _news_item(
                title="Заголовок B",
                source="РБК",
                snippet="Снипет B.",
            ),
        ]
    )
    client = FakeChatClient(result="1. Сводка.")
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news"), "request-news-3", ""
    )

    assert client.messages == [
        ChatMessage(
            role=ChatRole.SYSTEM,
            content=NEWS_SUMMARY_SYSTEM_PROMPT,
        ),
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "1. [ТАСС] Заголовок A — Снипет A.\n"
                "2. [РБК] Заголовок B — Снипет B."
            ),
        ),
    ]
    # The dialog history and the normal SMS system prompt
    # must never leak into the summary request.
    joined = " ".join(
        message.content for message in client.messages
    )
    assert "старый вопрос" not in joined
    assert "старый ответ" not in joined
    assert SMS_SYSTEM_PROMPT not in joined


@pytest.mark.asyncio
async def test_news_command_processor_reports_unavailable_when_no_items() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    news_provider = FakeNewsProvider(
        error=NewsError("nothing usable")
    )
    client = FakeChatClient(result="unused")
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news"), "request-news-4", ""
    )

    assert provider.sent == [
        ("71111111111", NEWS_UNAVAILABLE_MESSAGE)
    ]
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_news_command_processor_reports_ai_failure_on_gigachat_error() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    news_provider = FakeNewsProvider(
        items=[_news_item()]
    )
    client = FakeChatClient(
        error=GigaChatTransientError("down")
    )
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news"), "request-news-5", ""
    )

    assert provider.sent == [
        ("71111111111", AI_FAILURE_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_news_command_processor_uses_selected_model() -> None:
    provider = FakeSMSProvider()
    runtime_state = RuntimeState()
    runtime_state.set_selected_model(
        "71111111111", "GigaChat-2-Pro"
    )
    news_provider = FakeNewsProvider(
        items=[_news_item()]
    )
    client = FakeChatClient(result="1. Сводка.")
    router = build_router(client, provider, runtime_state)
    delivery = build_answer_delivery(provider)
    processor = NewsCommandProcessor(
        news_provider, router, delivery, runtime_state
    )

    await processor(
        incoming_sms("news"), "request-news-6", ""
    )

    assert client.model == "GigaChat-2-Pro"


def test_weather_and_currency_processors_never_depend_on_gigachat() -> None:
    """
    Neither weather nor currency data may come from
    GigaChat -- assert the processors don't even hold a
    ChatClient/MessageRouter dependency capable of issuing
    a chat request.
    """
    import inspect

    weather_params = inspect.signature(
        WeatherCommandProcessor.__init__
    ).parameters
    currency_params = inspect.signature(
        CurrencyCommandProcessor.__init__
    ).parameters

    for params in (weather_params, currency_params):
        assert "chat_client" not in params
        assert "message_router" not in params


# --- context exclusion for the new commands ----------------------------


@pytest.mark.asyncio
async def test_context_excludes_tool_commands_and_their_replies() -> None:
    dialog = [
        make_message(
            text="calc 2+2",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="4",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="translate en Привет",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="Hello",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 1, tzinfo=UTC),
        ),
        make_message(
            text="wiki DHCP",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 2, tzinfo=UTC),
        ),
        make_message(
            text="DHCP — протокол динамической настройки узла.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 2, 1, tzinfo=UTC),
        ),
        make_message(
            text="help",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 3, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 4, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 4, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(
        client, sms_provider, max_context_messages=20
    )

    await router.answer(incoming_sms(), "request-tools-1")

    contents = [
        message.content for message in client.messages
    ]
    assert "calc 2+2" not in contents
    assert "4" not in contents
    assert "translate en Привет" not in contents
    assert "Hello" not in contents
    assert "wiki DHCP" not in contents
    assert (
        "DHCP — протокол динамической настройки узла."
        not in contents
    )
    assert "help" not in contents
    assert "Что такое NAT?" in contents


@pytest.mark.asyncio
async def test_context_excludes_calc_reply_even_though_result_is_dynamic() -> None:
    """
    The calc reply is a computed number that cannot be
    matched by any fixed text -- only pairing it with the
    preceding "calc" command keeps it out of context.
    """
    dialog = [
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 5, tzinfo=UTC),
        ),
        make_message(
            text="calc 1250*1.2",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="1500",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 1, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-calc-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "calc 1250*1.2" not in contents
    assert "1500" not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_translate_reply_even_though_translation_is_dynamic() -> None:
    dialog = [
        make_message(
            text="translate Hello, how are you?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="Привет, как дела?",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-translate-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "translate Hello, how are you?" not in contents
    assert "Привет, как дела?" not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_wiki_reply_even_though_summary_is_dynamic() -> None:
    dialog = [
        make_message(
            text="wiki DHCP",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="DHCP — протокол динамической настройки узла.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-wiki-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "wiki DHCP" not in contents
    assert (
        "DHCP — протокол динамической настройки узла."
        not in contents
    )
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_multi_segment_wiki_reply() -> None:
    """
    translate/wiki replies can be split into several
    outgoing SMS segments by AnswerDelivery -- every
    segment up to the next incoming message must stay
    hidden, not just the first one.
    """
    dialog = [
        make_message(
            text="wiki DHCP",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="[1/2] DHCP — протокол динамической настройки узла сети.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="[2/2] Использует UDP-порты 67 и 68.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 2, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-wiki-multi-segment")

    contents = [
        message.content for message in client.messages
    ]
    assert "wiki DHCP" not in contents
    assert not any(
        "DHCP" in content for content in contents
    )
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_weather_reply_even_though_forecast_is_dynamic() -> None:
    dialog = [
        make_message(
            text="weather Якутск",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="Якутск: -18°, облачно; ветер 3 м/с; ночью -23°.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-weather-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "weather Якутск" not in contents
    assert not any(
        "Якутск" in content for content in contents
    )
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_currency_reply_even_though_rate_is_dynamic() -> None:
    dialog = [
        make_message(
            text="currency USD RUB",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="1 USD ≈ 92.34 RUB",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-currency-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "currency USD RUB" not in contents
    assert "1 USD ≈ 92.34 RUB" not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_weather_and_currency_error_replies() -> None:
    """
    An error reply (e.g. "Погода сейчас недоступна :(")
    must be hidden the same way a successful dynamic reply
    is -- the whole exchange is hidden regardless of what
    the outgoing SMS says.
    """
    dialog = [
        make_message(
            text="weather Абракадаброград",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text=WEATHER_NOT_FOUND_MESSAGE,
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="currency XXX YYY",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text=CURRENCY_UNAVAILABLE_MESSAGE,
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

    await router.answer(incoming_sms(), "request-weather-currency-error")

    contents = [
        message.content for message in client.messages
    ]
    assert "weather Абракадаброград" not in contents
    assert WEATHER_NOT_FOUND_MESSAGE not in contents
    assert "currency XXX YYY" not in contents
    assert CURRENCY_UNAVAILABLE_MESSAGE not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_news_reply_even_though_summary_is_dynamic() -> None:
    dialog = [
        make_message(
            text="news космос",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text="1. Запущен новый спутник.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-news-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "news космос" not in contents
    assert "1. Запущен новый спутник." not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_bare_news_command_and_error_reply() -> None:
    dialog = [
        make_message(
            text="news",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        make_message(
            text=NEWS_UNAVAILABLE_MESSAGE,
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 0, 1, tzinfo=UTC),
        ),
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
        ),
        make_message(
            text="NAT — это трансляция адресов.",
            incoming=False,
            sent_at=datetime(2026, 8, 20, 9, 1, 5, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-news-error-context")

    contents = [
        message.content for message in client.messages
    ]
    assert "news" not in contents
    assert NEWS_UNAVAILABLE_MESSAGE not in contents
    assert "NAT — это трансляция адресов." in contents


@pytest.mark.asyncio
async def test_context_excludes_help_reply_segments() -> None:
    help_segments = format_sms_answer(
        HELP_TEXT, add_warning=False
    )
    assert len(help_segments) > 1  # sanity: help splits

    dialog = [
        make_message(
            text=segment,
            incoming=False,
            sent_at=datetime(
                2026, 8, 20, 9, index, tzinfo=UTC
            ),
        )
        for index, segment in enumerate(help_segments)
    ] + [
        make_message(
            text="Что такое NAT?",
            incoming=True,
            sent_at=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        ),
    ]
    sms_provider = FakeSMSProvider(dialog=dialog)
    client = FakeChatClient(result="ответ")
    router = build_router(client, sms_provider)

    await router.answer(incoming_sms(), "request-tools-2")

    contents = [
        message.content for message in client.messages
    ]
    for segment in help_segments:
        assert segment not in contents
