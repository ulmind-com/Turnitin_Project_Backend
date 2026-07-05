from pydantic import BaseModel, Field
from typing import Optional


class DocumentResponse(BaseModel):
    id: str
    original_file_name: str
    file_type: str
    scan_status: str
    plagiarism_score: float = 0.0
    ai_score: float = 0.0
    scanned_at: str | None = None
    created_at: str


class DocumentDetailResponse(BaseModel):
    id: str
    original_file_name: str
    file_type: str
    extracted_text: str
    scan_status: str
    scan_result: dict | None = None
    scanned_at: str | None = None
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
