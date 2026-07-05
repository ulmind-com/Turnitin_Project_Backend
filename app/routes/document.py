import io

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from beanie import PydanticObjectId

from app.models.user import User
from app.models.document import ScanDocument, ScanStatus
from app.schemas.document import (
    DocumentResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    UploadResponse,
    AnalysisQueuedResponse,
)
from app.services.parser_service import parse_document
from app.utils.dependencies import get_current_user, get_arq_pool

router = APIRouter(prefix="/api/documents", tags=["Documents"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ── POST /upload ────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Stream-ingest a PDF or DOCX, parse its text, deduct 1 credit (ACID
    transaction), and persist the document record.

    Analysis is NOT triggered automatically. Call the dedicated
    `/analyze/ai` and `/analyze/plagiarism` endpoints separately.
    Returns the `document_id` needed for those calls.
    """
    if current_user.credits <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits. Please purchase a plan to continue scanning.",
        )

    filename = file.filename or ""
    file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if file_ext not in {"pdf", "docx"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF and DOCX files are supported.",
        )

    # Buffered streaming — prevents OOM on large uploads
    buffer = io.BytesIO()
    total_bytes = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MB per read
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File size exceeds the 10 MB limit.",
                )
            buffer.write(chunk)
        file_bytes = buffer.getvalue()
    finally:
        buffer.close()

    file_type = "pdf" if file_ext == "pdf" else "docx"
    extracted_text = await parse_document(file_bytes, file_type)

    if not extracted_text or len(extracted_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract meaningful text from the document.",
        )

    # Deduct credit + insert document atomically to prevent race conditions
    db_client = User.get_motor_collection().database.client
    async with db_client.start_session() as session:
        async with session.start_transaction():
            await current_user.update({"$inc": {"credits": -1}}, session=session)
            doc = ScanDocument(
                user_id=str(current_user.id),
                original_file_name=filename,
                file_type=file_type,
                extracted_text=extracted_text,
                # Statuses are None until the caller explicitly triggers each engine
                ai_scan_status=None,
                plagiarism_scan_status=None,
            )
            await doc.insert(session=session)

    return UploadResponse(
        document_id=str(doc.id),
        original_file_name=doc.original_file_name,
        file_type=doc.file_type,
        created_at=doc.created_at.isoformat(),
        message=(
            "Document uploaded successfully. "
            "Trigger AI analysis via POST /analyze/ai and "
            "plagiarism analysis via POST /analyze/plagiarism."
        ),
    )


# ── POST /{doc_id}/analyze/ai ───────────────────────────────────────────────


@router.post(
    "/{doc_id}/analyze/ai",
    response_model=AnalysisQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_ai_analysis(
    doc_id: str,
    arq_pool=Depends(get_arq_pool),
    current_user: User = Depends(get_current_user),
):
    """
    Enqueue the AI detection background job for the given document.

    Idempotency: returns 409 if the job is already queued or processing.
    Re-triggers are allowed after `completed` or `failed`.
    """
    doc = await ScanDocument.get(doc_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc.ai_scan_status in (ScanStatus.QUEUED, ScanStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AI analysis is already {doc.ai_scan_status.value}.",
        )

    job = await arq_pool.enqueue_job("analyze_ai_job", doc_id)

    # Mark queued immediately so duplicate triggers are blocked
    await doc.update({"$set": {"ai_scan_status": ScanStatus.QUEUED.value}})

    return AnalysisQueuedResponse(
        document_id=doc_id,
        job_id=job.job_id,
        status=ScanStatus.QUEUED.value,
        message="AI detection job queued. Poll GET /{doc_id} for status updates.",
    )


# ── POST /{doc_id}/analyze/plagiarism ──────────────────────────────────────


@router.post(
    "/{doc_id}/analyze/plagiarism",
    response_model=AnalysisQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_plagiarism_analysis(
    doc_id: str,
    arq_pool=Depends(get_arq_pool),
    current_user: User = Depends(get_current_user),
):
    """
    Enqueue the plagiarism detection background job for the given document.

    Idempotency: returns 409 if the job is already queued or processing.
    Re-triggers are allowed after `completed` or `failed`.
    """
    doc = await ScanDocument.get(doc_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc.plagiarism_scan_status in (ScanStatus.QUEUED, ScanStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plagiarism analysis is already {doc.plagiarism_scan_status.value}.",
        )

    job = await arq_pool.enqueue_job("analyze_plagiarism_job", doc_id)

    await doc.update({"$set": {"plagiarism_scan_status": ScanStatus.QUEUED.value}})

    return AnalysisQueuedResponse(
        document_id=doc_id,
        job_id=job.job_id,
        status=ScanStatus.QUEUED.value,
        message="Plagiarism detection job queued. Poll GET /{doc_id} for status updates.",
    )


# ── GET / (list) ────────────────────────────────────────────────────────────


@router.get("", response_model=DocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """List all documents uploaded by the current user, newest first."""
    docs = await ScanDocument.find(
        ScanDocument.user_id == str(current_user.id),
    ).sort("-created_at").to_list()

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=str(doc.id),
                original_file_name=doc.original_file_name,
                file_type=doc.file_type,
                ai_scan_status=doc.ai_scan_status.value if doc.ai_scan_status else None,
                plagiarism_scan_status=(
                    doc.plagiarism_scan_status.value if doc.plagiarism_scan_status else None
                ),
                plagiarism_score=(
                    doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0.0
                ),
                ai_score=doc.ai_result.ai_score if doc.ai_result else 0.0,
                scanned_at=doc.scanned_at.isoformat() if doc.scanned_at else None,
                created_at=doc.created_at.isoformat(),
            )
            for doc in docs
        ],
        total=len(docs),
    )


# ── GET /{document_id} ──────────────────────────────────────────────────────


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Full document details including both analysis results (partial results are returned as they complete)."""
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    return DocumentDetailResponse(
        id=str(doc.id),
        original_file_name=doc.original_file_name,
        file_type=doc.file_type,
        extracted_text=doc.extracted_text,
        ai_scan_status=doc.ai_scan_status.value if doc.ai_scan_status else None,
        plagiarism_scan_status=(
            doc.plagiarism_scan_status.value if doc.plagiarism_scan_status else None
        ),
        ai_result=doc.ai_result.model_dump() if doc.ai_result else None,
        plagiarism_result=doc.plagiarism_result.model_dump() if doc.plagiarism_result else None,
        scanned_at=doc.scanned_at.isoformat() if doc.scanned_at else None,
        created_at=doc.created_at.isoformat(),
    )


# ── GET /{document_id}/report ───────────────────────────────────────────────


@router.get("/{document_id}/report")
async def get_document_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Formatted report for the split-screen view.
    Returns whatever analysis results are available — both engines can complete
    independently so partial reports are valid and useful.
    """
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if (
        doc.ai_scan_status == ScanStatus.FAILED
        or doc.plagiarism_scan_status == ScanStatus.FAILED
    ):
        failing_engine = (
            "AI detection"
            if doc.ai_scan_status == ScanStatus.FAILED
            else "Plagiarism detection"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{failing_engine} scan failed. Re-trigger via the analyze endpoint.",
        )

    if not doc.ai_result and not doc.plagiarism_result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Neither analysis has completed yet.",
        )

    return {
        "document_id": str(doc.id),
        "file_name": doc.original_file_name,
        "ai_scan_status": doc.ai_scan_status.value if doc.ai_scan_status else None,
        "plagiarism_scan_status": (
            doc.plagiarism_scan_status.value if doc.plagiarism_scan_status else None
        ),
        "overall_ai_score": doc.ai_result.ai_score if doc.ai_result else None,
        "overall_plagiarism_score": (
            doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else None
        ),
        "ai_summary": doc.ai_result.summary if doc.ai_result else None,
        "plagiarism_summary": doc.plagiarism_result.summary if doc.plagiarism_result else None,
        "ai_heuristics": doc.ai_result.heuristics if doc.ai_result else None,
        "extracted_text": doc.extracted_text,
        "chunks": (
            [c.model_dump() for c in doc.plagiarism_result.chunks]
            if doc.plagiarism_result
            else []
        ),
        "matched_sources": (
            [s.model_dump() for s in doc.plagiarism_result.matched_sources]
            if doc.plagiarism_result
            else []
        ),
    }


# ── GET /{document_id}/download-report ──────────────────────────────────────


@router.get("/{document_id}/download-report")
async def download_document_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Generate and stream a Turnitin-style PDF originality report.
    Only available if both AI and plagiarism checks have completed.
    """
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if (
        doc.ai_scan_status != ScanStatus.COMPLETED
        or doc.plagiarism_scan_status != ScanStatus.COMPLETED
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot download PDF report until both AI and plagiarism scans are completed successfully.",
        )

    try:
        from app.utils.report_generator import build_report_pdf
        pdf_bytes = build_report_pdf(doc)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating PDF report: {str(e)}",
        )

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    report_filename = f"Originality_Report_{safe_filename.rsplit('.', 1)[0]}.pdf"

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{report_filename}"'
        },
    )
