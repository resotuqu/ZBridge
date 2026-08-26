import pytest

from app.core.security import (
    is_phone_allowed,
    is_sms_loop,
    mask_phone_number,
    normalize_phone_number,
    parse_auth_command,
    parse_model_command,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "+7 999 123-45-67",
            "79991234567",
        ),
        (
            "8 (999) 123-45-67",
            "79991234567",
        ),
        (
            "9991234567",
            "79991234567",
        ),
    ],
)
def test_normalize_phone_number(
    raw: str,
    expected: str,
) -> None:
    assert normalize_phone_number(raw) == expected


def test_invalid_phone_number() -> None:
    with pytest.raises(ValueError):
        normalize_phone_number("123")


def test_whitelist_and_masking() -> None:
    allowed = frozenset({"79991234567"})

    assert is_phone_allowed(
        "89991234567",
        allowed,
    )

    assert (
        mask_phone_number("89991234567")
        == "*******4567"
    )


def test_sms_loop_protection() -> None:
    service_number = "79990000000"

    assert is_sms_loop(
        service_number,
        service_number,
        service_number,
    )

    assert is_sms_loop(
        "79991234567",
        "79998888888",
        service_number,
    )

    assert not is_sms_loop(
        "79991234567",
        service_number,
        service_number,
    )


@pytest.mark.parametrize(
    ("text", "expected_pin"),
    [
        ("3957 auth", "3957"),
        ("  3957   AUTH  ", "3957"),
        ("3957 Auth", "3957"),
    ],
)
def test_parse_auth_command_matches(
    text: str,
    expected_pin: str,
) -> None:
    assert parse_auth_command(text) == expected_pin


@pytest.mark.parametrize(
    "text",
    [
        "auth",
        "3957",
        "3957 authorize",
        "Что такое VLAN?",
        "3957 auth extra",
    ],
)
def test_parse_auth_command_rejects(
    text: str,
) -> None:
    assert parse_auth_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "8241 model GigaChat-2-Pro",
            ("8241", "GigaChat-2-Pro"),
        ),
        (
            "  8241   MODEL   GigaChat-2-Pro  ",
            ("8241", "GigaChat-2-Pro"),
        ),
    ],
)
def test_parse_model_command_matches(
    text: str,
    expected: tuple[str, str],
) -> None:
    assert parse_model_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "model GigaChat-2-Pro",
        "8241 model",
        "8241 model a b",
        "Что такое VLAN?",
    ],
)
def test_parse_model_command_rejects(
    text: str,
) -> None:
    assert parse_model_command(text) is None