from __future__ import annotations

import re
from typing import Literal


Charset = Literal["gsm7", "ucs2"]

GSM7_SINGLE_LIMIT = 160
GSM7_MULTIPART_LIMIT = 153
UCS2_SINGLE_LIMIT = 70
UCS2_MULTIPART_LIMIT = 67

_MAX_SEGMENT_COUNT_GUESSES = 6

_GSM7_BASIC_CHARS = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅå"
    "ΔΦΓΛΩΠΨΣΘΞ"
    "ÆæßÉ"
    " !\"#¤%&'()*+,-./"
    "0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
    "_"
)

_GSM7_EXTENDED_CHARS = frozenset("\f^{}\\[~]|€")

_CODE_FENCE_RE = re.compile(r"```.*?\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"(\*\*|__)(?!\s)(.+?)(?<!\s)\1")
_EMPHASIS_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_UNDERSCORE_RE = re.compile(r"(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)")
_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?[\s:|-]+\|[\s:|-]*\s*$", re.MULTILINE
)
_WHITESPACE_RE = re.compile(r"\s+")


def is_gsm7_compatible(text: str) -> bool:
    return all(
        char in _GSM7_BASIC_CHARS
        or char in _GSM7_EXTENDED_CHARS
        for char in text
    )


def segment_length(text: str, charset: Charset) -> int:
    if charset == "gsm7":
        return sum(
            2 if char in _GSM7_EXTENDED_CHARS else 1
            for char in text
        )

    return len(text.encode("utf-16-le")) // 2


def _strip_markdown(text: str) -> str:
    result = _CODE_FENCE_RE.sub(
        lambda m: m.group(1), text
    )
    result = _TABLE_SEPARATOR_RE.sub("", result)
    result = _LINK_RE.sub(
        lambda m: f"{m.group(1)} ({m.group(2)})", result
    )
    result = _INLINE_CODE_RE.sub(
        lambda m: m.group(1), result
    )
    result = _BOLD_RE.sub(lambda m: m.group(2), result)
    result = _EMPHASIS_RE.sub(lambda m: m.group(1), result)
    result = _UNDERSCORE_RE.sub(
        lambda m: m.group(1), result
    )
    result = _HEADER_RE.sub("", result)
    result = _BULLET_RE.sub("", result)
    result = result.replace("|", " ")
    return result


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _char_cost(char: str, charset: Charset) -> int:
    if charset == "gsm7":
        return 2 if char in _GSM7_EXTENDED_CHARS else 1

    return len(char.encode("utf-16-le")) // 2


def _hard_split(
    word: str,
    budget: int,
    charset: Charset,
) -> tuple[str, str]:
    if budget <= 0:
        return "", word

    piece: list[str] = []
    used = 0

    for index, char in enumerate(word):
        cost = _char_cost(char, charset)

        if used + cost > budget:
            return "".join(piece), word[index:]

        piece.append(char)
        used += cost

    return word, ""


def _chunk_body(
    body: str,
    budget: int,
    charset: Charset,
) -> list[str]:
    if budget <= 0:
        budget = 1

    chunks: list[str] = []
    current = ""

    for word in body.split(" "):
        candidate = (
            f"{current} {word}" if current else word
        )

        if segment_length(candidate, charset) <= budget:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        remainder = word

        while segment_length(remainder, charset) > budget:
            piece, remainder = _hard_split(
                remainder, budget, charset
            )

            if not piece:
                break

            chunks.append(piece)

        current = remainder

    if current:
        chunks.append(current)

    return chunks or [""]


def format_sms_answer(
    text: str,
    *,
    add_warning: bool = False,
) -> list[str]:
    """
    Format a raw AI answer into ready-to-send SMS segments:
    strips Markdown, normalizes whitespace, picks GSM-7/UCS-2,
    splits on word boundaries within the segment budget, and
    adds "[!]" / "[i/N]" prefixes per architecture.md section 23.
    """
    cleaned = _normalize_whitespace(_strip_markdown(text))

    if not cleaned:
        cleaned = _normalize_whitespace(text) or "..."

    charset: Charset = (
        "gsm7"
        if is_gsm7_compatible(cleaned)
        else "ucs2"
    )
    single_limit, multipart_limit = (
        (GSM7_SINGLE_LIMIT, GSM7_MULTIPART_LIMIT)
        if charset == "gsm7"
        else (UCS2_SINGLE_LIMIT, UCS2_MULTIPART_LIMIT)
    )

    warning_prefix = "[!] " if add_warning else ""
    single_candidate = warning_prefix + cleaned

    if (
        segment_length(single_candidate, charset)
        <= single_limit
    ):
        return [single_candidate]

    count = 2
    chunks: list[str] = []

    for _ in range(_MAX_SEGMENT_COUNT_GUESSES):
        part_prefix_width = len(f"[{count}/{count}] ")
        budget = max(
            multipart_limit - part_prefix_width, 1
        )
        chunks = _chunk_body(cleaned, budget, charset)

        if len(chunks) == count:
            break

        count = max(len(chunks), 1)
    else:
        part_prefix_width = len(f"[{count}/{count}] ")
        budget = max(
            multipart_limit - part_prefix_width, 1
        )
        chunks = _chunk_body(cleaned, budget, charset)

    if len(chunks) <= 1:
        return [single_candidate]

    total = len(chunks)
    segments = []

    for index, chunk in enumerate(chunks, start=1):
        prefix = f"[{index}/{total}] "

        if index == 1 and add_warning:
            prefix = f"[!] {prefix}"

        segments.append(f"{prefix}{chunk}")

    return [
        segment for segment in segments if segment.strip()
    ]
