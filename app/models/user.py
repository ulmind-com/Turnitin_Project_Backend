from beanie import Document
from pydantic import EmailStr, Field
from typing import Optional
from enum import Enum
from datetime import datetime, timezone


class Role(str, Enum):
    USER = "user"
    ADMIN = "admin"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"


class User(Document):
    name: str
    email: EmailStr
    password_hash: str
    role: Role = Role.USER
    active_plan: Optional[str] = None  # Plan ID as string
    credits: int = Field(default=0, ge=0)
    account_status: AccountStatus = AccountStatus.ACTIVE
    plan_expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "users"
        indexes = [
            "email",
        ]
