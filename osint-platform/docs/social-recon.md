# Social & visual recon

How the platform records public profiles and public images, what it refuses to
do with them, and how an analyst's judgement is kept separate from the
platform's.

## The two claims

Every profile and every image carries two things that must never be blended:

| | What it is | Who made it |
|---|---|---|
| **Automated confidence** | What the anchors support | The platform |
| **Analyst decision** | What a human concluded | You |

They live in different tables. Recording a decision writes only to
`analyst_decisions` and has no write path to a confidence value — so "an analyst
decision never overwrites automated confidence" is structural here, not a
convention someone has to remember. Both appear in the UI and in the report.

Decisions: `CONFIRMED` · `REJECTED` · `UNRESOLVED` · `NEEDS_REVIEW`.
Withdrawing one restores nothing, because nothing was overwritten.

## Platforms

Classified by URL **shape** only — host and path. Classification says a URL *is*
a LinkedIn profile; it never says whose.

LinkedIn · Instagram · Facebook · YouTube · **X (Twitter)** · **TikTok** ·
Snapchat · Reddit · GitHub · GitLab · ORCID · PyPI · Mastodon · generic sites

X carries both `x.com` and `twitter.com`: years of both are in circulation.

### Content pages are not profiles

A reserved path segment *anywhere* means the URL addresses content, not a
person:

| URL | Classified as |
|---|---|
| `tiktok.com/@name` | profile |
| `tiktok.com/@name/video/123` | web page |
| `reddit.com/u/name` | profile |
| `reddit.com/u/name/comments/abc` | web page |
| `youtube.com/watch?v=…` | web page |

Each of the right-hand cases names a person on the way to something else.
Recording one as a profile would invent an attribution out of a path prefix.

### Platforms that refuse anonymous access

LinkedIn, Instagram, Facebook, Snapchat, X and TikTok refuse anonymous
server-side requests. They are marked `server_fetchable = false` with the reason
recorded, `accessibility = RESTRICTED`, and **only the URL shape is kept**.

Asking the platform to fetch an image on one of these declines and says so. The
block is a fact to record, never something to route around.

## Correlation

The same function the collectors use — `anchor_matches` — against the same
anchors on the target. A parallel implementation for the manual path is exactly
how the two came to disagree once before.

**A matching handle is not ownership.** Handles are reused, sold and
coincidental, and a profile whose only connection is a lookalike handle says so
in its own mismatch reasons. A name alone stays at the name-only ceiling of
**0.30**, far below anything that could merge identities.

## Public images

Recorded as **page context**: where a picture appears. Never as a claim about
who is in it.

| Field | Meaning |
|---|---|
| `fetch_state` | `FETCHED` · `REFERENCE_ONLY` · `BLOCKED` |
| `sha256` | Only for `FETCHED` — a hash of bytes this platform read |
| `redirects`, `final_url` | Where the bytes actually came from |
| `content_type`, `byte_length`, `width`, `height` | As observed |

`sha256` is null for anything not fetched. Claiming a hash for a URL nobody read
would be a fabricated integrity guarantee.

Fetching goes through `app.core.http`, which runs the SSRF guard on the URL and
on **every redirect hop**. There is no second HTTP path — a "just for images"
fetcher is how a hardened codebase grows an unguarded one. Dimensions are read
from file headers rather than by decoding the image, which avoids handing
attacker-controlled bytes to an imaging library.

Image bytes are not persisted. The hash plus a provenance descriptor in the
existing evidence store give the integrity guarantee; re-hosting someone's
photograph has no investigative payoff.

Thumbnails render **only** for images the platform fetched itself. Rendering an
unfetched URL would make the investigator's browser retrieve it from a page
nobody vetted, and would look like verification that never happened.

## What the platform will never do with an image

- No facial recognition, face embeddings or biometric matching
- No similarity comparison between any two images
- No identification of an unknown person from a photograph
- No reverse image search

`tests/unit/test_safety_boundaries.py` asserts each of these — no biometric
library is importable, the `image_evidence` table has no column that could hold
a facial descriptor, and `app.services.images` exposes no comparison function. A
documented limit that nothing checks is a limit that erodes.

## Grouping

By **candidate** and by **source/platform**. Never by appearance: the platform
performs no image comparison, so it has no basis for that grouping, and offering
it would imply an analysis that does not happen.

Evidence nobody has attributed yet appears under *Unattributed evidence* rather
than disappearing.

## Manual search recon

Unchanged in principle: the platform generates queries, you run them in your own
browser, and you import what is relevant. **Google HTML is never scraped**, and
there is no code path that could.

Query families now include **image** queries — `"A Name" photo`, `"A Name"
headshot` — which look for *pages that publish a photograph*. That is all image
evidence claims. Nothing searches by an image.

### The forbidden-term screen, and why it has two lists

`NEVER_SEARCHABLE` is refused everywhere, including inside an anchor: home
address, phone number, date of birth, credentials, family members, criminal
record. This is what stops the anchor fields being used to smuggle a query past
the screen.

`CONTEXTUAL_TERMS` — *medical, health, diagnosis, religion, family* — name a
sensitive category **and** appear in the names of real institutions. They are
refused when the platform composes a query on its own, and permitted inside a
value the investigator supplied.

The difference is not in the word, it is in who put it there. `"A Name" medical`
fishes for health data. *"Liaquat University of Medical & Health Sciences"* is an
employer, and refusing to search for a stated employer because its name contains
"medical" protects nobody — it makes the platform useless to anyone who works at
a hospital.

## Report

The canonical model carries `social_profiles`, `images` and `analyst_decisions`,
each with provenance, both judgements, and — on every image — `analysis: "none"`
and `biometric_matching: false`, so the limit travels with the data. V3.1's
professional renderer builds on this shape.

## Zero cost

Everything above runs with `SEARCH_PROVIDER=none` and no credential configured.
The paid search collector stays optional and is recorded `SKIPPED` with its
reason.
