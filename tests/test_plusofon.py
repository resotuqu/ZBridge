import json
from datetime import datetime
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


def history_response(
    *,
    data: list[dict] | None = None,
    next_page_url: str | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": data or [],
            "next_page_url": next_page_url,
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
        "reject_long": False,
        "count_pdu": True,
    }

    assert result.message_id == "sms-123"
    assert result.pdu_count == 2


@pytest.mark.asyncio
async def test_send_long_sms_as_one_concatenated_submission() -> None:
    long_text = " ".join(["длинный ответ"] * 20)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(SEND_URL).mock(
            return_value=success_response()
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            await client.send("79991234567", long_text)

    assert route.call_count == 1
    payload = json.loads(route.calls[0].request.content)
    assert payload["text"] == long_text
    assert payload["reject_long"] is False
    assert payload["count_pdu"] is True


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


# --- GET /api/v1/sms: JSON-body contract --------------------------------


@pytest.mark.asyncio
async def test_list_messages_sends_filters_as_json_body_not_query_params() -> (
    None
):
    """
    The confirmed Plusofon v1 contract for GET /api/v1/sms takes
    its filters as a JSON request body (client.request("GET", ...,
    json=...)), not query params. No filter may leak into the URL
    query string.
    """
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(HISTORY_URL).mock(
            return_value=history_response()
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            await client.list_messages(
                date_from=datetime(
                    2026, 8, 26, 0, 0, 0, tzinfo=TIMEZONE
                ),
                date_to=datetime(
                    2026, 8, 26, 23, 59, 59, tzinfo=TIMEZONE
                ),
                incoming=False,
                sender=OWN_NUMBER,
                receiver="79991234567",
                limit=100,
            )

    request = route.calls[0].request

    assert request.method == "GET"
    assert request.url.query == b""

    body = json.loads(request.content)

    assert body == {
        "date_from": "2026-08-26",
        "date_to": "2026-08-26",
        "incoming": 0,
        "sender": "70000000000",
        "receiver": "79991234567",
        "limit": 100,
    }
    assert isinstance(body["sender"], str)
    assert isinstance(body["receiver"], str)
    assert isinstance(body["incoming"], int)
    assert isinstance(body["limit"], int)
    assert request.headers["Content-Type"] == (
        "application/json"
    )
    assert request.headers["Authorization"] == (
        "Bearer test-token"
    )
    assert request.headers["Client"] == "10553"


@pytest.mark.asyncio
async def test_list_messages_formats_day_boundaries_exactly() -> (
    None
):
    """
    date_from/date_to are documented as type "date"
    (help.plusofon.ru/api/v1/sms), so only the calendar date is
    sent, as exactly "YYYY-MM-DD" -- resolved in the client's own
    timezone, regardless of what tzinfo the caller's datetime
    carries. A UTC timestamp of 2026-08-25 15:00 is already
    2026-08-26 00:00 in Asia/Yakutsk (UTC+9): if the conversion
    were skipped, this would wrongly come out as "2026-08-25".
    """
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(HISTORY_URL).mock(
            return_value=history_response()
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            await client.list_messages(
                date_from=datetime(
                    2026, 8, 25, 15, 0, 0, tzinfo=ZoneInfo("UTC")
                ),
                date_to=datetime(
                    2026, 8, 26, 14, 59, 59, tzinfo=ZoneInfo("UTC")
                ),
            )

    body = json.loads(route.calls[0].request.content)

    assert body["date_from"] == "2026-08-26"
    assert body["date_to"] == "2026-08-26"


@pytest.mark.asyncio
async def test_list_messages_rejects_naive_datetime_filters() -> (
    None
):
    async with httpx.AsyncClient() as http_client:
        client = build_client(http_client)

        with pytest.raises(ValueError):
            await client.list_messages(
                date_from=datetime(2026, 8, 26, 0, 0, 0)
            )


@pytest.mark.asyncio
async def test_list_messages_paginates_and_sums_pdu() -> None:
    page_one = history_response(
        data=[
            {
                "created_datetime": "2026-08-21 09:59:19",
                "sent_datetime": "2026-08-21 09:59:21",
                "sender": "70000000000",
                "receiver": "79991234567",
                "msg": "первая часть",
                "incoming": False,
                "pdu": 2,
            },
        ],
        next_page_url=f"{HISTORY_URL}?page=2",
    )
    page_two = history_response(
        data=[
            {
                "created_datetime": "2026-08-21 10:00:00",
                "sent_datetime": "2026-08-21 10:00:01",
                "sender": "79991234567",
                "receiver": "70000000000",
                "msg": "входящее",
                "incoming": True,
                "pdu": 1,
            },
        ],
        next_page_url=None,
    )

    responses = iter((page_one, page_two))

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(HISTORY_URL).mock(
            side_effect=lambda request: next(responses)
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            messages = await client.list_messages(
                date_from=datetime(
                    2026, 8, 1, 0, 0, 0, tzinfo=TIMEZONE
                ),
                date_to=datetime(
                    2026, 8, 21, 23, 59, 59, tzinfo=TIMEZONE
                ),
                incoming=False,
                receiver="79991234567",
            )

    assert len(messages) == 2
    assert [m.pdu for m in messages] == [2, 1]
    assert messages[0].incoming is False
    assert messages[1].incoming is True
    assert messages[0].sent_at.tzinfo is not None
    assert sum(m.pdu or 0 for m in messages) == 3

    # next_page_url (".../sms?page=2") is only a pagination
    # cursor -- it carries no date/sender/receiver filters of
    # its own -- so the exact same JSON filter body must be
    # resent on every page, not just the first.
    assert route.call_count == 2

    first_request = route.calls[0].request
    second_request = route.calls[1].request

    first_body = json.loads(first_request.content)
    second_body = json.loads(second_request.content)

    assert first_body
    assert first_body == second_body
    assert str(first_request.url) == HISTORY_URL
    assert str(second_request.url) == (
        f"{HISTORY_URL}?page=2"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_incoming", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
    ],
)
async def test_history_item_incoming_accepts_bool_and_int(
    raw_incoming: object, expected: bool
) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=history_response(
                data=[
                    {
                        "created_datetime": (
                            "2026-08-21 09:59:19"
                        ),
                        "sent_datetime": (
                            "2026-08-21 09:59:19"
                        ),
                        "sender": "70000000000",
                        "receiver": "79991234567",
                        "msg": "текст",
                        "incoming": raw_incoming,
                        "pdu": 1,
                    }
                ]
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.list_messages()

    assert messages[0].incoming is expected


@pytest.mark.asyncio
async def test_list_messages_handles_empty_data() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=history_response(data=[])
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.list_messages()

    assert messages == []


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
async def test_history_rejects_invalid_json() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"not json",
                headers={
                    "Content-Type": "application/json"
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.list_messages()


@pytest.mark.asyncio
async def test_history_rejects_missing_required_field() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    # "data" is missing entirely.
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
            return_value=history_response(
                data=[
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
                ]
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.list_messages()

    assert messages[0].sent_at == messages[0].created_at
    assert messages[0].incoming is False
    assert messages[0].pdu == 2


# --- GET /api/v1/sms/dialog/{number}: distinct, unpaginated ------------


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
    assert messages[1].pdu is None


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


@pytest.mark.asyncio
async def test_dialog_does_not_paginate() -> None:
    """
    The dialog endpoint's response is a plain {data: [...]}
    object without real pagination -- a next_page_url appearing
    in it (if the API ever sent one) must never be followed, and
    only a single request is made.
    """
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "sender": "79991234567",
                            "receiver": "70000000000",
                            "sent_datetime": (
                                "2026-08-21 09:00:00"
                            ),
                            "created_datetime": (
                                "2026-08-21 09:00:00"
                            ),
                            "msg": "привет",
                            "incoming": True,
                        },
                    ],
                    "next_page_url": (
                        f"{DIALOG_URL}?page=2"
                    ),
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.get_dialog(
                "79991234567", 10
            )

    assert len(messages) == 1
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_dialog_rejects_success_false() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={"success": False, "data": []},
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.get_dialog(
                    "79991234567", 10
                )


@pytest.mark.asyncio
async def test_dialog_rejects_invalid_json() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"not json",
                headers={
                    "Content-Type": "application/json"
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.get_dialog(
                    "79991234567", 10
                )


@pytest.mark.asyncio
async def test_dialog_rejects_missing_data_field() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={"success": True},
                # "data" is missing entirely.
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.get_dialog(
                    "79991234567", 10
                )


@pytest.mark.asyncio
async def test_dialog_rejects_missing_success_field() -> (
    None
):
    """
    "success" is a required field on the dialog response, just
    like it is on the list endpoint -- a response that omits it
    entirely must be rejected, not silently treated as success.
    """
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": []},
                # "success" is missing entirely.
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(PlusofonError):
                await client.get_dialog(
                    "79991234567", 10
                )


@pytest.mark.asyncio
async def test_dialog_accepts_success_true_with_empty_data() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={"success": True, "data": []},
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.get_dialog(
                "79991234567", 10
            )

    assert messages == []


@pytest.mark.asyncio
async def test_dialog_accepts_well_formed_success_true_response() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DIALOG_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "sender": "79991234567",
                            "receiver": "70000000000",
                            "sent_datetime": (
                                "2026-08-21 09:00:00"
                            ),
                            "created_datetime": (
                                "2026-08-21 09:00:00"
                            ),
                            "msg": "привет",
                            "incoming": True,
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)
            messages = await client.get_dialog(
                "79991234567", 10
            )

    assert len(messages) == 1
    assert messages[0].text == "привет"
    assert messages[0].incoming is True
