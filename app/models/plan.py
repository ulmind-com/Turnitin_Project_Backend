from beanie import Document
from pydantic import Field
from typing import Optional


class Plan(Document):
    name: str  # "Basic Plan", "Premium Plan", "Max Plan"
    slug: str  # "basic", "premium", "max"
    credits: int  # 10, 25, 50
    price: float  # Display price
    description: Optional[str] = None
    is_active: bool = True

    class Settings:
        name = "plans"
