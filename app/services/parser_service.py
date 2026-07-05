import io
import PyPDF2
import pdfplumber
from docx import Document as DocxDocument


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract text from a PDF file using pdfplumber (primary) with PyPDF2 fallback.

    Args:
        file_bytes: Raw bytes of the PDF file.

    Returns:
        Extracted text as a single string.
    """
    text_parts = []

    # Primary: pdfplumber (better at handling complex layouts)
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception:
        pass

    # Fallback: PyPDF2 if pdfplumber extracted nothing
    if not text_parts:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        except Exception:
            pass

    full_text = "\n".join(text_parts)
    return full_text.strip()


async def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extract text from a DOCX file using python-docx.

    Args:
        file_bytes: Raw bytes of the DOCX file.

    Returns:
        Extracted text as a single string.
    """
    try:
        doc = DocxDocument(io.BytesIO(file_bytes))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(paragraphs).strip()
    except Exception:
        return ""


async def parse_document(file_bytes: bytes, file_type: str) -> str:
    """
    Parse a document and extract its text content.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        file_type: Either 'pdf' or 'docx'.

    Returns:
        Extracted text content.
    """
    if file_type == "pdf":
        return await extract_text_from_pdf(file_bytes)
    elif file_type == "docx":
        return await extract_text_from_docx(file_bytes)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
