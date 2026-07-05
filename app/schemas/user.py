from pydantic import BaseModel, Field
from typing import Optional


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[str] = None


class DashboardResponse(BaseModel):
    credits: int
    total_scans: int
    completed_scans: int
    active_plan: dict | None = None
    account_status: str
    pending_payment: bool = False
    recent_documents: list[dict] = []
