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
    matched_text: str = ""
    original_text: str = ""
    similarity_score: float = 0.0
    chunk_index: int = 0


class ChunkResult(BaseModel):
    index: int = 0
    text: str = ""
    plagiarism_score: float = 0.0
    ai_score: float = 0.0
    sources: list[dict] = Field(default_factory=list)


class AIResult(BaseModel):
    """Stores the result of the isolated AI detection engine."""
    ai_score: float = 0.0
    summary: str = ""
    # Raw statistical heuristics computed before the LLM call (perplexity/burstiness proxies)
    heuristics: dict = Field(default_factory=dict)


class PlagiarismResult(BaseModel):
    """Stores the result of the isolated plagiarism detection engine."""
    plagiarism_score: float = 0.0
    summary: str = ""
    matched_sources: list[MatchedSource] = Field(default_factory=list)
    chunks: list[ChunkResult] = Field(default_factory=list)


class ScanDocument(Document):
    user_id: str
    original_file_name: str
    file_type: str = ""
    extracted_text: str = ""

    # Independent lifecycle statuses — None means the analysis has not been triggered yet
    ai_scan_status: Optional[ScanStatus] = None
    plagiarism_scan_status: Optional[ScanStatus] = None

    # Independent results — updated atomically via $set so they never clobber each other
    ai_result: Optional[AIResult] = None
    plagiarism_result: Optional[PlagiarismResult] = None

    integrity_flags: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    scanned_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "documents"
        indexes = [
            "user_id",
            "ai_scan_status",
            "plagiarism_scan_status",
        ]
