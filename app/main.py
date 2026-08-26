from __future__ import annotations

import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.plusofon_webhook import (
    router as plusofon_webhook_router,
)
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.runtime_state import RuntimeState
from app.services.gigachat import GigaChatClient
from app.services.currency import (
    FrankfurterCurrencyProvider,
)
from app.services.message_router import (
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
)
from app.services.news import (
    DEFAULT_RUSSIAN_RSS_FEEDS,
    AggregatedNewsProvider,
    GoogleNewsRssProvider,
    GoogleNewsRssSearchProvider,
    RssKeywordSearchProvider,
    RussianRssNewsProvider,
)
from app.services.plusofon import PlusofonClient
from app.services.weather import OpenMeteoWeatherProvider
from app.services.wikipedia import WikipediaProvider


logger = logging.getLogger(__name__)


def build_ssl_context(
    ca_bundle: str | None,
) -> ssl.SSLContext:
    context = ssl.create_default_context()

    if ca_bundle:
        context.load_verify_locations(
            cafile=ca_bundle
        )

    return context


def _install_handler(
    application: FastAPI,
    *,
    state_attr: str,
    managed_attr: str,
    processor: object,
) -> None:
    current_handler = getattr(
        application.state, state_attr
    )
    managed_handler = getattr(
        application.state, managed_attr
    )

    if (
        current_handler is None
        or current_handler is managed_handler
    ):
        setattr(
            application.state, state_attr, processor
        )

    setattr(
        application.state, managed_attr, processor
    )


