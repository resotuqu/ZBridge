from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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

_CODE_RE = re.compile(r"^[A-Za-z]{3}$")


class CurrencyError(RuntimeError):
    pass


class CurrencyTransientError(CurrencyError):
    pass


def is_valid_currency_code(code: str) -> bool:
    return bool(_CODE_RE.match(code))


@dataclass(frozen=True)
class CurrencyRate:
    base: str
    quote: str
    rate: Decimal


class CurrencyProvider(Protocol):
    async def convert(
        self,
        base: str,
        quote: str,
    ) -> CurrencyRate: ...


class _FrankfurterRateResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    rates: dict[str, float] | None = None


def format_rate(value: Decimal) -> str:
    """
    Adaptive 2-6 decimal places, no trailing zeros, but
    never fewer than 2 decimals -- so small rates (e.g.
    RUB -> USD) keep their significant digits instead of
    rounding away to "0.00".
    """
    quantized = value.quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    text = format(quantized, "f")

    if "." not in text:
        return f"{text}.00"

    integer_part, _, fractional_part = text.partition(".")
    fractional_part = fractional_part.rstrip("0")

    if len(fractional_part) < 2:
        fractional_part = fractional_part.ljust(2, "0")

    return f"{integer_part}.{fractional_part}"


class FrankfurterCurrencyProvider:
    """
    Thin client for the public Frankfurter exchange-rate
    API (no API key required). Isolated from webhook/
    command handling so it stays independently unit-
    testable. GigaChat is never used as a source of
    exchange rates.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_base_url: str = (
            "https://api.frankfurter.dev/v2"
        ),
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._http = http_client
        self._api_base_url = api_base_url.rstrip("/")
        self._retry_delays = retry_delays

    async def convert(
        self,
        base: str,
        quote: str,
    ) -> CurrencyRate:
        normalized_base = base.strip().upper()
        normalized_quote = quote.strip().upper()

        if not is_valid_currency_code(
            normalized_base
        ) or not is_valid_currency_code(
            normalized_quote
        ):
            raise CurrencyError(
                "Currency codes must be three letters."
            )

        url = (
            f"{self._api_base_url}/rate/"
            f"{normalized_base}/{normalized_quote}"
        )

        try:
            return await call_with_retries(
                lambda: self._fetch_once(
                    url,
                    normalized_base,
                    normalized_quote,
                ),
                retry_exceptions=(
                    *_SAFE_TRANSPORT_ERRORS,
                    CurrencyTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except _SAFE_TRANSPORT_ERRORS as exc:
            raise CurrencyTransientError(
                "Could not reach Frankfurter."
            ) from exc

    async def _fetch_once(
        self,
        url: str,
        base: str,
        quote: str,
    ) -> CurrencyRate:
        response = await self._http.get(
            url,
            headers={"Accept": "application/json"},
        )

        if response.status_code in (400, 404):
            raise CurrencyError(
                "Unknown currency pair: "
                f"{response.status_code}"
            )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise CurrencyTransientError(
                "Frankfurter temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise CurrencyError(
                "Unexpected Frankfurter status: "
                f"{response.status_code}"
            )

        try:
            payload = (
                _FrankfurterRateResponse.model_validate(
                    response.json()
                )
            )
        except (ValueError, ValidationError) as exc:
            raise CurrencyError(
                "Invalid Frankfurter response."
            ) from exc

        rate_value = (payload.rates or {}).get(quote)

        if rate_value is None:
            raise CurrencyError(
                "Missing exchange rate in response."
            )

        try:
            rate = Decimal(str(rate_value))
        except InvalidOperation as exc:
            raise CurrencyError(
                "Non-numeric exchange rate."
            ) from exc

        return CurrencyRate(
            base=base, quote=quote, rate=rate
        )

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "Currency request retry",
            extra={
                "event": "currency_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
            },
        )
