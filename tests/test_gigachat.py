from collections.abc import Iterator

import httpx
import pytest
import respx

from app.schemas.messages import (
    ChatMessage,
    ChatRole,
)
from app.services.gigachat import (
    GigaChatClient,
    GigaChatError,
)


OAUTH_URL = (
    "https://oauth.example.test"
    "/api/v2/oauth"
)

API_BASE_URL = (
    "https://api.example.test/v1"
)

CHAT_URL = (
    f"{API_BASE_URL}/chat/completions"
)

MODELS_URL = f"{API_BASE_URL}/models"

FUTURE_EXPIRATION = 4_102_444_800


def oauth_response(
    token: str = "token-one",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": token,
            "expires_at": FUTURE_EXPIRATION,
        },
    )


def chat_response(
    text: str = "Короткий ответ.",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": text,
                    }
                }
            ]
        },
    )


def build_client(
    http_client: httpx.AsyncClient,
) -> GigaChatClient:
    return GigaChatClient(
        http_client,
        credentials="test-credentials",
        scope="GIGACHAT_API_PERS",
        api_base_url=API_BASE_URL,
        oauth_url=OAUTH_URL,
        retry_delays=(0.0, 0.0),
    )


def user_messages() -> list[ChatMessage]:
    return [
        ChatMessage(
            role=ChatRole.USER,
            content="Что такое VLAN?",
        )
    ]


@pytest.mark.asyncio
async def test_oauth_token_is_cached() -> None:
    with respx.mock(
        assert_all_called=True
    ) as mock:
        oauth_route = mock.post(
            OAUTH_URL
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": (
                        "token-one"
                    ),
                    "expires_at": (
                        FUTURE_EXPIRATION
                    ),
                },
            )
        )

        chat_route = mock.post(
            CHAT_URL
        ).mock(
            return_value=chat_response()
        )

        async with (
            httpx.AsyncClient()
            as http_client
        ):
            client = build_client(
                http_client
            )

            first = await client.chat(
                user_messages(),
                model="GigaChat-3-Ultra",
            )

            second = await client.chat(
                user_messages(),
                model="GigaChat-3-Ultra",
            )

    assert first == "Короткий ответ."
    assert second == "Короткий ответ."
    assert oauth_route.call_count == 1
    assert chat_route.call_count == 2


@pytest.mark.asyncio
async def test_401_refreshes_token_once() -> None:
    tokens: Iterator[str] = iter(
        (
            "token-one",
            "token-two",
        )
    )

    def oauth_handler(
        _: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": next(tokens),
                "expires_at": (
                    FUTURE_EXPIRATION
                ),
            },
        )

    def chat_handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if (
            request.headers[
                "Authorization"
            ]
            == "Bearer token-one"
        ):
            return httpx.Response(401)

        return chat_response(
            "После обновления токена."
        )

    with respx.mock(
        assert_all_called=True
    ) as mock:
        oauth_route = mock.post(
            OAUTH_URL
        ).mock(
            side_effect=oauth_handler
        )

        chat_route = mock.post(
            CHAT_URL
        ).mock(
            side_effect=chat_handler
        )

        async with (
            httpx.AsyncClient()
            as http_client
        ):
            client = build_client(
                http_client
            )

            result = await client.chat(
                user_messages(),
                model="GigaChat-3-Ultra",
            )

    assert (
        result
        == "После обновления токена."
    )
    assert oauth_route.call_count == 2
    assert chat_route.call_count == 2


@pytest.mark.asyncio
async def test_transient_chat_error_is_retried() -> None:
    responses = iter(
        (
            httpx.Response(500),
            httpx.Response(503),
            chat_response(
                "После повторов."
            ),
        )
    )

    with respx.mock(
        assert_all_called=True
    ) as mock:
        mock.post(
            OAUTH_URL
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": (
                        "token-one"
                    ),
                    "expires_at": (
                        FUTURE_EXPIRATION
                    ),
                },
            )
        )

        chat_route = mock.post(
            CHAT_URL
        ).mock(
            side_effect=(
                lambda _: next(responses)
            )
        )

        async with (
            httpx.AsyncClient()
            as http_client
        ):
            client = build_client(
                http_client
            )

            result = await client.chat(
                user_messages(),
                model="GigaChat-3-Ultra",
            )

    assert result == "После повторов."
    assert chat_route.call_count == 3


@pytest.mark.asyncio
async def test_list_models_parses_response_and_uses_bearer_token() -> None:
    with respx.mock(assert_all_called=True) as mock:
        oauth_route = mock.post(OAUTH_URL).mock(
            return_value=oauth_response("token-one")
        )

        models_route = mock.get(MODELS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "GigaChat-2",
                            "object": "model",
                            "owned_by": "provider",
                        },
                        {
                            "id": "GigaChat-2-Pro",
                            "object": "model",
                            "owned_by": "provider",
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            models = await client.list_models()

    assert [model.id for model in models] == [
        "GigaChat-2",
        "GigaChat-2-Pro",
    ]
    assert oauth_route.call_count == 1
    assert models_route.call_count == 1

    request = models_route.calls[0].request
    assert (
        request.headers["Authorization"]
        == "Bearer token-one"
    )


@pytest.mark.asyncio
async def test_list_models_refreshes_token_after_401() -> None:
    tokens: Iterator[str] = iter(
        ("token-one", "token-two")
    )

    def oauth_handler(
        _: httpx.Request,
    ) -> httpx.Response:
        return oauth_response(next(tokens))

    def models_handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if (
            request.headers["Authorization"]
            == "Bearer token-one"
        ):
            return httpx.Response(401)

        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "GigaChat-2-Pro",
                        "object": "model",
                        "owned_by": "provider",
                    },
                ],
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        oauth_route = mock.post(OAUTH_URL).mock(
            side_effect=oauth_handler
        )

        models_route = mock.get(MODELS_URL).mock(
            side_effect=models_handler
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            models = await client.list_models()

    assert [model.id for model in models] == [
        "GigaChat-2-Pro"
    ]
    assert oauth_route.call_count == 2
    assert models_route.call_count == 2


@pytest.mark.asyncio
async def test_list_models_invalid_schema_raises_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post(OAUTH_URL).mock(
            return_value=oauth_response()
        )

        mock.get(MODELS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"unexpected": "shape"},
            )
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(GigaChatError):
                await client.list_models()


@pytest.mark.asyncio
async def test_list_models_final_error_raises_gigachat_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.post(OAUTH_URL).mock(
            return_value=oauth_response()
        )

        mock.get(MODELS_URL).mock(
            return_value=httpx.Response(403)
        )

        async with httpx.AsyncClient() as http_client:
            client = build_client(http_client)

            with pytest.raises(GigaChatError):
                await client.list_models()