"""
Turnitin-style PDF report generator.

NEW APPROACH: Preserves the original uploaded PDF and overlays highlight
annotations directly on it. Summary/overview pages are prepended.

Produces three report types:
  1. Plagiarism Similarity Report (light brown highlights on original PDF)
  2. AI Writing Detection Report (sky blue highlights on original PDF)
  3. Combined Originality Report (both)
"""

import io
import os
import re
import logging
import fitz  # PyMuPDF
import httpx
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from xhtml2pdf import pisa
from app.models.document import ScanDocument

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# Highlight colors (R, G, B) — values 0-1
COLOR_PLAGIARISM = (0.96, 0.82, 0.66)  # Light brown #f5d0a9
COLOR_AI = (0.75, 0.86, 0.99)          # Sky blue #bfdbfe


def _get_template(name: str):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    return env.get_template(name)


def _html_to_pdf_bytes(html: str) -> bytes:
    """Convert HTML string to PDF bytes using xhtml2pdf."""
    buf = io.BytesIO()
    status = pisa.CreatePDF(html, dest=buf)
    if status.err:
        raise Exception("Failed to convert HTML to PDF.")
    pdf = buf.getvalue()
    buf.close()
    return pdf


def _download_original_pdf(url: str) -> bytes:
    """Download the original file from Cloudinary."""
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.content


def _highlight_text_in_pdf(pdf_bytes: bytes, texts_to_highlight: list[str], color: tuple) -> bytes:
    """
    Open a PDF, search for each text snippet, and add colored highlight
    annotations at the exact positions. Returns the modified PDF bytes.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for text in texts_to_highlight:
        if not text or len(text.strip()) < 10:
            continue

        # Search across all pages
        for page in doc:
            # Search for the text — returns list of Rect objects
            text_instances = page.search_for(text, quads=True)
            if text_instances:
                for quad in text_instances:
                    annot = page.add_highlight_annot(quad)
                    annot.set_colors(stroke=color)
                    annot.set_opacity(0.4)
                    annot.update()

    result = doc.tobytes()
    doc.close()
    return result


def _highlight_sentences_in_pdf(pdf_bytes: bytes, sentences: list[str], color: tuple) -> bytes:
    """
    Highlight individual sentences in the PDF. For long sentences, search
    for the first 60 chars to increase match probability.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for sentence in sentences:
        if not sentence or len(sentence.strip()) < 10:
            continue

        # Use shorter search strings for better matching
        # (PDF text extraction might have different whitespace)
        search_text = sentence.strip()
        if len(search_text) > 80:
            search_text = search_text[:80]

        for page in doc:
            text_instances = page.search_for(search_text, quads=True)
            if text_instances:
                for quad in text_instances:
                    annot = page.add_highlight_annot(quad)
                    annot.set_colors(stroke=color)
                    annot.set_opacity(0.4)
                    annot.update()
                break  # Found on this page, move to next sentence

    result = doc.tobytes()
    doc.close()
    return result


def _merge_pdfs(summary_pdf_bytes: bytes, original_pdf_bytes: bytes) -> bytes:
    """Prepend the summary pages before the original document pages."""
    summary_doc = fitz.open(stream=summary_pdf_bytes, filetype="pdf")
    original_doc = fitz.open(stream=original_pdf_bytes, filetype="pdf")

    # Insert summary pages at the beginning of the original
    original_doc.insert_pdf(summary_doc, to_page=-1, start_at=0)

    result = original_doc.tobytes()
    summary_doc.close()
    original_doc.close()
    return result


# ────────────────────────────────────────────────────────────────────────────
#  Collect texts to highlight
# ────────────────────────────────────────────────────────────────────────────

def _get_plagiarism_texts(doc: ScanDocument) -> list[str]:
    """Collect all plagiarized text snippets from scan results."""
    texts = []

    if doc.plagiarism_result and doc.plagiarism_result.chunks:
        for chunk in doc.plagiarism_result.chunks:
            if chunk.plagiarism_score >= 15 and chunk.text:
                texts.append(chunk.text)

    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for source in doc.plagiarism_result.matched_sources:
            if source.similarity_score >= 15 and source.original_text:
                texts.append(source.original_text)

    return texts


