import inspect
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.services.news import (
    AggregatedNewsProvider,
    GoogleNewsRssProvider,
    GoogleNewsRssSearchProvider,
    NewsItem,
    NewsUnavailableError,
    RssKeywordSearchProvider,
    RussianRssNewsProvider,
    SearchResult,
)


TASS_URL = "https://tass.example.test/rss/v2.xml"
RBC_URL = "https://rbc.example.test/rbcnews/full.rss"
YSIA_URL = "https://ysia.example.test/feed/"
GOOGLE_GENERAL_URL = "https://news.example.test/rss"
GOOGLE_SEARCH_URL = (
    "https://news.example.test/rss/search"
)

FEEDS = (
    ("ТАСС", TASS_URL),
    ("РБК", RBC_URL),
    ("ЯСИА", YSIA_URL),
)


def rss2_feed(
    items: list[dict],
) -> bytes:
    parts = ["<rss version=\"2.0\"><channel>"]
    parts.append("<title>Test feed</title>")

    for item in items:
        parts.append("<item>")
        parts.append(
            f"<title>{item['title']}</title>"
        )

        if "link" in item:
            parts.append(f"<link>{item['link']}</link>")

        if "description" in item:
            parts.append(
                "<description><![CDATA["
                f"{item['description']}"
                "]]></description>"
            )

        if "pub_date" in item:
            parts.append(
                f"<pubDate>{item['pub_date']}</pubDate>"
            )

        for category in item.get("categories", []):
            parts.append(
                f"<category>{category}</category>"
            )

        if "source" in item:
            parts.append(
                f"<source>{item['source']}</source>"
            )

        parts.append("</item>")

    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def atom_feed(entries: list[dict]) -> bytes:
    parts = [
        '<feed xmlns="http://www.w3.org/2005/Atom">'
    ]
    parts.append("<title>Test atom feed</title>")

    for entry in entries:
        parts.append("<entry>")
        parts.append(
            f"<title>{entry['title']}</title>"
        )

        if "link" in entry:
            parts.append(
                f'<link href="{entry["link"]}" '
                'rel="alternate"/>'
            )

        if "summary" in entry:
            parts.append(
                f"<summary>{entry['summary']}</summary>"
            )

        if "updated" in entry:
            parts.append(
                f"<updated>{entry['updated']}</updated>"
            )

        for category in entry.get("categories", []):
            parts.append(
                f'<category term="{category}"/>'
            )

        parts.append("</entry>")

    parts.append("</feed>")
    return "".join(parts).encode("utf-8")


def build_russian_provider(
    http_client: httpx.AsyncClient,
    *,
    feeds: tuple[tuple[str, str], ...] = FEEDS,
) -> RussianRssNewsProvider:
    return RussianRssNewsProvider(
        http_client,
        feeds=feeds,
        retry_delays=(0.0, 0.0),
    )


def build_google_search_provider(
    http_client: httpx.AsyncClient,
) -> GoogleNewsRssSearchProvider:
    return GoogleNewsRssSearchProvider(
        http_client,
        url=GOOGLE_SEARCH_URL,
        retry_delays=(0.0, 0.0),
    )


def build_google_general_provider(
    http_client: httpx.AsyncClient,
) -> GoogleNewsRssProvider:
    return GoogleNewsRssProvider(
        http_client,
        url=GOOGLE_GENERAL_URL,
        retry_delays=(0.0, 0.0),
    )


def empty_rss() -> bytes:
    return rss2_feed([])


# --- RSS2 / Atom parsing --------------------------------------------------


@pytest.mark.asyncio
async def test_russian_provider_parses_rss2() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Заголовок ТАСС",
                            "link": (
                                "https://tass.ru/a1"
                            ),
                            "description": (
                                "Описание новости."
                            ),
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert items == [
        NewsItem(
            title="Заголовок ТАСС",
            source="ТАСС",
            url="https://tass.ru/a1",
            published_at=datetime(
                2026, 3, 25, 7, 0, tzinfo=timezone.utc
            ),
            snippet="Описание новости.",
        )
    ]


