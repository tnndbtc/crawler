import logging

logger = logging.getLogger(__name__)

_KEEP_POS = {'NN', 'NP', 'PPER', 'PDEM'}  # nouns + proper nouns in perceptron tagger

def extract_thai_entities(title: str, lang: str = '') -> list[str]:
    """Extract proper nouns from Thai text using pythainlp."""
    try:
        from pythainlp.tokenize import word_tokenize
        from pythainlp.tag import pos_tag
        from pythainlp.corpus import thai_stopwords
    except ImportError:
        logger.warning("pythainlp not installed — skipping Thai extraction")
        return []

    try:
        sw = thai_stopwords()
    except Exception:
        sw = set()

    try:
        tokens = word_tokenize(title, engine='newmm')
        tagged = pos_tag(tokens, engine='perceptron')
    except Exception as e:
        logger.warning(f"pythainlp processing failed: {e}")
        return []

    entities = []
    for word, pos in tagged:
        word = word.strip()
        if not word or word in sw:
            continue
        if pos not in _KEEP_POS:
            continue
        if len(word) < 2:
            continue
        entities.append(word)

    return entities
