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

## Name variants

An investigation for **Tabitha Afzal Imdad** found nothing, because public
sources write her as **Tabitha Afzal**. Searching only the canonical spelling is
too literal to be useful.

`app/services/name_variants.py` generates spellings **by rule, never by
permutation**. Four transformations, each of which a person could plausibly have
used about themselves:

| Kind | Example | Rule |
|---|---|---|
| `EXACT_NAME` | `Tabitha Afzal Imdad` | as supplied |
| `INITIAL_VARIANT` | `Tabitha A Imdad`, `Tabitha A. Imdad` | a middle name to its initial |
| `REDUCED_NAME_VARIANT` | `Tabitha Afzal`, `Tabitha Imdad` | one part dropped |
| `HYPHENATION_VARIANT` | `Tabitha Afzal-Imdad` | the last two parts joined |

Plus one kind only a *source* can produce: `EXTENDED_NAME_MATCH`, where a page
publishes a fuller name carrying every searched part.

**Bounds, stated:** at most 8 variants; names of more than 4 parts are searched
only as written (dropping parts from a transliterated or compound name invents
people); a 2-part name yields only itself (there is no middle to drop, and one
token matches far too much); particles stay attached to the part they qualify, so
"Anna van der Berg" never becomes "Anna van". Dropping the *last* part is offered
only for a 3-part name — on a longer one it drops two at once, which is a
different name rather than a shorter form.

Every variant carries `search_variant`, `variant_type`, `canonical_target` and
`variant_generation_reason`. Nothing writes a variant back onto the target.

### A variant is a discovery signal, not identity proof

Each kind fires its own named confidence rule, with its own ceiling:

| Kind | Rule | Score | Ceiling |
|---|---|---|---|
| `EXACT_NAME` / `EXTENDED_NAME_MATCH` | `same_person_name` | 0.15 | 0.30 |
| `HYPHENATION_VARIANT` | `name_variant_hyphenation` | 0.14 | 0.28 |
| `INITIAL_VARIANT` | `name_variant_initial` | 0.12 | 0.24 |
| `REDUCED_NAME_VARIANT` | `name_variant_reduced` | 0.08 | 0.18 |
| `PARTIAL_NAME_MATCH` | `name_variant_partial` | 0.05 | 0.12 |

A shorter name is shared by more people, so it is worth less — and every ceiling
sits below the auto-merge threshold, so no spelling can merge identities however
many pages carry it. Strengthening comes only from an independent anchor:
an employer, a school, a handle, an ORCID, a profile URL, a place.

Both halves of the scoring path use this. `assess()` in the collectors and
`assess_profile()` in promotion call the same classifier — an existing test from
PR #9 caught the first attempt, where only one did and the two disagreed again.

## Automated search ingestion

The acceptance failure: forty-six well-aimed queries, **zero findings**, because
a query is a suggestion and nothing came back unless the investigator pasted it
in by hand.

With a provider configured, `app/services/search_ingest.py` runs the plan and
feeds every result through the pipeline a collector's findings use:

```
SearchResult -> URL validation (SSRF) -> platform classification
            -> candidate finding -> anchor correlation
            -> social / web / document / image evidence -> report
```

`SearchResult` carries `query`, `search_variant`, `variant_type`, `query_family`,
`displayed_url`, `result_type`, `image_url`, `rank`, `provider` and
`retrieved_at`. Providers fill in content; the caller that ran the query fills in
provenance, so an adapter cannot forget or misstate which search found what.

### Three rules on every result

**A search result earns no confidence for being a search result.** The name rule
that fires depends on the *spelling* that found it. A test asserts a mismatch
reason saying so appears on every ingested result.

**The same page found four ways is one page.** A URL reached from two queries and
two variants is one source with four provenance records. The repeat appends to
`searches` and deliberately does **not** touch `confidence` — a search engine
repeating itself is not a second source agreeing.

**Provider output is untrusted input.** Every URL goes through the same SSRF
guard a pasted URL does: loopback, RFC1918, link-local, metadata endpoints,
credentials in the authority and non-http(s) schemes are all refused before
anything is stored. No second HTTP path exists.

### $0 mode stays useful

