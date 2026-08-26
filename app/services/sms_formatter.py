from __future__ import annotations

import re
from typing import Literal


Charset = Literal["gsm7", "ucs2"]

GSM7_SINGLE_LIMIT = 160
GSM7_MULTIPART_LIMIT = 153
UCS2_SINGLE_LIMIT = 70
UCS2_MULTIPART_LIMIT = 67

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


def format_sms_answer(
    text: str,
    *,
    add_warning: bool = False,
) -> str:
    """
    Format a raw answer into one logical SMS submission.

    Long text is intentionally *not* split into separate API calls here.
    Plusofon receives the complete text with ``reject_long=False`` and
    creates a concatenated SMS, which compatible handsets display as one
    message bubble. The provider's returned ``pdu`` remains the source of
    truth for billing and daily usage.
    """
    cleaned = _normalize_whitespace(_strip_markdown(text))

    if not cleaned:
        cleaned = _normalize_whitespace(text) or "..."

    warning_prefix = "[!] " if add_warning else ""
    return warning_prefix + cleaned