def _get_ai_sentences(doc: ScanDocument) -> list[str]:
    """
    Determine which sentences to highlight as AI-generated based on
    the AI score and sentence-level heuristics.
    """
    text = doc.extracted_text
    ai_score = doc.ai_result.ai_score if doc.ai_result else 0

    if ai_score <= 5 or not text:
        return []

    # Split into sentences
    sentence_pattern = re.compile(r'[^.!?]+[.!?]+')
    sentences = [m.group().strip() for m in sentence_pattern.finditer(text)]

    if not sentences:
        return []

    # AI keywords for scoring
    ai_keywords = [
        "delve", "tapestry", "moreover", "furthermore", "testament", "notably",
        "in conclusion", "it is important to note", "consequently", "pivotal",
        "beacon", "comprehensive", "demystify", "multifaceted", "paramount",
        "additionally", "in this context", "in the realm of", "is crucial",
        "when it comes to", "needless to say",
    ]

    # Score each sentence
    scored = []
    for sent in sentences:
        sent_lower = sent.lower()
        score = 0
        word_count = len(sent_lower.split())
        if 15 <= word_count <= 25:
            score += 3
        for kw in ai_keywords:
            if kw in sent_lower:
                score += 10
        if sent_lower.strip().startswith(("this ", "these ", "those ", "such ")):
            score += 2
        scored.append((score, sent))

    # Highlight top N sentences based on AI score percentage
    num_to_highlight = max(1, int(len(sentences) * (ai_score / 100.0)))
    scored.sort(key=lambda x: x[0], reverse=True)

    return [sent for _, sent in scored[:num_to_highlight]]


# ────────────────────────────────────────────────────────────────────────────
#  Summary page generators (HTML → PDF for prepending)
# ────────────────────────────────────────────────────────────────────────────

def _build_plagiarism_summary_pdf(doc: ScanDocument) -> bytes:
    """Generate the summary/overview pages for plagiarism report."""
    metadata = doc.metadata or {}
    scanned_at = (
        doc.scanned_at.strftime("%b %d, %Y, %I:%M %p UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%b %d, %Y, %I:%M %p UTC")
    )

    # Compute match groups
    total_sources = 0
    plag_score = doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0

    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        total_sources = len(doc.plagiarism_result.matched_sources)

    # Compute source breakdown
    internet_score = 0.0
    student_score = 0.0
    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for src in doc.plagiarism_result.matched_sources:
            if src.url == "Submitted Work (Student Paper)":
                student_score += src.similarity_score
            else:
                internet_score += src.similarity_score
    total_sim = internet_score + student_score
    internet_pct = round((internet_score / total_sim) * plag_score, 1) if total_sim > 0 else 0
    student_pct = round((student_score / total_sim) * plag_score, 1) if total_sim > 0 else 0

    # Prepare matched sources
    matched_sources = []
    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for src in doc.plagiarism_result.matched_sources:
            if src.similarity_score < 5:
                continue
            source_type = "student" if src.url == "Submitted Work (Student Paper)" else "internet"
            total_src_sim = sum(s.similarity_score for s in doc.plagiarism_result.matched_sources if s.similarity_score >= 5)
            sim_pct = round((src.similarity_score / total_src_sim) * plag_score, 1) if total_src_sim > 0 else 0
            matched_sources.append({
                "title": src.title or src.url,
                "url": src.url,
                "similarity_pct": max(sim_pct, 1) if sim_pct > 0 else "<1",
                "source_type": source_type,
                "raw_score": src.similarity_score,
            })
        matched_sources.sort(key=lambda x: x["raw_score"], reverse=True)
        matched_sources = matched_sources[:15]

    template = _get_template("plagiarism_report.html")
    html = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type.upper(),
        scanned_at=scanned_at,
        page_count=metadata.get("page_count", "-"),
        word_count=f"{metadata.get('token_count', 0):,}",
        char_count=f"{metadata.get('character_count', 0):,}",
        overall_plagiarism_score=round(plag_score, 1),
        filtered_sections=["Bibliography"],
        integrity_flags=doc.integrity_flags or [],
        integrity_flag_count=len(doc.integrity_flags) if doc.integrity_flags else 0,
        matched_sources=matched_sources,
        highlighted_text=Markup(""),  # No text body — it's in the original PDF
        not_cited_count=total_sources,
        not_cited_pct=round(plag_score, 1),
        missing_quote_count=0, missing_quote_pct=0,
        missing_citation_count=0, missing_citation_pct=0,
        cited_quoted_count=0, cited_quoted_pct=0,
        internet_pct=internet_pct,
        publication_pct=0,
        student_pct=student_pct,
    )

    return _html_to_pdf_bytes(html)


