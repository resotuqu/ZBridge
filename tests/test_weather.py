import httpx
import pytest
import respx

from app.services.weather import (
    OpenMeteoWeatherProvider,
    WeatherError,
    WeatherInfo,
    WeatherNotFoundError,
)


GEOCODING_BASE_URL = (
    "https://geocoding.example.test/v1"
)
FORECAST_BASE_URL = (
    "https://forecast.example.test/v1"
)
GEOCODING_URL = f"{GEOCODING_BASE_URL}/search"
FORECAST_URL = f"{FORECAST_BASE_URL}/forecast"


def build_provider(
    http_client: httpx.AsyncClient,
) -> OpenMeteoWeatherProvider:
    return OpenMeteoWeatherProvider(
        http_client,
        geocoding_base_url=GEOCODING_BASE_URL,
        forecast_base_url=FORECAST_BASE_URL,
        retry_delays=(0.0, 0.0),
    )


def geocoding_response(
    *, name: str = "Якутск"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "name": name,
                    "latitude": 62.03,
                    "longitude": 129.73,
                }
            ]
        },
    )


def forecast_response(
    *,
    temperature: float = -18.3,
    weather_code: int = 3,
    wind_speed: float = 3.2,
    night_min: float = -23.4,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "current": {
                "temperature_2m": temperature,
                "weather_code": weather_code,
                "wind_speed_10m": wind_speed,
            },
            "daily": {
                "temperature_2m_min": [night_min]
            },
        },
    )


@pytest.mark.asyncio
async def test_get_weather_returns_formatted_info() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            return_value=forecast_response()
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            weather = await provider.get_weather(
                "Якутск"
            )

    assert weather == WeatherInfo(
        city="Якутск",
        temperature_c=-18,
        night_min_temperature_c=-23,
        wind_speed_ms=3,
        condition="облачно",
    )


@pytest.mark.asyncio
async def test_get_weather_passes_geocoding_params() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            return_value=forecast_response()
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            await provider.get_weather("Москва")

    request = route.calls[0].request
    assert request.url.params["name"] == "Москва"
    assert request.url.params["count"] == "1"
    assert request.url.params["language"] == "ru"


@pytest.mark.asyncio
async def test_get_weather_requests_wind_speed_in_ms() -> (
    None
):
    """
    Open-Meteo defaults to wind_speed_unit=kmh; the forecast
    request must explicitly ask for m/s, otherwise the value
    displayed as "м/с" would actually be km/h (~3.6x too
    high).
    """
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        route = mock.get(FORECAST_URL).mock(
            return_value=forecast_response()
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            await provider.get_weather("Якутск")

    request = route.calls[0].request
    assert (
        request.url.params["wind_speed_unit"] == "ms"
    )


@pytest.mark.asyncio
async def test_get_weather_maps_known_wmo_codes() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            return_value=forecast_response(
                weather_code=95
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            weather = await provider.get_weather(
                "Якутск"
            )

    assert weather.condition == "гроза"


@pytest.mark.asyncio
async def test_get_weather_city_not_found() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=httpx.Response(
                200, json={"results": []}
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WeatherNotFoundError):
                await provider.get_weather(
                    "Несуществующгород"
                )


@pytest.mark.asyncio
async def test_get_weather_empty_city_raises_not_found() -> (
    None
):
    async with httpx.AsyncClient() as http_client:
        provider = build_provider(http_client)

        with pytest.raises(WeatherNotFoundError):
            await provider.get_weather("   ")


@pytest.mark.asyncio
async def test_get_weather_invalid_geocoding_json_raises_error() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
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

            with pytest.raises(WeatherError):
                await provider.get_weather("Якутск")


@pytest.mark.asyncio
async def test_get_weather_incomplete_forecast_schema_raises_error() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "current": {
                        "temperature_2m": -18.3
                        # weather_code and
                        # wind_speed_10m are missing
                    },
                    "daily": {
                        "temperature_2m_min": [-23.4]
                    },
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WeatherError):
                await provider.get_weather("Якутск")


@pytest.mark.asyncio
async def test_get_weather_timeout_raises_after_retries() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WeatherError):
                await provider.get_weather("Якутск")


@pytest.mark.asyncio
async def test_get_weather_server_error_raises() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            return_value=httpx.Response(503)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)

            with pytest.raises(WeatherError):
                await provider.get_weather("Якутск")


@pytest.mark.asyncio
async def test_get_weather_retries_transport_errors_then_succeeds() -> (
    None
):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1

        if calls["count"] < 2:
            raise httpx.ConnectError(
                "boom", request=request
            )

        return forecast_response()

    with respx.mock(assert_all_called=True) as mock:
        mock.get(GEOCODING_URL).mock(
            return_value=geocoding_response()
        )
        mock.get(FORECAST_URL).mock(
            side_effect=handler
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_provider(http_client)
            weather = await provider.get_weather(
                "Якутск"
            )

    assert weather.temperature_c == -18
    assert calls["count"] == 2
