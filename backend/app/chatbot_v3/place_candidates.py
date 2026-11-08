"""Bounded catalog evidence, not fuzzy correction or a general Korean parser.

Candidates grant permission to select a place; they are not model predictions.
Only confirmed context is consulted, never generated dialogue history.
"""
import re


_PARTICLES = r"(?:은|는|이|가|을|를|에|에서|으로|로|의|도|만|랑|과|와)?"
_END = rf"(?={_PARTICLES}(?:\s|[?!.,]|$)|주차장)"
_NEGATIVE = re.compile(r"^\s*(?:주차장)?\s*" + _PARTICLES + r"\s*(?:말고|아니라|아닌|아니고|제외|빼고|싫고|안\s*가고)")
_REFERENCE = re.compile(
    r"^(?:거기|그곳|그\s*주차장|해당\s*주차장|이\s*주차장)" + _PARTICLES
    + r"\s*(?:(?:빈자리|자리|시설|주차장|안내|위치|전기차|일반|장애인)\s*)*"
    + r"(?:좀\s*)?(?:(?:있어|있나요|알려줘|어디|어때|보여줘|확인해줘)\s*)?[?!.\s]*$"
)


def _matches(text, phrase):
    # Optional inter-word spacing supports catalog variants without substring
    # matching '공항' in '김포공항', '비' in '비용', or '몰' in '몰라'.
    pattern = r"(?<![가-힣A-Za-z0-9])" + r"\s*".join(re.escape(c) for c in phrase if not c.isspace()) + _END
    return list(re.finditer(pattern, text, re.IGNORECASE))


def allows_all_lots(text):
    """A null place may browse all lots only for an explicit global request."""
    return bool(re.fullmatch(
        r"(?:(?:전체|모든|각)\s*주차장|주차장\s*(?:전체|모두)|주차장별)(?:의|은|은요)?\s*(?:빈자리|자리|현황)(?:\s*현황)?\s*"
        r"(?:알려줘|보여줘|있어|있나요)?[?!.\s]*", text.strip()))


def place_candidates(text, catalog, context):
    """Return stable IDs supported by positive registered mentions or a reference.

    Ambiguous catalog examples and unknown place compounds force clarification.
    Multiple positive places remain candidates: choosing an intent is the model's
    job, and compound-request handling remains a separately evaluated contract.
    """
    mentions = []
    named_lots = set()
    ambiguous = []
    for row in catalog['items']:
        profile = row['language_profile']
        phrases = [row['name'], row['lot_id'], row['lot_id'] + ' 주차장']
        for phrase in phrases + profile.get('name_variants', []):
            if any(not _NEGATIVE.match(text[m.end():]) for m in _matches(text, phrase)):
                named_lots.add(row['lot_id'])
        for key in ('name_variants', 'types', 'related_places', 'purposes', 'semantic_examples'):
            phrases.extend(profile.get(key, []))
        # Productive purpose ending, applied only to registered -기 expressions:
        # 영화 보기 -> 영화 보러, 비행기 타기 -> 비행기 타러.
        phrases.extend(p[:-1] + '러' for p in profile.get('purposes', []) if p.endswith('기'))
        for phrase in set(phrases):
            for match in _matches(text, phrase):
                mentions.append((match.start(), match.end(), row['lot_id']))
        for phrase in profile.get('ambiguous_examples', []):
            ambiguous.extend(_matches(text, phrase))

    # Longer phrases own their span, so a negated full phrase cannot leave a
    # shorter nested alias (e.g. '기차' in '기차 주차장 말고') as positive evidence.
    mentions = [m for m in mentions if not any(
        other[0] <= m[0] and m[1] < other[1] for other in mentions)]
    positive = [m for m in mentions if not _NEGATIVE.match(text[m[1]:])]
    if any(not _NEGATIVE.match(text[m.end():]) for m in ambiguous):
        return []
    if not mentions and _REFERENCE.fullmatch(text.strip()) and context.get('lot_id') in {'A', 'B', 'C', 'D'}:
        return [context['lot_id']]

    # Detect unregistered compounds such as 서울역/김포공항/롯데백화점 and
    # a modifier before a generic place ('신새계 백화점'). Do not map either
    # to the generic catalog alias. This is intentionally conservative.
    for match in re.finditer(r"(?<![가-힣A-Za-z0-9])(?!(?:말고|아니라|아니고|빼고|제외)\b)[가-힣A-Za-z0-9]+(?:\s+(?:공항|백화점|주차장))?", text):
        word = match.group()
        if ' ' in word and re.search(r'(?:는데|싫고|가고)$', word.split()[0]):
            continue
        if not re.search(r"(?:역|공항|백화점|주차장)(?:은|는|에|에서|이|을|도)?$", word):
            continue
        if _NEGATIVE.match(text[match.end():]):
            continue
        if not any(start <= match.start() < end for start, end, _ in mentions):
            return []

    if positive:
        # A related purpose conflicting with a confirmed place is ambiguous;
        # an explicit new name may replace the previous place.
        return sorted({lot for _, _, lot in positive
                       if lot in named_lots or context.get('lot_id') in (None, lot)})
    return []
