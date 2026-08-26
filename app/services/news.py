from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from dateutil import parser as dateutil_parser

from app.services.retry import call_with_retries


logger = logging.getLogger(__name__)

_SAFE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ReadTimeout,
)

# A hard, small cap on how many raw entries are kept per feed,
# before merging/dedup/sort/limit -- per-source noise never grows
# unbounded regardless of how large a feed is.
_MAX_ENTRIES_PER_FEED = 15

GOOGLE_NEWS_GENERAL_URL = "https://news.google.com/rss"
GOOGLE_NEWS_SEARCH_URL = (
    "https://news.google.com/rss/search"
)
_GOOGLE_NEWS_PARAMS = {
    "hl": "ru",
    "gl": "RU",
    "ceid": "RU:ru",
}

# Built-in allowlist -- never fetched from SMS input or from links
# discovered inside RSS content, only ever these fixed, operator-
# configured HTTPS endpoints (or the validated NEWS_RSS_FEEDS
# override wired in app/main.py).
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


class NewsError(RuntimeError):
    pass


class NewsTransientError(NewsError):
    pass


class NewsUnavailableError(NewsError):
    """No usable, fresh items were found after trying every
    configured source."""


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    url: str | None
    published_at: datetime | None
    snippet: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    source: str
    url: str | None
    published_at: datetime | None
    snippet: str


class NewsProvider(Protocol):
    async def get_news(
        self,
        topic: str | None,
        limit: int,
    ) -> list[NewsItem]: ...


class SearchProvider(Protocol):
    async def search_news(
        self,
        query: str,
        limit: int,
    ) -> list[SearchResult]: ...


def _news_item_to_search_result(
    item: NewsItem,
) -> SearchResult:
    return SearchResult(
        title=item.title,
        source=item.source,
        url=item.url,
        published_at=item.published_at,
        snippet=item.snippet,
    )


def _search_result_to_news_item(
    result: SearchResult,
) -> NewsItem:
    return NewsItem(
        title=result.title,
        source=result.source,
        url=result.url,
        published_at=result.published_at,
        snippet=result.snippet,
    )


# --- RSS 2.0 / Atom parsing (shared) ---------------------------------


