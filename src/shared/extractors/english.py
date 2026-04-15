import re

_RE_ALLCAPS = re.compile(r'\b[A-Z]{2,6}\b')
_RE_TITLECASE = re.compile(r'\b(?:[A-Z][a-z]+\s){1,3}[A-Z][a-z]+\b')
_RE_CAMELCASE = re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b')
_RE_NOUN_VERB = re.compile(
    r'\b([A-Z][a-z]*(?:[A-Z][a-z]*)*[a-z]+)\s+'
    r'(?:said|announced|reported|reports|unveiled|banned|launched|warned|'
    r'pledged|signed|approved|filed|sued|wins|loses|beats|misses|cuts|raises|faces)\b'
)

_STOPWORDS_EN = {
    'The', 'This', 'That', 'New', 'Top', 'Big', 'US', 'UK', 'EU', 'UN',
    'A', 'An', 'In', 'On', 'At', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have',
    'Had', 'Be', 'By', 'Of', 'To', 'As', 'For', 'Or', 'But', 'And', 'Not',
    'Its', 'It', 'He', 'She', 'They', 'We', 'You', 'How', 'Why', 'What',
    'When', 'Where', 'Who', 'Which', 'After', 'Before', 'Over', 'Under',
    'More', 'Most', 'Some', 'All', 'One', 'Two', 'First', 'Last', 'Next',
}

def extract_english_entities(title: str, lang: str = '') -> list[str]:
    """Extract named entities from an English title using regex patterns."""
    entities = set()
    for m in _RE_ALLCAPS.finditer(title):
        entities.add(m.group())
    for m in _RE_TITLECASE.finditer(title):
        entities.add(m.group().strip())
    for m in _RE_NOUN_VERB.finditer(title):
        entities.add(m.group(1))
    for m in _RE_CAMELCASE.finditer(title):
        entities.add(m.group())
    return [e for e in entities if e not in _STOPWORDS_EN and len(e) >= 2]
