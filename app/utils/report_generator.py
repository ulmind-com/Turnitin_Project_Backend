"""
Turnitin-style PDF report generator.

Produces three report types:
  1. Plagiarism Similarity Report (light brown highlights)
  2. AI Writing Detection Report (sky blue highlights)
  3. Combined Originality Report (both)
"""

import io
import os
import re
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa
from app.models.document import ScanDocument


TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


def _get_template(name: str):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    return env.get_template(name)


def _html_to_pdf(html: str) -> bytes:
    buf = io.BytesIO()
    status = pisa.CreatePDF(html, dest=buf)
    if status.err:
        raise Exception("Failed to convert HTML to PDF.")
    pdf = buf.getvalue()
    buf.close()
    return pdf


def _escape_html(char: str) -> str:
    if char == '&': return '&amp;'
    if char == '<': return '&lt;'
    if char == '>': return '&gt;'
    if char == '\n': return '<br/>'
    return char


# ────────────────────────────────────────────────────────────────────────────
#  Plagiarism Report
# ────────────────────────────────────────────────────────────────────────────

def _generate_plagiarism_highlighted_html(doc: ScanDocument) -> str:
    """
    Highlight plagiarized chunks in the document text using light brown.
    Also injects source-number markers inline.
    """
    text = doc.extracted_text
    n = len(text)
    if n == 0:
        return ""

    # Track which characters belong to which source (0 = none)
    char_source = [0] * n

    if doc.plagiarism_result and doc.plagiarism_result.chunks:
        for chunk in doc.plagiarism_result.chunks:
            if chunk.plagiarism_score >= 15:
                chunk_text = chunk.text
                idx = text.find(chunk_text)
                if idx != -1:
                    source_num = chunk.index + 1
                    for i in range(idx, min(idx + len(chunk_text), n)):
                        if char_source[i] == 0:
                            char_source[i] = source_num

    # Also highlight matched_sources text
    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for src_idx, source in enumerate(doc.plagiarism_result.matched_sources):
            if source.similarity_score >= 15 and source.original_text:
                # Try to find the original_text in the document
                search_text = source.original_text.rstrip(".")
                idx = text.find(search_text)
                if idx != -1:
                    src_num = source.chunk_index + 1
                    for i in range(idx, min(idx + len(search_text), n)):
                        if char_source[i] == 0:
                            char_source[i] = src_num

    # Build HTML with highlights
    html_parts = []
    in_highlight = False
    current_source = 0

    for i in range(n):
        src = char_source[i]

        if src > 0 and not in_highlight:
            # Start new highlight
            html_parts.append('<mark class="plag-highlight">')
            in_highlight = True
            current_source = src
        elif src == 0 and in_highlight:
            # End highlight, add source marker
            html_parts.append('</mark>')
            html_parts.append(f'<span class="source-marker">{current_source}</span>')
            in_highlight = False
            current_source = 0
        elif src > 0 and in_highlight and src != current_source:
            # Switch source
            html_parts.append('</mark>')
            html_parts.append(f'<span class="source-marker">{current_source}</span>')
            html_parts.append('<mark class="plag-highlight">')
            current_source = src

        html_parts.append(_escape_html(text[i]))

    if in_highlight:
        html_parts.append('</mark>')
        html_parts.append(f'<span class="source-marker">{current_source}</span>')

    return "".join(html_parts)


def _compute_match_groups(doc: ScanDocument) -> dict:
    """Compute match group statistics from plagiarism results."""
    total_sources = 0
    total_pct = 0.0

    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        total_sources = len(doc.plagiarism_result.matched_sources)
        for s in doc.plagiarism_result.matched_sources:
            total_pct += s.similarity_score

    return {
        "not_cited_count": total_sources,
        "not_cited_pct": round(doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0, 1),
        "missing_quote_count": 0,
        "missing_quote_pct": 0,
        "missing_citation_count": 0,
        "missing_citation_pct": 0,
        "cited_quoted_count": 0,
        "cited_quoted_pct": 0,
    }


