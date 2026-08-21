import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness check",
)
async def ready(
    request: Request,
) -> ReadyResponse:
    required_resources = (
        "settings",
        "runtime_state",
        "http_client",
        "gigachat_client",
        "plusofon_client",
        "incoming_sms_handler",
    )

    missing_resources = [
        name
        for name in required_resources
        if getattr(
            request.app.state,
            name,
            None,
        ) is None
    ]

    if missing_resources:
        logger.error(
            "Application is not ready",
            extra={
                "event": "readiness_failed",
                "missing_resource_count": (
                    len(missing_resources)
                ),
            },
        )

        raise HTTPException(
            status_code=503,
            detail="Service unavailable.",
        )

    return ReadyResponse()