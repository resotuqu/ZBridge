from app.core.runtime_state import RuntimeState


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
