from beanie import Document
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime, timezone


class ScanStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MatchedSource(BaseModel):
    url: str = ""
    title: str = ""
    matched_text: str = ""  # snippet from web source
    original_text: str = ""  # snippet from uploaded document
    similarity_score: float = 0.0
    chunk_index: int = 0


class ChunkResult(BaseModel):
    index: int = 0
    text: str = ""
    plagiarism_score: float = 0.0
    ai_score: float = 0.0
    sources: list[dict] = Field(default_factory=list)  # [{url, title, similarity}]


class ScanResult(BaseModel):
    plagiarism_score: float = 0.0  # 0–100%
    ai_score: float = 0.0  # 0–100%
    summary: str = ""
    matched_sources: list[MatchedSource] = Field(default_factory=list)
    chunks: list[ChunkResult] = Field(default_factory=list)


class ScanDocument(Document):
    user_id: str  # ref → User
    original_file_name: str
    file_type: str = ""  # "pdf" | "docx"
    extracted_text: str = ""
    scan_status: ScanStatus = ScanStatus.QUEUED
    scan_result: Optional[ScanResult] = None
    scanned_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "documents"
        indexes = [
            "user_id",
            "scan_status",
        ]
