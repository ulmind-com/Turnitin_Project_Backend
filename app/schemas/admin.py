from pydantic import BaseModel, Field
from typing import Optional


class ApprovePaymentRequest(BaseModel):
    pass  # No body needed, action is in the route


class RejectPaymentRequest(BaseModel):
    admin_note: str = Field("", max_length=500)


class EditCreditsRequest(BaseModel):
    credits: int = Field(..., ge=0)


class AssignPlanRequest(BaseModel):
    plan_id: str


class CreatePlanRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50)
    credits: int = Field(..., ge=1)
    price: float = Field(..., ge=0)
    currency: str = Field("INR", max_length=10)
    currency_symbol: str = Field("₹", max_length=5)
    description: Optional[str] = None
    features: list[str] = []
    is_active: bool = True
    display_order: int = 0


class UpdatePlanRequest(BaseModel):
    name: Optional[str] = None
    credits: Optional[int] = Field(None, ge=1)
    price: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = None
    currency_symbol: Optional[str] = None
    description: Optional[str] = None
    features: Optional[list[str]] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None


class SuspendUserRequest(BaseModel):
    suspended: bool  # True to suspend, False to unsuspend


class AdminUserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    credits: int
    account_status: str
    active_plan: dict | None = None
    plan_expires_at: str | None = None
    total_scans: int = 0
    created_at: str


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int


class AdminDashboardResponse(BaseModel):
    total_users: int
    total_scans: int
    completed_scans: int
    pending_payments: int
    total_credits_distributed: int
    plans_breakdown: list[dict] = []


class AdminPaymentResponse(BaseModel):
    id: str
    user_id: str
    user_name: str = ""
    user_email: str = ""
    plan_id: str
    plan_name: str = ""
    plan_credits: int = 0
    transaction_id: str
    screenshot_url: str
    status: str
    admin_note: str = ""
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    created_at: str


class AdminPaymentListResponse(BaseModel):
    payments: list[AdminPaymentResponse]
    total: int
