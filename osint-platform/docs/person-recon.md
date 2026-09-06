# PERSON reconnaissance

How this platform investigates a named person, what it deliberately will not do,
and how to work a case at zero cost.

## The shape of the problem

A name is not an identifier. Searching one returns pages about everyone who
shares it, and the investigator's real job is to *rule candidates out*. Every
design decision below follows from that: the platform's role is to gather public
records, keep them apart, and explain what each one does and does not show. It
never decides who someone is.

## Architecture

```
PERSON target (name + optional anchors)
        │
        ├── free structured collectors ──► candidates (API-fetched)
        │     orcid, openalex, crossref, wikidata,
        │     github_people, reddit, person_usernames
        │
        ├── recon query generator ──► queries you run in your own browser
        │                                    │
        │                            manual import ──► candidates
        │                                              (investigator-imported)
        │
        └── anchor correlation + cross-source corroboration
                        │
                        └──► candidates, each with reasons for and against
```

## Public vs manual-search evidence

Three evidence classes, and reports keep them apart because they are not equally
strong and they fail in different ways:

| Class | Meaning | Example |
|---|---|---|
| `api_fetched` | A public API returned it to the platform | An ORCID registry record |
| `page_fetched` | The platform fetched a public page directly | A profile page for a handle you supplied |
| `investigator_imported` | **You** selected it from a search you ran | A LinkedIn URL you pasted in |

An imported result is never presented as something the platform fetched. It is
filed under its own collector, `manual_search_recon`, with the query and engine
that produced it.

## Why Google HTML is not scraped

Scraping a search engine's result pages violates its terms, breaks whenever the
markup changes, and drags a platform into CAPTCHA-solving and proxy rotation —
which is where lawful research stops and evasion starts. So the platform does
not do it, and there is no code path that could.

Paid search APIs exist and are supported (`SEARCH_PROVIDER`), but nothing depends
on them.

### How manual Google recon works instead

1. The platform generates the queries an investigator would type, each with a
   rationale, a family, and the anchors it used.
2. You click **Open in Google** (or Bing, DuckDuckGo, Startpage). The link opens
   in *your* browser under *your* session. The platform submits nothing.
3. You read the results and decide which are worth keeping.
4. You paste the relevant ones back in. Each import is hashed into the evidence
   store with its query, engine and timestamp.

Nothing is automated across that boundary, and that is the point: a human
judged relevance, and the record says so.

## Supported social URL types

Classified by URL *shape* only — never by fetching a private page:

`linkedin.com` · `instagram.com` · `facebook.com` · `youtube.com` ·
`snapchat.com` · `github.com` · `gitlab.com` · `orcid.org` · `pypi.org` ·
`mastodon.social` · `reddit.com`

Publication hosts (`doi.org`, `arxiv.org`, `semanticscholar.org`, …) are
recognised as publications, not profiles. A platform page that is not
profile-shaped — a YouTube video, a LinkedIn job listing — is recorded as a web
page, not as a person: treating a video page as a candidate would invent an
identity out of a URL.

LinkedIn, Instagram, Facebook and Snapchat refuse anonymous server-side
requests. The platform records that and moves on. It does not rotate proxies,
spoof browsers or answer CAPTCHAs to get around it.

## Anchors

Optional context you supply about the subject:

known usernames · public profile URLs · public websites · organisation ·
school/university · occupation · city · country · ORCID · GitHub username

Anchors narrow the *judgement*, never the search. They are never sent to a
source as extra search terms about a private person, and they never become
findings — you already knew them.

**Deliberately not accepted:** home address, coordinates, phone number, date of
birth, family members. There is nowhere in the schema to put them. A street
address is not corroboration; it is tracking.

## Confidence and correlation

Every candidate starts from "the name matched", which is capped at **0.30** —
below the POSSIBLE_MATCH band. It rises only when an anchor independently
corroborates it:

