from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.security import normalize_phone_number
from app.schemas.messages import IncomingSMS


class PlusofonIncomingWebhook(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    src_number: str
    dst_number: str
    content: str
    date: datetime

    @field_validator(
        "src_number",
        "dst_number",
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

    def to_incoming_sms(self) -> IncomingSMS:
        return IncomingSMS(
            sender=self.src_number,
            recipient=self.dst_number,
            content=self.content,
            received_at=self.date,
        )