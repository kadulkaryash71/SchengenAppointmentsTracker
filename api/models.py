from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class SubscribeRequest(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    telegram: Optional[str] = None
    countries: List[str] = Field(..., min_length=1)
    visa_type: Optional[str] = "Tourism"
    consented_at: datetime

    @field_validator("phone", "telegram", mode="before")
    @classmethod
    def blank_to_none(cls, v):
        return v or None

    @field_validator("visa_type", mode="before")
    @classmethod
    def blank_to_default(cls, v):
        return v or "Tourism"


class SubscribeResponse(BaseModel):
    status: str = "ok"
    email: EmailStr
