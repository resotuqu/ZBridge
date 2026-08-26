from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.runtime_state import (
    TTLKeyCache,
    build_webhook_key,
)
from app.main import create_app
from app.schemas.messages import IncomingSMS
from app.schemas.plusofon import (
    PlusofonIncomingWebhook,
)


WEBHOOK_TOKEN = "test-webhook-token"


def valid_payload() -> dict[str, str]:
    return {
        "src_number": "71111111111",
        "dst_number": "70000000000",
        "content": "Что такое VLAN?",
        "date": "2026-08-21 22:00:00",
    }


@pytest.fixture
def webhook_app() -> FastAPI:
    return create_app()


def test_plusofon_payload_is_normalized() -> None:
    payload = (
        PlusofonIncomingWebhook
        .model_validate(
            {
                "src_number": (
                    "+7 999 123-45-67"
                ),
                "dst_number": (
                    "8 999 000-00-00"
                ),
                "content": (
                    "Что такое VLAN?"
                ),
                "date": (
                    "2026-08-21 22:00:00"
                ),
                "future_field": "ignored",
            }
        )
    )

    assert (
        payload.src_number
        == "79991234567"
    )
    assert (
        payload.dst_number
        == "79990000000"
    )
    assert (
        payload.content
        == "Что такое VLAN?"
    )


def test_webhook_key_is_deterministic() -> None:
    arguments = {
        "src_number": "79991234567",
        "dst_number": "79990000000",
        "received_at": datetime(
            2026,
            8,
            21,
            22,
            0,
            0,
        ),
        "content": "Что такое VLAN?",
        "default_timezone": ZoneInfo(
            "Asia/Yakutsk"
        ),
    }

    first_key = build_webhook_key(**arguments)
    second_key = build_webhook_key(**arguments)

    assert first_key == second_key


@pytest.mark.asyncio
async def test_ttl_cache_detects_duplicate_and_expires() -> None:
    current_time = [0.0]

    cache = TTLKeyCache(
        ttl_seconds=10,
        clock=lambda: current_time[0],
    )

    assert not await cache.seen_or_add("key")
    assert await cache.seen_or_add("key")

    current_time[0] = 11.0

    assert not await cache.seen_or_add("key")


def test_invalid_webhook_token_returns_404(
    webhook_app: FastAPI,
) -> None:
    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                "incoming/wrong-token"
            ),
            json=valid_payload(),
        )

    assert response.status_code == 404


def test_invalid_payload_returns_422(
    webhook_app: FastAPI,
) -> None:
    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json={
                "src_number": "71111111111"
            },
        )

    assert response.status_code == 422


def test_unknown_phone_is_silently_ignored(
    webhook_app: FastAPI,
) -> None:
    payload = valid_payload()
    payload["src_number"] = "72222222222"

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sms_loop_is_silently_ignored(
    webhook_app: FastAPI,
) -> None:
    payload = valid_payload()
    payload["src_number"] = "70000000000"

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_webhook_is_processed_once(
    webhook_app: FastAPI,
) -> None:
    received: list[
        tuple[IncomingSMS, str]
    ] = []

    async def handler(
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        received.append(
            (message, request_id)
        )

    webhook_app.state.incoming_sms_handler = (
        handler
    )

    with TestClient(webhook_app) as client:
        first_response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=valid_payload(),
        )

        duplicate_response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=valid_payload(),
        )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert len(received) == 1
    assert (
        received[0][0].sender
        == "71111111111"
    )


