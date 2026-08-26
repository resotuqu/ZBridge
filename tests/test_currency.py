from decimal import Decimal

import httpx
import pytest
import respx

from app.services.currency import (
    CurrencyError,
    CurrencyRate,
    FrankfurterCurrencyProvider,
    format_rate,
    is_valid_currency_code,
)


API_BASE_URL = "https://frankfurter.example.test/v2"
RATE_URL = f"{API_BASE_URL}/rate/USD/RUB"


def build_provider(
    http_client: httpx.AsyncClient,
) -> FrankfurterCurrencyProvider:
    return FrankfurterCurrencyProvider(
        http_client,
        api_base_url=API_BASE_URL,
        retry_delays=(0.0, 0.0),
    )


@pytest.mark.asyncio
async def test_convert_returns_rate() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(RATE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"rates": {"RUB": 92.34}},
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            rate = await provider.convert(
                "USD", "RUB"
            )

    assert rate == CurrencyRate(
        base="USD",
        quote="RUB",
        rate=Decimal("92.34"),
    )
    request = route.calls[0].request
    assert request.headers["Accept"] == (
        "application/json"
    )


@pytest.mark.asyncio
async def test_convert_normalizes_lowercase_codes() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"rates": {"RUB": 92.34}},
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            rate = await provider.convert(
                "usd", "rub"
            )

    assert rate.base == "USD"
    assert rate.quote == "RUB"


@pytest.mark.asyncio
async def test_convert_404_raises_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(404)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_400_raises_error() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(400)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_server_error_raises() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(503)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_invalid_json_raises_error() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
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

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_missing_rate_raises_error() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(
                200, json={"rates": {}}
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_non_numeric_rate_raises_error() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            return_value=httpx.Response(
                200,
                json={"rates": {"RUB": "not-a-number"}},
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_timeout_raises_after_retries() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(CurrencyError):
                await provider.convert("USD", "RUB")


@pytest.mark.asyncio
async def test_convert_retries_transport_errors_then_succeeds() -> (
    None
):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1

        if calls["count"] < 2:
            raise httpx.ConnectError(
                "boom", request=request
            )

        return httpx.Response(
            200, json={"rates": {"RUB": 92.34}}
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(RATE_URL).mock(side_effect=handler)

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            rate = await provider.convert(
                "USD", "RUB"
            )

    assert rate.rate == Decimal("92.34")
    assert calls["count"] == 2


def test_is_valid_currency_code() -> None:
    assert is_valid_currency_code("USD")
    assert is_valid_currency_code("usd")
    assert not is_valid_currency_code("US")
    assert not is_valid_currency_code("USDT4")
    assert not is_valid_currency_code("US1")
    assert not is_valid_currency_code("")


@pytest.mark.parametrize(
    ("raw", "formatted"),
    [
        (Decimal("92.34"), "92.34"),
        (Decimal("92"), "92.00"),
        (Decimal("92.3"), "92.30"),
        (Decimal("0.010800"), "0.0108"),
        (Decimal("0.00000123"), "0.000001"),
    ],
)
def test_format_rate_is_adaptive_without_trailing_zeros(
    raw: Decimal, formatted: str
) -> None:
    assert format_rate(raw) == formatted
