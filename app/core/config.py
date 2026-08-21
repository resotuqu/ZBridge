from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    SettingsConfigDict,
)

from app.core.security import normalize_phone_number, secrets_equal


AllowedPhoneNumbers = Annotated[frozenset[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True
    )

    app_env: Literal[
        "development",
        "test",
        "production",
    ] = "production"

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    timezone: str = "Asia/Yakutsk"

    plusofon_token: SecretStr
    plusofon_client_id: int = Field(default=10553, gt=0)
    plusofon_number_id: int = Field(gt=0)
    plusofon_number: str
    plusofon_webhook_token: SecretStr
    plusofon_api_base_url: str = "https://restapi.plusofon.ru/api/v1"

    gigachat_credentials: SecretStr
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-3-Ultra"
    gigachat_api_base_url: str = (
        "https://api.giga.chat/v1"
    )

    gigachat_oauth_url: str = (
        "https://ngw.devices.sberbank.ru:9443"
        "/api/v2/oauth"
    )

    gigachat_ca_bundle: str | None = None

    allowed_phone_numbers: AllowedPhoneNumbers

    master_pin: SecretStr | None = None
    auth_pin: SecretStr | None = None

    daily_warning_threshold: int = Field(default=5, ge=0)
    max_context_messages: int = Field(default=8, ge=1)

    default_latin_mode: Literal["on", "off"] = "off"

    sms_price_rub: Decimal = Field(
        default=Decimal("2.00"),
        ge=0,
    )

    http_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
    )

    @field_validator("plusofon_number", mode="before")
    @classmethod
    def normalize_service_number(cls, value: object) -> str:
        return normalize_phone_number(str(value))

    @field_validator("allowed_phone_numbers", mode="before")
    @classmethod
    def parse_allowed_phone_numbers(
        cls,
        value: object,
    ) -> frozenset[str]:
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
        else:
            raise ValueError(
                "ALLOWED_PHONE_NUMBERS must be a comma-separated list."
            )

        normalized = frozenset(
            normalize_phone_number(str(item).strip())
            for item in items
            if str(item).strip()
        )

        if not normalized:
            raise ValueError(
                "ALLOWED_PHONE_NUMBERS must contain at least one number."
            )

        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc

        return value

    @model_validator(mode="after")
    def validate_pin_pair(self) -> Settings:
        if self.master_pin is None or self.auth_pin is None:
            return self

        master_pin = self.master_pin.get_secret_value()
        auth_pin = self.auth_pin.get_secret_value()

        if secrets_equal(master_pin, auth_pin):
            raise ValueError(
                "MASTER_PIN and AUTH_PIN must be different."
            )

        return self

    @property
    def timezone_info(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def default_latin_enabled(self) -> bool:
        return self.default_latin_mode == "on"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()