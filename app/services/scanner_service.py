import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.models.document import ScanDocument, ScanResult, MatchedSource, ChunkResult, ScanStatus
from app.utils.chunker import create_overlapping_chunks, extract_key_phrases
from app.services.tavily_service import search_web_for_chunk
from app.services.groq_service import analyze_plagiarism, detect_ai_writing


async def process_single_chunk(chunk: dict, chunk_index: int) -> dict:
    """
    Process a single text chunk through the full analysis pipeline.

    1. Extract key phrases
    2. Search web for matching sources (Tavily)
    3. Analyze plagiarism (Groq)
    4. Detect AI writing (Groq)

    Returns combined results for this chunk.
    """
    text = chunk["text"]

    # Step 1: Extract key phrases for web search
    key_phrases = extract_key_phrases(text)

    # Step 2: Search web for matching content
    web_sources = await search_web_for_chunk(text, key_phrases)

    # Step 3 & 4: Run plagiarism and AI detection in parallel
    plagiarism_result, ai_result = await asyncio.gather(
        analyze_plagiarism(text, web_sources),
        detect_ai_writing(text),
    )

    return {
        "index": chunk_index,
        "text": text,
        "plagiarism_score": plagiarism_result.get("plagiarism_score", 0),
        "ai_score": ai_result.get("ai_score", 0),
        "matched_sources": plagiarism_result.get("matched_sources", []),
        "plagiarism_analysis": plagiarism_result.get("analysis", ""),
        "ai_analysis": ai_result.get("analysis", ""),
        "web_sources": web_sources,
    }


async def scan_document(document: ScanDocument) -> ScanDocument:
    """
    Run the full plagiarism + AI detection pipeline on a document.

    Steps:
    1. Chunk the extracted text
    2. Process each chunk (web search + Groq analysis)
    3. Aggregate scores
    4. Build final report
    5. Update document in database

    Args:
        document: The ScanDocument with extracted_text already populated.

    Returns:
        Updated ScanDocument with scan_result.
    """
    try:
        # Mark as processing
        document.scan_status = ScanStatus.PROCESSING
        await document.save()

        text = document.extracted_text
        if not text or len(text.strip()) < 50:
            document.scan_status = ScanStatus.COMPLETED
            document.scan_result = ScanResult(
                plagiarism_score=0,
                ai_score=0,
                summary="Document text is too short for meaningful analysis.",
                matched_sources=[],
                chunks=[],
            )
            document.scanned_at = datetime.now(timezone.utc)
            await document.save()
            return document

        # Step 1: Create overlapping chunks
        chunks = create_overlapping_chunks(text)

        if not chunks:
            document.scan_status = ScanStatus.COMPLETED
            document.scan_result = ScanResult(
                plagiarism_score=0,
                ai_score=0,
                summary="Could not extract meaningful content for analysis.",
            )
            document.scanned_at = datetime.now(timezone.utc)
            await document.save()
            return document

        # Step 2: Process chunks (with concurrency limit to avoid API rate limits)
        semaphore = asyncio.Semaphore(3)  # max 3 concurrent chunk analyses

        async def process_with_limit(chunk, idx):
            async with semaphore:
                return await process_single_chunk(chunk, idx)

        tasks = [process_with_limit(chunk, i) for i, chunk in enumerate(chunks)]
        chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Step 3: Aggregate results
        valid_results = [r for r in chunk_results if isinstance(r, dict)]

        if not valid_results:
            document.scan_status = ScanStatus.FAILED
            document.scan_result = ScanResult(
                summary="All chunk analyses failed.",
            )
            document.scanned_at = datetime.now(timezone.utc)
            await document.save()
            return document

        # Calculate weighted average scores
        total_plagiarism = sum(r["plagiarism_score"] for r in valid_results)
        total_ai = sum(r["ai_score"] for r in valid_results)
        num_chunks = len(valid_results)

        avg_plagiarism = round(total_plagiarism / num_chunks, 1)
        avg_ai = round(total_ai / num_chunks, 1)

        # Build matched sources list (deduplicated)
        all_matched_sources = []
        seen_urls = set()
        for result in valid_results:
            for source in result.get("matched_sources", []):
                url = source.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_matched_sources.append(
                        MatchedSource(
                            url=url,
                            title=source.get("title", ""),
                            matched_text=source.get("matched_text", ""),
                            original_text=result["text"][:200],
                            similarity_score=source.get("similarity", 0),
                            chunk_index=result["index"],
                        )
                    )

        # Build chunk results
        chunk_details = []
        for result in valid_results:
            chunk_sources = [
                {
                    "url": s.get("url", ""),
                    "title": s.get("title", ""),
                    "similarity": s.get("similarity", 0),
                }
                for s in result.get("matched_sources", [])
            ]
            chunk_details.append(
                ChunkResult(
                    index=result["index"],
                    text=result["text"],
                    plagiarism_score=result["plagiarism_score"],
                    ai_score=result["ai_score"],
                    sources=chunk_sources,
                )
            )

        # Build summary
        summary_parts = []
        if avg_plagiarism > 50:
            summary_parts.append(f"High plagiarism detected ({avg_plagiarism}%).")
        elif avg_plagiarism > 20:
            summary_parts.append(f"Moderate plagiarism detected ({avg_plagiarism}%).")
        else:
            summary_parts.append(f"Low plagiarism levels ({avg_plagiarism}%).")

        if avg_ai > 50:
            summary_parts.append(f"Content appears to be AI-generated ({avg_ai}%).")
        elif avg_ai > 20:
            summary_parts.append(f"Some AI-generated patterns detected ({avg_ai}%).")
        else:
            summary_parts.append(f"Content appears to be human-written ({avg_ai}% AI).")

        summary_parts.append(
            f"Analyzed {num_chunks} text segments against {len(all_matched_sources)} web sources."
        )

        # Step 4: Save final results
        document.scan_status = ScanStatus.COMPLETED
        document.scan_result = ScanResult(
            plagiarism_score=avg_plagiarism,
            ai_score=avg_ai,
            summary=" ".join(summary_parts),
            matched_sources=all_matched_sources,
            chunks=chunk_details,
        )
        document.scanned_at = datetime.now(timezone.utc)
        await document.save()

        return document

    except Exception as e:
        document.scan_status = ScanStatus.FAILED
        document.scan_result = ScanResult(
            summary=f"Scan failed with error: {str(e)}",
        )
        document.scanned_at = datetime.now(timezone.utc)
        await document.save()
        return document
