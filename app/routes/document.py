import io

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from fastapi.responses import Response
from beanie import PydanticObjectId

from app.models.user import User
from app.models.document import ScanDocument, ScanStatus
from app.schemas.document import (
    DocumentResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    UploadResponse,
    AnalysisQueuedResponse,
    GradeRequest,
)
from app.services.parser_service import parse_document
from app.utils.dependencies import get_current_user
from app.services.scanner_service import analyze_ai_job, analyze_plagiarism_job
from app.utils.text_processor import clean_text, tokenize_text, filter_bibliography_and_quotes
from app.utils.integrity_checker import check_integrity

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

    raw_extracted_text, page_count = await parse_document(file_bytes, file_ext)

    if not raw_extracted_text or len(raw_extracted_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract meaningful text from the document.",
        )

    # Clean and process text
    integrity_flags = check_integrity(raw_extracted_text)
    cleaned_text, tokens = filter_bibliography_and_quotes(raw_extracted_text)

    metadata = {
        "file_size": len(file_bytes),
        "page_count": page_count,
        "character_count": len(raw_extracted_text),
        "token_count": len(tokens),
    }

    # Upload original file to Cloudinary for later PDF report generation
    try:
        from app.services.cloudinary_service import upload_raw_file
        original_file_url = await upload_raw_file(file_bytes, folder="original_documents")
    except Exception:
        original_file_url = ""

    # Deduct credit + insert document
    # Note: MongoDB Atlas free/shared tier (M0) does not support multi-document
    # transactions, so we perform sequential operations instead.
    await current_user.update({"$inc": {"credits": -1}})
    doc = ScanDocument(
        user_id=str(current_user.id),
        original_file_name=filename,
        file_type=file_ext,
        original_file_url=original_file_url,
        extracted_text=cleaned_text,
        integrity_flags=integrity_flags,
        metadata=metadata,
        ai_scan_status=None,
        plagiarism_scan_status=None,
    )
    await doc.insert()

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
    background_tasks: BackgroundTasks,
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

    # Mark queued immediately so duplicate triggers are blocked
    await doc.update({"$set": {"ai_scan_status": ScanStatus.QUEUED.value}})

    background_tasks.add_task(analyze_ai_job, doc_id)

    return AnalysisQueuedResponse(
        document_id=doc_id,
        job_id="background",
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
    background_tasks: BackgroundTasks,
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

    await doc.update({"$set": {"plagiarism_scan_status": ScanStatus.QUEUED.value}})

    background_tasks.add_task(analyze_plagiarism_job, doc_id)

    return AnalysisQueuedResponse(
        document_id=doc_id,
        job_id="background",
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
        integrity_flags=doc.integrity_flags,
        metadata=doc.metadata,
        grade=doc.grade,
        feedback=doc.feedback,
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

    # --- Compute Plagiarism Breakdown ---
    plag_breakdown = None
    if doc.plagiarism_result:
        plag_score = doc.plagiarism_result.plagiarism_score
        chunks = doc.plagiarism_result.chunks
        not_cited_chunks, missing_quote_chunks, missing_citation_chunks, cited_quoted_chunks = [], [], [], []

        for chunk in chunks:
            if chunk.plagiarism_score < 10:
                continue
            match_type = "not_cited"
            for src_info in chunk.sources:
                if isinstance(src_info, dict) and src_info.get("match_type"):
                    match_type = src_info["match_type"]
                    break

            if match_type == "missing_quote": missing_quote_chunks.append(chunk)
            elif match_type == "missing_citation": missing_citation_chunks.append(chunk)
            elif match_type == "cited_quoted": cited_quoted_chunks.append(chunk)
            elif match_type != "original": not_cited_chunks.append(chunk)

        total_flagged = len(not_cited_chunks) + len(missing_quote_chunks) + len(missing_citation_chunks) + len(cited_quoted_chunks)

        if total_flagged > 0 and plag_score > 0:
            not_cited_pct = round((len(not_cited_chunks) / total_flagged) * plag_score, 1)
            missing_quote_pct = round((len(missing_quote_chunks) / total_flagged) * plag_score, 1)
            missing_citation_pct = round((len(missing_citation_chunks) / total_flagged) * plag_score, 1)
            cited_quoted_pct = round((len(cited_quoted_chunks) / total_flagged) * plag_score, 1)
        else:
            not_cited_pct, missing_quote_pct, missing_citation_pct, cited_quoted_pct = round(plag_score, 1), 0, 0, 0

        # Source breakdown
        internet_score = 0.0
        student_score = 0.0
        if doc.plagiarism_result.matched_sources:
            for src in doc.plagiarism_result.matched_sources:
                if src.url == "Submitted Work (Student Paper)": student_score += src.similarity_score
                else: internet_score += src.similarity_score
        total_sim = internet_score + student_score
        internet_pct = round((internet_score / total_sim) * plag_score, 1) if total_sim > 0 else 0
        student_pct = round((student_score / total_sim) * plag_score, 1) if total_sim > 0 else 0

        # Matched Sources mapped
        matched_sources = []
        if doc.plagiarism_result.matched_sources:
            for src in doc.plagiarism_result.matched_sources:
                if src.similarity_score < 5: continue
                source_type = "student" if src.url == "Submitted Work (Student Paper)" else "internet"
                total_src_sim = sum(s.similarity_score for s in doc.plagiarism_result.matched_sources if s.similarity_score >= 5)
                sim_pct = round((src.similarity_score / total_src_sim) * plag_score, 1) if total_src_sim > 0 else 0
                matched_sources.append({
                    "title": src.title or src.url,
                    "url": src.url,
                    "similarity_pct": max(sim_pct, 1) if sim_pct > 0 else 0,
                    "source_type": source_type,
                    "raw_score": src.similarity_score
                })
            matched_sources.sort(key=lambda x: x["raw_score"], reverse=True)
            matched_sources = matched_sources[:15]

        # Filtered sections
        filtered_sections = []
        text_lower = (doc.extracted_text or "").lower()
        if "bibliography" in text_lower or "references" in text_lower: filtered_sections.append("Bibliography")
        if "abstract" in text_lower: filtered_sections.append("Abstract")

        plag_breakdown = {
            "match_groups": {
                "not_cited": {"count": len(not_cited_chunks), "pct": not_cited_pct},
                "missing_quote": {"count": len(missing_quote_chunks), "pct": missing_quote_pct},
                "missing_citation": {"count": len(missing_citation_chunks), "pct": missing_citation_pct},
                "cited_quoted": {"count": len(cited_quoted_chunks), "pct": cited_quoted_pct}
            },
            "sources_breakdown": {
                "internet_pct": internet_pct,
                "publication_pct": 0,
                "student_pct": student_pct
            },
            "filtered_sections": filtered_sections,
            "matched_sources": matched_sources
        }

    return {
        "document_id": str(doc.id),
        "file_name": doc.original_file_name,
        "file_type": doc.file_type,
        "scanned_at": doc.scanned_at.isoformat() if doc.scanned_at else doc.created_at.isoformat(),
        "metadata": doc.metadata,
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
        "plagiarism_breakdown": plag_breakdown,
        "integrity_flags": doc.integrity_flags or [],
        "integrity_flag_count": len(doc.integrity_flags) if doc.integrity_flags else 0,
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
        )
    }


# ── GET /{document_id}/download-report ──────────────────────────────────────


@router.get("/{document_id}/download-report")
async def download_document_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Generate and stream a combined Turnitin-style PDF originality report.
    Requires both AI and plagiarism checks to have completed.
    """
    doc = await ScanDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    # Allow admin to download any report, regular users only their own
    if doc.user_id != str(current_user.id) and current_user.role != "admin":
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


# ── GET /{document_id}/download-report/plagiarism ───────────────────────────


@router.get("/{document_id}/download-report/plagiarism")
async def download_plagiarism_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Generate and download a Turnitin-style Plagiarism Similarity Report PDF."""
    doc = await ScanDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if doc.user_id != str(current_user.id) and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc.plagiarism_scan_status != ScanStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plagiarism scan has not completed yet.",
        )

    try:
        from app.utils.report_generator import build_plagiarism_report_pdf
        pdf_bytes = build_plagiarism_report_pdf(doc)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating plagiarism PDF: {str(e)}",
        )

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    report_filename = f"Similarity_Report_{safe_filename.rsplit('.', 1)[0]}.pdf"

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_filename}"'},
    )


