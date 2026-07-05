import re
from typing import Optional


def split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences using regex.
    Handles common abbreviations and edge cases.
    """
    # Clean up whitespace
    text = re.sub(r"\s+", " ", text.strip())

    # Split on sentence-ending punctuation followed by space + uppercase
    # or end of string
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

    # Filter out very short fragments (< 10 chars)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 10]

    return sentences


def create_overlapping_chunks(
    text: str,
    sentences_per_chunk: int = 4,
    overlap_sentences: int = 1,
) -> list[dict]:
    """
    Split text into overlapping sentence-based chunks.

    Args:
        text: The full document text.
        sentences_per_chunk: Number of sentences per chunk (3-5 recommended).
        overlap_sentences: Number of overlapping sentences between chunks.

    Returns:
        List of dicts with 'index', 'text', 'start_sentence', 'end_sentence'.
    """
    sentences = split_into_sentences(text)

    if not sentences:
        return []

    # If text is very short, return as single chunk
    if len(sentences) <= sentences_per_chunk:
        return [
            {
                "index": 0,
                "text": " ".join(sentences),
                "start_sentence": 0,
                "end_sentence": len(sentences) - 1,
            }
        ]

    chunks = []
    step = max(1, sentences_per_chunk - overlap_sentences)
    chunk_index = 0

    for i in range(0, len(sentences), step):
        end = min(i + sentences_per_chunk, len(sentences))
        chunk_sentences = sentences[i:end]
        chunk_text = " ".join(chunk_sentences)

        if len(chunk_text.strip()) >= 20:  # skip tiny chunks
            chunks.append(
                {
                    "index": chunk_index,
                    "text": chunk_text,
                    "start_sentence": i,
                    "end_sentence": end - 1,
                }
            )
            chunk_index += 1

        # Stop if we've reached the end
        if end >= len(sentences):
            break

    return chunks


def extract_key_phrases(text: str, max_phrases: int = 3) -> list[str]:
    """
    Extract key phrases from a chunk of text for web search queries.
    Uses a simple heuristic: longest unique n-grams that aren't too common.
    """
    # Clean text
    clean = re.sub(r"[^\w\s]", "", text.lower())
    words = clean.split()

    if len(words) < 4:
        return [text[:100]]

    # Extract 4-6 word phrases from beginning, middle, and end
    phrases = []
    phrase_len = min(6, len(words) // 2)

    if phrase_len >= 3:
        # Beginning
        phrases.append(" ".join(words[:phrase_len]))
        # Middle
        mid = len(words) // 2
        phrases.append(" ".join(words[mid : mid + phrase_len]))
        # End
        if len(words) > phrase_len * 2:
            phrases.append(" ".join(words[-phrase_len:]))

    return phrases[:max_phrases]
