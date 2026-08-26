from __future__ import annotations

import hmac
import re
from collections.abc import Collection


_NON_DIGIT_RE = re.compile(r"\D+")
_AUTH_COMMAND_RE = re.compile(
    r"^\s*(\S+)\s+auth\s*$",
    re.IGNORECASE,
)
_MODEL_COMMAND_RE = re.compile(
    r"^\s*(\S+)\s+model\s+(\S+)\s*$",
    re.IGNORECASE,
)


def normalize_phone_number(value: str | int) -> str:
    """Normalize a Russian phone number to 11 digits starting with 7."""
    digits = _NON_DIGIT_RE.sub("", str(value))

    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"

    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError(
            "Phone number must contain 10 digits without country code "
            "or 11 digits starting with 7/8."
        )

    return digits


def is_phone_allowed(
    phone: str,
    allowed_numbers: Collection[str],
) -> bool:
    return normalize_phone_number(phone) in allowed_numbers


def is_sms_loop(
    src_number: str,
    dst_number: str,
    service_number: str,
) -> bool:
    normalized_service = normalize_phone_number(service_number)

    return (
        normalize_phone_number(src_number) == normalized_service
        or normalize_phone_number(dst_number) != normalized_service
    )


def parse_auth_command(text: str) -> str | None:
    """Return the PIN candidate from an "<PIN> auth" command, if present."""
    match = _AUTH_COMMAND_RE.match(text)

    if match is None:
        return None

    return match.group(1)


def parse_model_command(
    text: str,
) -> tuple[str, str] | None:
    """Return (pin_candidate, model_name) for a "<PIN> model <name>" command."""
    match = _MODEL_COMMAND_RE.match(text)

    if match is None:
        return None

    return match.group(1), match.group(2)


def secrets_equal(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(
        candidate.encode("utf-8"),
        expected.encode("utf-8"),
    )


def mask_phone_number(phone: str) -> str:
    normalized = normalize_phone_number(phone)
    return f"{'*' * (len(normalized) - 4)}{normalized[-4:]}"