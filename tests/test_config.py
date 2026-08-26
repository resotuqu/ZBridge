import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_RUSSIAN_RSS_FEEDS, Settings


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


# --- SSRF: only the built-in feeds' hosts are ever allowed --------------


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/feed",
        "https://127.0.0.1/feed",
        "https://[::1]/feed",
        "https://user@tass.ru/feed",
        "https://example.com/feed",
        "https://tass.ru:8443/feed",
    ],
)
def test_news_rss_feeds_rejects_ssrf_vectors(
    url: str,
) -> None:
    """
    None of these may ever be accepted: a loopback hostname,
    a loopback IPv4/IPv6 literal, a URL carrying userinfo, an
    arbitrary external host, and a non-standard port -- even
    though the last two use a syntactically valid https://
    URL. The only thing that makes a host acceptable is
    appearing in the code-level allowlist derived from
    DEFAULT_RUSSIAN_RSS_FEEDS, not "https + has a netloc".
    """
    with pytest.raises(ValidationError):
        Settings(news_rss_feeds=f"Evil={url}")


def test_news_rss_feeds_accepts_a_valid_allowed_host_url() -> (
    None
):
    settings = Settings(
        news_rss_feeds=(
            "ТАСС-Наука=https://tass.ru/nauka/rss.xml"
        )
    )

    assert settings.news_rss_feeds == (
        (
            "ТАСС-Наука",
            "https://tass.ru/nauka/rss.xml",
        ),
    )


# --- merge semantics: additive, not a full replacement -------------------


def test_effective_news_rss_feeds_defaults_when_unset() -> (
    None
):
    settings = Settings()

    assert (
        settings.effective_news_rss_feeds
        == DEFAULT_RUSSIAN_RSS_FEEDS
    )


def test_effective_news_rss_feeds_adds_a_new_named_feed() -> (
    None
):
    settings = Settings(
        news_rss_feeds=(
            "ЯСИА-2=https://ysia.ru/rss/extra.xml"
        )
    )

    effective = settings.effective_news_rss_feeds

    # every built-in feed is still present, unchanged
    for default_entry in DEFAULT_RUSSIAN_RSS_FEEDS:
        assert default_entry in effective

    assert (
        "ЯСИА-2",
        "https://ysia.ru/rss/extra.xml",
    ) in effective
    assert len(effective) == len(
        DEFAULT_RUSSIAN_RSS_FEEDS
    ) + 1


def test_effective_news_rss_feeds_replaces_only_the_named_source() -> (
    None
):
    settings = Settings(
        news_rss_feeds=(
            "ТАСС=https://tass.ru/rss/other.xml"
        )
    )

    effective = settings.effective_news_rss_feeds

    assert len(effective) == len(
        DEFAULT_RUSSIAN_RSS_FEEDS
    )
    assert (
        "ТАСС",
        "https://tass.ru/rss/other.xml",
    ) in effective

    other_defaults = [
        entry
        for entry in DEFAULT_RUSSIAN_RSS_FEEDS
        if entry[0] != "ТАСС"
    ]

    for entry in other_defaults:
        assert entry in effective


def test_effective_news_rss_feeds_name_match_is_case_insensitive() -> (
    None
):
    settings = Settings(
        news_rss_feeds=(
            "тасс=https://tass.ru/rss/other.xml"
        )
    )

    effective = settings.effective_news_rss_feeds

    assert len(effective) == len(
        DEFAULT_RUSSIAN_RSS_FEEDS
    )
    assert (
        "тасс",
        "https://tass.ru/rss/other.xml",
    ) in effective
