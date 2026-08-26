import json
from datetime import date
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.services.plusofon import (
    PlusofonClient,
    PlusofonDeliveryUnknownError,
    PlusofonError,
)


API_BASE_URL = "https://plusofon.example.test/api/v1"
SEND_URL = f"{API_BASE_URL}/sms"
HISTORY_URL = f"{API_BASE_URL}/sms"
DIALOG_URL = f"{API_BASE_URL}/sms/dialog/79991234567"
OWN_NUMBER = "70000000000"
TIMEZONE = ZoneInfo("Asia/Yakutsk")


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
        own_number=OWN_NUMBER,
        default_timezone=TIMEZONE,
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
        "reject_long": True,
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


@pytest.mark.asyncio
async def test_list_messages_paginates_and_maps_fields() -> None:
    page_one = httpx.Response(
        200,
        json={
            "success": True,
            "current_page": 1,
            "data": [
                {
                    "created_datetime": (
                        "2026-08-21 09:59:19"
                    ),
                    "sent_datetime": (
                        "2026-08-21 09:59:21"
                    ),
                    "sender": "70000000000",
                    "receiver": "79991234567",
                    "msg": "первая часть",
                    "incoming": False,
                    "pdu": 2,
                },
            ],
            "next_page_url": (
                f"{HISTORY_URL}?page=2"
            ),
        },
    )
    page_two = httpx.Response(
        200,
        json={
            "success": True,
            "current_page": 2,
            "data": [
                {
                    "created_datetime": (
                        "2026-08-21 10:00:00"
                    ),
                    "sent_datetime": (
                        "2026-08-21 10:00:01"
                    ),
                    "sender": "79991234567",
                    "receiver": "70000000000",
                    "msg": "входящее",
                    "incoming": True,
                    "pdu": 1,
                },
            ],
            "next_page_url": None,
        },
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL, params={
            "date_from": "2026-08-01",
            "date_to": "2026-08-21",
            "incoming": "0",
            "receiver": "79991234567",
        }).mock(return_value=page_one)
        mock.get(f"{HISTORY_URL}?page=2").mock(
            return_value=page_two
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            messages = await client.list_messages(
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 21),
                incoming=False,
                receiver="79991234567",
            )

    assert len(messages) == 2
    assert [m.pdu for m in messages] == [2, 1]
    assert messages[0].incoming is False
    assert messages[1].incoming is True
    assert messages[0].sent_at.tzinfo is not None
    assert sum(m.pdu or 0 for m in messages) == 3


@pytest.mark.asyncio
async def test_get_dialog_derives_direction_and_limit() -> None:
    response = httpx.Response(
        200,
        json={
            "success": True,
            "data": [
                {
                    "id": 1,
                    "jasmax_id": "a",
                    "sender": "79991234567",
                    "receiver": "70000000000",
                    "sent_datetime": (
                        "2026-08-21 09:00:00"
                    ),
                    "created_datetime": (
                        "2026-08-21 09:00:00"
                    ),
                    "msg": "что такое vlan",
                },
                {
                    "id": 2,
                    "jasmax_id": "b",
                    "sender": "70000000000",
                    "receiver": "79991234567",
                    "sent_datetime": (
                        "2026-08-21 09:00:05"
                    ),
                    "created_datetime": (
                        "2026-08-21 09:00:05"
                    ),
                    "msg": "виртуальная сеть",
                },
                {
                    "id": 3,
                    "jasmax_id": "c",
                    "sender": "79991234567",
                    "receiver": "70000000000",
                    "sent_datetime": (
                        "2026-08-21 09:00:10"
                    ),
                    "created_datetime": (
                        "2026-08-21 09:00:10"
                    ),
                    "msg": "спасибо",
                },
            ],
            "next_page_url": None,
        },
    )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=response
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            messages = await client.get_dialog(
                "79991234567",
                2,
            )

    assert len(messages) == 2
    assert messages[0].text == "виртуальная сеть"
    assert messages[0].incoming is False
    assert messages[1].text == "спасибо"
    assert messages[1].incoming is True
    assert messages[0].pdu is None


@pytest.mark.asyncio
async def test_history_rejects_success_false() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": False,
                    "data": [],
                    "next_page_url": None,
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.list_messages()


@pytest.mark.asyncio
async def test_history_rejects_external_next_page_url() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [],
                    "next_page_url": (
                        "https://evil.example.test/steal"
                    ),
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(
                PlusofonError,
                match="Unsafe Plusofon pagination URL",
            ):
                await client.list_messages()

    assert route.call_count == 1


@pytest.mark.asyncio
async def test_history_rejects_pagination_loop() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [],
                    "next_page_url": HISTORY_URL,
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(
                PlusofonError,
                match="pagination loop",
            ):
                await client.list_messages()

    assert route.call_count == 1


@pytest.mark.asyncio
async def test_nullable_sent_datetime_uses_created_datetime() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "created_datetime": (
                                "2026-08-21 09:59:19"
                            ),
                            "sent_datetime": None,
                            "sender": "70000000000",
                            "receiver": "79991234567",
                            "msg": "ожидает отправки",
                            "incoming": 0,
                            "pdu": "2",
                        },
                    ],
                    "next_page_url": None,
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.list_messages()

    assert messages[0].sent_at == messages[0].created_at
    assert messages[0].incoming is False
    assert messages[0].pdu == 2


@pytest.mark.asyncio
async def test_dialog_prefers_incoming_field() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "sender": "70000000000",
                            "receiver": "79991234567",
                            "sent_datetime": None,
                            "created_datetime": (
                                "2026-08-21 09:00:00"
                            ),
                            "msg": "входящий флаг",
                            "incoming": 1,
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.get_dialog(
                "79991234567",
                1,
            )

    assert messages[0].incoming is True
    assert messages[0].sent_at == messages[0].created_at
