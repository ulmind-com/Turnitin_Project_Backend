from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from app.models.user import User
from app.models.document import ScanDocument, ScanStatus
from app.schemas.document import DocumentResponse, DocumentDetailResponse, DocumentListResponse
from app.services.parser_service import parse_document
from app.services.scanner_service import scan_document
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/api/documents", tags=["Documents"])

# Max file size: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a PDF or DOCX file for plagiarism and AI detection scanning.
    Deducts 1 credit from the user's balance. Scan runs in the background.
    """
    # Check credits
    if current_user.credits <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits. Please purchase a plan to continue scanning.",
        )

    # Validate file type
    filename = file.filename or ""
    file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed_extensions = {"pdf", "docx"}

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF and DOCX files are supported",
        )

    import io

    # Stream the incoming file in manageable 1MB chunks to prevent memory exhaustion (DoS/OOM)
    chunk_size = 1024 * 1024  # 1MB
    accumulated_bytes = io.BytesIO()
    total_bytes_read = 0

    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            
            total_bytes_read += len(chunk)
            if total_bytes_read > MAX_FILE_SIZE:
                accumulated_bytes.close()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="File size exceeds 10MB limit",
                )
            accumulated_bytes.write(chunk)
        
        file_bytes = accumulated_bytes.getvalue()
    finally:
        accumulated_bytes.close()

    # Parse document text
    file_type = "pdf" if file_ext == "pdf" else "docx"
    extracted_text = await parse_document(file_bytes, file_type)

    if not extracted_text or len(extracted_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract meaningful text from the document",
        )

    # Deduct 1 credit
    current_user.credits -= 1
    await current_user.save()

    # Create document record
    doc = ScanDocument(
        user_id=str(current_user.id),
        original_file_name=filename,
        file_type=file_type,
        extracted_text=extracted_text,
        scan_status=ScanStatus.QUEUED,
    )
    await doc.insert()

    # Start scan in background
    background_tasks.add_task(scan_document, doc)

    return DocumentResponse(
        id=str(doc.id),
        original_file_name=doc.original_file_name,
        file_type=doc.file_type,
        scan_status=doc.scan_status,
        plagiarism_score=0,
        ai_score=0,
        scanned_at=None,
        created_at=doc.created_at.isoformat(),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(current_user: User = Depends(get_current_user)):
    """List all documents uploaded by the current user."""
    docs = await ScanDocument.find(
        ScanDocument.user_id == str(current_user.id),
    ).sort("-created_at").to_list()

    documents = []
    for doc in docs:
        documents.append(
            DocumentResponse(
                id=str(doc.id),
                original_file_name=doc.original_file_name,
                file_type=doc.file_type,
                scan_status=doc.scan_status,
                plagiarism_score=doc.scan_result.plagiarism_score if doc.scan_result else 0,
                ai_score=doc.scan_result.ai_score if doc.scan_result else 0,
                scanned_at=doc.scanned_at.isoformat() if doc.scanned_at else None,
                created_at=doc.created_at.isoformat(),
            )
        )

    return DocumentListResponse(documents=documents, total=len(documents))


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get full document details including scan results."""
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return DocumentDetailResponse(
        id=str(doc.id),
        original_file_name=doc.original_file_name,
        file_type=doc.file_type,
        extracted_text=doc.extracted_text,
        scan_status=doc.scan_status,
        scan_result=doc.scan_result.model_dump() if doc.scan_result else None,
        scanned_at=doc.scanned_at.isoformat() if doc.scanned_at else None,
        created_at=doc.created_at.isoformat(),
    )


@router.get("/{document_id}/report")
async def get_document_report(
    document_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the formatted report data for the split-screen view.
    Returns text with chunk mapping + matched sources.
    """
    doc = await ScanDocument.get(document_id)
    if not doc or doc.user_id != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if doc.scan_status == ScanStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scan failed: {doc.scan_result.summary if doc.scan_result else 'Unknown error'}",
        )

    if doc.scan_status != ScanStatus.COMPLETED or not doc.scan_result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scan is not yet completed",
        )

    result = doc.scan_result

    return {
        "document_id": str(doc.id),
        "file_name": doc.original_file_name,
        "overall_plagiarism_score": result.plagiarism_score,
        "overall_ai_score": result.ai_score,
        "summary": result.summary,
        "extracted_text": doc.extracted_text,
        "chunks": [chunk.model_dump() for chunk in result.chunks],
        "matched_sources": [source.model_dump() for source in result.matched_sources],
    }
