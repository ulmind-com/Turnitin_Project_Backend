import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.models.document import (
    ScanDocument, ScanStatus,
    AIResult, PlagiarismResult,
    MatchedSource, ChunkResult,
)
from app.models.repository import SubmittedPaper
from app.utils.similarity_engine import generate_ngram_hashes, calculate_jaccard_similarity
from app.utils.chunker import (
    create_overlapping_chunks,
    create_large_window_chunks,
    extract_key_phrases,
)
from app.services.tavily_service import search_web_for_chunk
from app.services.groq_service import analyze_plagiarism, detect_ai_writing_full


async def analyze_ai_job(doc_id: str) -> None:
    """
    Evaluate the full document text for AI-generated content.

    Strategy:
      1. Split the document into large 800-word sections (not micro-chunks) so
         the LLM can observe sentence-rhythm patterns across a meaningful span.
      2. Compute perplexity/burstiness proxies locally per section before calling
         the LLM — these hard numbers anchor the model's score.
      3. Aggregate section scores into a single document-level ai_score.
      4. Persist with a targeted $set — plagiarism fields are never touched.
    """
    doc = await ScanDocument.get(doc_id)
    if not doc:
        return

    try:
        await doc.update({"$set": {"ai_scan_status": ScanStatus.PROCESSING.value}})

        text = doc.extracted_text
        if not text or len(text.strip()) < 50:
            await doc.update({"$set": {
                "ai_scan_status": ScanStatus.COMPLETED.value,
                "ai_result": AIResult(
                    ai_score=0.0,
                    summary="Document text is too short for AI analysis.",
                ).model_dump(),
            }})
            return

        sections = create_large_window_chunks(text, words_per_chunk=800, overlap_words=100)

        semaphore = asyncio.Semaphore(2)

        async def _analyze_section(section: dict) -> dict:
            async with semaphore:
                return await detect_ai_writing_full(section["text"])

        raw_results = await asyncio.gather(
            *[_analyze_section(s) for s in sections],
            return_exceptions=True,
        )

        valid = [r for r in raw_results if isinstance(r, dict)]
        if not valid:
            await doc.update({"$set": {
                "ai_scan_status": ScanStatus.FAILED.value,
                "ai_result": AIResult(summary="All section analyses failed.").model_dump(),
            }})
            return

        avg_score = round(sum(r.get("ai_score", 0) for r in valid) / len(valid), 1)

        # Aggregate heuristics (mean across sections)
        heuristic_keys = ["burstiness", "type_token_ratio", "avg_sentence_length",
                          "ai_phrase_density", "sentence_count", "word_count"]
        section_heuristics = [r.get("heuristics", {}) for r in valid if r.get("heuristics")]
        agg_heuristics: dict = {}
        if section_heuristics:
            agg_heuristics = {
                k: round(
                    sum(h.get(k, 0) for h in section_heuristics) / len(section_heuristics),
                    4,
                )
                for k in heuristic_keys
            }

        # Human-readable verdict
        burstiness = agg_heuristics.get("burstiness", 0)
        ttr = agg_heuristics.get("type_token_ratio", 0)

        if avg_score >= 76:
            verdict = f"Very likely AI-generated ({avg_score}% AI score)."
        elif avg_score >= 56:
            verdict = f"Likely AI-generated ({avg_score}% AI score)."
        elif avg_score >= 36:
            verdict = f"Mixed signals — uncertain origin ({avg_score}% AI score)."
        elif avg_score >= 16:
            verdict = f"Mostly human, minor AI-like patterns ({avg_score}% AI score)."
        else:
            verdict = f"Content appears human-written ({avg_score}% AI score)."

        summary = (
            f"{verdict} "
            f"Sentence-length burstiness: {burstiness:.3f} "
            f"(human baseline ≥ 0.50). "
            f"Vocabulary diversity (TTR): {ttr:.3f}."
        )

        await doc.update({"$set": {
            "ai_scan_status": ScanStatus.COMPLETED.value,
            "ai_result": AIResult(
                ai_score=avg_score,
                summary=summary,
                heuristics=agg_heuristics,
            ).model_dump(),
            "scanned_at": datetime.now(timezone.utc),
        }})

    except Exception as exc:
        # Re-fetch to avoid stale state; use $set so plagiarism data is safe
        doc = await ScanDocument.get(doc_id)
        if doc:
            await doc.update({"$set": {
                "ai_scan_status": ScanStatus.FAILED.value,
                "ai_result": AIResult(
                    summary=f"AI scan failed: {exc}",
                ).model_dump(),
            }})


