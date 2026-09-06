"""The anchor vocabulary: what an investigator can supply, and what it fires.

Lives in the correlation layer rather than in a collector because both sides
need it — the collectors that *detect* an anchor match and the extraction that
*records* one. Keeping a second copy on the extraction side is exactly how a new
anchor comes to score correctly on a finding and then vanish from the entity.
"""

from __future__ import annotations

#: Anchor kind -> confidence rule. An anchor with no rule here cannot influence
#: a score at all, which keeps the scoring surface closed and reviewable.
ANCHOR_RULES: dict[str, str] = {
    "profile_url": "context_profile_url_match",
    "orcid": "anchor_orcid_match",
    "github_username": "anchor_github_match",
    "username": "context_username_match",
    "website": "anchor_website_match",
    "affiliation": "context_affiliation_match",
    "occupation": "anchor_occupation_match",
    "location": "context_location_match",
}

#: One sentence per anchor kind, written for the investigator who supplied it.
ANCHOR_REASONS: dict[str, str] = {
    "profile_url": "This is a profile URL you supplied for the subject ({detail})",
    "orcid": "The record carries ORCID {detail}, exactly the one you supplied",
    "github_username": "The GitHub account is {detail}, the username you supplied",
    "username": "The account handle {detail} is one you supplied as known",
    "website": "The record links to {detail}, a website you supplied",
    "affiliation": "The source lists {detail}, matching an affiliation you supplied",
    "occupation": "The source describes this record as {detail}, the occupation you supplied",
    "location": "The source places this record in {detail}, as you supplied",
}
