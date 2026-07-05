import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional

from app.models.document import (
    ScanDocument, ScanStatus,
    AIResult, PlagiarismResult,
    MatchedSource, ChunkResult,
)
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
    """
    print(f"\n{'='*60}")
    print(f"🤖 AI SCAN START — doc_id: {doc_id}")
    print(f"{'='*60}")

    doc = await ScanDocument.get(doc_id)
    if not doc:
        print(f"❌ Document {doc_id} not found")
        return

    try:
        await doc.update({"$set": {"ai_scan_status": ScanStatus.PROCESSING.value}})

        text = doc.extracted_text
        if not text or len(text.strip()) < 50:
            print(f"⚠️ Text too short ({len(text.strip()) if text else 0} chars)")
            await doc.update({"$set": {
                "ai_scan_status": ScanStatus.COMPLETED.value,
                "ai_result": AIResult(
                    ai_score=0.0,
                    summary="Document text is too short for AI analysis.",
                ).model_dump(),
            }})
            return

        sections = create_large_window_chunks(text, words_per_chunk=800, overlap_words=100)
        print(f"📄 Created {len(sections)} sections for AI analysis")

        semaphore = asyncio.Semaphore(2)

        async def _analyze_section(section: dict, idx: int) -> dict:
            async with semaphore:
                print(f"   🔬 Analyzing section {idx} ({len(section['text'].split())} words)...")
                result = await detect_ai_writing_full(section["text"])
                print(f"   ✅ Section {idx} → AI score: {result.get('ai_score', 'N/A')}%")
                return result

        raw_results = await asyncio.gather(
            *[_analyze_section(s, i) for i, s in enumerate(sections)],
            return_exceptions=True,
        )

        # Log exceptions
        for i, r in enumerate(raw_results):
            if isinstance(r, Exception):
                print(f"   ❌ Section {i} FAILED: {r}")
                traceback.print_exception(type(r), r, r.__traceback__)

        valid = [r for r in raw_results if isinstance(r, dict)]
        print(f"📊 {len(valid)}/{len(raw_results)} sections succeeded")

        if not valid:
            print(f"❌ ALL sections failed!")
            await doc.update({"$set": {
                "ai_scan_status": ScanStatus.FAILED.value,
                "ai_result": AIResult(summary="All section analyses failed.").model_dump(),
            }})
            return

        avg_score = round(sum(r.get("ai_score", 0) for r in valid) / len(valid), 1)
        print(f"🎯 Final AI Score: {avg_score}%")

        # Aggregate heuristics
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
        print(f"✅ AI SCAN COMPLETE — Score: {avg_score}%\n")

    except Exception as exc:
        print(f"❌ AI SCAN CRASHED: {exc}")
        traceback.print_exc()
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
    Detect plagiarism via external web search + LLM analysis.
    Pure real-time external analysis — no internal database matching.
    """
    print(f"\n{'='*60}")
    print(f"🔎 PLAGIARISM SCAN START — doc_id: {doc_id}")
    print(f"{'='*60}")

    doc = await ScanDocument.get(doc_id)
    if not doc:
        print(f"❌ Document {doc_id} not found")
        return

    try:
        await doc.update({"$set": {"plagiarism_scan_status": ScanStatus.PROCESSING.value}})

        text = doc.extracted_text
        if not text or len(text.strip()) < 50:
            print(f"⚠️ Text too short ({len(text.strip()) if text else 0} chars)")
            await doc.update({"$set": {
                "plagiarism_scan_status": ScanStatus.COMPLETED.value,
                "plagiarism_result": PlagiarismResult(
                    summary="Document text is too short for plagiarism analysis.",
                ).model_dump(),
            }})
            return

        # ── Step 1: Create chunks ──
        chunks = create_overlapping_chunks(text, sentences_per_chunk=6, overlap_sentences=1)
        print(f"📄 Created {len(chunks)} chunks for plagiarism analysis")

        if not chunks:
            print("⚠️ No chunks created from text")
            await doc.update({"$set": {
                "plagiarism_scan_status": ScanStatus.COMPLETED.value,
                "plagiarism_result": PlagiarismResult(
                    plagiarism_score=0.0,
                    summary="Could not create text chunks for analysis.",
                ).model_dump(),
            }})
            return

        # ── Step 2: Process each chunk (web search → LLM analysis) ──
        semaphore = asyncio.Semaphore(3)  # Limit concurrent API calls

        async def _process_chunk(chunk: dict, idx: int) -> dict:
            async with semaphore:
                try:
                    print(f"\n   📝 Chunk {idx}: {chunk['text'][:80]}...")

                    # Extract key phrases for search
                    key_phrases = extract_key_phrases(chunk["text"])
                    print(f"   🔑 Key phrases: {[p[:40] for p in key_phrases]}")

                    # Search the web
                    web_sources = await search_web_for_chunk(chunk["text"], key_phrases[:2])
                    print(f"   🌐 Web sources found: {len(web_sources)}")

                    # Analyze with LLM
                    result = await analyze_plagiarism(chunk["text"], web_sources)
                    score = result.get("plagiarism_score", 0)
                    sources_count = len(result.get("matched_sources", []))
                    print(f"   🎯 Chunk {idx} → Score: {score}%, Sources: {sources_count}")

                    return {
                        "index": idx,
                        "text": chunk["text"],
                        "plagiarism_score": score,
                        "match_type": result.get("match_type", "original"),
                        "matched_sources": result.get("matched_sources", []),
                    }
                except Exception as e:
                    print(f"   ❌ Chunk {idx} FAILED: {e}")
                    traceback.print_exc()
                    # Return a result with 0 score instead of raising
                    return {
                        "index": idx,
                        "text": chunk["text"],
                        "plagiarism_score": 0,
                        "match_type": "original",
                        "matched_sources": [],
                    }

        raw_results = await asyncio.gather(
            *[_process_chunk(c, i) for i, c in enumerate(chunks)],
            return_exceptions=True,
        )

        # Filter valid results
        valid = [r for r in raw_results if isinstance(r, dict)]
        failed = [r for r in raw_results if isinstance(r, Exception)]

        print(f"\n📊 Chunks: {len(valid)} succeeded, {len(failed)} failed")
        for i, f in enumerate(failed):
            print(f"   ❌ Failed chunk: {f}")

        # ── Step 3: Aggregate results ──
        if valid:
            avg_web_score = round(sum(r["plagiarism_score"] for r in valid) / len(valid), 1)
        else:
            avg_web_score = 0.0

        final_plagiarism_score = avg_web_score

        # Deduplicate matched sources
        all_sources: list[MatchedSource] = []
        seen_urls = set()

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

        print(f"\n🎯 FINAL: Score={final_plagiarism_score}%, Sources={len(all_sources)}")

        summary_parts = []
        if len(all_sources) > 0:
            summary_parts.append(f"Found {len(all_sources)} matching web source(s) with {avg_web_score}% average web similarity.")
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
        print(f"✅ PLAGIARISM SCAN COMPLETE — Score: {final_plagiarism_score}%\n")

    except Exception as exc:
        print(f"❌ PLAGIARISM SCAN CRASHED: {exc}")
        traceback.print_exc()
        doc = await ScanDocument.get(doc_id)
        if doc:
            await doc.update({"$set": {
                "plagiarism_scan_status": ScanStatus.FAILED.value,
                "plagiarism_result": PlagiarismResult(
                    summary=f"Plagiarism scan failed: {exc}",
                ).model_dump(),
            }})
