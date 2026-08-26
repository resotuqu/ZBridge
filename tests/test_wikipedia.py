import httpx
import pytest
import respx

from app.services.wikipedia import (
    WikipediaError,
    WikipediaNotFoundError,
    WikipediaProvider,
)


API_BASE_URL = "https://ru.wikipedia.example.test/api/rest_v1"
SUMMARY_URL = f"{API_BASE_URL}/page/summary/DHCP"


def build_provider(
    http_client: httpx.AsyncClient,
) -> WikipediaProvider:
    return WikipediaProvider(
        http_client,
        api_base_url=API_BASE_URL,
        retry_delays=(0.0, 0.0),
    )


@pytest.mark.asyncio
async def test_get_summary_returns_extract() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(SUMMARY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "title": "DHCP",
                    "extract": (
                        "DHCP — протокол динамической "
                        "настройки узла."
                    ),
                    "type": "standard",
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            summary = await provider.get_summary("DHCP")

    assert summary == (
        "DHCP — протокол динамической настройки узла."
    )
    assert route.call_count == 1

    request = route.calls[0].request
    assert request.headers["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_get_summary_encodes_spaces_in_topic() -> None:
    url = f"{API_BASE_URL}/page/summary/Виртуальная_машина"

    with respx.mock(assert_all_called=True) as mock:
        mock.get(url).mock(
            return_value=httpx.Response(
                200,
                json={"extract": "Краткое описание."},
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            summary = await provider.get_summary(
                "Виртуальная машина"
            )

    assert summary == "Краткое описание."


@pytest.mark.asyncio
async def test_get_summary_empty_topic_raises_not_found() -> None:
    async with httpx.AsyncClient() as http_client:
        provider = build_provider(http_client)

        with pytest.raises(WikipediaNotFoundError):
            await provider.get_summary("   ")


@pytest.mark.asyncio
async def test_get_summary_404_raises_not_found() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(
            return_value=httpx.Response(404)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WikipediaNotFoundError):
                await provider.get_summary("DHCP")


@pytest.mark.asyncio
async def test_get_summary_empty_extract_raises_not_found() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(
            return_value=httpx.Response(
                200,
                json={"extract": "   "},
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WikipediaNotFoundError):
                await provider.get_summary("DHCP")


@pytest.mark.asyncio
async def test_get_summary_invalid_json_raises_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(
            return_value=httpx.Response(
                200,
                content=b"not json",
                headers={
                    "Content-Type": "application/json"
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WikipediaError):
                await provider.get_summary("DHCP")


@pytest.mark.asyncio
async def test_get_summary_retries_transport_errors() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1

        if calls["count"] < 2:
            raise httpx.ConnectError(
                "boom", request=request
            )

        return httpx.Response(
            200,
            json={"extract": "После повтора."},
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(side_effect=handler)

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            summary = await provider.get_summary("DHCP")

    assert summary == "После повтора."
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_get_summary_timeout_raises_after_retries() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WikipediaError):
                await provider.get_summary("DHCP")


@pytest.mark.asyncio
async def test_get_summary_server_error_raises() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUMMARY_URL).mock(
            return_value=httpx.Response(503)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WikipediaError):
                await provider.get_summary("DHCP")
