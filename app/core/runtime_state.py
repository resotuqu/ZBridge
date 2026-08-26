from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from zoneinfo import ZoneInfo

from app.core.security import normalize_phone_number


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