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


def test_format_short_answer_is_single_segment_without_prefix() -> None:
    segments = format_sms_answer(
        "Виртуальная локальная сеть.",
        add_warning=False,
    )

    assert segments == ["Виртуальная локальная сеть."]


def test_format_short_answer_with_warning_gets_prefix() -> None:
    segments = format_sms_answer(
        "Виртуальная локальная сеть.",
        add_warning=True,
    )

    assert segments == [
        "[!] Виртуальная локальная сеть."
    ]


def test_format_strips_markdown_and_normalizes_whitespace() -> None:
    raw = (
        "# Заголовок\n\n"
        "Это **жирный**  и  *курсив*, а также `код`.\n"
        "- пункт один\n"
        "- пункт два\n"
        "[ссылка](https://example.test)"
    )

    segments = format_sms_answer(raw, add_warning=False)

    text = " ".join(
        segment.split(" ", 1)[-1]
        if segment.startswith("[")
        else segment
        for segment in segments
    )
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


def test_format_splits_long_gsm7_answer_with_part_prefixes() -> None:
    word = "word"
    raw = " ".join([word] * 60)

    segments = format_sms_answer(raw, add_warning=False)

    assert len(segments) > 1

    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        assert segment.startswith(f"[{index}/{total}] ")
        assert len(segment) <= 153

    reconstructed = " ".join(
        segment.split(" ", 1)[1] for segment in segments
    )
    assert reconstructed == raw


def test_format_splits_long_gsm7_answer_with_warning_prefix_on_first_only() -> None:
    raw = " ".join(["word"] * 60)

    segments = format_sms_answer(raw, add_warning=True)

    assert segments[0].startswith("[!] [1/")
    for segment in segments[1:]:
        assert not segment.startswith("[!]")


def test_format_splits_long_ucs2_answer_respecting_budget() -> None:
    raw = " ".join(["слово"] * 30)

    segments = format_sms_answer(raw, add_warning=False)

    assert len(segments) > 1

    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        assert segment.startswith(f"[{index}/{total}] ")
        assert segment_length(segment, "ucs2") <= 67

    reconstructed = " ".join(
        segment.split(" ", 1)[1] for segment in segments
    )
    assert reconstructed == raw


def test_format_hard_splits_a_single_overlong_word() -> None:
    raw = "a" * 400

    segments = format_sms_answer(raw, add_warning=False)

    assert len(segments) > 1
    assert "".join(
        segment.split(" ", 1)[1] for segment in segments
    ) == raw


def test_format_never_produces_empty_segments() -> None:
    segments = format_sms_answer("", add_warning=False)

    assert all(segment.strip() for segment in segments)
    assert len(segments) >= 1
