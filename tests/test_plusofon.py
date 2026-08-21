import json

import httpx
import pytest
import respx

from app.services.plusofon import (
    PlusofonClient,
    PlusofonDeliveryUnknownError,
)


API_BASE_URL = "https://plusofon.example.test/api/v1"
SEND_URL = f"{API_BASE_URL}/sms"


def success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "id": "sms-123",
                "pdu": 2,
            },
        },
    )


def build_client(
    http_client: httpx.AsyncClient,
) -> PlusofonClient:
    return PlusofonClient(
        http_client,
        token="test-token",
        client_id=10553,
        number_id=123,
        api_base_url=API_BASE_URL,
        retry_delays=(0.0, 0.0, 0.0),
    )


@pytest.mark.asyncio
async def test_send_sms_uses_expected_request() -> None:
    with respx.mock(
        assert_all_called=True
    ) as mock:
        route = mock.post(SEND_URL).mock(
            return_value=success_response()
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            result = await client.send(
                "+7 999 123-45-67",
                "Короткий ответ.",
            )

    request = route.calls[0].request
    payload = json.loads(request.content)

    assert request.headers["Authorization"] == (
        "Bearer test-token"
    )
    assert request.headers["Client"] == "10553"

    assert payload == {
        "text": "Короткий ответ.",
        "number_id": 123,
        "to": 79991234567,
        "reject_long": False,
        "count_pdu": True,
    }

    assert result.message_id == "sms-123"
    assert result.pdu_count == 2


@pytest.mark.asyncio
async def test_rate_limit_is_retried() -> None:
    responses = iter(
        (
            httpx.Response(429),
            httpx.Response(429),
            success_response(),
        )
    )

    with respx.mock(
        assert_all_called=True
    ) as mock:
        route = mock.post(SEND_URL).mock(
            side_effect=lambda _: next(responses)
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            result = await client.send(
                "79991234567",
                "Короткий ответ.",
            )

    assert route.call_count == 3
    assert result.message_id == "sms-123"


@pytest.mark.asyncio
async def test_server_error_is_not_retried() -> None:
    with respx.mock(
        assert_all_called=True
    ) as mock:
        route = mock.post(SEND_URL).mock(
            return_value=httpx.Response(500)
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(
                PlusofonDeliveryUnknownError
            ):
                await client.send(
                    "79991234567",
                    "Короткий ответ.",
                )

    assert route.call_count == 1