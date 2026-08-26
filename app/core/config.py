from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    SettingsConfigDict,
)

from app.core.security import normalize_phone_number, secrets_equal


AllowedPhoneNumbers = Annotated[frozenset[str], NoDecode]
NewsRssFeeds = Annotated[
    tuple[tuple[str, str], ...] | None, NoDecode
]

# The only "news" RSS/Atom sources the service will ever fetch.
# NEWS_RSS_FEEDS (below) can only add another feed on one of these
# exact hosts, or replace the URL of one of the named defaults --
# it can never introduce a new host. Onboarding a genuinely new
# outlet requires changing this code-level allowlist, not just an
# environment variable.
DEFAULT_RUSSIAN_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("ТАСС", "https://tass.ru/rss/v2.xml"),
    (
        "РБК",
        (
            "https://rssexport.rbc.ru/rbcnews/news/"
            "30/full.rss"
        ),
    ),
    ("Лента.ру", "https://lenta.ru/rss"),
    ("ЯСИА", "https://ysia.ru/feed/"),
)

_ALLOWED_NEWS_RSS_HOSTS: frozenset[str] = frozenset(
    urlsplit(url).hostname for _name, url in DEFAULT_RUSSIAN_RSS_FEEDS
)


def _validate_news_feed_url(url: str) -> None:
    parsed = urlsplit(url)

    if parsed.scheme != "https":
        raise ValueError(
            "NEWS_RSS_FEEDS entry must use an https:// "
            f"URL: {url!r}"
        )

    if (
        parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "NEWS_RSS_FEEDS entry must not contain a "
            f"username or password: {url!r}"
        )

    if parsed.port is not None:
        raise ValueError(
            "NEWS_RSS_FEEDS entry must not specify a "
            f"non-standard port: {url!r}"
        )

    hostname = parsed.hostname

    if (
        hostname is None
        or hostname not in _ALLOWED_NEWS_RSS_HOSTS
    ):
        raise ValueError(
            "NEWS_RSS_FEEDS host is not on the allowed "
            f"RSS source allowlist: {url!r}. Adding a "
            "new outlet requires a code change to "
            "DEFAULT_RUSSIAN_RSS_FEEDS, not just this "
            "environment variable."
        )


def _merge_news_rss_feeds(
    overrides: tuple[tuple[str, str], ...] | None,
) -> tuple[tuple[str, str], ...]:
    """
    NEWS_RSS_FEEDS is additive: the built-in defaults are always
    kept. An override entry whose name normalizes to the same
    value as a default replaces only that one default feed (same
    position); any other override name is appended as an extra
    feed. The built-in feeds that aren't named in the override
    never disappear.
    """
    if not overrides:
        return DEFAULT_RUSSIAN_RSS_FEEDS

    merged: dict[str, tuple[str, str]] = {
        name.strip().lower(): (name, url)
        for name, url in DEFAULT_RUSSIAN_RSS_FEEDS
    }
    order: list[str] = list(merged.keys())

    for name, url in overrides:
        key = name.strip().lower()

        if key not in merged:
            order.append(key)

        merged[key] = (name, url)

    return tuple(merged[key] for key in order)


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

    max_inbound_per_minute: int = Field(
        default=5, gt=0
    )
    max_inbound_per_hour: int = Field(
        default=60, gt=0
    )

    default_latin_mode: Literal["on", "off"] = "off"

    sms_price_rub: Decimal = Field(
        default=Decimal("2.00"),
        ge=0,
    )

    http_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
    )

    news_rss_feeds: NewsRssFeeds = None

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

    @field_validator("news_rss_feeds", mode="before")
    @classmethod
    def parse_news_rss_feeds(
        cls,
        value: object,
    ) -> tuple[tuple[str, str], ...] | None:
        if value is None:
            return None

        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple)):
            items = list(value)
        else:
            raise ValueError(
                "NEWS_RSS_FEEDS must be a comma-separated "
                "list of Name=https://url pairs."
            )

        feeds: list[tuple[str, str]] = []

        for raw_item in items:
            item = str(raw_item).strip()

            if not item:
                continue

            if "=" not in item:
                raise ValueError(
                    "Invalid NEWS_RSS_FEEDS entry "
                    "(expected Name=https://url): "
                    f"{item!r}"
                )

            name, _, url = item.partition("=")
            name = name.strip()
            url = url.strip()

            if not name:
                raise ValueError(
                    "NEWS_RSS_FEEDS entry is missing a "
                    "source name."
                )

            _validate_news_feed_url(url)

            feeds.append((name, url))

        if not feeds:
            return None

        return tuple(feeds)

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

    @property
    def effective_news_rss_feeds(
        self,
    ) -> tuple[tuple[str, str], ...]:
        return _merge_news_rss_feeds(self.news_rss_feeds)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()