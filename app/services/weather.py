from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.retry import call_with_retries


logger = logging.getLogger(__name__)

_SAFE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadTimeout,
)

_DEFAULT_CONDITION = "переменная облачность"

_WMO_CONDITIONS: dict[int, str] = {
    0: "ясно",
    1: "малооблачно",
    2: "малооблачно",
    3: "облачно",
    45: "туман",
    48: "туман",
    51: "дождь",
    53: "дождь",
    55: "дождь",
    56: "дождь",
    57: "дождь",
    61: "дождь",
    63: "дождь",
    65: "дождь",
    66: "дождь",
    67: "дождь",
    71: "снег",
    73: "снег",
    75: "снег",
    77: "снег",
    80: "дождь",
    81: "дождь",
    82: "дождь",
    85: "снег",
    86: "снег",
    95: "гроза",
    96: "гроза",
    99: "гроза",
}


class WeatherError(RuntimeError):
    pass


class WeatherNotFoundError(WeatherError):
    pass


class WeatherTransientError(WeatherError):
    pass


@dataclass(frozen=True)
class WeatherInfo:
    city: str
    temperature_c: int
    night_min_temperature_c: int
    wind_speed_ms: int
    condition: str


class WeatherProvider(Protocol):
    async def get_weather(self, city: str) -> WeatherInfo: ...


class _GeocodingResult(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    name: str
    latitude: float
    longitude: float


class _GeocodingResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    results: list[_GeocodingResult] | None = None


class _CurrentWeather(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    temperature_2m: float
    weather_code: int
    wind_speed_10m: float


class _DailyWeather(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    temperature_2m_min: list[float]


class _ForecastResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    current: _CurrentWeather
    daily: _DailyWeather


def _round_to_int(value: float) -> int:
    return int(
        Decimal(str(value)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


class OpenMeteoWeatherProvider:
    """
    Thin client for the public Open-Meteo geocoding and
    forecast APIs (no API key required). Isolated from
    webhook/command handling so it stays independently
    unit-testable. GigaChat is never used to interpret
    weather data.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        geocoding_base_url: str = (
            "https://geocoding-api.open-meteo.com/v1"
        ),
        forecast_base_url: str = (
            "https://api.open-meteo.com/v1"
        ),
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._http = http_client
        self._geocoding_base_url = (
            geocoding_base_url.rstrip("/")
        )
        self._forecast_base_url = (
            forecast_base_url.rstrip("/")
        )
        self._retry_delays = retry_delays

    async def get_weather(self, city: str) -> WeatherInfo:
        normalized_city = city.strip()

        if not normalized_city:
            raise WeatherNotFoundError(
                "City must not be blank."
            )

        try:
            return await call_with_retries(
                lambda: self._fetch_once(
                    normalized_city
                ),
                retry_exceptions=(
                    *_SAFE_TRANSPORT_ERRORS,
                    WeatherTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except _SAFE_TRANSPORT_ERRORS as exc:
            raise WeatherTransientError(
                "Could not reach Open-Meteo."
            ) from exc

    async def _fetch_once(
        self, city: str
    ) -> WeatherInfo:
        location = await self._geocode(city)
        return await self._forecast(location)

    async def _geocode(
        self, city: str
    ) -> _GeocodingResult:
        response = await self._http.get(
            f"{self._geocoding_base_url}/search",
            params={
                "name": city,
                "count": 1,
                "language": "ru",
            },
            headers={"Accept": "application/json"},
        )

        self._raise_for_status(response)

        try:
            payload = (
                _GeocodingResponse.model_validate(
                    response.json()
                )
            )
        except (ValueError, ValidationError) as exc:
            raise WeatherError(
                "Invalid geocoding response."
            ) from exc

        if not payload.results:
            raise WeatherNotFoundError(
                "City not found."
            )

        return payload.results[0]

    async def _forecast(
        self, location: _GeocodingResult
    ) -> WeatherInfo:
        response = await self._http.get(
            f"{self._forecast_base_url}/forecast",
            params={
                "latitude": location.latitude,
                "longitude": location.longitude,
                "current": (
                    "temperature_2m,weather_code,"
                    "wind_speed_10m"
                ),
                "daily": "temperature_2m_min",
                "forecast_days": 1,
                "timezone": "auto",
            },
            headers={"Accept": "application/json"},
        )

        self._raise_for_status(response)

        try:
            payload = (
                _ForecastResponse.model_validate(
                    response.json()
                )
            )
        except (ValueError, ValidationError) as exc:
            raise WeatherError(
                "Invalid forecast response."
            ) from exc

        if not payload.daily.temperature_2m_min:
            raise WeatherError(
                "Missing daily forecast."
            )

        condition = _WMO_CONDITIONS.get(
            payload.current.weather_code,
            _DEFAULT_CONDITION,
        )

        return WeatherInfo(
            city=location.name,
            temperature_c=_round_to_int(
                payload.current.temperature_2m
            ),
            night_min_temperature_c=_round_to_int(
                payload.daily.temperature_2m_min[0]
            ),
            wind_speed_ms=_round_to_int(
                payload.current.wind_speed_10m
            ),
            condition=condition,
        )

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
    ) -> None:
        if response.status_code == 404:
            raise WeatherNotFoundError(
                f"Not found: {response.status_code}"
            )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise WeatherTransientError(
                "Open-Meteo temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise WeatherError(
                "Unexpected Open-Meteo status: "
                f"{response.status_code}"
            )

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "Weather request retry",
            extra={
                "event": "weather_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
            },
        )
