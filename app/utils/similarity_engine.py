import re
import hashlib
from app.utils.text_processor import tokenize_text


def generate_ngrams(text: str, n: int = 5) -> list[str]:
    """
    Splits text into a list of contiguous N-grams (sequences of N words).
    """
    # Use standard tokenizer
    words = tokenize_text(text)
    if len(words) < n:
        # If text is too short, return the entire sentence as a single n-gram
        return [" ".join(words)] if words else []

    ngrams = []
    for i in range(len(words) - n + 1):
        ngrams.append(" ".join(words[i : i + n]))
    return ngrams


def generate_ngram_hashes(text: str, n: int = 5) -> set[str]:
    """
    Generates a set of MD5 hashes for all N-grams in the text.
    MD5 hashes are deterministic and efficient to store and index.
    """
    ngrams = generate_ngrams(text, n)
    hashes = set()
    for ngram in ngrams:
        hashed = hashlib.md5(ngram.encode("utf-8")).hexdigest()
        hashes.add(hashed)
    return hashes


def calculate_jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    """
    Calculates Jaccard Similarity between two sets of N-gram hashes.
    Formula: J(A, B) = |A ∩ B| / |A ∪ B|
    Returns percentage representation (0.0 to 100.0).
    """
    if not set1 or not set2:
        return 0.0
    
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    
    if union == 0:
        return 0.0
        
    return round((intersection / union) * 100.0, 2)
