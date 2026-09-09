# Discovery pipeline

How public data a collector retrieves becomes evidence an investigator can see,
correlate, review and report — instead of sitting in a JSON blob.

## The problem this solves

A real report showed a GitHub candidate with `profile_detail_fetched: true`, and
never mentioned the account's avatar or listed the profile as a profile. The
same URL pasted in by hand produced *more* than the URL discovered
automatically, because `record_profile` and `record_image` were only ever called
from the manual import path.

```
PERSON
  → public collectors            (what they already fetch)
  → promotion                    (this document)
  → social profiles · public contacts · image evidence
  → candidate correlation        (the same anchor engine, both paths)
  → analyst review
  → report / export
```

## What each collector now contributes

| Collector | Promoted |
|---|---|
| `github_people` | profile URL, login, display name, **avatar**, **published email**, company, location, blog, bio, repo count, **the special profile repository's README** |
| `orcid` | ORCID iD, institutions, **published email** where the researcher made one public |
| `openalex` | author record, institutions, works |
| `crossref` | authored works, DOIs |
| `wikidata` | item, occupations, **Wikipedia article as a public website** |
| `person_usernames` | profile URLs for handles you supplied |
| `reddit` | public account, where Reddit allows anonymous access |

`avatar_url` and `email` were being read from the GitHub API response and then
dropped on the floor. Every GitHub account has an avatar; it was the single
largest piece of public data the collector discarded.

## The special GitHub profile repository

GitHub gives every account a documented way to publish a personal page: a public
repository whose name equals the login, whose README is rendered on the profile.
It is the one place on GitHub where a person writes about themselves in prose,
and it was being ignored — which is how an account could be reported as a bare
login while its own profile stated the person's post, their employer and their
field.

Two documented API calls per login, and no more:

```
GET /repos/{login}/{login}          does it exist, is it public, what is its
                                    description / homepage / default branch
GET /repos/{login}/{login}/readme   the README, base64 in the JSON body
```

Never the rendered page at `github.com/<login>`. GitHub publishes this as data,
so parsing HTML would be scraping something already offered as an API — against
the rules, and worse evidence besides. A test asserts every fetch in
`github_profile.py` targets an `{api}/` endpoint.

**Bounded, not a crawler.** At most three accounts per run, the one you anchored
on first. No repository listing, no second page, and nothing found inside a
README is followed. A test asserts `/users/{login}/repos` is never called.

### What counts as "explicitly stated"

Two shapes, both closed:

1. **A labelled field** — the author wrote `Occupation:`, `Employer:`,
   `Email:`. Read anywhere in the README, because a label is the author saying
   what a value means. The label vocabulary is a fixed map; an unrecognised
   label produces nothing.
2. **A recognised phrase**, in the first 30 lines only — the header region where
   a profile states who someone is, rather than the project prose below it. A
   segment must match a closed vocabulary: a country name, an organisation word
   (`Government`, `University`, `Ministry`…), or an occupational noun
   (`Lecturer`, `Engineer`, `Linguist`…). The matched word is never the value;
   the value is the segment exactly as written.

A post is told from a field syntactically, not by world knowledge: a role phrase
carrying a subject, an employer, a grade or a seniority level (`in`, `of`, `at`,
`(BPS-17)`, `Senior`) names a **post**; a bare role phrase names a **field**. So

```
Lecturer in English (BPS-17), Government of Sindh, Pakistan - Applied Linguist
```

yields occupation `Lecturer in English (BPS-17)`, employer
`Government of Sindh`, geographic association `Pakistan`, professional field
`Applied Linguist` — four claims, each carrying that line verbatim as its
source, and each linked to the README's URL and stored SHA-256.

Anything the vocabulary does not recognise produces nothing. That is the
intended failure: a missed statement leaves an investigator where they already
were, while an invented one puts a fabrication in a report.

### A country is not a nationality

A place named on a profile is recorded as a **public geographic association**
and labelled that way everywhere — model, API, report and UI. Nationality,
citizenship and residence are legal statuses, and naming a country asserts none
of them. There is no fact kind that could carry one, which makes the limit
structural rather than a rule someone has to remember; a test asserts the
vocabulary contains no such kind.

### A declared name is recorded, never applied

A GitHub profile declaring `Timotheous Samar Dass` for a search of
`Timotheous Samar` is corroboration worth showing. It is not permission to
rewrite the target: the investigator named the subject, and a source's spelling
is that source's claim. So both names are stored side by side with a computed
relationship (`extends_searched_name`, `shortens_searched_name`,
`partial_overlap`, `unrelated`) and a sentence explaining it. Nothing writes a
declared name back onto a `Target`, and a test asserts the target is unchanged
after a run.

### A link published on a profile is provenance, not proof

A LinkedIn URL in a GitHub README is a real correlation signal: the account
holder published the connection. It is recorded as a match *reason* naming the
README — and it fires **no** confidence rule. A person can link to an account
that is not theirs, and the anchor model exists so a score rises only on
something the investigator supplied independently of the source. The linked
profile therefore lands at the name-only ceiling, well below anything that could
merge identities.