def _compute_source_breakdown(doc: ScanDocument) -> dict:
    """Compute percentage breakdown by source type."""
    internet_score = 0.0
    student_score = 0.0
    internet_count = 0
    student_count = 0

    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for src in doc.plagiarism_result.matched_sources:
            if src.url == "Submitted Work (Student Paper)":
                student_score += src.similarity_score
                student_count += 1
            else:
                internet_score += src.similarity_score
                internet_count += 1

    total = internet_score + student_score
    if total > 0:
        plag_score = doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0
        internet_pct = round((internet_score / total) * plag_score, 1) if total > 0 else 0
        student_pct = round((student_score / total) * plag_score, 1) if total > 0 else 0
    else:
        internet_pct = 0
        student_pct = 0

    return {
        "internet_pct": internet_pct,
        "publication_pct": 0,
        "student_pct": student_pct,
    }


def _prepare_matched_sources_for_template(doc: ScanDocument) -> list[dict]:
    """Prepare matched sources list for the template, sorted by similarity."""
    if not doc.plagiarism_result or not doc.plagiarism_result.matched_sources:
        return []

    sources = []
    for src in doc.plagiarism_result.matched_sources:
        if src.similarity_score < 5:
            continue
        source_type = "student" if src.url == "Submitted Work (Student Paper)" else "internet"
        plag_score = doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0
        # Scale individual source percentage relative to overall
        total_sim = sum(s.similarity_score for s in doc.plagiarism_result.matched_sources if s.similarity_score >= 5)
        if total_sim > 0:
            sim_pct = round((src.similarity_score / total_sim) * plag_score, 1)
        else:
            sim_pct = 0

        sources.append({
            "title": src.title or src.url,
            "url": src.url,
            "similarity_pct": max(sim_pct, 1) if sim_pct > 0 else "<1",
            "source_type": source_type,
            "raw_score": src.similarity_score,
        })

    # Sort by raw_score descending
    sources.sort(key=lambda x: x["raw_score"], reverse=True)
    return sources[:15]  # Top 15


def build_plagiarism_report_pdf(doc: ScanDocument) -> bytes:
    """Build a Turnitin-style Plagiarism Similarity Report PDF."""
    highlighted_html = _generate_plagiarism_highlighted_html(doc)
    match_groups = _compute_match_groups(doc)
    source_breakdown = _compute_source_breakdown(doc)
    matched_sources = _prepare_matched_sources_for_template(doc)

    scanned_at = (
        doc.scanned_at.strftime("%b %d, %Y, %I:%M %p UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%b %d, %Y, %I:%M %p UTC")
    )

    metadata = doc.metadata or {}

    template = _get_template("plagiarism_report.html")
    html = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type,
        scanned_at=scanned_at,
        page_count=metadata.get("page_count", "—"),
        word_count=f"{metadata.get('token_count', 0):,}",
        char_count=f"{metadata.get('character_count', 0):,}",
        overall_plagiarism_score=round(doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0, 1),
        filtered_sections=["Bibliography"],
        integrity_flags=doc.integrity_flags or [],
        integrity_flag_count=len(doc.integrity_flags) if doc.integrity_flags else 0,
        matched_sources=matched_sources,
        highlighted_text=highlighted_html,
        **match_groups,
        **source_breakdown,
    )

    return _html_to_pdf(html)


# ────────────────────────────────────────────────────────────────────────────
#  AI Detection Report
# ────────────────────────────────────────────────────────────────────────────

