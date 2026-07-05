import json
from groq import AsyncGroq
from app.config import settings


def get_groq_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


async def detect_ai_writing_full(section_text: str) -> dict:
    """
    Full-context AI detection using statistical heuristics + Groq LLM.

    Computes perplexity/burstiness proxies locally first, then injects them
    into the Groq prompt so the model anchors its score on objective measurements
    rather than purely subjective linguistic intuition.

    Uses llama-3.3-70b-versatile for higher accuracy on large text sections.

    Returns:
        {ai_score: float, analysis: str, heuristics: dict}
    """
    from app.utils.chunker import compute_text_heuristics

    if not settings.GROQ_API_KEY:
        return {"ai_score": 0, "analysis": "API key not configured", "heuristics": {}}

    heuristics = compute_text_heuristics(section_text)
    client = get_groq_client()

    burstiness = heuristics["burstiness"]
    ttr = heuristics["type_token_ratio"]
    avg_sent_len = heuristics["avg_sentence_length"]
    ai_phrase_density = heuristics["ai_phrase_density"]

    # Derive readable signal labels to guide the LLM
    signals = []
    if burstiness < 0.25:
        signals.append(f"STRONG AI signal — burstiness {burstiness:.2f} (very uniform sentences)")
    elif burstiness < 0.45:
        signals.append(f"WEAK AI signal — burstiness {burstiness:.2f} (moderately uniform)")
    else:
        signals.append(f"HUMAN signal — burstiness {burstiness:.2f} (natural variation)")

    if ttr < 0.45:
        signals.append(f"STRONG AI signal — TTR {ttr:.2f} (low vocabulary diversity)")
    elif ttr > 0.65:
        signals.append(f"HUMAN signal — TTR {ttr:.2f} (rich vocabulary)")
    else:
        signals.append(f"NEUTRAL — TTR {ttr:.2f}")

    if ai_phrase_density > 0.5:
        signals.append(f"STRONG AI signal — AI phrase density {ai_phrase_density:.2f}/100w")
    elif ai_phrase_density > 0.2:
        signals.append(f"WEAK AI signal — AI phrase density {ai_phrase_density:.2f}/100w")

    signals_text = " | ".join(signals)

    prompt = f"""You are an expert AI writing detection engine with access to both objective statistical metrics and linguistic analysis.

STATISTICAL METRICS (computed from this text section):
- Sentence Burstiness (CV of word-counts per sentence): {burstiness:.4f}
  [Human baseline: ≥ 0.50 | AI-generated text baseline: < 0.30]
- Vocabulary Diversity (Type-Token Ratio): {ttr:.4f}
  [Human baseline: ≥ 0.60 | AI-generated text baseline: < 0.50]
- Average Sentence Length: {avg_sent_len:.1f} words
- AI Phrase Density: {ai_phrase_density:.4f} per 100 words
- Composite Signal: {signals_text}

TEXT SECTION TO ANALYZE:
\"\"\"{section_text[:3000]}\"\"\"

TASK:
Weight the statistical metrics HEAVILY — they are objective, reproducible measurements.
Use your linguistic analysis of the text to provide supporting evidence.
Assign a final AI probability score on a 0–100 scale.

SCORING GUIDE:
- 0–15: Definitely human-written (high burstiness, high TTR, genuine personal voice)
- 16–35: Mostly human, minor AI-like patterns
- 36–55: Mixed signals — uncertain origin
- 56–75: Likely AI-generated (low burstiness + low TTR + AI phrases)
- 76–100: Very likely AI-generated (all metrics converge on AI pattern)

RESPOND IN THIS EXACT JSON FORMAT ONLY (no markdown, no preamble):
{{
  "ai_score": <integer 0-100>,
  "analysis": "<2-3 sentences citing both the statistical evidence and specific linguistic patterns observed>"
}}"""

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an AI writing detection engine. "
                        "You MUST weight the provided statistical metrics heavily. "
                        "Respond ONLY with valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )

        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        result = json.loads(result_text)
        result["heuristics"] = heuristics
        return result

    except json.JSONDecodeError:
        return {"ai_score": 0, "analysis": "Failed to parse AI response", "heuristics": heuristics}
    except Exception as e:
        return {"ai_score": 0, "analysis": f"Error: {str(e)}", "heuristics": heuristics}