@pytest.mark.asyncio
async def test_russian_provider_parses_atom() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=atom_feed(
                    [
                        {
                            "title": "Atom заголовок",
                            "link": (
                                "https://tass.ru/atom1"
                            ),
                            "summary": (
                                "Atom описание."
                            ),
                            "updated": (
                                "2026-03-25T10:00:00Z"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert items == [
        NewsItem(
            title="Atom заголовок",
            source="ТАСС",
            url="https://tass.ru/atom1",
            published_at=datetime(
                2026, 3, 25, 10, 0, tzinfo=timezone.utc
            ),
            snippet="Atom описание.",
        )
    ]


@pytest.mark.asyncio
async def test_html_and_cdata_are_cleaned_from_snippet() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "T",
                            "link": "https://tass.ru/x",
                            "description": (
                                "<p>Текст с "
                                "<b>разметкой</b> "
                                "&amp; сущностями.</p>"
                            ),
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert items[0].snippet == (
        "Текст с разметкой & сущностями."
    )
    assert "<" not in items[0].snippet


# --- topic handling --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_news_without_topic_returns_all_items() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Про космос",
                            "link": (
                                "https://tass.ru/space"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        },
                        {
                            "title": "Про спорт",
                            "link": (
                                "https://tass.ru/sport"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "09:00:00 +0300"
                            ),
                        },
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert {item.title for item in items} == {
        "Про космос",
        "Про спорт",
    }


@pytest.mark.asyncio
async def test_get_news_with_topic_filters_locally() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Про космос",
                            "link": (
                                "https://tass.ru/space"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                            "categories": ["Наука"],
                        },
                        {
                            "title": "Про спорт",
                            "link": (
                                "https://tass.ru/sport"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "09:00:00 +0300"
                            ),
                        },
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(
                "космос", 10
            )

    assert [item.title for item in items] == [
        "Про космос"
    ]


@pytest.mark.asyncio
async def test_keyword_search_provider_delegates_topic() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Apple выпустила",
                            "link": (
                                "https://tass.ru/apple"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            russian_provider = build_russian_provider(
                http_client
            )
            search_provider = RssKeywordSearchProvider(
                russian_provider
            )
            results = await search_provider.search_news(
                "Apple", 10
            )

    assert results == [
        SearchResult(
            title="Apple выпустила",
            source="ТАСС",
            url="https://tass.ru/apple",
            published_at=datetime(
                2026, 3, 25, 7, 0, tzinfo=timezone.utc
            ),
            snippet="d",
        )
    ]


@pytest.mark.asyncio
async def test_google_search_provider_sends_query_param() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(GOOGLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Minecraft news",
                            "link": (
                                "https://example.com/m"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                            "source": "IGN",
                        }
                    ]
                ),
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_google_search_provider(
                http_client
            )
            results = await provider.search_news(
                "Minecraft", 10
            )

    request = route.calls[0].request
    assert request.url.params["q"] == "Minecraft"
    assert request.url.params["hl"] == "ru"
    assert results[0].source == "IGN"


# --- dedup / sort / limit --------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_sort_and_limit() -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Новость 1",
                            "link": (
                                "https://x.ru/1"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "08:00:00 +0300"
                            ),
                        },
                        {
                            # exact duplicate URL
                            "title": "Новость 1 повтор",
                            "link": (
                                "https://x.ru/1"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "08:00:00 +0300"
                            ),
                        },
                        {
                            "title": "Новость 2",
                            "link": (
                                "https://x.ru/2"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "12:00:00 +0300"
                            ),
                        },
                        {
                            "title": "Новость 3",
                            "link": (
                                "https://x.ru/3"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        },
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 2)

    assert [item.title for item in items] == [
        "Новость 2",
        "Новость 3",
    ]


@pytest.mark.asyncio
async def test_dedup_merges_same_story_from_tass_and_rbc() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Общая новость",
                            "link": (
                                "https://wire.example/"
                                "story-1"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Общая новость",
                            "link": (
                                "https://wire.example/"
                                "story-1"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:05:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert len(items) == 1
    assert items[0].source == "ТАСС, РБК"


# --- error handling --------------------------------------------------------


@pytest.mark.asyncio
async def test_single_feed_timeout_is_ignored_others_still_used() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "РБК жива",
                            "link": (
                                "https://rbc.ru/x"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert [item.title for item in items] == [
        "РБК жива"
    ]


@pytest.mark.asyncio
async def test_single_feed_5xx_is_ignored_others_still_used() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(503)
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "РБК жива",
                            "link": (
                                "https://rbc.ru/x"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert [item.title for item in items] == [
        "РБК жива"
    ]


@pytest.mark.asyncio
async def test_invalid_feed_xml_is_ignored_others_still_used() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200, content=b"not xml at all"
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "РБК жива",
                            "link": (
                                "https://rbc.ru/x"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )
            items = await provider.get_news(None, 10)

    assert [item.title for item in items] == [
        "РБК жива"
    ]


@pytest.mark.asyncio
async def test_all_feeds_failing_raises_unavailable() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(503)
        )
        mock.get(RBC_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=b"not xml"
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )

            with pytest.raises(NewsUnavailableError):
                await provider.get_news(None, 10)


@pytest.mark.asyncio
async def test_all_feeds_empty_raises_unavailable() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )

            with pytest.raises(NewsUnavailableError):
                await provider.get_news(None, 10)


# --- resilience: AggregatedNewsProvider ------------------------------------


def build_aggregated_provider(
    http_client: httpx.AsyncClient,
) -> AggregatedNewsProvider:
    russian_provider = build_russian_provider(http_client)
    return AggregatedNewsProvider(
        russian_provider,
        RssKeywordSearchProvider(russian_provider),
        build_google_search_provider(http_client),
        build_google_general_provider(http_client),
    )


@pytest.mark.asyncio
async def test_headline_news_never_calls_google_when_russian_has_enough() -> (
    None
):
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Главная новость",
                            "link": "https://tass.ru/1",
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        # Google routes are intentionally NOT mocked --
        # assert_all_mocked=True makes any request to them
        # fail the test outright.

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news(None, 5)

    assert [item.title for item in items] == [
        "Главная новость"
    ]


@pytest.mark.asyncio
async def test_headline_news_falls_back_to_google_when_russian_empty() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )
        mock.get(GOOGLE_GENERAL_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Google новость",
                            "link": (
                                "https://example.com/g"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news(None, 5)

    assert [item.title for item in items] == [
        "Google новость"
    ]


@pytest.mark.asyncio
async def test_headline_news_available_when_google_down_but_russian_ok() -> (
    None
):
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Российская новость",
                            "link": "https://tass.ru/2",
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        # Google general feed would be down/unreachable --
        # never even mocked, and headline news must still
        # succeed from Russian sources alone.

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news(None, 5)

    assert [item.title for item in items] == [
        "Российская новость"
    ]


@pytest.mark.asyncio
async def test_headline_news_unavailable_when_everything_fails() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )
        mock.get(GOOGLE_GENERAL_URL).mock(
            return_value=httpx.Response(503)
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )

            with pytest.raises(NewsUnavailableError):
                await provider.get_news(None, 5)


@pytest.mark.asyncio
async def test_topic_news_supplements_with_google_when_russian_insufficient() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Apple в России",
                            "link": (
                                "https://tass.ru/apple"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(GOOGLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Apple worldwide",
                            "link": (
                                "https://example.com/a"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "11:00:00 +0300"
                            ),
                            "source": "Reuters",
                        }
                    ]
                ),
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news("Apple", 5)

    titles = {item.title for item in items}
    assert titles == {
        "Apple в России",
        "Apple worldwide",
    }


@pytest.mark.asyncio
async def test_topic_news_does_not_call_google_when_russian_already_fills_limit() -> (
    None
):
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(TASS_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Космос 1",
                            "link": "https://tass.ru/s1",
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                            "categories": ["Космос"],
                        }
                    ]
                ),
            )
        )
        mock.get(RBC_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        mock.get(YSIA_URL).mock(
            return_value=httpx.Response(
                200, content=empty_rss()
            )
        )
        # Google search route is intentionally NOT mocked;
        # assert_all_mocked=True would fail the test if it
        # were called.

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news("Космос", 1)

    assert [item.title for item in items] == [
        "Космос 1"
    ]


@pytest.mark.asyncio
async def test_topic_news_falls_back_to_google_when_russian_has_nothing() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )
        mock.get(GOOGLE_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                content=rss2_feed(
                    [
                        {
                            "title": "Minecraft update",
                            "link": (
                                "https://example.com/mc"
                            ),
                            "description": "d",
                            "pub_date": (
                                "Wed, 25 Mar 2026 "
                                "10:00:00 +0300"
                            ),
                            "source": "IGN",
                        }
                    ]
                ),
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )
            items = await provider.get_news(
                "Minecraft", 5
            )

    assert [item.title for item in items] == [
        "Minecraft update"
    ]


