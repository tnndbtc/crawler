import logging

logger = logging.getLogger(__name__)

# Keep proper nouns (NNP) and common nouns with >= 2 syllables (NNG)
_KEEP_POS = {'NNP', 'NNG'}

# Module-level caches — populated on first call, reused for all subsequent items
_kiwi = None
_sw_ko = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


def _get_stopwords_ko() -> frozenset:
    global _sw_ko
    if _sw_ko is None:
        try:
            import stopwordsiso
            _sw_ko = frozenset(stopwordsiso.stopwords('ko') or [])
        except Exception:
            _sw_ko = frozenset()
    return _sw_ko


def extract_korean_entities(title: str, lang: str = '') -> list[str]:
    """Extract proper nouns from Korean text using kiwipiepy."""
    try:
        kiwi = _get_kiwi()
    except ImportError:
        logger.warning("kiwipiepy not installed — skipping Korean extraction")
        return []

    sw = _get_stopwords_ko()

    try:
        result_list = kiwi.analyze(title)
    except Exception as e:
        logger.warning(f"kiwipiepy analysis failed: {e}")
        return []

    if not result_list:
        return []

    # kiwi.analyze returns list of (result, score); take best
    tokens = result_list[0][0]

    entities = []
    for token in tokens:
        pos = token.tag if hasattr(token, 'tag') else str(token[1])
        surface = token.form if hasattr(token, 'form') else token[0]

        # NNG: require >= 2 syllables to reduce common-noun noise
        if pos == 'NNG' and len(surface) < 2:
            continue
        if pos not in _KEEP_POS:
            continue
        if surface in sw:
            continue
        entities.append(surface)

    return entities
