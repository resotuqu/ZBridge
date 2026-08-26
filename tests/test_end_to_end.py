import json

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import create_app


OAUTH_URL = (
    "https://ngw.devices.sberbank.ru:9443"
    "/api/v2/oauth"
)
CHAT_URL = (
    "https://api.giga.chat/v1/chat/completions"
)
SEND_URL = (
    "https://restapi.plusofon.ru/api/v1/sms"
)
DIALOG_URL = (
    "https://restapi.plusofon.ru/api/v1"
    "/sms/dialog/71111111111"
)


def test_webhook_generates_and_sends_reply() -> None:
    application = create_app()

    with respx.mock(
        assert_all_called=True
    ) as mock:
        oauth_route = mock.post(OAUTH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "test-access-token",
                    "expires_at": 4_102_444_800,
                },
            )
        )

        chat_route = mock.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "VLAN — виртуальная "
                                    "локальная сеть."
                                ),
                            }
                        }
                    ]
                },
            )
        )

        send_route = mock.post(SEND_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "id": "sms-123",
                        "pdu": 1,
                    },
                },
            )
        )

        dialog_route = mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [],
                    "next_page_url": None,
                },
            )
        )

        with TestClient(application) as client:
            response = client.post(
                (
                    "/webhooks/plusofon/incoming/"
                    "test-webhook-token"
                ),
                json={
                    "src_number": "71111111111",
                    "dst_number": "70000000000",
                    "content": "Что такое VLAN?",
                    "date": "2026-08-21 22:00:00",
                },
            )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok"
    }

    assert oauth_route.call_count == 1
    assert chat_route.call_count == 1
    assert send_route.call_count == 1
    assert dialog_route.call_count == 1

    send_payload = json.loads(
        send_route.calls[0].request.content
    )

    assert send_payload["to"] == 71111111111
    assert send_payload["text"] == (
        "VLAN — виртуальная локальная сеть."
    )