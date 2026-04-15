import logging

logger = logging.getLogger(__name__)

_REGION_SUFFIXES = {'都', '道', '府', '県', '市', '区', '町', '村'}

# Module-level caches — populated on first call, reused for all subsequent items
_tagger = None
_sw_ja = None


def _get_tagger():
    global _tagger
    if _tagger is None:
        import fugashi
        _tagger = fugashi.Tagger()
    return _tagger


def _get_stopwords_ja() -> frozenset:
    global _sw_ja
    if _sw_ja is None:
        try:
            import stopwordsiso
            _sw_ja = frozenset(stopwordsiso.stopwords('ja') or [])
        except Exception:
            _sw_ja = frozenset()
    return _sw_ja


def _is_proper_noun(feature) -> bool:
    # fugashi with unidic-lite returns a UnidicFeatures26 object with named
    # attributes (pos1, pos2, ...), not a plain comma-separated string.
    try:
        return feature.pos1 == '名詞' and feature.pos2 == '固有名詞'
    except AttributeError:
        # Fallback for any tagger variant that returns a plain string
        parts = str(feature).split(',')
        return len(parts) >= 2 and parts[0] == '名詞' and parts[1] == '固有名詞'

def extract_japanese_entities(title: str, lang: str = '') -> list[str]:
    """Extract proper nouns from Japanese text using fugashi + unidic-lite."""
    try:
        tagger = _get_tagger()
    except ImportError:
        logger.warning("fugashi not installed — skipping Japanese extraction")
        return []
    except Exception as e:
        logger.warning(f"fugashi tagger init failed: {e}")
        return []

    sw = _get_stopwords_ja()

    tokens = list(tagger(title))
    entities: list[str] = []

    i = 0
    while i < len(tokens):
        node = tokens[i]
        try:
            feature = node.feature
        except Exception:
            i += 1
            continue

        if not _is_proper_noun(feature):
            i += 1
            continue

        # Start a compound
        compound_parts = [node.surface]
        j = i + 1
        while j < len(tokens) and len(compound_parts) < 4:
            next_node = tokens[j]
            try:
                next_feature = next_node.feature
            except Exception:
                break
            if _is_proper_noun(next_feature):
                compound_parts.append(next_node.surface)
                j += 1
            elif next_node.surface in _REGION_SUFFIXES:
                compound_parts.append(next_node.surface)
                j += 1
                break  # region suffix ends compound
            else:
                break

        # Emit individual proper nouns
        for part in compound_parts:
            if len(part) >= 2 and part not in sw:
                entities.append(part)

        # Emit compound if it grew beyond a single token
        if len(compound_parts) > 1:
            compound = ''.join(compound_parts)
            if compound not in sw:
                entities.append(compound)

        i = j if j > i + 1 else i + 1

    # Deduplicate, preserve order
    seen: set[str] = set()
    result = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result