def _generate_ai_highlighted_html(doc: ScanDocument) -> str:
    """
    Highlight AI-detected text sections in sky blue.
    Uses sentence-level scoring based on the AI score.
    """
    text = doc.extracted_text
    n = len(text)
    if n == 0:
        return ""

    ai_score = doc.ai_result.ai_score if doc.ai_result else 0
    if ai_score <= 5:
        # Score too low — no highlighting needed
        return "".join(_escape_html(c) for c in text)

    # Split into sentences
    sentence_ends = [m.end() for m in re.finditer(r'[^.!?]+[.!?]+', text)]
    sentences = []
    last_idx = 0
    for end in sentence_ends:
        sentences.append((last_idx, end))
        last_idx = end
    if last_idx < n:
        sentences.append((last_idx, n))

    if not sentences:
        return "".join(_escape_html(c) for c in text)

    # AI keywords for scoring individual sentences
    ai_keywords = [
        "delve", "tapestry", "moreover", "furthermore", "testament", "notably",
        "in conclusion", "it is important to note", "consequently", "pivotal",
        "beacon", "comprehensive", "demystify", "multifaceted", "paramount",
        "it's important to note", "in summary", "to summarize", "additionally",
        "in this context", "in the realm of", "is crucial", "when it comes to",
        "it's worth noting", "having said that", "on the other hand",
        "needless to say", "it goes without saying",
    ]

    # Score each sentence
    sentence_scores = []
    for start, end in sentences:
        sent_text = text[start:end].lower()
        score = 0

        # Length uniformity check (AI writes very uniform sentences)
        word_count = len(sent_text.split())
        if 15 <= word_count <= 25:
            score += 3  # Very "average" length = suspicious

        for kw in ai_keywords:
            if kw in sent_text:
                score += 10

        # Check for overly smooth transitions
        if sent_text.strip().startswith(("this ", "these ", "those ", "such ")):
            score += 2

        sentence_scores.append((score, start, end))

    # Determine how many sentences to highlight based on AI score
    num_to_highlight = max(1, int(len(sentences) * (ai_score / 100.0)))

    # Sort by score descending, take top N
    sorted_sentences = sorted(sentence_scores, key=lambda x: x[0], reverse=True)
    highlighted_ranges = set()
    for _, start, end in sorted_sentences[:num_to_highlight]:
        for i in range(start, end):
            highlighted_ranges.add(i)

    # Build HTML
    html_parts = []
    in_highlight = False

    for i in range(n):
        is_highlighted = i in highlighted_ranges

        if is_highlighted and not in_highlight:
            html_parts.append('<mark class="ai-highlight">')
            in_highlight = True
        elif not is_highlighted and in_highlight:
            html_parts.append('</mark>')
            in_highlight = False

        html_parts.append(_escape_html(text[i]))

    if in_highlight:
        html_parts.append('</mark>')

    return "".join(html_parts)


def build_ai_report_pdf(doc: ScanDocument) -> bytes:
    """Build a Turnitin-style AI Writing Detection Report PDF."""
    highlighted_html = _generate_ai_highlighted_html(doc)

    ai_score = doc.ai_result.ai_score if doc.ai_result else 0
    heuristics = doc.ai_result.heuristics if doc.ai_result else {}

    # Determine caution level
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

    # Split AI score into "generated" and "paraphrased" (estimate)
    ai_generated_pct = round(ai_score * 0.85, 1) if ai_score > 0 else 0
    ai_paraphrased_pct = round(ai_score * 0.15, 1) if ai_score > 0 else 0

    scanned_at = (
        doc.scanned_at.strftime("%b %d, %Y, %I:%M %p UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%b %d, %Y, %I:%M %p UTC")
    )

    metadata = doc.metadata or {}

    template = _get_template("ai_report.html")
    html = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type,
        scanned_at=scanned_at,
        page_count=metadata.get("page_count", "—"),
        word_count=f"{metadata.get('token_count', 0):,}",
        char_count=f"{metadata.get('character_count', 0):,}",
        overall_ai_score=round(ai_score, 1),
        caution_level=caution_level,
        ai_generated_pct=ai_generated_pct,
        ai_paraphrased_pct=ai_paraphrased_pct,
        heuristics=heuristics if heuristics else None,
        highlighted_text=highlighted_html,
    )

    return _html_to_pdf(html)