`SEARCH_PROVIDER=none` is the default and every workflow still runs: name
variants, the staged plan, the $0 collectors (GitHub, ORCID, OpenAlex, Crossref,
Wikidata, handle checks), published-link pivots from pages we legitimately fetch,
manual import, promotion, analyst review and the full report. What changes with a
provider configured is that results arrive **without the investigator copying
each one**.

## The staged plan

One list of forty-six queries is a wall, not a workflow. The plan is now five
stages, in the order an investigator actually works:

1. **The name as supplied** — broad, mostly other people.
2. **Spellings a public source might use** — usually what finds a real footprint.
3. **Paired with what you already know** — supplied anchors; fewest strangers.
4. **Paired with what the investigation found** — a claim a page actually
   published, carrying the URL that published it.
5. **Targeted platform, image and document sweeps.**

Stage 4 is the iterative part, and it is bounded by provenance:
`discovered_anchors()` reads claims off persisted findings and keeps only those
with a source URL. The query's rationale cites that page, so an investigator can
see where a search term came from and reject it. An anchor without a source would
be the platform inventing a term and then searching for it.

## Source coverage

The worst thing a report can do is let "nobody looked" read as "there is nothing
to find". Every source family resolves to exactly one state:

| State | Means |
|---|---|
| `FOUND` | Searched; records returned. |
| `NO_MATCH_RETURNED` | Searched; nothing returned. **The only state that is evidence of absence** — and only for what that source indexes. |
| `NOT_SEARCHED` | Never attempted. Says nothing about the subject. |
| `SKIPPED` | Declined an anonymous automated request. |
| `FAILED` | Broke upstream. A gap, not a negative result. |
| `MANUAL_REVIEW_AVAILABLE` | Not automatable; queries are generated for you. |
| `PROVIDER_NOT_CONFIGURED` | A channel switched off by configuration. |

The executive summary carries the gap sentences **first among the caveats and
never omits them**, and when there are no findings at all it says explicitly that
this is a statement about what was searched rather than about what exists.

## Current versus historical executions

A case with five reruns had forty collector rows, and the report counted them as
forty sources. It had used eight, five times.

**No new entity, no migration.** A `Job` *is* an execution, and
`CollectorRun.job_id` already records which one a run belonged to. So the latest
execution is the latest job's runs; everything else is history, kept and counted
separately. Runs with no job (a direct engine call) group as one unattributed
execution rather than being dropped.

The report now shows `investigation executions`, the latest execution's
attempted / successful / failed / skipped counts, and `historical collector runs`
as a distinct figure. Coverage describes **today's** outcome, not the best one
across reruns.

## Crossref: "Event loop is closed"

Root cause, reproduced before fixing: `httpx.AsyncClient` binds its connection
pool to the loop that created it, and `AsyncClient.is_closed` only tracks an
explicit `aclose()` — not whether that loop is still alive. The engine runs each
target under its own `asyncio.run`, so the second run reused a module-global
client whose keep-alive connections belonged to a loop that had already closed,
and the next request on one of them raised `RuntimeError: Event loop is closed`.

Intermittent by nature: it needed a connection the previous loop had actually
kept alive, which is why it surfaced on Crossref rather than on every source.

Two complementary fixes:

- **The engine closes the pool inside its own loop** (`_collect`, in a `finally`).
  The loop's owner owns the client's lifecycle.
- **`get_http_client()` rebuilds when the running loop is not the one the client
  was created in**, and logs that it had to. A caller that forgets cannot
  resurrect the bug, only lose a connection pool.

A client injected by a test is never rebuilt and never closed by
`close_owned_client()` — swapping it would silently disconnect the mock transport.

## Wikidata: citizenship is not a location

PR #9 flagged it; this round fixes it. `P27` is *country of citizenship* and it
was being read into `candidate.locations` — where the anchor engine compares a
supplied city or country. That made a citizenship claim matchable against a
place, which is precisely the nationality inference this platform refuses, in the
one place it would have been invisible.

It is kept, because Wikidata genuinely publishes it about public figures, but as
an explicitly source-claimed `citizenship_claims` fact that feeds **nothing**:
not a location, not an affiliation, not an anchor. It carries its own
interpretation string, and `LOCATION_PROPERTIES` is now empty — Wikidata's place
claims that *are* places (birthplace, residence) were never collected and still
are not. No migration: it lives in the finding's `extra`.

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
