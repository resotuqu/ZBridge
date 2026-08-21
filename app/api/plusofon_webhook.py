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
    secrets_equal,
)
from app.schemas.plusofon import (
    PlusofonIncomingWebhook,
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

    if not is_phone_allowed(
        message.sender,
        settings.allowed_phone_numbers,
    ):
        logger.warning(
            "Unauthorized phone ignored",
            extra={
                "event": "auth_rejected",
                "request_id": request_id,
                "phone": message.sender,
            },
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