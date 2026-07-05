from pydantic import BaseModel
from typing import Optional


class DocumentResponse(BaseModel):
    id: str
    original_file_name: str
    file_type: str
    ai_scan_status: str | None = None
    plagiarism_scan_status: str | None = None
    plagiarism_score: float = 0.0
    ai_score: float = 0.0
    scanned_at: str | None = None
    created_at: str


class DocumentDetailResponse(BaseModel):
    id: str
    original_file_name: str
    file_type: str
    extracted_text: str
    ai_scan_status: str | None = None
    plagiarism_scan_status: str | None = None
    ai_result: dict | None = None
    plagiarism_result: dict | None = None
    integrity_flags: list[dict] = []
    metadata: dict = {}
    scanned_at: str | None = None
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class UploadResponse(BaseModel):
    """Returned immediately after upload — no scan is auto-triggered."""
    document_id: str
    original_file_name: str
    file_type: str
    created_at: str
    message: str


class AnalysisQueuedResponse(BaseModel):
    """Returned when an analysis job is successfully enqueued."""
    document_id: str
    job_id: Optional[str] = None
    status: str
    message: str