def test_unknown_phone_with_correct_auth_pin_is_authorized(
    webhook_app: FastAPI,
) -> None:
    auth_received: list[
        tuple[IncomingSMS, str]
    ] = []
    sms_received: list[
        tuple[IncomingSMS, str]
    ] = []

    async def auth_handler(
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        auth_received.append(
            (message, request_id)
        )

    async def sms_handler(
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        sms_received.append(
            (message, request_id)
        )

    webhook_app.state.auth_command_handler = (
        auth_handler
    )
    webhook_app.state.incoming_sms_handler = (
        sms_handler
    )

    auth_payload = valid_payload()
    auth_payload["src_number"] = "72222222222"
    auth_payload["content"] = "9999 auth"

    follow_up_payload = valid_payload()
    follow_up_payload["src_number"] = (
        "72222222222"
    )

    with TestClient(webhook_app) as client:
        auth_response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=auth_payload,
        )

        follow_up_response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=follow_up_payload,
        )

    assert auth_response.status_code == 200
    assert (
        follow_up_response.status_code == 200
    )
    assert len(auth_received) == 1
    assert (
        auth_received[0][0].sender
        == "72222222222"
    )
    assert len(sms_received) == 1
    assert (
        sms_received[0][0].sender
        == "72222222222"
    )


def test_unknown_phone_with_wrong_auth_pin_is_ignored(
    webhook_app: FastAPI,
) -> None:
    auth_received: list[
        tuple[IncomingSMS, str]
    ] = []

    async def auth_handler(
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        auth_received.append(
            (message, request_id)
        )

    webhook_app.state.auth_command_handler = (
        auth_handler
    )

    payload = valid_payload()
    payload["src_number"] = "72222222222"
    payload["content"] = "0000 auth"

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert auth_received == []


def test_authorized_phone_with_correct_master_pin_changes_model(
    webhook_app: FastAPI,
) -> None:
    admin_received: list[
        tuple[IncomingSMS, str, str]
    ] = []

    async def admin_handler(
        message: IncomingSMS,
        request_id: str,
        model: str,
    ) -> None:
        admin_received.append(
            (message, request_id, model)
        )

    webhook_app.state.admin_command_handler = (
        admin_handler
    )

    payload = valid_payload()
    payload["content"] = (
        "8241 model GigaChat-2-Pro"
    )

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert len(admin_received) == 1
    assert (
        admin_received[0][0].sender
        == "71111111111"
    )
    assert admin_received[0][2] == "GigaChat-2-Pro"
    assert (
        webhook_app.state.runtime_state
        .get_selected_model("71111111111")
        == "GigaChat-2-Pro"
    )


def test_authorized_phone_with_wrong_master_pin_is_ignored(
    webhook_app: FastAPI,
) -> None:
    admin_received: list[
        tuple[IncomingSMS, str, str]
    ] = []
    sms_received: list[
        tuple[IncomingSMS, str]
    ] = []

    async def admin_handler(
        message: IncomingSMS,
        request_id: str,
        model: str,
    ) -> None:
        admin_received.append(
            (message, request_id, model)
        )

    async def sms_handler(
        message: IncomingSMS,
        request_id: str,
    ) -> None:
        sms_received.append(
            (message, request_id)
        )

    webhook_app.state.admin_command_handler = (
        admin_handler
    )
    webhook_app.state.incoming_sms_handler = (
        sms_handler
    )

    payload = valid_payload()
    payload["content"] = (
        "0000 model GigaChat-2-Pro"
    )

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert admin_received == []
    assert sms_received == []
    assert (
        webhook_app.state.runtime_state
        .get_selected_model("71111111111")
        is None
    )


def test_unknown_phone_cannot_use_master_pin(
    webhook_app: FastAPI,
) -> None:
    admin_received: list[
        tuple[IncomingSMS, str, str]
    ] = []

    async def admin_handler(
        message: IncomingSMS,
        request_id: str,
        model: str,
    ) -> None:
        admin_received.append(
            (message, request_id, model)
        )

    webhook_app.state.admin_command_handler = (
        admin_handler
    )

    payload = valid_payload()
    payload["src_number"] = "73333333333"
    payload["content"] = (
        "8241 model GigaChat-2-Pro"
    )

    with TestClient(webhook_app) as client:
        response = client.post(
            (
                "/webhooks/plusofon/"
                f"incoming/{WEBHOOK_TOKEN}"
            ),
            json=payload,
        )

    assert response.status_code == 200
    assert admin_received == []