# ────────────────────────────────────────────────────────────────────────────
#  Combined Report (both plagiarism + AI)
# ────────────────────────────────────────────────────────────────────────────

def _generate_combined_highlighted_html(doc: ScanDocument) -> str:
    """
    Highlight both plagiarism (light brown) and AI (sky blue) in a single view.
    Plagiarism takes priority if both overlap.
    """
    text = doc.extracted_text
    n = len(text)
    if n == 0:
        return ""

    # 0 = normal, 1 = AI, 2 = Plagiarism (priority)
    char_tags = [0] * n

    # Mark plagiarism
    if doc.plagiarism_result and doc.plagiarism_result.chunks:
        for chunk in doc.plagiarism_result.chunks:
            if chunk.plagiarism_score >= 15:
                idx = text.find(chunk.text)
                if idx != -1:
                    for i in range(idx, min(idx + len(chunk.text), n)):
                        char_tags[i] = 2

    if doc.plagiarism_result and doc.plagiarism_result.matched_sources:
        for source in doc.plagiarism_result.matched_sources:
            if source.similarity_score >= 15 and source.original_text:
                idx = text.find(source.original_text.rstrip("."))
                if idx != -1:
                    for i in range(idx, min(idx + len(source.original_text), n)):
                        char_tags[i] = 2

    # Mark AI (only where not already plagiarism)
    ai_score = doc.ai_result.ai_score if doc.ai_result else 0
    if ai_score > 15:
        sentence_ends = [m.end() for m in re.finditer(r'[^.!?]+[.!?]+', text)]
        sentences = []
        last_idx = 0
        for end in sentence_ends:
            sentences.append((last_idx, end))
            last_idx = end
        if last_idx < n:
            sentences.append((last_idx, n))

        ai_keywords = [
            "delve", "tapestry", "moreover", "furthermore", "testament", "notably",
            "in conclusion", "it is important to note", "consequently", "pivotal",
        ]

        sentence_scores = []
        for start, end in sentences:
            sent_text = text[start:end].lower()
            score = sum(10 for kw in ai_keywords if kw in sent_text)
            sentence_scores.append((score, start, end))

        num_to_highlight = max(1, int(len(sentences) * (ai_score / 100.0)))
        sorted_sentences = sorted(sentence_scores, key=lambda x: x[0], reverse=True)

        for _, start, end in sorted_sentences[:num_to_highlight]:
            for i in range(start, end):
                if char_tags[i] == 0:
                    char_tags[i] = 1

    # Build HTML
    html_parts = []
    current_tag = 0

    for i in range(n):
        tag = char_tags[i]
        if tag != current_tag:
            if current_tag in (1, 2):
                html_parts.append('</mark>')
            if tag == 1:
                html_parts.append('<mark class="ai-highlight">')
            elif tag == 2:
                html_parts.append('<mark class="plag-highlight">')
            current_tag = tag
        html_parts.append(_escape_html(text[i]))

    if current_tag in (1, 2):
        html_parts.append('</mark>')

    return "".join(html_parts)


def build_report_pdf(doc: ScanDocument) -> bytes:
    """Build the combined originality report (backward compat)."""
    highlighted_html = _generate_combined_highlighted_html(doc)

    scanned_at = (
        doc.scanned_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    )

    template = _get_template("report.html")
    html = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type,
        overall_plagiarism_score=round(doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0, 1),
        overall_ai_score=round(doc.ai_result.ai_score if doc.ai_result else 0, 1),
        plagiarism_summary=doc.plagiarism_result.summary if doc.plagiarism_result else None,
        ai_summary=doc.ai_result.summary if doc.ai_result else None,
        highlighted_text=highlighted_html,
        scanned_at=scanned_at,
    )

    return _html_to_pdf(html)
