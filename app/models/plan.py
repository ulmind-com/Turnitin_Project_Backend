from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime, timezone


class Plan(Document):
    name: str  # "Basic Plan", "Premium Plan", "Max Plan"
    slug: str  # "basic", "premium", "max"
    credits: int  # 10, 25, 50
    price: float  # Display price
    currency: str = "INR"  # Currency code: INR, USD, EUR, BDT etc.
    currency_symbol: str = "₹"  # Display symbol: ₹, $, €, ৳ etc.
    description: Optional[str] = None
    features: list[str] = []  # ["10 scans", "AI Detection", "PDF Reports"]
    is_active: bool = True
    display_order: int = 0  # For sorting plans on frontend
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "plans"
