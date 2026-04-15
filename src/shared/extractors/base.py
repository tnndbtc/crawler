"""
Extractor dispatch table.

base.EXTRACTORS maps lang_group -> extractor function.
base.DEFAULT_EXTRACTOR is the fallback for unregistered langs.

Both are populated by shared.extractors.__init__ at import time.
Import from the package (not this module directly) to get the populated state:

    from shared.extractors import EXTRACTORS, DEFAULT_EXTRACTOR
"""
from typing import Callable

# Signature: (title: str, lang: str) -> list[str]
ExtractorFn = Callable[[str, str], list[str]]

EXTRACTORS: dict[str, ExtractorFn] = {}
DEFAULT_EXTRACTOR: ExtractorFn  # assigned by __init__.py
