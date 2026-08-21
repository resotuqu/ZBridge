from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.security import normalize_phone_number


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    role: ChatRole
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "Message content must not be blank."
            )
        return value


class IncomingSMS(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    sender: str
    recipient: str
    content: str
    received_at: datetime

    @field_validator(
        "sender",
        "recipient",
        mode="before",
    )
    @classmethod
    def normalize_phone(cls, value: object) -> str:
        return normalize_phone_number(str(value))

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "SMS content must not be blank."
            )
        return value