@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    settings = application.state.settings
    ssl_context = build_ssl_context(
        settings.gigachat_ca_bundle
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.http_timeout_seconds
        ),
        verify=ssl_context,
    ) as http_client:
        gigachat_client = GigaChatClient(
            http_client,
            credentials=(
                settings
                .gigachat_credentials
                .get_secret_value()
            ),
            scope=settings.gigachat_scope,
            api_base_url=(
                settings.gigachat_api_base_url
            ),
            oauth_url=settings.gigachat_oauth_url,
        )

        plusofon_client = PlusofonClient(
            http_client,
            token=(
                settings
                .plusofon_token
                .get_secret_value()
            ),
            client_id=settings.plusofon_client_id,
            number_id=settings.plusofon_number_id,
            api_base_url=(
                settings.plusofon_api_base_url
            ),
            own_number=settings.plusofon_number,
            default_timezone=settings.timezone_info,
        )

        message_router = MessageRouter(
            gigachat_client,
            plusofon_client,
            application.state.runtime_state,
            model=settings.gigachat_model,
            max_context_messages=(
                settings.max_context_messages
            ),
            default_latin_enabled=(
                settings.default_latin_enabled
            ),
        )

        answer_delivery = AnswerDelivery(
            plusofon_client,
            daily_warning_threshold=(
                settings.daily_warning_threshold
            ),
            timezone=settings.timezone_info,
        )

        sms_processor = IncomingSMSProcessor(
            message_router,
            answer_delivery,
            application.state.runtime_state,
        )

        auth_command_processor = AuthCommandProcessor(
            plusofon_client
        )

        admin_command_processor = AdminCommandProcessor(
            plusofon_client,
            gigachat_client,
            application.state.runtime_state,
        )

        clear_command_processor = ClearCommandProcessor(
            plusofon_client
        )

        models_command_processor = ModelsCommandProcessor(
            plusofon_client,
            gigachat_client,
            application.state.runtime_state,
            default_model=settings.gigachat_model,
        )

        latin_command_processor = LatinCommandProcessor(
            plusofon_client
        )

        stat_command_processor = StatCommandProcessor(
            plusofon_client,
            application.state.runtime_state,
            default_model=settings.gigachat_model,
            sms_price_rub=settings.sms_price_rub,
            timezone=settings.timezone_info,
        )

        continue_command_processor = (
            ContinueCommandProcessor(
                message_router,
                answer_delivery,
                application.state.runtime_state,
            )
        )

        wikipedia_provider = WikipediaProvider(
            http_client
        )

        help_command_processor = HelpCommandProcessor(
            plusofon_client,
            application.state.runtime_state,
        )

        calc_command_processor = CalcCommandProcessor(
            plusofon_client,
            application.state.runtime_state,
        )

        translate_command_processor = (
            TranslateCommandProcessor(
                message_router,
                answer_delivery,
                application.state.runtime_state,
            )
        )

        wiki_command_processor = WikiCommandProcessor(
            wikipedia_provider,
            answer_delivery,
            application.state.runtime_state,
        )

        weather_provider = OpenMeteoWeatherProvider(
            http_client
        )

        currency_provider = FrankfurterCurrencyProvider(
            http_client
        )

        weather_command_processor = (
            WeatherCommandProcessor(
                weather_provider,
                answer_delivery,
                application.state.runtime_state,
            )
        )

        currency_command_processor = (
            CurrencyCommandProcessor(
                currency_provider,
                answer_delivery,
                application.state.runtime_state,
            )
        )

        russian_news_provider = RussianRssNewsProvider(
            http_client,
            feeds=(
                settings.news_rss_feeds
                or DEFAULT_RUSSIAN_RSS_FEEDS
            ),
        )

        news_keyword_search_provider = (
            RssKeywordSearchProvider(russian_news_provider)
        )

        google_news_search_provider = (
            GoogleNewsRssSearchProvider(http_client)
        )

        google_news_provider = GoogleNewsRssProvider(
            http_client
        )

        news_provider = AggregatedNewsProvider(
            russian_news_provider,
            news_keyword_search_provider,
            google_news_search_provider,
            google_news_provider,
        )

        news_command_processor = NewsCommandProcessor(
            news_provider,
            message_router,
            answer_delivery,
            application.state.runtime_state,
        )

        application.state.http_client = http_client
        application.state.gigachat_client = (
            gigachat_client
        )
        application.state.plusofon_client = (
            plusofon_client
        )
        application.state.message_router = (
            message_router
        )
        application.state.answer_delivery = (
            answer_delivery
        )
        application.state.sms_processor = (
            sms_processor
        )
        application.state.auth_command_processor = (
            auth_command_processor
        )
        application.state.admin_command_processor = (
            admin_command_processor
        )
        application.state.clear_command_processor = (
            clear_command_processor
        )
        application.state.stat_command_processor = (
            stat_command_processor
        )
        application.state.continue_command_processor = (
            continue_command_processor
        )
        application.state.models_command_processor = (
            models_command_processor
        )
        application.state.latin_command_processor = (
            latin_command_processor
        )
        application.state.wikipedia_provider = (
            wikipedia_provider
        )
        application.state.help_command_processor = (
            help_command_processor
        )
        application.state.calc_command_processor = (
            calc_command_processor
        )
        application.state.translate_command_processor = (
            translate_command_processor
        )
        application.state.wiki_command_processor = (
            wiki_command_processor
        )
        application.state.weather_provider = (
            weather_provider
        )
        application.state.currency_provider = (
            currency_provider
        )
        application.state.weather_command_processor = (
            weather_command_processor
        )
        application.state.currency_command_processor = (
            currency_command_processor
        )
        application.state.news_provider = news_provider
        application.state.news_command_processor = (
            news_command_processor
        )

        _install_handler(
            application,
            state_attr="incoming_sms_handler",
            managed_attr=(
                "managed_incoming_sms_handler"
            ),
            processor=sms_processor,
        )
        _install_handler(
            application,
            state_attr="auth_command_handler",
            managed_attr=(
                "managed_auth_command_handler"
            ),
            processor=auth_command_processor,
        )
        _install_handler(
            application,
            state_attr="admin_command_handler",
            managed_attr=(
                "managed_admin_command_handler"
            ),
            processor=admin_command_processor,
        )
        _install_handler(
            application,
            state_attr="clear_command_handler",
            managed_attr=(
                "managed_clear_command_handler"
            ),
            processor=clear_command_processor,
        )
        _install_handler(
            application,
            state_attr="stat_command_handler",
            managed_attr=(
                "managed_stat_command_handler"
            ),
            processor=stat_command_processor,
        )
        _install_handler(
            application,
            state_attr="continue_command_handler",
            managed_attr=(
                "managed_continue_command_handler"
            ),
            processor=continue_command_processor,
        )
        _install_handler(
            application,
            state_attr="models_command_handler",
            managed_attr=(
                "managed_models_command_handler"
            ),
            processor=models_command_processor,
        )
        _install_handler(
            application,
            state_attr="latin_command_handler",
            managed_attr=(
                "managed_latin_command_handler"
            ),
            processor=latin_command_processor,
        )
        _install_handler(
            application,
            state_attr="help_command_handler",
            managed_attr=(
                "managed_help_command_handler"
            ),
            processor=help_command_processor,
        )
        _install_handler(
            application,
            state_attr="calc_command_handler",
            managed_attr=(
                "managed_calc_command_handler"
            ),
            processor=calc_command_processor,
        )
        _install_handler(
            application,
            state_attr="translate_command_handler",
            managed_attr=(
                "managed_translate_command_handler"
            ),
            processor=translate_command_processor,
        )
        _install_handler(
            application,
            state_attr="wiki_command_handler",
            managed_attr=(
                "managed_wiki_command_handler"
            ),
            processor=wiki_command_processor,
        )
        _install_handler(
            application,
            state_attr="weather_command_handler",
            managed_attr=(
                "managed_weather_command_handler"
            ),
            processor=weather_command_processor,
        )
        _install_handler(
            application,
            state_attr="currency_command_handler",
            managed_attr=(
                "managed_currency_command_handler"
            ),
            processor=currency_command_processor,
        )
        _install_handler(
            application,
            state_attr="news_command_handler",
            managed_attr=(
                "managed_news_command_handler"
            ),
            processor=news_command_processor,
        )

        logger.info(
            "Application resources started",
            extra={
                "event": "application_started"
            },
        )

        yield

    logger.info(
        "Application resources stopped",
        extra={
            "event": "application_stopped"
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    expose_docs = (
        settings.app_env != "production"
    )

    application = FastAPI(
        title="SMS-AI",
        version="0.1.0",
        docs_url=(
            "/docs"
            if expose_docs
            else None
        ),
        redoc_url=None,
        openapi_url=(
            "/openapi.json"
            if expose_docs
            else None
        ),
        lifespan=lifespan,
    )

    application.state.settings = settings
    application.state.runtime_state = RuntimeState()
    application.state.incoming_sms_handler = None
    application.state.managed_incoming_sms_handler = None
    application.state.auth_command_handler = None
    application.state.managed_auth_command_handler = None
    application.state.admin_command_handler = None
    application.state.managed_admin_command_handler = None
    application.state.clear_command_handler = None
    application.state.managed_clear_command_handler = None
    application.state.stat_command_handler = None
    application.state.managed_stat_command_handler = None
    application.state.continue_command_handler = None
    application.state.managed_continue_command_handler = (
        None
    )
    application.state.models_command_handler = None
    application.state.managed_models_command_handler = (
        None
    )
    application.state.latin_command_handler = None
    application.state.managed_latin_command_handler = (
        None
    )
    application.state.help_command_handler = None
    application.state.managed_help_command_handler = (
        None
    )
    application.state.calc_command_handler = None
    application.state.managed_calc_command_handler = (
        None
    )
    application.state.translate_command_handler = None
    application.state.managed_translate_command_handler = (
        None
    )
    application.state.wiki_command_handler = None
    application.state.managed_wiki_command_handler = (
        None
    )
    application.state.weather_command_handler = None
    application.state.managed_weather_command_handler = (
        None
    )
    application.state.currency_command_handler = None
    application.state.managed_currency_command_handler = (
        None
    )
    application.state.news_command_handler = None
    application.state.managed_news_command_handler = (
        None
    )

    application.include_router(health_router)
    application.include_router(
        plusofon_webhook_router
    )

    logger.info(
        "Application configured",
        extra={
            "event": "application_configured",
            "app_env": settings.app_env,
        },
    )

    return application


app = create_app()