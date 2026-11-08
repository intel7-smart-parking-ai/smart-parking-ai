"""Verify selected places against catalog evidence; never select or correct a lot."""
from dataclasses import dataclass
import re
from .place_candidates import place_candidates


@dataclass(frozen=True)
class PlaceEvidence:
    source: str
    phrase: str


def _normalize(text):
    return re.sub(r'\s+', '', text).casefold()


def _match(text, phrases):
    normalized = _normalize(text)
    for phrase in phrases:
        candidate = _normalize(phrase)
        if not candidate:
            continue
        # Single-character spoken lot labels must not match inside ordinary words.
        if len(candidate) == 1:
            if re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.IGNORECASE):
                return phrase
        elif candidate in normalized:
            return phrase
    return None


def place_evidence(catalog, lot_id, text, context):
    """Return verifiable evidence for this selection, or None for clarification.

    Exact catalog phrases tolerate whitespace/case only. There is no edit-distance,
    typo correction, or model-supplied assertion accepted as proof.
    """
    if lot_id not in place_candidates(text, catalog, context):
        return None
    selected = None
    named = {}
    for row in catalog['items']:
        if row['lot_id'] == lot_id:
            selected = row
        profile = row['language_profile']
        phrase = _match(text, [row['name'], *profile.get('name_variants', [])])
        if phrase is not None:
            named[row['lot_id']] = phrase
    if selected is None:
        return None
    if lot_id in named:
        return PlaceEvidence('catalog_name', named[lot_id])
    profile = selected['language_profile']
    semantic_phrase = _match(text, [p for key in ('types', 'related_places', 'purposes', 'semantic_examples')
                                  for p in profile.get(key, [])])
    if semantic_phrase is not None and context.get('lot_id') in (None, lot_id):
        return PlaceEvidence('catalog_related', semantic_phrase)
    if context.get('lot_id') == lot_id:
        reference = _match(text, ['거기', '그곳', '그 주차장', '해당 주차장', '이 주차장'])
        if reference is not None:
            return PlaceEvidence('confirmed_context', reference)
    # Shared candidate validation also admits registered purpose inflections and
    # explicit lot IDs. Do not apply a second, incompatible phrase grammar.
    return PlaceEvidence('catalog_related', text)