@pytest.mark.asyncio
async def test_topic_news_unavailable_when_everything_fails() -> (
    None
):
    with respx.mock(assert_all_called=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )
        mock.get(GOOGLE_SEARCH_URL).mock(
            side_effect=httpx.ReadTimeout(
                "timed out", request=None
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = build_aggregated_provider(
                http_client
            )

            with pytest.raises(NewsUnavailableError):
                await provider.get_news("Ничего", 5)


# --- SSRF / no-arbitrary-URL safety -----------------------------------


@pytest.mark.asyncio
async def test_topic_text_is_never_fetched_as_a_url() -> (
    None
):
    """
    A topic that looks like a URL must never cause a request
    to that URL -- only the fixed, allowlisted feed URLs are
    ever fetched. assert_all_mocked=True makes respx fail the
    test if any other host is requested.
    """
    suspicious_topic = (
        "https://evil.example.com/steal?x=1"
    )

    with respx.mock(assert_all_mocked=True) as mock:
        for url in (TASS_URL, RBC_URL, YSIA_URL):
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=empty_rss()
                )
            )

        async with httpx.AsyncClient() as http_client:
            provider = build_russian_provider(
                http_client
            )

            with pytest.raises(NewsUnavailableError):
                await provider.get_news(
                    suspicious_topic, 10
                )


# --- no ChatClient dependency ----------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        RussianRssNewsProvider,
        RssKeywordSearchProvider,
        GoogleNewsRssProvider,
        GoogleNewsRssSearchProvider,
        AggregatedNewsProvider,
    ],
)
def test_news_providers_never_depend_on_gigachat(
    cls: type,
) -> None:
    params = inspect.signature(
        cls.__init__
    ).parameters

    assert "chat_client" not in params
    assert "message_router" not in params
