import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_news_rss_feeds_defaults_to_none() -> None:
    settings = Settings()

    assert settings.news_rss_feeds is None


def test_news_rss_feeds_parses_valid_pairs() -> None:
    settings = Settings(
        news_rss_feeds=(
            "ТАСС=https://tass.ru/rss/v2.xml,"
            "РБК=https://rssexport.rbc.ru/rbcnews/"
            "news/30/full.rss"
        )
    )

    assert settings.news_rss_feeds == (
        ("ТАСС", "https://tass.ru/rss/v2.xml"),
        (
            "РБК",
            (
                "https://rssexport.rbc.ru/rbcnews/"
                "news/30/full.rss"
            ),
        ),
    )


def test_news_rss_feeds_ignores_blank_entries() -> None:
    settings = Settings(
        news_rss_feeds=(
            "ТАСС=https://tass.ru/rss/v2.xml, , "
        )
    )

    assert settings.news_rss_feeds == (
        ("ТАСС", "https://tass.ru/rss/v2.xml"),
    )


@pytest.mark.parametrize(
    "raw_value",
    [
        "",
        "   ",
        ",,,",
    ],
)
def test_news_rss_feeds_empty_value_falls_back_to_none(
    raw_value: str,
) -> None:
    """
    An empty or all-blank override must resolve to "use the
    built-in defaults", never to an empty allowlist that some
    other code path could quietly repopulate -- and never to
    accepting the raw value as-is.
    """
    settings = Settings(news_rss_feeds=raw_value)

    assert settings.news_rss_feeds is None


@pytest.mark.parametrize(
    "raw_value",
    [
        "no-equals-sign-here",
        "=https://tass.ru/rss/v2.xml",
        "Evil=http://internal.example/admin",
        "Evil=ftp://tass.ru/rss/v2.xml",
        "Evil=file:///etc/passwd",
        "Evil=javascript:alert(1)",
        "Evil=https:///no-host",
        "Evil=https://",
    ],
)
def test_news_rss_feeds_rejects_invalid_or_unsafe_entries(
    raw_value: str,
) -> None:
    """
    A malformed entry, or a non-https:// scheme (the classic
    SSRF vectors: http, file, ftp, javascript, or a URL with
    no host at all) must fail configuration validation loudly
    rather than being silently accepted or silently dropped.
    """
    with pytest.raises(ValidationError):
        Settings(news_rss_feeds=raw_value)


def test_news_rss_feeds_rejects_one_bad_entry_in_a_list() -> (
    None
):
    with pytest.raises(ValidationError):
        Settings(
            news_rss_feeds=(
                "ТАСС=https://tass.ru/rss/v2.xml,"
                "Evil=http://internal.example/x"
            )
        )