@dataclass(frozen=True)
class _ParsedEntry:
    title: str
    link: str | None
    snippet: str
    published_at: datetime | None
    categories: tuple[str, ...] = ()
    source_override: str | None = None

    def to_news_item(self, source: str) -> NewsItem:
        return NewsItem(
            title=self.title,
            source=self.source_override or source,
            url=self.link,
            published_at=self.published_at,
            snippet=self.snippet,
        )

    def matches_topic(self, normalized_topic: str) -> bool:
        haystack = " ".join(
            [
                self.title,
                self.snippet,
                " ".join(self.categories),
            ]
        ).lower()
        return normalized_topic in haystack


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_html(text: str | None) -> str:
    if not text:
        return ""

    unescaped = html.unescape(text)
    without_tags = _TAG_RE.sub(" ", unescaped)
    return _WHITESPACE_RE.sub(" ", without_tags).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_text(
    element: ET.Element, *names: str
) -> str | None:
    for child in element:
        if _local_name(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _find_all_terms(
    element: ET.Element, *names: str
) -> tuple[str, ...]:
    values: list[str] = []

    for child in element:
        if _local_name(child.tag) not in names:
            continue

        text = (
            child.text or child.get("term") or ""
        ).strip()

        if text:
            values.append(text)

    return tuple(values)


def _find_link(element: ET.Element) -> str | None:
    candidates: list[tuple[str, str]] = []

    for child in element:
        if _local_name(child.tag) != "link":
            continue

        href = child.get("href")

        if href and href.strip():
            rel = child.get("rel") or "alternate"
            candidates.append((rel, href.strip()))
        elif child.text and child.text.strip():
            return child.text.strip()

    for rel, href in candidates:
        if rel == "alternate":
            return href

    return candidates[0][1] if candidates else None


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None

    try:
        parsed = dateutil_parser.parse(text)
    except (ValueError, OverflowError, TypeError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _parse_entry(element: ET.Element) -> _ParsedEntry:
    title = _clean_html(_find_text(element, "title"))
    snippet = _clean_html(
        _find_text(
            element, "description", "summary", "content"
        )
    )
    date_text = _find_text(
        element, "pubDate", "published", "updated", "date"
    )
    source_override = _find_text(element, "source")

    return _ParsedEntry(
        title=title,
        link=_find_link(element),
        snippet=snippet,
        published_at=_parse_date(date_text),
        categories=_find_all_terms(element, "category"),
        source_override=source_override,
    )


def parse_feed(content: bytes) -> list[_ParsedEntry]:
    """
    Parses either an RSS 2.0 or an Atom feed into a common
    entry shape. Raises NewsError for anything that isn't a
    well-formed feed of one of those two kinds.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise NewsError("Invalid feed XML.") from exc

    root_name = _local_name(root.tag)

    if root_name == "rss":
        item_name = "item"
    elif root_name == "feed":
        item_name = "entry"
    else:
        raise NewsError(
            f"Unsupported feed root element: {root_name!r}"
        )

    return [
        _parse_entry(element)
        for element in root.iter()
        if _local_name(element.tag) == item_name
    ]


# --- merge helpers -----------------------------------------------------


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None

    if not parts.scheme or not parts.netloc:
        return None

    path = parts.path.rstrip("/")

    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, "", "")
    )


def _normalize_title_source(title: str, source: str) -> str:
    return f"{title.strip().lower()}|{source.strip().lower()}"


def _deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """
    Dedupes by normalized URL, falling back to normalized
    title+source when there's no URL. When two entries from
    different sources share the same normalized URL, they are
    merged into one item whose "source" records every outlet
    it came from -- one publisher's story is never presented
    as independently confirmed by several sources, but the
    origin of a real cross-source duplicate is preserved.
    """
    order: list[str] = []
    canonical: dict[str, NewsItem] = {}
    extra_sources: dict[str, list[str]] = {}

    for item in items:
        key = _normalize_url(item.url) or (
            _normalize_title_source(item.title, item.source)
        )

        if key not in canonical:
            canonical[key] = item
            extra_sources[key] = []
            order.append(key)
            continue

        known_sources = {
            canonical[key].source,
            *extra_sources[key],
        }

        if item.source not in known_sources:
            extra_sources[key].append(item.source)

    deduped: list[NewsItem] = []

    for key in order:
        item = canonical[key]
        extras = extra_sources[key]

        if extras:
            item = replace(
                item,
                source=", ".join([item.source, *extras]),
            )

        deduped.append(item)

    return deduped


def _sort_by_freshness(
    items: list[NewsItem],
) -> list[NewsItem]:
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        items,
        key=lambda item: item.published_at or epoch,
        reverse=True,
    )


# --- feed fetching -------------------------------------------------------


class _FeedFetcher:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._http = http_client
        self._retry_delays = retry_delays

    async def fetch(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[_ParsedEntry]:
        try:
            return await call_with_retries(
                lambda: self._fetch_once(url, params),
                retry_exceptions=(
                    *_SAFE_TRANSPORT_ERRORS,
                    NewsTransientError,
                ),
                delays=self._retry_delays,
                on_retry=self._log_retry,
            )
        except _SAFE_TRANSPORT_ERRORS as exc:
            raise NewsTransientError(
                f"Could not reach {url}."
            ) from exc

    async def _fetch_once(
        self,
        url: str,
        params: dict[str, str] | None,
    ) -> list[_ParsedEntry]:
        response = await self._http.get(
            url,
            params=params,
            headers={
                "Accept": (
                    "application/rss+xml, "
                    "application/atom+xml, "
                    "application/xml;q=0.9, "
                    "text/xml;q=0.9"
                )
            },
        )

        if (
            response.status_code == 429
            or response.status_code >= 500
        ):
            raise NewsTransientError(
                "Feed temporarily unavailable: "
                f"{response.status_code}"
            )

        if response.status_code != 200:
            raise NewsError(
                f"Unexpected feed status: "
                f"{response.status_code}"
            )

        return parse_feed(response.content)[
            :_MAX_ENTRIES_PER_FEED
        ]

    @staticmethod
    def _log_retry(
        error: BaseException,
        attempt: int,
        delay: float,
    ) -> None:
        logger.warning(
            "News feed request retry",
            extra={
                "event": "news_feed_retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
            },
        )


# --- providers -----------------------------------------------------------


class RussianRssNewsProvider:
    """
    Reads the allowlisted Russian RSS/Atom feeds in parallel.
    A single failing feed is logged and skipped -- it never
    aborts the whole request. Isolated from webhook/command
    handling and from GigaChat, so it stays independently unit
    -testable; GigaChat is never used as a source of news facts,
    only to compress items this provider already found.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        feeds: tuple[
            tuple[str, str], ...
        ] = DEFAULT_RUSSIAN_RSS_FEEDS,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._fetcher = _FeedFetcher(
            http_client, retry_delays=retry_delays
        )
        self._feeds = feeds

    async def get_news(
        self,
        topic: str | None,
        limit: int,
    ) -> list[NewsItem]:
        results = await asyncio.gather(
            *(
                self._fetcher.fetch(url)
                for _name, url in self._feeds
            ),
            return_exceptions=True,
        )

        entries: list[tuple[str, _ParsedEntry]] = []

        for (name, _url), result in zip(
            self._feeds, results
        ):
            if isinstance(result, BaseException):
                logger.warning(
                    "Russian RSS source unavailable",
                    extra={
                        "event": "news_source_failed",
                        "source": name,
                        "error_type": (
                            type(result).__name__
                        ),
                    },
                )
                continue

            entries.extend(
                (name, entry) for entry in result
            )

        normalized_topic = (topic or "").strip().lower()

        if normalized_topic:
            entries = [
                (name, entry)
                for name, entry in entries
                if entry.matches_topic(normalized_topic)
            ]

        items = [
            entry.to_news_item(name)
            for name, entry in entries
        ]
        items = _sort_by_freshness(_deduplicate(items))

        if not items:
            raise NewsUnavailableError(
                "No usable Russian RSS items."
            )

        return items[:limit]


class RssKeywordSearchProvider:
    """
    Searches for a topic locally, over title/snippet/category
    text of the items the allowlisted Russian RSS feeds already
    returned -- not a remote search API call.
    """

    def __init__(
        self, russian_news_provider: NewsProvider
    ) -> None:
        self._russian_news_provider = (
            russian_news_provider
        )

    async def search_news(
        self,
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        items = await self._russian_news_provider.get_news(
            query, limit
        )
        return [
            _news_item_to_search_result(item)
            for item in items
        ]


class GoogleNewsRssProvider:
    """
    The general (no-topic) Google News RSS feed. Used only as a
    fallback when RussianRssNewsProvider yields nothing, so
    "news" without a topic keeps working even if every Russian
    source is briefly down.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        url: str = GOOGLE_NEWS_GENERAL_URL,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._fetcher = _FeedFetcher(
            http_client, retry_delays=retry_delays
        )
        self._url = url

    async def get_news(
        self,
        topic: str | None,
        limit: int,
    ) -> list[NewsItem]:
        entries = await self._fetcher.fetch(
            self._url, params=dict(_GOOGLE_NEWS_PARAMS)
        )
        items = [
            entry.to_news_item(
                entry.source_override or "Google News"
            )
            for entry in entries
        ]
        items = _sort_by_freshness(_deduplicate(items))

        if not items:
            raise NewsUnavailableError(
                "No usable Google News items."
            )

        return items[:limit]


class GoogleNewsRssSearchProvider:
    """
    Additional source for topic queries: Google News RSS
    search (https://news.google.com/rss/search). Only used to
    top up a topic search when the Russian RSS allowlist alone
    didn't return enough -- its own unavailability never breaks
    a topic query that Russian sources already answered.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        url: str = GOOGLE_NEWS_SEARCH_URL,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._fetcher = _FeedFetcher(
            http_client, retry_delays=retry_delays
        )
        self._url = url

    async def search_news(
        self,
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        normalized_query = query.strip()

        if not normalized_query:
            return []

        params = dict(_GOOGLE_NEWS_PARAMS)
        params["q"] = normalized_query

        entries = await self._fetcher.fetch(
            self._url, params=params
        )
        items = [
            entry.to_news_item(
                entry.source_override or "Google News"
            )
            for entry in entries
        ]
        items = _sort_by_freshness(_deduplicate(items))

        return [
            _news_item_to_search_result(item)
            for item in items[:limit]
        ]


class AggregatedNewsProvider:
    """
    The resilience policy: Russian RSS first, Google News RSS
    only as a fallback (no topic) or a supplement when Russian
    sources alone are not enough (topic search) -- so "news"
    keeps working if Google, or any single Russian source, is
    unreachable.
    """

    def __init__(
        self,
        russian_provider: NewsProvider,
        keyword_search_provider: SearchProvider,
        google_search_provider: SearchProvider,
        google_general_provider: NewsProvider,
    ) -> None:
        self._russian_provider = russian_provider
        self._keyword_search_provider = (
            keyword_search_provider
        )
        self._google_search_provider = (
            google_search_provider
        )
        self._google_general_provider = (
            google_general_provider
        )

    async def get_news(
        self,
        topic: str | None,
        limit: int,
    ) -> list[NewsItem]:
        normalized_topic = (topic or "").strip()

        if normalized_topic:
            return await self._get_topic_news(
                normalized_topic, limit
            )

        return await self._get_headline_news(limit)

    async def _get_headline_news(
        self, limit: int
    ) -> list[NewsItem]:
        try:
            items = await self._russian_provider.get_news(
                None, limit
            )
        except NewsError:
            items = []

        if items:
            return items

        try:
            return (
                await self
                ._google_general_provider
                .get_news(None, limit)
            )
        except NewsError as exc:
            raise NewsUnavailableError(
                "No usable headlines from any source."
            ) from exc

    async def _get_topic_news(
        self, topic: str, limit: int
    ) -> list[NewsItem]:
        try:
            russian_results = (
                await self
                ._keyword_search_provider
                .search_news(topic, limit)
            )
        except NewsError:
            russian_results = []

        items = [
            _search_result_to_news_item(result)
            for result in russian_results
        ]

        if len(items) < limit:
            try:
                google_results = (
                    await self
                    ._google_search_provider
                    .search_news(topic, limit)
                )
            except NewsError:
                google_results = []

            items.extend(
                _search_result_to_news_item(result)
                for result in google_results
            )

        items = _sort_by_freshness(_deduplicate(items))

        if not items:
            raise NewsUnavailableError(
                f"No usable news for topic {topic!r}."
            )

        return items[:limit]