| Anchor | Rule | Ceiling |
|---|---|---|
| ORCID exact match | `anchor_orcid_match` | 0.92 |
| Supplied profile URL | `context_profile_url_match` | 0.90 |
| GitHub username exact | `anchor_github_match` | 0.88 |
| Independent corroboration | `independent_corroboration` | 0.80 |
| Supplied username | `context_username_match` | 0.80 |
| Supplied website | `anchor_website_match` | 0.75 |
| Affiliation | `context_affiliation_match` | 0.70 |
| City / country | `context_location_match` | 0.45 |
| Occupation | `anchor_occupation_match` | 0.35 |
| Name only | `same_person_name` | 0.30 |

The auto-merge threshold is **0.95**, above every ceiling here. That is
structural, not a policy note: no combination of anchors can reach it, so the
platform cannot automatically merge two candidates. A human decides.

Each anchor kind fires **once**, however many values agree — five matching
affiliations are one fact observed once, not five.

### Cross-source corroboration

Two independently operated sources publishing the same identifier is real
evidence. Two views of the same upstream data are not.

- ORCID **and** OpenAlex publishing the same ORCID iD → corroboration.
- OpenAlex **and** Crossref agreeing on a DOI → **not** corroboration. OpenAlex
  ingests Crossref, so that is one deposit seen twice.

Provenance families are declared in `app/correlation/corroboration.py`, and
members of a family never corroborate each other.

## Image evidence and its limits

Images are stored as **page context**, with the source page, caption, discovery
query and import timestamp.

What image evidence can support:

- "This image appears on a page that explicitly names the subject."
- "The image result came from a page whose title matches the candidate."

What it can never support, because the platform does not do it:

- **No facial recognition.** No biometric embeddings, no facial similarity
  matching, no comparison of any two images.
- No claim that two photos depict the same person.
- No identification of an unknown person from a picture.

Every image record carries `biometric_matching: false` and `analysis: "none"`,
restated on the entity so the limit travels with the data.

## What "candidate" and "corroborated" mean

**Candidate** — a public record that carries the searched name. A *question*
("is this them?"), never an answer. Candidates are keyed on the page they came
from, so ten pages about ten different people who share a name stay ten
candidates.

**Corroborated** — at least one anchor you supplied, or an identifier two
independent sources agree on, matched this candidate. It raises confidence and
it is always attributed. It is **not** confirmation: a corroborated candidate is
still a candidate, still below the merge threshold, still marked
`identity_established: false`.

## What the system cannot establish

- That a candidate **is** the subject. Only a human reviewer concludes that.
- Who appears in a photograph.
- Anything about a private account, a login-gated page, or non-public content.
- A person's address, location, phone number, or family.
- That two accounts belong to one person because the handles match.

## Failure and coverage

A source that returns nothing is a **gap in coverage**, not a negative result,
and the UI lists it as such. Statuses mean:

| Status | Meaning |
|---|---|
| `SUCCESS` | The public endpoint answered |
| `SKIPPED` | Access blocked, rate-limited, or a credential is not configured — with the reason |
| `FAILED` | A genuine parsing, code or network fault worth investigating |

Reddit commonly refuses anonymous API access from server networks, and a
connection refused before any request is answered is recorded as `SKIPPED` with
the actual error, not as a failure and not as "no results".

## User workflow

1. **Create a PERSON target.** A bare name is ambiguous, so choose PERSON
   explicitly — the platform will not guess between a person and an organisation.
2. **Add any known public anchors.** Optional, but an ORCID or a GitHub username
   is worth more than everything else combined.
3. **Run the free collectors.** No API key needed for any of them.
4. **Review the generated recon queries** on the Recon tab. Anchored queries are
   listed first because they return the fewest strangers.
5. **Open selected queries** in your own browser.
6. **Import the relevant public results**, including image results.
7. **Review Social profiles, Images and Candidates.**
8. **Inspect the reasons and provenance** on each candidate — why it may be them,
   why it may not, which anchors matched, which conflicted, and which sources
   corroborate each claim.
9. **Confirm or reject each candidate yourself.** The platform will not do it
   for you.

## Zero-cost guarantee

With `SEARCH_PROVIDER=none` and no credential configured anywhere, a PERSON
investigation still runs seven collectors, generates the full recon query set,
and accepts manual imports. The paid search collector is recorded `SKIPPED` with
its reason and nothing else changes. `tests/integration/test_person_zero_cost.py`
and `test_person_recon_acceptance.py` assert this end to end.
