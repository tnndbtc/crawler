"""
Language detection utilities for the language-aware ingestion system.

Uses langdetect library for base language detection and combines it with
region/locale metadata to classify items into language groups.

Key Functions:
- detect_language(): Base language detection from text
- classify_item_language(): Full language classification (base_lang, locale, lang_group)

Locale Mapping Strategy:
- zh-Hans, zh-Hant, zh-CN, zh-TW → lang_group='zh'
- en-US, en-GB, en-AU → lang_group='en'
- ja-JP → lang_group='ja'
- etc.

This enables language-family grouping for selective translation.
"""

import logging
from typing import Tuple, Optional
from langdetect import detect, LangDetectException

logger = logging.getLogger(__name__)


# Locale to language group mapping
# Maps specific locales to their language family for grouping
LOCALE_TO_LANG_GROUP = {
    # Chinese variants
    'zh-Hans': 'zh',
    'zh-Hant': 'zh',
    'zh-CN': 'zh',
    'zh-TW': 'zh',
    'zh-HK': 'zh',
    'zh-SG': 'zh',

    # English variants
    'en-US': 'en',
    'en-GB': 'en',
    'en-AU': 'en',
    'en-CA': 'en',
    'en-NZ': 'en',
    'en-IE': 'en',

    # Japanese
    'ja-JP': 'ja',
    'ja': 'ja',

    # Korean
    'ko-KR': 'ko',
    'ko': 'ko',

    # Spanish variants
    'es-ES': 'es',
    'es-MX': 'es',
    'es-AR': 'es',
    'es-CO': 'es',
    'es-CL': 'es',

    # French variants
    'fr-FR': 'fr',
    'fr-CA': 'fr',
    'fr-BE': 'fr',
    'fr-CH': 'fr',

    # German variants
    'de-DE': 'de',
    'de-AT': 'de',
    'de-CH': 'de',

    # Portuguese variants
    'pt-BR': 'pt',
    'pt-PT': 'pt',

    # Italian
    'it-IT': 'it',
    'it': 'it',

    # Russian
    'ru-RU': 'ru',
    'ru': 'ru',

    # Arabic variants
    'ar-SA': 'ar',
    'ar-AE': 'ar',
    'ar-EG': 'ar',

    # Hindi
    'hi-IN': 'hi',
    'hi': 'hi',

    # Indonesian
    'id-ID': 'id',
    'id': 'id',

    # Thai
    'th-TH': 'th',
    'th': 'th',

    # Vietnamese
    'vi-VN': 'vi',
    'vi': 'vi',

    # Dutch
    'nl-NL': 'nl',
    'nl-BE': 'nl',
    'nl': 'nl',

    # Polish
    'pl-PL': 'pl',
    'pl': 'pl',

    # Turkish
    'tr-TR': 'tr',
    'tr': 'tr',

    # Swedish
    'sv-SE': 'sv',
    'sv': 'sv',

    # Norwegian
    'no-NO': 'no',
    'nb-NO': 'no',
    'nn-NO': 'no',
    'no': 'no',

    # Danish
    'da-DK': 'da',
    'da': 'da',

    # Finnish
    'fi-FI': 'fi',
    'fi': 'fi',

    # Greek
    'el-GR': 'el',
    'el': 'el',

    # Hebrew
    'he-IL': 'he',
    'he': 'he',

    # Czech
    'cs-CZ': 'cs',
    'cs': 'cs',

    # Hungarian
    'hu-HU': 'hu',
    'hu': 'hu',

    # Romanian
    'ro-RO': 'ro',
    'ro': 'ro',

    # Ukrainian
    'uk-UA': 'uk',
    'uk': 'uk',
}


# Base language to locale mapping (for when we only detect base lang)
# Maps ISO 639-1 codes to default locale
BASE_LANG_TO_DEFAULT_LOCALE = {
    'zh': 'zh-Hans',  # Default to Simplified Chinese
    'en': 'en-US',
    'ja': 'ja-JP',
    'ko': 'ko-KR',
    'es': 'es-ES',
    'fr': 'fr-FR',
    'de': 'de-DE',
    'pt': 'pt-BR',
    'it': 'it-IT',
    'ru': 'ru-RU',
    'ar': 'ar-SA',
    'hi': 'hi-IN',
    'id': 'id-ID',
    'th': 'th-TH',
    'vi': 'vi-VN',
    'nl': 'nl-NL',
    'pl': 'pl-PL',
    'tr': 'tr-TR',
    'sv': 'sv-SE',
    'no': 'no-NO',
    'da': 'da-DK',
    'fi': 'fi-FI',
    'el': 'el-GR',
    'he': 'he-IL',
    'cs': 'cs-CZ',
    'hu': 'hu-HU',
    'ro': 'ro-RO',
    'uk': 'uk-UA',
}