## Social discovery

### The capability registry

Three questions kept being answered by an `if` in whichever module needed
them — can a logged-out server read this platform, does it publish an API, is a
`site:` query worth generating. `app/collectors/capabilities.py` is now the one
answer. It does not restate the two tables that already exist:
`collectors/social.py` owns URL *shape*, `collectors/platforms.py` owns
*existence checks*, and the registry composes them and adds only what neither
records.

| | Platforms |
|---|---|
| **Directly checked** (single unauthenticated request) | GitHub, GitLab, PyPI, Reddit, Mastodon, Keybase, Hacker News, DEV, npm |
| **API-backed** | GitHub, GitLab, ORCID, Reddit, Mastodon, Keybase, Hacker News, DEV, npm |
| **Reference-only** (refuses anonymous automation) | LinkedIn, Instagram, Facebook, X/Twitter, TikTok, Snapchat |
| **Manual search only** | LinkedIn, Instagram, Facebook, YouTube, Snapchat, ORCID, X/Twitter, TikTok |

A platform in the reference-only row is a boundary respected, not a gap in
coverage. Nothing is fetched from it, and **a refusal to answer is never
recorded as an absence** — a test asserts no capability note says "not found".

### Layer A — supplied handles

For each handle the investigator supplied, the blocking platforms get a
**reference-only lead**: the URL where that handle *would* live, marked
`SAME_USERNAME`, `verification=manual_required`. Bounded at 12 per run.

The lead deliberately carries **no handle** into the anchor engine. Matching a
string against the string it was built from is the same fact twice, and scoring
it would let a lead about a stranger reach the confidence of a confirmed
account.

This surfaced a real defect in the anchor model. `PersonContext.all_handles`
merged `github_username` into the general handle pool, so a *GitHub* anchor
fired the generic `username` rule on any platform — a LinkedIn account reusing
the handle scored 0.70 as though the investigator had vouched for it.
`_match_handle` now reads `known_usernames` only; `github_username` is matched
by the GitHub branch that already existed. `known_usernames` carries no
platform, so it still matches anywhere, which is what the investigator meant by
supplying it.

### Layer B — links a page publishes about its owner

A GitHub profile README and an ORCID record's `researcher-urls` mean the same
thing: the person saying, in public, where else to find them. One function
promotes both (`_promote_links`), because two copies is how two paths come to
disagree about what a published link is worth.

It is worth **provenance and no score**. The linked profile lands at the
name-only ceiling with a match reason naming the page that published it. A
person can link to an account that is not theirs.

### Layer C — manual search recon

The platform generates queries; the investigator runs them in their own
browser. No engine is ever requested by this codebase, and a test greps every
source file for a consumer search URL to keep it that way.

Queries are built from the capability registry, so a platform added there is
searched without touching the query generator. Both filters are generated where
a platform has them — `site:linkedin.com/in` finds profiles and nothing else,
`site:linkedin.com` also finds the posts that mention someone — narrowest
first. Handle queries are generated only for platforms that *cannot* be checked
directly, which is exactly where a human has to look.

A fuller name a discovered profile declares (`Timotheous Samar Dass` for a
search of `Timotheous Samar`) becomes an **additional** query. Nothing renames
the target.

### Layer D — imported results

An import runs through the same anchor correlation a collected result does.
There is no shortcut for pasting a URL: choosing a result is a judgement about
relevance, not evidence about identity, and a test asserts an imported profile
scores exactly what `assess_profile` gives the same URL.

A handle typed into the form is used only where the URL's own shape yielded
none, and is recorded as `handle_source: investigator` — the investigator's
reading of the page, not the page's claim.

## Two rules that govern every promotion

**Nothing is derived.** An address is promoted only when a source published one.
`first.last@employer.com` is a guess, and a guess printed beside real evidence
reads as a finding — which is worse than returning nothing. A test greps
`promotion.py` for address construction.

**Nothing claims identity from an image.** An avatar is evidence that an account
publishes a picture. It says nothing about who is in it. No facial recognition,
no embeddings, no comparison of any two images.

## Correlation is recomputed; provenance merges

A report showed a GitHub *finding* at 0.70 saying the handle matched, beside the
*promoted profile* for the same account at 0.15 saying no anchors were supplied.
Both cannot be true of the same evidence.

The cause: `record_profile`'s existing-row branch updated names and attributes
but never the correlation. So the split is now explicit.

**Correlation is replaced.** Confidence, match reasons, mismatch reasons,
`corroborated_by`, accessibility and fetch notes are a pure function of the URL
and the anchors *currently* on the target. An older answer is not a second
opinion; it is a wrong one. Withdraw an anchor and the score drops with it.

**Provenance merges.** How a profile was found, the page that linked it, and the
facts a README stated are things that happened. A later pass that read nothing
new must not erase what an earlier one read.

