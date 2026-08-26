from __future__ import annotations

import logging
from json import JSONDecodeError
from typing import Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
)

from app.core.config import Settings
from app.core.runtime_state import (
    RuntimeState,
    build_webhook_key,
)
from app.core.security import (
    is_phone_allowed,
    is_sms_loop,
    parse_auth_command,
    parse_model_command,
    secrets_equal,
)
from app.schemas.plusofon import (
    PlusofonIncomingWebhook,
)
from app.services.message_router import (
    is_clear_command,
    is_continue_command,
    is_help_command,
    is_models_command,
    is_stat_command,
    parse_calc_command,
    parse_latin_command,
    parse_translate_command,
    parse_wiki_command,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["plusofon"])


class WebhookAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


@router.post(
    "/webhooks/plusofon/incoming/{webhook_token}",
    response_model=WebhookAcknowledgement,
    summary="Receive an incoming Plusofon SMS",
)
async def receive_incoming_sms(
    webhook_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> WebhookAcknowledgement:
    request_id = str(uuid4())

    settings: Settings = (
        request.app.state.settings
    )
    runtime_state: RuntimeState = (
        request.app.state.runtime_state
    )

    expected_token = (
        settings
        .plusofon_webhook_token
        .get_secret_value()
    )

    if not secrets_equal(
        webhook_token,
        expected_token,
    ):
        logger.warning(
            "Webhook rejected",
            extra={
                "event": "webhook_auth_rejected",
                "request_id": request_id,
            },
        )

        raise HTTPException(
            status_code=404,
            detail="Not Found",
        )

    try:
        raw_payload = await request.json()
    except (
        JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        logger.warning(
            "Invalid webhook JSON",
            extra={
                "event": "webhook_invalid_payload",
                "request_id": request_id,
                "error_type": type(exc).__name__,
            },
        )

        raise HTTPException(
            status_code=422,
            detail="Invalid webhook payload.",
        ) from None

    try:
        payload = (
            PlusofonIncomingWebhook
            .model_validate(raw_payload)
        )
    except ValidationError as exc:
        logger.warning(
            "Invalid webhook payload",
            extra={
                "event": "webhook_invalid_payload",
                "request_id": request_id,
                "error_count": exc.error_count(),
            },
        )

        raise HTTPException(
            status_code=422,
            detail="Invalid webhook payload.",
        ) from None

    message = payload.to_incoming_sms()

    if is_sms_loop(
        message.sender,
        message.recipient,
        settings.plusofon_number,
    ):
        logger.warning(
            "SMS loop or wrong destination ignored",
            extra={
                "event": "webhook_loop_rejected",
                "request_id": request_id,
                "phone": message.sender,
            },
        )

        return WebhookAcknowledgement()

    webhook_key = build_webhook_key(
        src_number=message.sender,
        dst_number=message.recipient,
        received_at=message.received_at,
        content=message.content,
        default_timezone=settings.timezone_info,
    )

    is_duplicate = await (
        runtime_state
        .recent_webhook_keys
        .seen_or_add(webhook_key)
    )

    if is_duplicate:
        logger.info(
            "Duplicate webhook ignored",
            extra={
                "event": "webhook_duplicate",
                "request_id": request_id,
                "phone": message.sender,
            },
        )

        return WebhookAcknowledgement()

    is_authorized = is_phone_allowed(
        message.sender,
        settings.allowed_phone_numbers,
    ) or runtime_state.is_temporarily_authorized(
        message.sender
    )

    if not is_authorized:
        pin_candidate = parse_auth_command(
            message.content
        )

        if (
            pin_candidate is not None
            and settings.auth_pin is not None
            and secrets_equal(
                pin_candidate,
                settings.auth_pin.get_secret_value(),
            )
        ):
            runtime_state.authorize_temporarily(
                message.sender
            )

            logger.info(
                "Phone temporarily authorized",
                extra={
                    "event": "auth_success",
                    "request_id": request_id,
                    "phone": message.sender,
                },
            )

            auth_handler = getattr(
                request.app.state,
                "auth_command_handler",
                None,
            )

            if auth_handler is not None:
                background_tasks.add_task(
                    auth_handler,
                    message,
                    request_id,
                )

            return WebhookAcknowledgement()

        logger.warning(
            "Unauthorized phone ignored",
            extra={
                "event": "auth_rejected",
                "request_id": request_id,
                "phone": message.sender,
            },
        )

        return WebhookAcknowledgement()

    model_command = parse_model_command(
        message.content
    )

    if model_command is not None:
        pin_candidate, model_name = model_command

        if (
            settings.master_pin is not None
            and secrets_equal(
                pin_candidate,
                settings.master_pin.get_secret_value(),
            )
        ):
            logger.info(
                "Admin model command routed",
                extra={
                    "event": "command_routed",
                    "request_id": request_id,
                    "phone": message.sender,
                    "command": "model",
                },
            )

            admin_handler = getattr(
                request.app.state,
                "admin_command_handler",
                None,
            )

            if admin_handler is not None:
                background_tasks.add_task(
                    admin_handler,
                    message,
                    request_id,
                    model_name,
                )

            return WebhookAcknowledgement()

        logger.warning(
            "Admin command rejected",
            extra={
                "event": "admin_command_rejected",
                "request_id": request_id,
                "phone": message.sender,
            },
        )

        return WebhookAcknowledgement()

    if is_clear_command(message.content):
        boundary = message.received_at

        if boundary.tzinfo is None:
            boundary = boundary.replace(
                tzinfo=settings.timezone_info
            )

        runtime_state.set_context_boundary(
            message.sender,
            boundary,
        )

        logger.info(
            "Context cleared",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "clear",
            },
        )

        clear_handler = getattr(
            request.app.state,
            "clear_command_handler",
            None,
        )

        if clear_handler is not None:
            background_tasks.add_task(
                clear_handler,
                message,
                request_id,
            )

        return WebhookAcknowledgement()

    if is_stat_command(message.content):
        logger.info(
            "Stat command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "stat",
            },
        )

        stat_handler = getattr(
            request.app.state,
            "stat_command_handler",
            None,
        )

        if stat_handler is not None:
            background_tasks.add_task(
                stat_handler,
                message,
                request_id,
            )

        return WebhookAcknowledgement()

    if is_models_command(message.content):
        logger.info(
            "Models command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "models",
            },
        )

        models_handler = getattr(
            request.app.state,
            "models_command_handler",
            None,
        )

        if models_handler is not None:
            background_tasks.add_task(
                models_handler,
                message,
                request_id,
            )

        return WebhookAcknowledgement()

    latin_command = parse_latin_command(
        message.content
    )

    if latin_command is not None:
        runtime_state.set_latin_mode(
            message.sender,
            latin_command,
        )

        logger.info(
            "Latin mode changed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "latin",
            },
        )

        latin_handler = getattr(
            request.app.state,
            "latin_command_handler",
            None,
        )

        if latin_handler is not None:
            background_tasks.add_task(
                latin_handler,
                message,
                request_id,
                latin_command,
            )

        return WebhookAcknowledgement()

    if is_help_command(message.content):
        logger.info(
            "Help command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "help",
            },
        )

        help_handler = getattr(
            request.app.state,
            "help_command_handler",
            None,
        )

        if help_handler is not None:
            background_tasks.add_task(
                help_handler,
                message,
                request_id,
            )

        return WebhookAcknowledgement()

    calc_expression = parse_calc_command(
        message.content
    )

    if calc_expression is not None:
        logger.info(
            "Calc command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "calc",
            },
        )

        calc_handler = getattr(
            request.app.state,
            "calc_command_handler",
            None,
        )

        if calc_handler is not None:
            background_tasks.add_task(
                calc_handler,
                message,
                request_id,
                calc_expression,
            )

        return WebhookAcknowledgement()

    translate_argument = parse_translate_command(
        message.content
    )

    if translate_argument is not None:
        logger.info(
            "Translate command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "translate",
            },
        )

        translate_handler = getattr(
            request.app.state,
            "translate_command_handler",
            None,
        )

        if translate_handler is not None:
            background_tasks.add_task(
                translate_handler,
                message,
                request_id,
                translate_argument,
            )

        return WebhookAcknowledgement()

    wiki_topic = parse_wiki_command(
        message.content
    )

    if wiki_topic is not None:
        logger.info(
            "Wiki command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "wiki",
            },
        )

        wiki_handler = getattr(
            request.app.state,
            "wiki_command_handler",
            None,
        )

        if wiki_handler is not None:
            background_tasks.add_task(
                wiki_handler,
                message,
                request_id,
                wiki_topic,
            )

        return WebhookAcknowledgement()

    if is_continue_command(message.content):
        logger.info(
            "Continue command routed",
            extra={
                "event": "command_routed",
                "request_id": request_id,
                "phone": message.sender,
                "command": "continue",
            },
        )

        continue_handler = getattr(
            request.app.state,
            "continue_command_handler",
            None,
        )

        if continue_handler is not None:
            background_tasks.add_task(
                continue_handler,
                message,
                request_id,
            )

        return WebhookAcknowledgement()

    handler = getattr(
        request.app.state,
        "incoming_sms_handler",
        None,
    )

    if handler is None:
        logger.warning(
            "Incoming SMS processor is not configured",
            extra={
                "event": "sms_processor_unavailable",
                "request_id": request_id,
                "phone": message.sender,
            },
        )

        return WebhookAcknowledgement()

    background_tasks.add_task(
        handler,
        message,
        request_id,
    )

    logger.info(
        "Webhook accepted",
        extra={
            "event": "webhook_received",
            "request_id": request_id,
            "phone": message.sender,
        },
    )

    return WebhookAcknowledgement()