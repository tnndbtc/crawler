"""
Multilingual named-entity extractor package.

Usage:
    from shared.extractors import EXTRACTORS, DEFAULT_EXTRACTOR

    extractor = EXTRACTORS.get(lang, DEFAULT_EXTRACTOR)
    entities = extractor(title, lang)
"""

from . import base
from .english import extract_english_entities
from .chinese import extract_chinese_entities
from .latin_unicode import extract_latin_unicode_entities
from .japanese import extract_japanese_entities
from .korean import extract_korean_entities
from .arabic import extract_arabic_entities
from .thai import extract_thai_entities
from .hindi import extract_hindi_entities

# Set default (English regex fallback for unregistered langs)
base.DEFAULT_EXTRACTOR = extract_english_entities

# Phase 1-6 dispatch table
base.EXTRACTORS.update({
    # Phase 0 — Chinese (pre-existing)
    'zh': extract_chinese_entities,
    # Phase 1 — Unicode regex (Latin+diacritics + Cyrillic)
    'pt': extract_latin_unicode_entities,
    'es': extract_latin_unicode_entities,
    'fr': extract_latin_unicode_entities,
    'de': extract_latin_unicode_entities,
    'it': extract_latin_unicode_entities,
    'tr': extract_latin_unicode_entities,
    'pl': extract_latin_unicode_entities,
    'sv': extract_latin_unicode_entities,
    'vi': extract_latin_unicode_entities,
    'ms': extract_latin_unicode_entities,
    'nl': extract_latin_unicode_entities,
    'id': extract_latin_unicode_entities,
    'ru': extract_latin_unicode_entities,
    'uk': extract_latin_unicode_entities,
    # Phase 2 — Japanese
    'ja': extract_japanese_entities,
    # Phase 3 — Korean
    'ko': extract_korean_entities,
    # Phase 4 — Arabic
    'ar': extract_arabic_entities,
    # Phase 5 — Thai
    'th': extract_thai_entities,
    # Phase 6 — Hindi
    'hi': extract_hindi_entities,
})

# Re-export for convenience
EXTRACTORS = base.EXTRACTORS
DEFAULT_EXTRACTOR = base.DEFAULT_EXTRACTOR
