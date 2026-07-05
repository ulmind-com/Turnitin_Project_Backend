import re
from typing import Optional


# Known AI filler phrases used as an AI-probability signal
_AI_PHRASES = [
    "it's important to note", "it is important to note", "in conclusion",
    "furthermore", "delve into", "it is worth noting", "in summary",
    "to summarize", "moreover", "additionally", "in this context",
    "in the realm of", "is crucial", "when it comes to",
    "it's worth noting", "having said that", "on the other hand",
    "this is because", "needless to say", "it goes without saying",
]


def split_into_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if len(s.strip()) >= 10]


def create_overlapping_chunks(
    text: str,
    sentences_per_chunk: int = 4,
    overlap_sentences: int = 1,
) -> list[dict]:
    """
    Small sentence-based overlapping chunks for precise web search queries.
    Used by the plagiarism engine (Tavily + similarity scoring).
    """
    sentences = split_into_sentences(text)
    if not sentences:
        return []

    if len(sentences) <= sentences_per_chunk:
        return [{"index": 0, "text": " ".join(sentences),
                 "start_sentence": 0, "end_sentence": len(sentences) - 1}]

    chunks = []
    step = max(1, sentences_per_chunk - overlap_sentences)
    chunk_index = 0

    for i in range(0, len(sentences), step):
        end = min(i + sentences_per_chunk, len(sentences))
        chunk_text = " ".join(sentences[i:end])
        if len(chunk_text.strip()) >= 20:
            chunks.append({
                "index": chunk_index,
                "text": chunk_text,
                "start_sentence": i,
                "end_sentence": end - 1,
            })
            chunk_index += 1
        if end >= len(sentences):
            break

    return chunks


def create_large_window_chunks(
    text: str,
    words_per_chunk: int = 800,
    overlap_words: int = 100,
) -> list[dict]:
    """
    Large overlapping word-windows for AI detection.
    Bigger context lets the LLM observe sentence-rhythm patterns.
    """
    words = text.split()
    if not words:
        return []

    if len(words) <= words_per_chunk:
        return [{"index": 0, "text": text}]

    chunks = []
    step = max(1, words_per_chunk - overlap_words)
    idx = 0

    for start in range(0, len(words), step):
        end = min(start + words_per_chunk, len(words))
        chunk_text = " ".join(words[start:end])
        if len(chunk_text.strip()) >= 50:
            chunks.append({"index": idx, "text": chunk_text})
            idx += 1
        if end >= len(words):
            break

    return chunks


def compute_text_heuristics(text: str) -> dict:
    """Compute statistical heuristics for an AI detection section."""
    sentences = split_into_sentences(text)
    words = re.findall(r"\b\w+\b", text.lower())

    # Burstiness
    if len(sentences) >= 2:
        lengths = [len(re.findall(r"\b\w+\b", s)) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        if mean_len > 0:
            variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
            burstiness = (variance ** 0.5) / mean_len
        else:
            burstiness = 0.0
        avg_sentence_len = mean_len
    else:
        burstiness = 0.0
        avg_sentence_len = float(len(words))

    # Type-Token Ratio
    ttr = len(set(words)) / len(words) if words else 0.0

    # AI phrase density
    text_lower = text.lower()
    ai_phrase_count = sum(1 for p in _AI_PHRASES if p in text_lower)
    ai_phrase_density = ai_phrase_count / max(len(words) / 100.0, 1.0)

    return {
        "burstiness": round(burstiness, 4),
        "type_token_ratio": round(ttr, 4),
        "avg_sentence_length": round(avg_sentence_len, 2),
        "ai_phrase_density": round(ai_phrase_density, 4),
        "sentence_count": len(sentences),
        "word_count": len(words),
    }


def extract_key_phrases(text: str, max_phrases: int = 3) -> list[str]:
    """
    Extract short, distinctive key phrases for web search.
    
    Strategy: Take 5-8 word windows from unique parts of the text.
    Keeps original casing and punctuation for natural search queries.
    """
    sentences = split_into_sentences(text)
    if not sentences:
        return [text[:80]]

    phrases = []
    # Pick from different parts of the chunk: beginning, middle, end
    pick_indices = [0]
    if len(sentences) > 2:
        pick_indices.append(len(sentences) // 2)
    if len(sentences) > 1:
        pick_indices.append(len(sentences) - 1)

    for idx in pick_indices:
        if idx < len(sentences):
            words = sentences[idx].split()
            # Take 5-8 words — short enough for search, long enough for uniqueness
            phrase_words = words[:min(8, len(words))]
            phrase = " ".join(phrase_words)
            if len(phrase) >= 15:  # Skip very short phrases
                phrases.append(phrase)

    return phrases[:max_phrases] if phrases else [text[:80]]
