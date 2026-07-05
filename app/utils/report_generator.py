import io
import os
import re
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa
from app.models.document import ScanDocument

def generate_highlighted_html(doc: ScanDocument) -> str:
    text = doc.extracted_text
    n = len(text)
    
    # Track the highlight type for each character
    # 0 = normal, 1 = AI, 2 = Plagiarism (Plagiarism takes priority)
    char_tags = [0] * n
    
    # 1. Mark plagiarism ranges
    if doc.plagiarism_result and doc.plagiarism_result.chunks:
        for chunk in doc.plagiarism_result.chunks:
            if chunk.plagiarism_score >= 20: # threshold for plagiarism
                chunk_text = chunk.text
                start = 0
                while True:
                    idx = text.find(chunk_text, start)
                    if idx == -1:
                        break
                    for i in range(idx, idx + len(chunk_text)):
                        char_tags[i] = 2 # Mark as Plagiarism
                    start = idx + 1
                    
        # Also check matched_sources.matched_text
        for source in doc.plagiarism_result.matched_sources:
            if source.similarity_score >= 20 and source.matched_text:
                matched = source.matched_text
                start = 0
                while True:
                    idx = text.find(matched, start)
                    if idx == -1:
                        break
                    for i in range(idx, idx + len(matched)):
                        char_tags[i] = 2
                    start = idx + 1

    # 2. Mark AI ranges
    if doc.ai_result and doc.ai_result.ai_score > 15:
        ai_score = doc.ai_result.ai_score
        
        # Simple sentence splitter
        sentence_ends = [m.end() for m in re.finditer(r'[^.!?]+[.!?]+', text)]
        sentences = []
        last_idx = 0
        for end in sentence_ends:
            sentences.append((last_idx, end))
            last_idx = end
        if last_idx < n:
            sentences.append((last_idx, n))
            
        # Score each sentence for AI likelihood based on common AI vocabulary
        ai_keywords = [
            "delve", "tapestry", "moreover", "furthermore", "testament", "notably", 
            "in conclusion", "it is important to note", "consequently", "pivotal",
            "beacon", "comprehensive", "demystify", "multifaceted", "paramount"
        ]
        
        sentence_scores = []
        for start, end in sentences:
            sent_text = text[start:end].lower()
            score = 0
            for kw in ai_keywords:
                if kw in sent_text:
                    score += 10
            sentence_scores.append((score, start, end))
            
        # Sort sentences by their AI likelihood score
        num_to_highlight = int(len(sentences) * (ai_score / 100.0))
        if num_to_highlight == 0 and len(sentences) > 0:
            num_to_highlight = 1
            
        sorted_sentences = sorted(sentence_scores, key=lambda x: x[0], reverse=True)
        highlighted_sentences = sorted_sentences[:num_to_highlight]
        
        for _, start, end in highlighted_sentences:
            for i in range(start, end):
                if char_tags[i] == 0:
                    char_tags[i] = 1 # Mark as AI

    # 3. Reconstruct HTML
    html_parts = []
    current_tag = 0
    
    def escape_html(char):
        if char == '&': return '&amp;'
        if char == '<': return '&lt;'
        if char == '>': return '&gt;'
        if char == '\n': return '<br/>'
        return char

    for i in range(n):
        tag = char_tags[i]
        if tag != current_tag:
            if current_tag == 1 or current_tag == 2:
                html_parts.append('</mark>')
            if tag == 1:
                html_parts.append('<mark class="ai-highlight">')
            elif tag == 2:
                html_parts.append('<mark class="plagiarism-highlight">')
            current_tag = tag
            
        html_parts.append(escape_html(text[i]))
        
    if current_tag == 1 or current_tag == 2:
        html_parts.append('</mark>')
        
    return "".join(html_parts)

def build_report_pdf(doc: ScanDocument) -> bytes:
    highlighted_html = generate_highlighted_html(doc)
    
    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report.html")
    
    scanned_at_str = (
        doc.scanned_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if doc.scanned_at
        else doc.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    
    html_content = template.render(
        document_id=str(doc.id),
        file_name=doc.original_file_name,
        file_type=doc.file_type,
        overall_plagiarism_score=doc.plagiarism_result.plagiarism_score if doc.plagiarism_result else 0.0,
        overall_ai_score=doc.ai_result.ai_score if doc.ai_result else 0.0,
        plagiarism_summary=doc.plagiarism_result.summary if doc.plagiarism_result else None,
        ai_summary=doc.ai_result.summary if doc.ai_result else None,
        highlighted_text=highlighted_html,
        scanned_at=scanned_at_str
    )
    
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)
    if pisa_status.err:
        raise Exception("Failed to generate PDF report from HTML template.")
    
    pdf_bytes = pdf_buffer.getvalue()
    pdf_buffer.close()
    return pdf_bytes
