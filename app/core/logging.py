from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.security import mask_phone_number


_STANDARD_LOG_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__
) | {
    "asctime",
    "message",
}

_SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "pin",
    "secret",
    "token",
)

_PHONE_KEYS = {
    "dst_number",
    "from_number",
    "phone",
    "src_number",
    "to",
}


def _sanitize_value(key: str, value: Any) -> Any:
    normalized_key = key.lower()

    if any(
        part in normalized_key
        for part in _SECRET_KEY_PARTS
    ):
        return "[REDACTED]"

    if (
        normalized_key in _PHONE_KEYS
        and isinstance(value, (str, int))
    ):
        try:
            return mask_phone_number(str(value))
        except ValueError:
            return "[INVALID PHONE]"

    if isinstance(value, Mapping):
        return {
            str(nested_key): _sanitize_value(
                str(nested_key),
                nested_value,
            )
            for nested_key, nested_value in value.items()
        }

    if isinstance(
        value,
        (list, tuple, set, frozenset),
    ):
        return [
            _sanitize_value(key, item)
            for item in value
        ]

    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS:
                payload[key] = _sanitize_value(
                    key,
                    value,
                )

        if record.exc_info:
            payload["exception_type"] = (
                record.exc_info[0].__name__
            )

        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        )


def configure_logging(log_level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    logging.captureWarnings(True)

    uvicorn_access_logger = logging.getLogger(
        "uvicorn.access"
    )
    uvicorn_access_logger.handlers.clear()
    uvicorn_access_logger.propagate = False
    uvicorn_access_logger.disabled = True

    access_logger = logging.getLogger(
        "uvicorn.access"
    )
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True