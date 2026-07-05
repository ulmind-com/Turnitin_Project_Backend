import re

def check_integrity(text: str) -> list[dict]:
    """
    Scans the raw extracted text for evasion tactics:
    - Hidden/zero-width characters (e.g. U+200B, U+200C, Soft Hyphens)
    - Character swapping (homoglyphs) where non-Latin characters (like Cyrillic/Greek) 
      are mixed into Latin words to bypass pattern match engines.
      
    Returns a list of structured flags (dicts containing type and description).
    """
    flags = []
    if not text:
        return flags

    # 1. Zero-width / hidden characters check
    zero_width_chars = [
        ("\u200b", "Zero-Width Space (U+200B)"),
        ("\u200c", "Zero-Width Non-Joiner (U+200C)"),
        ("\u200d", "Zero-Width Joiner (U+200D)"),
        ("\ufeff", "Byte Order Mark (U+FEFF)"),
        ("\u00ad", "Soft Hyphen (U+00AD)"),
    ]
    
    zw_counts = {}
    for char, name in zero_width_chars:
        count = text.count(char)
        if count > 0:
            zw_counts[name] = count
            
    if zw_counts:
        desc = ", ".join([f"{name} ({count}x)" for name, count in zw_counts.items()])
        flags.append({
            "type": "Hidden Characters",
            "description": f"Detected invisible/zero-width formatting characters used to split words: {desc}"
        })
        
    # 2. Homoglyph / character swapping check
    # Find all words/tokens in the text
    words = re.findall(r"\w+", text)
    mixed_script_count = 0
    suspicious_words = []
    
    for word in words:
        has_latin = False
        has_cyrillic = False
        has_greek = False
        for char in word:
            code = ord(char)
            # Basic Latin & Latin Extended
            if (0x0041 <= code <= 0x005a) or (0x0061 <= code <= 0x007a) or (0x00c0 <= code <= 0x017f):
                has_latin = True
            # Cyrillic blocks
            elif 0x0400 <= code <= 0x04ff or 0x0500 <= code <= 0x052f:
                has_cyrillic = True
            # Greek block
            elif 0x0370 <= code <= 0x03ff:
                has_greek = True
                
        # If a single word mixes scripts, it's a homoglyph swap!
        if (has_latin and has_cyrillic) or (has_latin and has_greek) or (has_cyrillic and has_greek):
            mixed_script_count += 1
            # Clean non-printable/zero-width characters for display
            clean_w = "".join(ch for ch in word if ord(ch) >= 32)
            if clean_w not in suspicious_words and len(suspicious_words) < 5:
                suspicious_words.append(clean_w)
                
    if mixed_script_count > 0:
        examples = ", ".join([f"\"{w}\"" for w in suspicious_words])
        flags.append({
            "type": "Character Substitution",
            "description": f"Detected {mixed_script_count} word(s) mixing Latin and Cyrillic/Greek characters to bypass detection. Examples: {examples}."
        })
        
    return flags
