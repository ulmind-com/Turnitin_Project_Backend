from beanie import Document
from pydantic import Field
from typing import Optional
from enum import Enum
from datetime import datetime, timezone


class PaymentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Payment(Document):
    user_id: str  # ref → User
    plan_id: str  # ref → Plan
    transaction_id: str
    screenshot_url: str  # Cloudinary URL
    status: PaymentStatus = PaymentStatus.PENDING
    admin_note: str = ""
    reviewed_by: Optional[str] = None  # ref → User (admin)
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "payments"
        indexes = [
            "user_id",
            "status",
        ]