# ── GET /{document_id}/download-report/ai ────────────────────────────────────


@router.get("/{document_id}/download-report/ai")
async def download_ai_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Generate and download a Turnitin-style AI Writing Detection Report PDF."""
    doc = await ScanDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if doc.user_id != str(current_user.id) and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc.ai_scan_status != ScanStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI scan has not completed yet.",
        )

    try:
        from app.utils.report_generator import build_ai_report_pdf
        pdf_bytes = build_ai_report_pdf(doc)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating AI report PDF: {str(e)}",
        )

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    report_filename = f"AI_Writing_Report_{safe_filename.rsplit('.', 1)[0]}.pdf"

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_filename}"'},
    )


# ── GET /{document_id}/download-highlighted/* (For Frontend PDF Merging) ─────

@router.get("/{document_id}/download-highlighted")
async def download_highlighted_combined(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download ONLY the original PDF with combined highlights (No summary pages). For frontend merging."""
    doc = await ScanDocument.get(document_id)
    if not doc or (doc.user_id != str(current_user.id) and current_user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    try:
        from app.utils.report_generator import build_highlighted_original_combined
        pdf_bytes = build_highlighted_original_combined(doc)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="Highlighted_{safe_filename}.pdf"'})


@router.get("/{document_id}/download-highlighted/plagiarism")
async def download_highlighted_plagiarism(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download ONLY the original PDF with plagiarism highlights (No summary pages). For frontend merging."""
    doc = await ScanDocument.get(document_id)
    if not doc or (doc.user_id != str(current_user.id) and current_user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    try:
        from app.utils.report_generator import build_highlighted_original_plagiarism
        pdf_bytes = build_highlighted_original_plagiarism(doc)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="Highlighted_Plagiarism_{safe_filename}.pdf"'})


@router.get("/{document_id}/download-highlighted/ai")
async def download_highlighted_ai(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download ONLY the original PDF with AI highlights (No summary pages). For frontend merging."""
    doc = await ScanDocument.get(document_id)
    if not doc or (doc.user_id != str(current_user.id) and current_user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    try:
        from app.utils.report_generator import build_highlighted_original_ai
        pdf_bytes = build_highlighted_original_ai(doc)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    safe_filename = "".join(c for c in doc.original_file_name if c.isalnum() or c in "._- ")
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="Highlighted_AI_{safe_filename}.pdf"'})



# ── POST /{document_id}/grade ────────────────────────────────────────────────


@router.post("/{document_id}/grade")
async def save_document_grade(
    document_id: str,
    payload: GradeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Save or update the instructor's numerical grade and feedback comments.
    """
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    await doc.update({"$set": {
        "grade": payload.grade,
        "feedback": payload.feedback
    }})

    return {"message": "Grade and feedback updated successfully."}


# ── DELETE /{document_id} ──────────────────────────────────────────────────


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Delete a document and its associated repository entry.
    Users can only delete their own documents.
    """
    doc = await ScanDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own documents.")

    # Also remove from the internal plagiarism repository
    from app.models.repository import SubmittedPaper
    await SubmittedPaper.find(
        SubmittedPaper.document_id == document_id
    ).delete()

    await doc.delete()

    return {"message": f"Document '{doc.original_file_name}' deleted successfully."}
