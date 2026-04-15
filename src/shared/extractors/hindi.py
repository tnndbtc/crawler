import logging
import regex

logger = logging.getLogger(__name__)

# Match runs of 2+ Devanagari characters (U+0900–U+097F)
_RE_DEVANAGARI = regex.compile(r'[\u0900-\u097F]{2,}')

def extract_hindi_entities(title: str, lang: str = '') -> list[str]:
    """Extract candidate entities from Hindi text using Devanagari regex + stopwords."""
    try:
        import stopwordsiso
        sw = frozenset(stopwordsiso.stopwords('hi') or [])
    except Exception:
        sw = frozenset()

    matches = _RE_DEVANAGARI.findall(title)
    result = []
    seen: set[str] = set()
    for word in matches:
        if word in sw:
            continue
        if word in seen:
            continue
        seen.add(word)
        result.append(word)

    return result
