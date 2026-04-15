import logging
import regex  # Unicode-property regex

logger = logging.getLogger(__name__)

# Unicode-aware equivalents of the English regex patterns
_RE_ALLCAPS_U    = regex.compile(r'\b\p{Lu}{2,6}\b')
_RE_TITLECASE_U  = regex.compile(r'\b(?:\p{Lu}\p{Ll}+\s){1,3}\p{Lu}\p{Ll}+\b')
_RE_CAMELCASE_U  = regex.compile(r'\b\p{Lu}\p{Ll}+(?:\p{Lu}\p{Ll}+)+\b')

# Cache stopword sets per lang to avoid re-loading on each call
_SW_CACHE: dict[str, frozenset[str]] = {}

def _get_stopwords(lang: str) -> frozenset[str]:
    if lang not in _SW_CACHE:
        try:
            import stopwordsiso
            sw = stopwordsiso.stopwords(lang)
            _SW_CACHE[lang] = frozenset(w.casefold() for w in sw) if sw else frozenset()
        except Exception:
            _SW_CACHE[lang] = frozenset()
    return _SW_CACHE[lang]

def extract_latin_unicode_entities(title: str, lang: str = '') -> list[str]:
    """
    Extract named entities from Latin+diacritics or Cyrillic titles.
    Uses Unicode property classes so é, ñ, ü, А-Я etc. are handled correctly.
    """
    entities: set[str] = set()

    for m in _RE_ALLCAPS_U.finditer(title):
        entities.add(m.group())
    for m in _RE_TITLECASE_U.finditer(title):
        entities.add(m.group().strip())
    for m in _RE_CAMELCASE_U.finditer(title):
        entities.add(m.group())

    stopwords = _get_stopwords(lang) if lang else frozenset()

    results = []
    for e in entities:
        # Turkish casefold: use casefold() which handles İ/I correctly
        e_cf = e.casefold()
        if e_cf in stopwords:
            continue
        if len(e) < 2:
            continue
        # German pre-filter (Fix 10): drop candidates that appear only once in the title
        if lang == 'de' and title.count(e) < 2:
            continue
        results.append(e)

    return results
