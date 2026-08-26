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
from app.services.message_router import (
    AdminCommandProcessor,
    AuthCommandProcessor,
    IncomingSMSProcessor,
    MessageRouter,
)
from app.services.plusofon import PlusofonClient


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
        )

        message_router = MessageRouter(
            gigachat_client,
            model=settings.gigachat_model,
        )

        sms_processor = IncomingSMSProcessor(
            message_router,
            plusofon_client,
            application.state.runtime_state,
        )

        auth_command_processor = AuthCommandProcessor(
            plusofon_client
        )

        admin_command_processor = AdminCommandProcessor(
            plusofon_client
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
        application.state.sms_processor = (
            sms_processor
        )
        application.state.auth_command_processor = (
            auth_command_processor
        )
        application.state.admin_command_processor = (
            admin_command_processor
        )

        current_handler = (
            application.state.incoming_sms_handler
        )
        managed_handler = (
            application
            .state
            .managed_incoming_sms_handler
        )

        if (
            current_handler is None
            or current_handler is managed_handler
        ):
            application.state.incoming_sms_handler = (
                sms_processor
            )

        application.state.managed_incoming_sms_handler = (
            sms_processor
        )

        current_auth_handler = (
            application.state.auth_command_handler
        )
        managed_auth_handler = (
            application
            .state
            .managed_auth_command_handler
        )

        if (
            current_auth_handler is None
            or current_auth_handler
            is managed_auth_handler
        ):
            application.state.auth_command_handler = (
                auth_command_processor
            )

        application.state.managed_auth_command_handler = (
            auth_command_processor
        )

        current_admin_handler = (
            application.state.admin_command_handler
        )
        managed_admin_handler = (
            application
            .state
            .managed_admin_command_handler
        )

        if (
            current_admin_handler is None
            or current_admin_handler
            is managed_admin_handler
        ):
            application.state.admin_command_handler = (
                admin_command_processor
            )

        application.state.managed_admin_command_handler = (
            admin_command_processor
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