**Analyst decisions are untouched by both.** They live in their own table, so a
refreshed score cannot reset a decision and a decision cannot edit a score.
Tests assert the decision *and its note* survive a refresh that moves the
confidence.

## Images in reports

The canonical model now carries `render_safe` per image — https only, no
credentials, no private literal — decided on shape with **no DNS lookup**,
because building a report must not resolve a hostname per image.

Whether a format *draws* the image is the renderer's choice:

- **Static Markdown does not embed by default.** A remote `![](https://…)`
  makes the reader's viewer fetch a third-party URL when the document is
  opened — an outbound request the investigator never made, to a host that logs
  when and from where the report was read. The default is an explicit image
  card: the URL as a link, candidate and profile context, state, provenance,
  hash where one exists. `?embed_images=true` opts in.
- **The frontend draws thumbnails**, including reference-only ones, because the
  investigator is already online and looking at their own case. A reference-only
  thumbnail is visually distinct and labelled, so it cannot read as verification
  that did not happen.

No SHA-256 is invented for bytes nobody read; the card says *why* there is none.

## Public contacts

Professional and business contact points only. There is no field for a
residential address and no code path that could produce one.

| Classification | Meaning |
|---|---|
| `VERIFIED_PUBLIC_BUSINESS` | Published by the organisation on an official page |
| `PUBLIC_PROFESSIONAL` | A professional registry or staff directory |
| `PUBLIC_SELF_PUBLISHED` | The person published it on their own profile |
| `UNVERIFIED_PUBLIC_REFERENCE` | Seen publicly, with nothing establishing ownership |

Classification follows **provenance, not plausibility**: an address on a
person's own GitHub profile is self-published however official its domain looks.

When a case has none: **`No verified public contact found.`**

Deduplicated on `(contact_type, value)` per case — the same address from two
sources is one contact corroborated twice, not two contacts, and never counts as
independent agreement with itself.

## Why `public_contacts` is its own table

`Entity(EntityType.EMAIL)` could have held the value. It was rejected because
the classification would have lived in a JSON blob rather than a constrained
column, "contacts for this candidate" would have become a graph traversal, and
`DecisionSubject` had no member for it — so analyst review would not have
reached contacts at all.

The table is shaped like `social_profiles` deliberately: same candidate
attribution, same separation of automated confidence from analyst judgement,
same provenance. That is following the established pattern, not adding a
parallel one.

## Source reliability is not identity confidence

They are different questions and stay separate:

- **Source reliability** — how well established the *source* is. For a contact
  this is its `classification`.
- **Identity confidence** — how strongly this record ties to *this subject*.
  Computed by the anchor engine, in `confidence`.

ORCID is a highly reliable source, and an ORCID record matching only on a name
is still a weak identity match. Conflating the two would let a reputable source
launder a weak match into a strong-looking one.

And both are separate again from the **analyst decision**, which lives in its
own table and never edits either.

## Recon queries

Grouped and collapsible rather than one vertical wall, ordered narrowest-first:

**Anchored** → **Social** → **Images** → **Professional & academic** → **Web** →
**Documents**

Anchored and Social start open; the rest collapse. "Open all" is capped at 8
tabs — a browser blocks a burst of popups, and thirty tabs is not a workflow.

Every link opens in *your* browser, in *your* session. The platform submits
nothing and scrapes nothing.

## What was deliberately not added

**No new external adapters.** The brief asked for research rather than
enthusiasm, and said to say so if nothing is worth adding. Candidate categories
(institutional staff directories, conference speaker data, professional
registries) share a problem: the ones with real programmatic APIs are mostly
already covered by ORCID/OpenAlex/Crossref/Wikidata, and the rest would need
scraping pages that were not published as data. This environment also cannot
reach arbitrary hosts to validate rate limits or terms, so adding an adapter
here would mean shipping something untested against the live service.

The larger honest point: this round's gain came from surfacing data the platform
*already had*, not from new sources. That was the correct place to spend the
effort.

**Education levels are not professional fields.** "…teaching across secondary,
undergraduate and postgraduate levels" describes who somebody teaches, not what
they are, and `undergraduate` was being promoted as a professional field. Two
conservative guards fixed it without loosening anything: a closed
`EDUCATION_LEVEL_TERMS` set whose members yield nothing on their own, and a
line-shape test that skips running prose — a line that is one long segment is a
sentence, and a line starting in lower case is the tail of a wrapped one. Both
fail towards producing nothing.

**No free-text interpretation.** The README extractor recognises a closed
vocabulary and nothing else. A language model reading the prose would find more,
and would also produce claims no line supports — and this is a report an
investigator signs. Extraction that cannot point at the exact words it came from
is not extraction.

**Deep public-page extraction** (rel=me, JSON-LD, mailto harvesting from
arbitrary pages) is deferred. `http_meta` already extracts title, description,
canonical URL and OpenGraph data, and the promotion path can consume those; a
general link-following extractor is a crawler with a bounded depth, and it
deserves its own design pass rather than being bolted on here.
