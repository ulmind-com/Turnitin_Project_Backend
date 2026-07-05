import re
import unicodedata

def clean_text(text: str) -> str:
    """
    Cleans and normalizes raw extracted text.
    - Standardizes Unicode characters using NFKC normalization.
    - Replaces consecutive whitespace characters with a single space.
    - Keeps standard punctuation but strips non-printable control characters.
    """
    if not text:
        return ""
    # Unicode NFKC normalization (standardizes characters like curly quotes, ligature glyphs, etc.)
    text = unicodedata.normalize("NFKC", text)
    # Remove control characters (except newline/tab)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    # Replace multiple spaces with a single space (while keeping lines intact)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def tokenize_text(text: str) -> list[str]:
    """
    Breaks a clean text string into standardized lowercase word tokens.
    """
    if not text:
        return []
    # Split on non-alphanumeric characters, keeping words
    tokens = re.findall(r"\b\w+\b", text.lower())
    return tokens


def filter_bibliography_and_quotes(text: str) -> tuple[str, list[str]]:
    """
    Detects and filters out bibliography/reference sections and properly cited quotes.
    Returns:
        tuple containing (filtered_text, filtered_tokens)
    """
    if not text:
        return "", []

    # 1. Detect and exclude Bibliography/References section
    # Search for headings like References, Bibliography, Works Cited at the start of a line
    bib_patterns = [
        r"(?i)\n\s*(references|bibliography|works\s+cited|literature\s+cited|sources)\s*[\n\:]",
        r"(?i)^\s*(references|bibliography|works\s+cited|literature\s+cited|sources)\s*[\n\:]"
    ]
    
    split_index = len(text)
    for pattern in bib_patterns:
        match = re.search(pattern, text)
        if match:
            # Mark the start of the references section
            split_index = min(split_index, match.start())
            
    # Text before the references section
    content_text = text[:split_index]
    
    # 2. Exclude properly cited quotes
    # Matches text inside standard quotation marks, e.g., "quote" or “quote” or 'quote'
    # Followed by a citation like (Author, Year) or [1] or [12] within 30 characters
    quote_pattern = r'(["\'“‘])(.*?)(["\'”’])(\s*[\(\[][^\]\)]+[\)\]])?'
    
    def quote_replacer(match):
        quote_body = match.group(2)
        citation = match.group(4)
        # If there is a citation following the quote, exclude the quote body text from plagiarism scan
        if citation:
            # We replace the quote with a blank or placeholder to ignore its content
            return f" [CITED_QUOTE_EXCLUDED] {citation}"
        return match.group(0) # Keep unmodified if not cited

    filtered_text = re.sub(quote_pattern, quote_replacer, content_text)
    
    # Clean up placeholders/extra spaces
    filtered_text = clean_text(filtered_text)
    tokens = tokenize_text(filtered_text)
    
    return filtered_text, tokens
