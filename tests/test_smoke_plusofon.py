from datetime import datetime, timedelta

import httpx
import pytest

from app.core.config import get_settings
from app.services.plusofon import SMSMessage
from scripts.smoke_e2e import find_round_trip
from scripts.smoke_plusofon import (
    build_plusofon_client,
)


def message(
    *,
    created_at: datetime,
    text: str,
    incoming: bool,
) -> SMSMessage:
    return SMSMessage(
        created_at=created_at,
        sent_at=created_at,
        sender=(
            "71111111111"
            if incoming
            else "70000000000"
        ),
        receiver=(
            "70000000000"
            if incoming
            else "71111111111"
        ),
        text=text,
        incoming=incoming,
    )


@pytest.mark.asyncio
async def test_smoke_client_matches_production_constructor() -> None:
    async with httpx.AsyncClient(
        trust_env=False
    ) as http_client:
        client = build_plusofon_client(
            http_client,
            get_settings(),
        )

    assert client is not None


def test_find_round_trip_requires_later_reply() -> None:
    now = datetime.now().astimezone()
    marker = "ZB-E2E-123456. Ответь только OK."
    messages = [
        message(
            created_at=now,
            text=marker,
            incoming=True,
        ),
        message(
            created_at=now + timedelta(seconds=1),
            text="OK",
            incoming=False,
        ),
    ]

    result = find_round_trip(
        messages,
        marker=marker,
        not_before=now - timedelta(seconds=1),
    )

    assert result == (messages[0], messages[1])


def test_find_round_trip_ignores_old_marker() -> None:
    now = datetime.now().astimezone()
    marker = "ZB-E2E-123456. Ответь только OK."
    messages = [
        message(
            created_at=now - timedelta(minutes=5),
            text=marker,
            incoming=True,
        ),
        message(
            created_at=now - timedelta(minutes=4),
            text="OK",
            incoming=False,
        ),
    ]

    result = find_round_trip(
        messages,
        marker=marker,
        not_before=now,
    )

    assert result is None
