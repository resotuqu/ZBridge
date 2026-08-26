import pytest

from app.core.runtime_state import (
    InboundRateLimiter,
    RuntimeState,
)


def test_selected_model_is_independent_per_phone() -> None:
    runtime_state = RuntimeState()

    runtime_state.set_selected_model(
        "79991234567", "GigaChat-2-Pro"
    )
    runtime_state.set_selected_model(
        "79997654321", "GigaChat-2-Max"
    )

    assert (
        runtime_state.get_selected_model("79991234567")
        == "GigaChat-2-Pro"
    )
    assert (
        runtime_state.get_selected_model("79997654321")
        == "GigaChat-2-Max"
    )
    assert (
        runtime_state.get_selected_model("79990000000")
        is None
    )


def test_selected_model_normalizes_phone_numbers() -> None:
    runtime_state = RuntimeState()

    runtime_state.set_selected_model(
        "+7 999 123-45-67", "GigaChat-2-Pro"
    )

    assert (
        runtime_state.get_selected_model("89991234567")
        == "GigaChat-2-Pro"
    )


def test_latin_mode_is_independent_per_phone() -> None:
    runtime_state = RuntimeState()

    runtime_state.set_latin_mode("79991234567", True)
    runtime_state.set_latin_mode("79997654321", False)

    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=False
        )
        is True
    )
    assert (
        runtime_state.get_latin_mode(
            "79997654321", default=True
        )
        is False
    )


def test_latin_mode_uses_default_when_not_set() -> None:
    runtime_state = RuntimeState()

    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=True
        )
        is True
    )
    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=False
        )
        is False
    )


def test_latin_mode_explicit_false_overrides_default_on() -> None:
    runtime_state = RuntimeState()

    runtime_state.set_latin_mode("79991234567", False)

    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=True
        )
        is False
    )


def test_latin_mode_explicit_true_overrides_default_off() -> None:
    runtime_state = RuntimeState()

    runtime_state.set_latin_mode("79991234567", True)

    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=False
        )
        is True
    )


def test_new_runtime_state_starts_empty() -> None:
    runtime_state = RuntimeState()

    assert runtime_state.selected_model_by_phone == {}
    assert runtime_state.latin_mode_by_phone == {}
    assert (
        runtime_state.clear_context_after_by_phone == {}
    )
    assert (
        runtime_state.temporary_authorized_numbers
        == set()
    )
    assert runtime_state.gigachat_requests_total == 0
    assert (
        runtime_state.gigachat_requests_by_model == {}
    )
    assert runtime_state.per_phone_locks == {}

    assert (
        runtime_state.get_selected_model("79991234567")
        is None
    )
    assert (
        runtime_state.get_latin_mode(
            "79991234567", default=False
        )
        is False
    )
    assert (
        runtime_state.get_context_boundary("79991234567")
        is None
    )
    assert not runtime_state.is_temporarily_authorized(
        "79991234567"
    )


def test_second_runtime_state_instance_does_not_share_state() -> None:
    first = RuntimeState()
    first.set_selected_model("79991234567", "GigaChat-2")
    first.set_latin_mode("79991234567", True)

    second = RuntimeState()

    assert (
        second.get_selected_model("79991234567") is None
    )
    assert (
        second.get_latin_mode(
            "79991234567", default=False
        )
        is False
    )


# --- InboundRateLimiter --------------------------------------------------


def build_limiter(
    *,
    max_per_minute: int = 5,
    max_per_hour: int = 60,
    max_tracked_numbers: int = 10_000,
    now: list[float] | None = None,
) -> tuple[InboundRateLimiter, list[float]]:
    clock_state = now if now is not None else [1_000.0]

    limiter = InboundRateLimiter(
        max_per_minute=max_per_minute,
        max_per_hour=max_per_hour,
        max_tracked_numbers=max_tracked_numbers,
        clock=lambda: clock_state[0],
    )

    return limiter, clock_state


@pytest.mark.asyncio
async def test_five_per_minute_pass_sixth_is_blocked() -> (
    None
):
    limiter, _clock = build_limiter(
        max_per_minute=5, max_per_hour=1000
    )

    results = [
        await limiter.allow("79991234567")
        for _ in range(6)
    ]

    assert results == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_hour_limit_blocks_after_it_is_reached() -> (
    None
):
    limiter, clock_state = build_limiter(
        max_per_minute=1000, max_per_hour=3
    )

    results = []

    for _ in range(4):
        results.append(
            await limiter.allow("79991234567")
        )
        # Space calls out so only the hour window (not the
        # minute window) is what's being exercised here.
        clock_state[0] += 100.0

    assert results == [True, True, True, False]


@pytest.mark.asyncio
async def test_minute_window_expires_with_fake_clock() -> (
    None
):
    limiter, clock_state = build_limiter(
        max_per_minute=2, max_per_hour=1000
    )

    assert await limiter.allow("79991234567") is True
    assert await limiter.allow("79991234567") is True
    assert await limiter.allow("79991234567") is False

    clock_state[0] += 61.0

    assert await limiter.allow("79991234567") is True


@pytest.mark.asyncio
async def test_hour_window_expires_with_fake_clock() -> (
    None
):
    limiter, clock_state = build_limiter(
        max_per_minute=1000, max_per_hour=2
    )

    assert await limiter.allow("79991234567") is True
    clock_state[0] += 100.0
    assert await limiter.allow("79991234567") is True
    clock_state[0] += 100.0
    assert await limiter.allow("79991234567") is False

    clock_state[0] += 3601.0

    assert await limiter.allow("79991234567") is True


@pytest.mark.asyncio
async def test_limit_is_independent_per_phone() -> None:
    limiter, _clock = build_limiter(
        max_per_minute=2, max_per_hour=1000
    )

    assert await limiter.allow("79991234567") is True
    assert await limiter.allow("79991234567") is True
    assert await limiter.allow("79991234567") is False

    # A different phone must not be affected by the first
    # phone already being at its limit.
    assert await limiter.allow("79997654321") is True
    assert await limiter.allow("79997654321") is True
    assert await limiter.allow("79997654321") is False


@pytest.mark.asyncio
async def test_rejects_non_positive_configuration() -> None:
    with pytest.raises(ValueError):
        InboundRateLimiter(
            max_per_minute=0, max_per_hour=60
        )

    with pytest.raises(ValueError):
        InboundRateLimiter(
            max_per_minute=5, max_per_hour=0
        )

    with pytest.raises(ValueError):
        InboundRateLimiter(
            max_per_minute=5,
            max_per_hour=60,
            max_tracked_numbers=0,
        )


@pytest.mark.asyncio
async def test_evicts_least_recently_active_phone_when_at_capacity() -> (
    None
):
    """
    Bounded memory: once max_tracked_numbers is reached, the
    phone that has been idle the longest is dropped to make
    room for a new one, rather than growing forever.
    """
    limiter, clock_state = build_limiter(
        max_per_minute=1000,
        max_per_hour=1000,
        max_tracked_numbers=2,
    )

    assert await limiter.allow("71111111111") is True
    clock_state[0] += 1.0
    assert await limiter.allow("72222222222") is True
    clock_state[0] += 1.0

    # A third, brand-new phone forces an eviction -- the
    # least recently active one (71111111111) should go.
    assert await limiter.allow("73333333333") is True

    # 71111111111 was evicted, so it gets a fresh window
    # instead of being denied.
    assert await limiter.allow("71111111111") is True
