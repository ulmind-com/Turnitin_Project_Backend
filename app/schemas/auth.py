from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    credits: int
    account_status: str
    active_plan: dict | None = None  # populated plan object
    plan_expires_at: str | None = None
    created_at: str

    class Config:
        from_attributes = True


class RefreshRequest(BaseModel):
    refresh_token: str