async def analyze_plagiarism_job(doc_id: str) -> None:
    """
    Detect plagiarism via a two-tier matching workflow:
      Step A (Internal Database Check): Query the SubmittedPaper collection using N-gram hashes
             and Jaccard Similarity to find any matches >15%.
      Step B (External Web Check): Run the existing AsyncTavilyClient web search logic.
      Step C (Aggregation & Update): Combine, deduplicate, calculate plagiarism_score, and update document.
      Step D (Global Ingestion): Save the current document's text and ngram_hashes into the SubmittedPaper collection.
    """
    doc = await ScanDocument.get(doc_id)
    if not doc:
        return

    try:
        await doc.update({"$set": {"plagiarism_scan_status": ScanStatus.PROCESSING.value}})

        text = doc.extracted_text
        if not text or len(text.strip()) < 50:
            await doc.update({"$set": {
                "plagiarism_scan_status": ScanStatus.COMPLETED.value,
                "plagiarism_result": PlagiarismResult(
                    summary="Document text is too short for plagiarism analysis.",
                ).model_dump(),
            }})
            return

        # ----------------------------------------------------
        # Step A: Internal Database Check (N-Gram & Jaccard)
        # ----------------------------------------------------
        current_hashes = generate_ngram_hashes(text, n=5)
        highest_internal_similarity = 0.0
        internal_sources = []

        if current_hashes:
            # Exclude ALL papers from the same user to prevent self-plagiarism
            # (e.g., user re-uploads same file → should NOT match against themselves)
            matching_papers = await SubmittedPaper.find(
                {
                    "ngram_hashes": {"$in": list(current_hashes)},
                    "user_id": {"$ne": doc.user_id},
                }
            ).to_list()

            for paper in matching_papers:
                paper_hashes = set(paper.ngram_hashes)
                sim = calculate_jaccard_similarity(current_hashes, paper_hashes)
                if sim > 15.0:
                    highest_internal_similarity = max(highest_internal_similarity, sim)
                    internal_sources.append(MatchedSource(
                        url="Submitted Work (Student Paper)",
                        title=f"Student Paper {paper.document_id[:8]}",
                        matched_text=paper.extracted_text[:300] + ("..." if len(paper.extracted_text) > 300 else ""),
                        original_text=text[:200],
                        similarity_score=sim,
                        chunk_index=0,
                    ))

        # ----------------------------------------------------
        # Step B: External Web Check
        # ----------------------------------------------------
        chunks = create_overlapping_chunks(text, sentences_per_chunk=6, overlap_sentences=1)
        valid = []
        if chunks:
            semaphore = asyncio.Semaphore(5)

            async def _process_chunk(chunk: dict, idx: int) -> dict:
                async with semaphore:
                    key_phrases = extract_key_phrases(chunk["text"])
                    web_sources = await search_web_for_chunk(chunk["text"], key_phrases[:2])
                    result = await analyze_plagiarism(chunk["text"], web_sources)
                    return {
                        "index": idx,
                        "text": chunk["text"],
                        "plagiarism_score": result.get("plagiarism_score", 0),
                        "match_type": result.get("match_type", "original"),
                        "matched_sources": result.get("matched_sources", []),
                    }

            raw_results = await asyncio.gather(
                *[_process_chunk(c, i) for i, c in enumerate(chunks)],
                return_exceptions=True,
            )
            valid = [r for r in raw_results if isinstance(r, dict)]

        # ----------------------------------------------------
        # Step C: Aggregation & Update
        # ----------------------------------------------------
        avg_web_score = round(sum(r["plagiarism_score"] for r in valid) / len(valid), 1) if valid else 0.0
        final_plagiarism_score = max(avg_web_score, highest_internal_similarity)

        # Deduplicate matched sources
        all_sources: list[MatchedSource] = list(internal_sources)
        seen_urls = {src.title for src in internal_sources}

        for r in valid:
            for src in r.get("matched_sources", []):
                url = src.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_sources.append(MatchedSource(
                        url=url,
                        title=src.get("title", ""),
                        matched_text=src.get("matched_text", ""),
                        original_text=r["text"][:200],
                        similarity_score=src.get("similarity", 0),
                        chunk_index=r["index"],
                    ))

        chunk_details: list[ChunkResult] = [
            ChunkResult(
                index=r["index"],
                text=r["text"],
                plagiarism_score=r["plagiarism_score"],
                ai_score=0.0,
                sources=[
                    {
                        "url": s.get("url", ""),
                        "title": s.get("title", ""),
                        "similarity": s.get("similarity", 0),
                        "match_type": r.get("match_type", "original"),
                    }
                    for s in r.get("matched_sources", [])
                ],
            )
            for r in valid
        ]

        web_match_count = len(all_sources) - len(internal_sources)
        summary_parts = []
        if highest_internal_similarity > 15.0:
            summary_parts.append(f"Significant match with internal student work ({highest_internal_similarity}% similarity).")
        if web_match_count > 0:
            summary_parts.append(f"Found {web_match_count} matching web source(s) with {avg_web_score}% average web similarity.")
        else:
            summary_parts.append("No significant matching web sources found.")

        summary = " ".join(summary_parts)

        await doc.update({"$set": {
            "plagiarism_scan_status": ScanStatus.COMPLETED.value,
            "plagiarism_result": PlagiarismResult(
                plagiarism_score=final_plagiarism_score,
                summary=summary,
                matched_sources=all_sources,
                chunks=chunk_details,
            ).model_dump(),
            "scanned_at": datetime.now(timezone.utc),
        }})

        # ----------------------------------------------------
        # Step D: Global Ingestion
        # ----------------------------------------------------
        if current_hashes:
            paper = SubmittedPaper(
                document_id=str(doc.id),
                user_id=doc.user_id,
                extracted_text=text,
                ngram_hashes=list(current_hashes),
            )
            await paper.insert()

    except Exception as exc:
        doc = await ScanDocument.get(doc_id)
        if doc:
            await doc.update({"$set": {
                "plagiarism_scan_status": ScanStatus.FAILED.value,
                "plagiarism_result": PlagiarismResult(
                    summary=f"Plagiarism scan failed: {exc}",
                ).model_dump(),
            }})
