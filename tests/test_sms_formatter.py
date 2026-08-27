import pytest

from app.services.sms_formatter import (
    format_sms_answer,
    is_gsm7_compatible,
    segment_length,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, world! 123", True),
        ("Price: 10€ {a} [b] ~c^", True),
        ("Привет", False),
        ("emoji 🙂", False),
        ("₽", False),
    ],
)
def test_is_gsm7_compatible(
    text: str, expected: bool
) -> None:
    assert is_gsm7_compatible(text) is expected


def test_segment_length_gsm7_counts_extension_chars_double() -> None:
    assert segment_length("abc", "gsm7") == 3
    assert segment_length("a{b}c", "gsm7") == 7


def test_segment_length_ucs2_counts_utf16_units() -> None:
    assert segment_length("привет", "ucs2") == 6
    assert segment_length("🙂", "ucs2") == 2


def test_format_short_answer_without_prefix() -> None:
    assert format_sms_answer(
        "Виртуальная локальная сеть.",
        add_warning=False,
    ) == "Виртуальная локальная сеть."


def test_format_short_answer_with_warning_prefix() -> None:
    assert format_sms_answer(
        "Виртуальная локальная сеть.",
        add_warning=True,
    ) == "[!] Виртуальная локальная сеть."


def test_format_strips_markdown_and_normalizes_whitespace() -> None:
    raw = (
        "# Заголовок\n\n"
        "Это **жирный**  и  *курсив*, а также `код`.\n"
        "- пункт один\n"
        "- пункт два\n"
        "[ссылка](https://example.test)"
    )

    text = format_sms_answer(raw, add_warning=False)

    assert "#" not in text
    assert "**" not in text
    assert "*" not in text
    assert "`" not in text
    assert "- " not in text
    assert "жирный" in text
    assert "курсив" in text
    assert "код" in text
    assert "ссылка (https://example.test)" in text
    assert "\n" not in text
    assert "  " not in text


def test_format_keeps_long_gsm7_answer_as_one_logical_message() -> None:
    raw = " ".join(["word"] * 60)

    formatted = format_sms_answer(raw, add_warning=False)

    assert segment_length(formatted, "gsm7") > 160
    assert formatted == raw
    assert "[1/" not in formatted


def test_format_keeps_long_ucs2_answer_as_one_logical_message() -> None:
    raw = " ".join(["слово"] * 30)

    formatted = format_sms_answer(raw, add_warning=False)

    assert segment_length(formatted, "ucs2") > 70
    assert formatted == raw
    assert "[1/" not in formatted


def test_format_adds_one_warning_prefix_to_a_long_message() -> None:
    raw = " ".join(["слово"] * 30)

    formatted = format_sms_answer(raw, add_warning=True)

    assert formatted == f"[!] {raw}"
    assert "[1/" not in formatted


def test_format_never_returns_blank_text() -> None:
    assert format_sms_answer("", add_warning=False) == "..."
