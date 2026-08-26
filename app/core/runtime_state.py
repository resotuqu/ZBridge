from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from zoneinfo import ZoneInfo

from app.core.security import normalize_phone_number


_MINUTE_SECONDS = 60.0
_HOUR_SECONDS = 3600.0


class InboundRateLimiter:
    """
    Emergency, in-memory-only anti-abuse limit on inbound SMS per
    phone number: a sliding-window log of event timestamps, capped
    per phone by the hour window (older entries are purged as they
    age out) and capped overall by evicting the least-recently-
    active phone once max_tracked_numbers is reached -- so memory
    use stays bounded regardless of how many distinct numbers (real
    or spoofed) show up. There is no persistence: limits reset on
    every restart by design, matching the rest of RuntimeState.
    """

    def __init__(
        self,
        *,
        max_per_minute: int,
        max_per_hour: int,
        max_tracked_numbers: int = 10_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_per_minute <= 0:
            raise ValueError(
                "max_per_minute must be positive."
            )

        if max_per_hour <= 0:
            raise ValueError(
                "max_per_hour must be positive."
            )

        if max_tracked_numbers <= 0:
            raise ValueError(
                "max_tracked_numbers must be positive."
            )

        self._max_per_minute = max_per_minute
        self._max_per_hour = max_per_hour
        self._max_tracked_numbers = max_tracked_numbers
        self._clock = clock
        self._events_by_phone: dict[
            str, deque[float]
        ] = {}
        self._lock = asyncio.Lock()

    async def allow(self, phone: str) -> bool:
        """
        Returns True and records this event if the phone is
        still within both windows; returns False (without
        recording anything) if either window is already at its
        limit.
        """
        normalized = normalize_phone_number(phone)

        async with self._lock:
            now = self._clock()
            events = self._events_by_phone.get(
                normalized
            )

            if events is not None:
                self._purge_expired(events, now)

                if not events:
                    del self._events_by_phone[
                        normalized
                    ]
                    events = None

            hour_count = (
                len(events) if events is not None else 0
            )
            minute_cutoff = now - _MINUTE_SECONDS
            minute_count = (
                sum(
                    1
                    for event_at in events
                    if event_at > minute_cutoff
                )
                if events is not None
                else 0
            )

            if (
                minute_count >= self._max_per_minute
                or hour_count >= self._max_per_hour
            ):
                return False

            if events is None:
                if (
                    len(self._events_by_phone)
                    >= self._max_tracked_numbers
                ):
                    self._evict_least_recently_active()

                events = deque()
                self._events_by_phone[normalized] = (
                    events
                )

            events.append(now)

            return True

    def _purge_expired(
        self,
        events: deque[float],
        now: float,
    ) -> None:
        cutoff = now - _HOUR_SECONDS

        while events and events[0] <= cutoff:
            events.popleft()

    def _evict_least_recently_active(self) -> None:
        oldest_phone = min(
            self._events_by_phone,
            key=lambda tracked_phone: (
                self._events_by_phone[tracked_phone][-1]
            ),
        )
        self._events_by_phone.pop(oldest_phone, None)


class TTLKeyCache:
    def __init__(
        self,
        ttl_seconds: float = 600.0,
        max_entries: int = 10_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(
                "ttl_seconds must be positive."
            )

        if max_entries <= 0:
            raise ValueError(
                "max_entries must be positive."
            )

        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._expires_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def seen_or_add(self, key: str) -> bool:
        """
        Return True for a live duplicate.
        Otherwise, store the key and return False.
        """
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)

            expires_at = self._expires_at.get(key)

            if (
                expires_at is not None
                and expires_at > now
            ):
                return True

            if (
                len(self._expires_at)
                >= self._max_entries
            ):
                oldest_key = min(
                    self._expires_at,
                    key=self._expires_at.__getitem__,
                )
                self._expires_at.pop(
                    oldest_key,
                    None,
                )

            self._expires_at[key] = (
                now + self._ttl_seconds
            )

            return False

    async def clear(self) -> None:
        async with self._lock:
            self._expires_at.clear()

    def _purge_expired(self, now: float) -> None:
        expired_keys = [
            key
            for key, expires_at
            in self._expires_at.items()
            if expires_at <= now
        ]

        for key in expired_keys:
            self._expires_at.pop(key, None)


def build_webhook_key(
    *,
    src_number: str,
    dst_number: str,
    received_at: datetime,
    content: str,
    default_timezone: ZoneInfo,
) -> str:
    if received_at.tzinfo is None:
        received_at = received_at.replace(
            tzinfo=default_timezone
        )

    normalized_date = (
        received_at
        .astimezone(UTC)
        .isoformat()
    )

    raw_key = "\x1f".join(
        (
            normalize_phone_number(src_number),
            normalize_phone_number(dst_number),
            normalized_date,
            content,
        )
    )

    return hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()


@dataclass(slots=True)
class RuntimeState:
    recent_webhook_keys: TTLKeyCache = field(
        default_factory=TTLKeyCache
    )

    temporary_authorized_numbers: set[str] = field(
        default_factory=set
    )

    selected_model_by_phone: dict[str, str] = field(
        default_factory=dict
    )

    clear_context_after_by_phone: dict[
        str, datetime
    ] = field(default_factory=dict)

    latin_mode_by_phone: dict[str, bool] = field(
        default_factory=dict
    )

    gigachat_requests_total: int = 0

    gigachat_requests_by_model: dict[str, int] = field(
        default_factory=dict
    )

    per_phone_locks: dict[
        str,
        asyncio.Lock,
    ] = field(
        default_factory=dict
    )

    def get_phone_lock(
        self,
        phone: str,
    ) -> asyncio.Lock:
        normalized = normalize_phone_number(phone)

        lock = self.per_phone_locks.get(normalized)

        if lock is None:
            lock = asyncio.Lock()
            self.per_phone_locks[normalized] = lock

        return lock

    def authorize_temporarily(
        self,
        phone: str,
    ) -> None:
        self.temporary_authorized_numbers.add(
            normalize_phone_number(phone)
        )

    def is_temporarily_authorized(
        self,
        phone: str,
    ) -> bool:
        return (
            normalize_phone_number(phone)
            in self.temporary_authorized_numbers
        )

    def set_selected_model(
        self,
        phone: str,
        model: str,
    ) -> None:
        self.selected_model_by_phone[
            normalize_phone_number(phone)
        ] = model

    def get_selected_model(
        self,
        phone: str,
    ) -> str | None:
        return self.selected_model_by_phone.get(
            normalize_phone_number(phone)
        )

    def set_context_boundary(
        self,
        phone: str,
        at: datetime,
    ) -> None:
        self.clear_context_after_by_phone[
            normalize_phone_number(phone)
        ] = at

    def get_context_boundary(
        self,
        phone: str,
    ) -> datetime | None:
        return self.clear_context_after_by_phone.get(
            normalize_phone_number(phone)
        )

    def set_latin_mode(
        self,
        phone: str,
        enabled: bool,
    ) -> None:
        self.latin_mode_by_phone[
            normalize_phone_number(phone)
        ] = enabled

    def get_latin_mode(
        self,
        phone: str,
        default: bool,
    ) -> bool:
        return self.latin_mode_by_phone.get(
            normalize_phone_number(phone),
            default,
        )

    def record_gigachat_request(
        self,
        model: str,
    ) -> None:
        self.gigachat_requests_total += 1
        self.gigachat_requests_by_model[model] = (
            self.gigachat_requests_by_model.get(model, 0)
            + 1
        )