def detect_language(text: str, fallback: str = 'en') -> str:
    """
    Detect base language from text using langdetect.

    Args:
        text: Text to analyze (title + description recommended)
        fallback: Fallback language if detection fails (default: 'en')

    Returns:
        ISO 639-1 language code (e.g., 'en', 'zh', 'ja')

    Note:
        - Combines title and description for better accuracy
        - Returns fallback on detection failure
        - Logs errors but never crashes
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for language detection, using fallback")
        return fallback

    try:
        # langdetect returns ISO 639-1 codes (2-letter)
        detected = detect(text)
        logger.debug(f"Detected language: {detected} from text: {text[:50]}...")
        return detected
    except LangDetectException as e:
        logger.warning(
            f"Language detection failed for text '{text[:50]}...': {e}. "
            f"Using fallback: {fallback}"
        )
        return fallback
    except Exception as e:
        logger.error(
            f"Unexpected error in language detection: {e}. "
            f"Using fallback: {fallback}",
            exc_info=True
        )
        return fallback


def normalize_locale(locale: str) -> str:
    """
    Normalize locale string to standard BCP47 format.

    Args:
        locale: Locale string (may be non-standard)

    Returns:
        Normalized locale string

    Examples:
        'zh_hans' → 'zh-Hans'
        'en_us' → 'en-US'
        'ja' → 'ja-JP'
    """
    # Convert underscores to hyphens
    locale = locale.replace('_', '-')

    # Handle common cases
    if locale.lower() in ['zh-hans', 'zh_hans']:
        return 'zh-Hans'
    elif locale.lower() in ['zh-hant', 'zh_hant', 'zh-tw', 'zh_tw']:
        return 'zh-Hant'
    elif locale.lower() == 'en':
        return 'en-US'
    elif locale.lower() == 'ja':
        return 'ja-JP'

    # For locales like 'en-us', capitalize country code
    parts = locale.split('-')
    if len(parts) == 2:
        return f"{parts[0].lower()}-{parts[1].upper()}"

    return locale


def locale_to_lang_group(locale: str) -> str:
    """
    Map locale to language group.

    Args:
        locale: Locale string (e.g., 'zh-Hans', 'en-US')

    Returns:
        Language group key (e.g., 'zh', 'en')

    Note:
        This is LINGUISTIC grouping only, not semantic similarity.
        zh-Hans and zh-Hant → 'zh' (same language family)
    """
    # Normalize first
    locale = normalize_locale(locale)

    # Direct lookup
    if locale in LOCALE_TO_LANG_GROUP:
        return LOCALE_TO_LANG_GROUP[locale]

    # Fallback: extract base language (before hyphen)
    base = locale.split('-')[0].lower()
    return base


def classify_item_language(
    title: str,
    description: Optional[str],
    region_default_locale: str
) -> Tuple[str, str, str]:
    """
    Classify item language into (base_lang, locale, lang_group).

    This is the main entry point for language classification during ingestion.

    Args:
        title: Item title (required)
        description: Item description (optional, improves accuracy)
        region_default_locale: Default locale from the region (e.g., 'ja-JP' for Japan region)

    Returns:
        Tuple of (base_lang, locale, lang_group)
        - base_lang: ISO 639-1 code ('en', 'zh', 'ja')
        - locale: BCP47 tag ('en-US', 'zh-Hans', 'ja-JP')
        - lang_group: Grouping key for feeds ('en', 'zh', 'ja')

    Example:
        >>> classify_item_language(
        ...     title="今日のトレンド",
        ...     description="日本で人気のニュース",
        ...     region_default_locale="ja-JP"
        ... )
        ('ja', 'ja-JP', 'ja')

        >>> classify_item_language(
        ...     title="Breaking News",
        ...     description="Latest updates from USA",
        ...     region_default_locale="en-US"
        ... )
        ('en', 'en-US', 'en')

    Strategy:
        1. Detect base language from text (title + description)
        2. If detected base_lang matches region, use region's full locale
        3. Otherwise, map detected base_lang to default locale
        4. Compute lang_group from locale
    """
    # Combine title and description for better detection accuracy
    combined_text = title
    if description:
        combined_text = f"{title} {description}"

    # Detect base language
    # Use region's base language as fallback (e.g., 'ja' from 'ja-JP')
    region_base_lang = region_default_locale.split('-')[0].lower()
    detected_base_lang = detect_language(combined_text, fallback=region_base_lang)

    # Determine locale
    # If detected language matches region, use region's full locale
    # Otherwise, use default locale for detected language
    if detected_base_lang == region_base_lang:
        locale = normalize_locale(region_default_locale)
    else:
        locale = BASE_LANG_TO_DEFAULT_LOCALE.get(
            detected_base_lang,
            f"{detected_base_lang}-{detected_base_lang.upper()}"  # Fallback: en → en-EN
        )

    # Compute language group
    lang_group = locale_to_lang_group(locale)

    logger.debug(
        f"Classified: base_lang={detected_base_lang}, "
        f"locale={locale}, lang_group={lang_group} | "
        f"title: {title[:50]}..."
    )

    return detected_base_lang, locale, lang_group


def is_same_language_family(locale1: str, locale2: str) -> bool:
    """
    Check if two locales belong to the same language family.

    Args:
        locale1: First locale
        locale2: Second locale

    Returns:
        True if both belong to same language family

    Example:
        >>> is_same_language_family('zh-Hans', 'zh-Hant')
        True
        >>> is_same_language_family('en-US', 'en-GB')
        True
        >>> is_same_language_family('en-US', 'ja-JP')
        False
    """
    return locale_to_lang_group(locale1) == locale_to_lang_group(locale2)
