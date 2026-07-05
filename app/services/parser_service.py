import io
import PyPDF2
import pdfplumber
from docx import Document as DocxDocument


async def extract_text_from_pdf(file_bytes: bytes) -> tuple[str, int]:
    """
    Extract text and page count from a PDF file using pdfplumber with PyPDF2 fallback.

    Returns:
        tuple containing (extracted_text, page_count).
    """
    text_parts = []
    page_count = 1

    # Primary: pdfplumber (better at handling complex layouts)
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception:
        pass

    # Fallback: PyPDF2 if pdfplumber extracted nothing or failed
    if not text_parts:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            page_count = len(reader.pages)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        except Exception:
            pass

    full_text = "\n".join(text_parts)
    return full_text.strip(), max(1, page_count)


async def extract_text_from_docx(file_bytes: bytes) -> tuple[str, int]:
    """
    Extract text and estimated page count from a DOCX file.

    Returns:
        tuple containing (extracted_text, page_count).
    """
    try:
        doc = DocxDocument(io.BytesIO(file_bytes))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        full_text = "\n".join(paragraphs).strip()
        # Estimate pages based on typical word count (approx 350 words per page)
        words_count = len(full_text.split())
        page_count = max(1, words_count // 350)
        return full_text, page_count
    except Exception:
        return "", 1


async def parse_document(file_bytes: bytes, file_type: str) -> tuple[str, int]:
    """
    Parse a document and extract its text content and page count.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        file_type: Either 'pdf' or 'docx'.

    Returns:
        tuple containing (extracted_text, page_count).
    """
    if file_type == "pdf":
        return await extract_text_from_pdf(file_bytes)
    elif file_type == "docx":
        return await extract_text_from_docx(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
