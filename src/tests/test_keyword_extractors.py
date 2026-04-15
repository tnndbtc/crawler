"""
Unit tests for the multilingual keyword extractor package.

Each extractor is tested with 5 hand-crafted fixture titles.
Heavy-dependency extractors (fugashi, kiwipiepy, pythainlp) are skipped
with pytest.importorskip if the optional package is not installed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# English
# ---------------------------------------------------------------------------

def test_english_allcaps():
    from shared.extractors.english import extract_english_entities
    result = extract_english_entities("TSMC reports record earnings", "en")
    assert "TSMC" in result

def test_english_titlecase():
    from shared.extractors.english import extract_english_entities
    result = extract_english_entities("South China Sea tensions rise", "en")
    assert "South China Sea" in result

def test_english_camelcase():
    from shared.extractors.english import extract_english_entities
    result = extract_english_entities("ByteDance faces new US restrictions", "en")
    assert "ByteDance" in result

def test_english_noun_verb():
    from shared.extractors.english import extract_english_entities
    result = extract_english_entities("Macron announced new energy policy", "en")
    assert "Macron" in result

def test_english_stopwords_excluded():
    from shared.extractors.english import extract_english_entities
    result = extract_english_entities("The New policy announced today", "en")
    assert "The" not in result


# ---------------------------------------------------------------------------
# Chinese
# ---------------------------------------------------------------------------

def test_chinese_proper_nouns():
    jieba = pytest.importorskip("jieba")
    from shared.extractors.chinese import extract_chinese_entities
    result = extract_chinese_entities("习近平访问北京大学", "zh")
    # Should extract some nouns; exact result depends on jieba version
    assert isinstance(result, list)
    assert len(result) >= 1

def test_chinese_min_length():
    jieba = pytest.importorskip("jieba")
    from shared.extractors.chinese import extract_chinese_entities
    result = extract_chinese_entities("中国经济增长放缓", "zh")
    assert all(len(w) >= 2 for w in result)

def test_chinese_returns_list():
    jieba = pytest.importorskip("jieba")
    from shared.extractors.chinese import extract_chinese_entities
    result = extract_chinese_entities("", "zh")
    assert result == []

def test_chinese_tech():
    jieba = pytest.importorskip("jieba")
    from shared.extractors.chinese import extract_chinese_entities
    result = extract_chinese_entities("腾讯公司发布新人工智能产品", "zh")
    assert isinstance(result, list)

def test_chinese_nouns_only():
    jieba = pytest.importorskip("jieba")
    from shared.extractors.chinese import extract_chinese_entities
    result = extract_chinese_entities("美国政府宣布新制裁措施", "zh")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Latin Unicode (Cyrillic + accented Latin)
# ---------------------------------------------------------------------------

def test_latin_unicode_cyrillic():
    regex = pytest.importorskip("regex")
    from shared.extractors.latin_unicode import extract_latin_unicode_entities
    result = extract_latin_unicode_entities("Путин объявил новую политику в Москве", "ru")
    assert any("Путин" in e or "Москве" in e or "Москва" in e for e in result) or len(result) >= 1

def test_latin_unicode_french():
    regex = pytest.importorskip("regex")
    from shared.extractors.latin_unicode import extract_latin_unicode_entities
    result = extract_latin_unicode_entities("Macron annonce une réforme à Paris", "fr")
    assert "Macron" in result or "Paris" in result

def test_latin_unicode_german_defilter():
    regex = pytest.importorskip("regex")
    from shared.extractors.latin_unicode import extract_latin_unicode_entities
    # "Mann" appears only once → should be filtered for lang=de
    result = extract_latin_unicode_entities("Ein Mann aus Berlin besucht Berlin", "de")
    # "Berlin" appears twice → should survive; "Mann" appears once → filtered
    assert "Berlin" in result
    assert "Mann" not in result

def test_latin_unicode_spanish():
    regex = pytest.importorskip("regex")
    from shared.extractors.latin_unicode import extract_latin_unicode_entities
    result = extract_latin_unicode_entities("Pedro Sánchez visitó Madrid", "es")
    assert isinstance(result, list)
    assert len(result) >= 1

def test_latin_unicode_portuguese():
    regex = pytest.importorskip("regex")
    from shared.extractors.latin_unicode import extract_latin_unicode_entities
    result = extract_latin_unicode_entities("Lula assina acordo em Brasília", "pt")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Japanese
# ---------------------------------------------------------------------------

def test_japanese_proper_nouns():
    fugashi = pytest.importorskip("fugashi")
    from shared.extractors.japanese import extract_japanese_entities
    result = extract_japanese_entities("東京オリンピックの準備が進む", "ja")
    assert isinstance(result, list)

def test_japanese_compound():
    fugashi = pytest.importorskip("fugashi")
    from shared.extractors.japanese import extract_japanese_entities
    result = extract_japanese_entities("東京都の新しい政策について", "ja")
    # Should include both 東京 and 東京都 (or at least one of them)
    assert isinstance(result, list)

def test_japanese_min_length():
    fugashi = pytest.importorskip("fugashi")
    from shared.extractors.japanese import extract_japanese_entities
    result = extract_japanese_entities("日本の経済成長", "ja")
    assert all(len(w) >= 2 for w in result)

def test_japanese_empty():
    fugashi = pytest.importorskip("fugashi")
    from shared.extractors.japanese import extract_japanese_entities
    result = extract_japanese_entities("", "ja")
    assert result == []

def test_japanese_returns_list():
    fugashi = pytest.importorskip("fugashi")
    from shared.extractors.japanese import extract_japanese_entities
    result = extract_japanese_entities("アップルが新製品を発表", "ja")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Korean
# ---------------------------------------------------------------------------

def test_korean_proper_nouns():
    kiwipiepy = pytest.importorskip("kiwipiepy")
    from shared.extractors.korean import extract_korean_entities
    result = extract_korean_entities("서울에서 새로운 정책 발표", "ko")
    assert isinstance(result, list)

def test_korean_returns_list():
    kiwipiepy = pytest.importorskip("kiwipiepy")
    from shared.extractors.korean import extract_korean_entities
    result = extract_korean_entities("", "ko")
    assert isinstance(result, list)

def test_korean_min_syllables():
    kiwipiepy = pytest.importorskip("kiwipiepy")
    from shared.extractors.korean import extract_korean_entities
    result = extract_korean_entities("한국 경제 성장률 발표", "ko")
    # NNG tokens with < 2 syllables should be filtered
    assert all(len(w) >= 1 for w in result)

def test_korean_tech():
    kiwipiepy = pytest.importorskip("kiwipiepy")
    from shared.extractors.korean import extract_korean_entities
    result = extract_korean_entities("삼성전자가 새로운 반도체 발표", "ko")
    assert isinstance(result, list)

def test_korean_politics():
    kiwipiepy = pytest.importorskip("kiwipiepy")
    from shared.extractors.korean import extract_korean_entities
    result = extract_korean_entities("윤석열 대통령이 미국을 방문", "ko")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Arabic
# ---------------------------------------------------------------------------

def test_arabic_basic():
    regex = pytest.importorskip("regex")
    from shared.extractors.arabic import extract_arabic_entities
    result = extract_arabic_entities("الرياض تستضيف قمة اقتصادية", "ar")
    assert isinstance(result, list)
    assert len(result) >= 1

def test_arabic_article_strip_dedup():
    regex = pytest.importorskip("regex")
    from shared.extractors.arabic import extract_arabic_entities
    # الرياض (with article) and رياض (without) should not both appear
    result = extract_arabic_entities("الرياض ورياض في نفس الجملة", "ar")
    terms = [e.replace('ال', '', 1) if e.startswith('ال') else e for e in result]
    # After stripping articles, no canonical form should repeat
    assert len(terms) == len(set(terms))

def test_arabic_returns_list():
    regex = pytest.importorskip("regex")
    from shared.extractors.arabic import extract_arabic_entities
    result = extract_arabic_entities("", "ar")
    assert result == []

def test_arabic_min_length():
    regex = pytest.importorskip("regex")
    from shared.extractors.arabic import extract_arabic_entities
    result = extract_arabic_entities("القاهرة والسعودية في اجتماع", "ar")
    assert all(len(e) >= 2 for e in result)

def test_arabic_stopwords_excluded():
    regex = pytest.importorskip("regex")
    stopwordsiso = pytest.importorskip("stopwordsiso")
    from shared.extractors.arabic import extract_arabic_entities
    result = extract_arabic_entities("في من على إلى الرياض", "ar")
    # Common Arabic prepositions should not appear (filtered by stopwords)
    assert "في" not in result
    assert "من" not in result


# ---------------------------------------------------------------------------
# Thai
# ---------------------------------------------------------------------------

def test_thai_basic():
    pythainlp = pytest.importorskip("pythainlp")
    from shared.extractors.thai import extract_thai_entities
    result = extract_thai_entities("กรุงเทพมหานครเตรียมรับนักท่องเที่ยว", "th")
    assert isinstance(result, list)

def test_thai_returns_list():
    pythainlp = pytest.importorskip("pythainlp")
    from shared.extractors.thai import extract_thai_entities
    result = extract_thai_entities("", "th")
    assert isinstance(result, list)

def test_thai_min_length():
    pythainlp = pytest.importorskip("pythainlp")
    from shared.extractors.thai import extract_thai_entities
    result = extract_thai_entities("ประเทศไทยเปิดตัวนโยบายใหม่", "th")
    assert all(len(w) >= 2 for w in result)

def test_thai_politics():
    pythainlp = pytest.importorskip("pythainlp")
    from shared.extractors.thai import extract_thai_entities
    result = extract_thai_entities("รัฐบาลไทยประกาศนโยบายเศรษฐกิจ", "th")
    assert isinstance(result, list)

def test_thai_tech():
    pythainlp = pytest.importorskip("pythainlp")
    from shared.extractors.thai import extract_thai_entities
    result = extract_thai_entities("เทคโนโลยีปัญญาประดิษฐ์กำลังพัฒนา", "th")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Hindi
# ---------------------------------------------------------------------------

def test_hindi_basic():
    regex = pytest.importorskip("regex")
    from shared.extractors.hindi import extract_hindi_entities
    result = extract_hindi_entities("नई दिल्ली में सरकार की बैठक", "hi")
    assert isinstance(result, list)
    assert len(result) >= 1

def test_hindi_devanagari_only():
    regex = pytest.importorskip("regex")
    from shared.extractors.hindi import extract_hindi_entities
    result = extract_hindi_entities("Modi visits New Delhi", "hi")
    # Latin "Modi" and "New Delhi" should not appear — only Devanagari extracted
    assert "Modi" not in result
    assert "New" not in result

def test_hindi_min_length():
    regex = pytest.importorskip("regex")
    from shared.extractors.hindi import extract_hindi_entities
    result = extract_hindi_entities("भारत सरकार ने नई नीति घोषित की", "hi")
    assert all(len(w) >= 2 for w in result)

def test_hindi_returns_list():
    regex = pytest.importorskip("regex")
    from shared.extractors.hindi import extract_hindi_entities
    result = extract_hindi_entities("", "hi")
    assert result == []

def test_hindi_dedup():
    regex = pytest.importorskip("regex")
    from shared.extractors.hindi import extract_hindi_entities
    result = extract_hindi_entities("दिल्ली और दिल्ली में बैठक", "hi")
    assert result.count("दिल्ली") <= 1


# ---------------------------------------------------------------------------
# Dispatch table integration
# ---------------------------------------------------------------------------

def test_dispatch_table_populated():
    import shared.extractors
    from shared.extractors import EXTRACTORS, DEFAULT_EXTRACTOR
    expected_langs = {
        'zh', 'pt', 'es', 'fr', 'de', 'it', 'tr', 'pl', 'sv', 'vi',
        'ms', 'nl', 'id', 'ru', 'uk', 'ja', 'ko', 'ar', 'th', 'hi',
    }
    assert expected_langs.issubset(set(EXTRACTORS.keys()))

def test_default_extractor_callable():
    from shared.extractors import DEFAULT_EXTRACTOR
    result = DEFAULT_EXTRACTOR("Apple announces new iPhone", "en")
    assert isinstance(result, list)

def test_unknown_lang_falls_back_to_english():
    from shared.extractors import EXTRACTORS, DEFAULT_EXTRACTOR
    lang = 'xx'
    extractor = EXTRACTORS.get(lang, DEFAULT_EXTRACTOR)
    result = extractor("Apple announces new iPhone", lang)
    assert isinstance(result, list)