async def analyze_plagiarism(chunk_text: str, web_sources: list[dict]) -> dict:
    """
    Analyze a text chunk for plagiarism against web sources using Groq LLM.
    Uses llama-3.3-70b-versatile for higher accuracy.
    """
    if not settings.GROQ_API_KEY:
        return {"plagiarism_score": 0, "matched_sources": [], "analysis": "", "match_type": "original"}

    client = get_groq_client()

    sources_context = ""
    for i, source in enumerate(web_sources[:5]):
        sources_context += f"\n--- Source {i + 1} ---\n"
        sources_context += f"URL: {source.get('url', 'N/A')}\n"
        sources_context += f"Title: {source.get('title', 'N/A')}\n"
        sources_context += f"Content: {source.get('content', 'N/A')}\n"

    prompt = f"""You are an expert plagiarism detection system. Analyze the following text chunk from a student's document and compare it against the web sources found online.

DOCUMENT TEXT:
\"\"\"{chunk_text}\"\"\"

WEB SOURCES FOUND:
{sources_context if sources_context.strip() else "No matching web sources found."}

INSTRUCTIONS:
1. Compare the document text with each web source for similarity.
2. Check for direct copying, paraphrasing, or close rewording.
3. Determine the match_type:
   - "not_cited": Text matches a source but has no citation or quotation marks
   - "missing_quote": Text is very similar to source but lacks quotation marks
   - "missing_citation": Text has quotation marks but no proper citation
   - "cited_quoted": Text is properly cited and quoted
   - "original": No significant match found
4. Assign a plagiarism score from 0 to 100:
   - 0-10: Original content, no matches found
   - 11-30: Minor similarities, likely coincidental
   - 31-60: Moderate similarity, possible paraphrasing
   - 61-80: High similarity, likely plagiarized with modifications
   - 81-100: Very high match, direct copying detected

RESPOND IN THIS EXACT JSON FORMAT ONLY (no extra text):
{{
  "plagiarism_score": <number 0-100>,
  "match_type": "<not_cited|missing_quote|missing_citation|cited_quoted|original>",
  "matched_sources": [
    {{
      "url": "<source url>",
      "title": "<source title>",
      "similarity": <number 0-100>,
      "matched_text": "<specific text from the student document that matches the source>"
    }}
  ],
  "analysis": "<brief 1-2 sentence explanation of findings>"
}}"""

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a plagiarism detection engine. Respond ONLY with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1000,
        )

        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        return json.loads(result_text)

    except json.JSONDecodeError:
        return {"plagiarism_score": 0, "matched_sources": [], "analysis": "Failed to parse AI response", "match_type": "original"}
    except Exception as e:
        return {"plagiarism_score": 0, "matched_sources": [], "analysis": f"Error: {str(e)}", "match_type": "original"}


async def detect_ai_writing(chunk_text: str) -> dict:
    """
    Legacy small-chunk AI detection (kept for scanner_service backward compat).
    New code should use detect_ai_writing_full with large-window sections.
    """
    if not settings.GROQ_API_KEY:
        return {"ai_score": 0, "analysis": "API key not configured"}

    client = get_groq_client()

    prompt = f"""You are an expert AI writing detection system. Analyze the following text and determine the likelihood that it was generated by an AI language model.

TEXT TO ANALYZE:
\"\"\"{chunk_text}\"\"\"

ANALYSIS CRITERIA:
1. Perplexity: AI text tends to have uniform, low perplexity (predictable word choices).
2. Burstiness: Human writing has variable sentence lengths. AI tends to be more uniform.
3. Vocabulary patterns: AI often uses phrases like "it's important to note", "furthermore", "delve into".
4. Structure: AI text is often overly organized with clear transitions.
5. Creativity markers: Human text has unique analogies and personal voice.

SCORING: 0-15 (human) | 16-35 (mostly human) | 36-55 (mixed) | 56-75 (likely AI) | 76-100 (very likely AI)

RESPOND IN THIS EXACT JSON FORMAT ONLY:
{{
  "ai_score": <number 0-100>,
  "analysis": "<brief 2-3 sentence explanation>"
}}"""

    try:
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are an AI writing detection engine. Respond ONLY with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        return json.loads(result_text)

    except json.JSONDecodeError:
        return {"ai_score": 0, "analysis": "Failed to parse AI response"}
    except Exception as e:
        return {"ai_score": 0, "analysis": f"Error: {str(e)}"}
