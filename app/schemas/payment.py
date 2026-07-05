from pydantic import BaseModel, Field
from typing import Optional


class PaymentSubmitRequest(BaseModel):
    plan_id: str
    transaction_id: str = Field(..., min_length=3, max_length=100)


class PaymentResponse(BaseModel):
    id: str
    user_id: str
    plan_id: str
    plan_name: str = ""
    transaction_id: str
    screenshot_url: str
    status: str
    admin_note: str = ""
    reviewed_at: str | None = None
    created_at: str


class PaymentListResponse(BaseModel):
    payments: list[PaymentResponse]
    total: int