def _build_ai_summary_pdf(doc: ScanDocument) -> bytes:
    """Generate the summary/overview pages for AI report."""
    metadata = doc.metadata or {}
    ai_score = doc.ai_result.ai_score if doc.ai_result else 0
    heuristics = doc.ai_result.heuristics if doc.ai_result else {}

    scanned_at = (
        doc.scanned_at.strftime("%b %d, %Y, %I:%M %p UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%b %d, %Y, %I:%M %p UTC")
    )

    if ai_score >= 76:
        caution_level = "High confidence: AI-generated."
    elif ai_score >= 56:
        caution_level = "Caution: Likely AI-generated."
    elif ai_score >= 36:
        caution_level = "Caution: Review required."
    elif ai_score >= 16:
        caution_level = "Low confidence: Mostly human."
    else:
        caution_level = "No significant AI detected."

    ai_generated_pct = round(ai_score * 0.85, 1) if ai_score > 0 else 0
    ai_paraphrased_pct = round(ai_score * 0.15, 1) if ai_score > 0 else 0

    template = _get_template("ai_report.html")
    html = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type.upper(),
        scanned_at=scanned_at,
        page_count=metadata.get("page_count", "-"),
        word_count=f"{metadata.get('token_count', 0):,}",
        char_count=f"{metadata.get('character_count', 0):,}",
        overall_ai_score=round(ai_score, 1),
        caution_level=caution_level,
        ai_generated_pct=ai_generated_pct,
        ai_paraphrased_pct=ai_paraphrased_pct,
        burstiness=heuristics.get("burstiness"),
        type_token_ratio=heuristics.get("type_token_ratio"),
        avg_sentence_length=heuristics.get("avg_sentence_length"),
        ai_phrase_density=heuristics.get("ai_phrase_density"),
        highlighted_text=Markup(""),  # No text body — it's in the original PDF
    )

    return _html_to_pdf_bytes(html)


# ────────────────────────────────────────────────────────────────────────────
#  Public API: Build Report PDFs
# ────────────────────────────────────────────────────────────────────────────

def build_plagiarism_report_pdf(doc: ScanDocument) -> bytes:
    """
    Build Turnitin-style Plagiarism Report:
    1. Download original PDF from Cloudinary
    2. Add light brown highlights on plagiarized text
    3. Generate summary pages
    4. Merge: summary + highlighted original
    """
    if not doc.original_file_url:
        raise Exception("Original file not available. Please re-upload the document.")

    # Download original
    original_bytes = _download_original_pdf(doc.original_file_url)

    # Get texts to highlight
    plag_texts = _get_plagiarism_texts(doc)

    # Highlight on original PDF
    if plag_texts:
        highlighted_pdf = _highlight_text_in_pdf(original_bytes, plag_texts, COLOR_PLAGIARISM)
    else:
        highlighted_pdf = original_bytes

    # Generate summary pages
    summary_pdf = _build_plagiarism_summary_pdf(doc)

    # Merge: summary first, then highlighted original
    return _merge_pdfs(summary_pdf, highlighted_pdf)


def build_ai_report_pdf(doc: ScanDocument) -> bytes:
    """
    Build Turnitin-style AI Detection Report:
    1. Download original PDF from Cloudinary
    2. Add sky blue highlights on AI-detected sentences
    3. Generate summary pages
    4. Merge: summary + highlighted original
    """
    if not doc.original_file_url:
        raise Exception("Original file not available. Please re-upload the document.")

    # Download original
    original_bytes = _download_original_pdf(doc.original_file_url)

    # Get sentences to highlight
    ai_sentences = _get_ai_sentences(doc)

    # Highlight on original PDF
    if ai_sentences:
        highlighted_pdf = _highlight_sentences_in_pdf(original_bytes, ai_sentences, COLOR_AI)
    else:
        highlighted_pdf = original_bytes

    # Generate summary pages
    summary_pdf = _build_ai_summary_pdf(doc)

    # Merge: summary first, then highlighted original
    return _merge_pdfs(summary_pdf, highlighted_pdf)


def build_report_pdf(doc: ScanDocument) -> bytes:
    """
    Build combined report (both plagiarism + AI highlights on original PDF).
    Backward compatible endpoint.
    """
    if not doc.original_file_url:
        raise Exception("Original file not available. Please re-upload the document.")

    # Download original
    original_bytes = _download_original_pdf(doc.original_file_url)

    # Apply plagiarism highlights first (light brown)
    plag_texts = _get_plagiarism_texts(doc)
    if plag_texts:
        original_bytes = _highlight_text_in_pdf(original_bytes, plag_texts, COLOR_PLAGIARISM)

    # Then apply AI highlights (sky blue)
    ai_sentences = _get_ai_sentences(doc)
    if ai_sentences:
        original_bytes = _highlight_sentences_in_pdf(original_bytes, ai_sentences, COLOR_AI)

    # Generate both summary pages and merge
    plag_summary = _build_plagiarism_summary_pdf(doc)
    ai_summary = _build_ai_summary_pdf(doc)

    # Merge: AI summary + Plagiarism summary + highlighted original
    combined_summary = fitz.open(stream=ai_summary, filetype="pdf")
    plag_doc = fitz.open(stream=plag_summary, filetype="pdf")
    combined_summary.insert_pdf(plag_doc)

    summary_bytes = combined_summary.tobytes()
    combined_summary.close()
    plag_doc.close()

    return _merge_pdfs(summary_bytes, original_bytes)
