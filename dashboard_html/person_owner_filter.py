"""Person-name detection for the Properties owner-presence filter.

The model runs locally; owner names are never sent to a third-party service.
Role evidence belongs to a source, not to a name: a registered agent can also
appear independently as an owner on a deed or tax record.
"""

from functools import lru_cache
import re

import probablepeople

from enrichment_service import (
    entity_match_quality, is_business_entity, is_sos_agent_title,
    split_candidate_names,
)

OWNER_FIELDS = (
    'sale_buyer_primary', 'current_owner_name', 'owner_name_hpd', 'owner_name_rpad',
)
PERSON_OWNER_FIELDS = OWNER_FIELDS + (
    'sos_principal_name', 'sos_principal_title', 'sos_entity_name',
)


@lru_cache(maxsize=65536)
def _is_person_name(name):
    # Retain the project's NYC-specific safeguards for banks, legal entities,
    # placeholders and care-of mailing instructions. The generic model is
    # then responsible for distinguishing names from other ordinary words.
    if is_business_entity(name.replace('&', ' AND ')):
        return False
    if re.search(r'\b(?:UNKNOWN|UNAVAILABLE|CURRENT OWNER|OWNER OF RECORD|NOT PROVIDED|ET AL|ET UX|ET VIR)\b', name):
        return False
    try:
        parts, kind = probablepeople.tag(name)
    except probablepeople.RepeatedLabelError:
        return False  # ambiguous records do not establish a person
    # A household establishes that people are named, even though it cannot
    # safely be sent to paid enrichment as one individual identity.
    return (kind in ('Person', 'Household') and bool(parts.get('Surname'))
            and bool(parts.get('GivenName') or parts.get('FirstInitial')))


def contains_person_name(value):
    """Check every separately recorded party, preserving LAST, FIRST names."""
    return any(_is_person_name(name.upper())
               for name in split_candidate_names(value))


def has_person_owner(row):
    """At least one person in an owner source or a matching SOS principal.

HPD managing agents/site managers, deed sellers, and SOS registered/service-
of-process agents do not establish ownership. An agent elsewhere on the row
does not disqualify a person independently recorded in an owner source.
"""
    if any(contains_person_name(row.get(field)) for field in OWNER_FIELDS):
        return True
    title = row.get('sos_principal_title')
    if is_sos_agent_title(title):
        return False
    match, _ = entity_match_quality(
        row.get('sos_entity_name'), [row.get(field) for field in OWNER_FIELDS],
    )
    return match != 'mismatch' and contains_person_name(row.get('sos_principal_name'))
