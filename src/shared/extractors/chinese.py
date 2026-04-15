import logging

logger = logging.getLogger(__name__)

_CHINESE_NOUN_FLAGS = {'n', 'nr', 'ns', 'nt', 'nz'}

def extract_chinese_entities(title: str, lang: str = '') -> list[str]:
    """Extract nouns from a Chinese title using jieba POS tagging."""
    try:
        import jieba.posseg as pseg
    except ImportError:
        logger.warning("jieba not installed — skipping Chinese title extraction")
        return []
    words = pseg.cut(title)
    return [
        w.word.strip()
        for w in words
        if w.flag in _CHINESE_NOUN_FLAGS and len(w.word.strip()) >= 2
    ]
