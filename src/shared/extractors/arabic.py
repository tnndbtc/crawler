import logging
import regex

logger = logging.getLogger(__name__)

# Match runs of 2+ Arabic characters (U+0600–U+06FF)
_RE_ARABIC = regex.compile(r'[\u0600-\u06FF]{2,}')
_ARABIC_ARTICLE = 'ال'

def _strip_arabic_article(word: str) -> str:
    """Strip ال definite article prefix for canonical form."""
    if word.startswith(_ARABIC_ARTICLE) and len(word) > len(_ARABIC_ARTICLE):
        return word[len(_ARABIC_ARTICLE):]
    return word

def extract_arabic_entities(title: str, lang: str = '') -> list[str]:
    """Extract candidate entities from Arabic text using regex + stopwords."""
    try:
        import stopwordsiso
        sw = frozenset(stopwordsiso.stopwords('ar') or [])
    except Exception:
        sw = frozenset()

    matches = _RE_ARABIC.findall(title)
    seen_canonical: set[str] = set()
    result = []
    for word in matches:
        canonical = _strip_arabic_article(word)
        if canonical in sw:
            continue
        if len(canonical) < 2:
            continue
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        result.append(word)  # emit original form (with article if present)

    return result
