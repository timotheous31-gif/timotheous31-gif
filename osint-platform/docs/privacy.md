# Privacy

The platform processes information about people. Most of it was published by
those people, but publication is not consent to aggregation, and aggregation is
what an OSINT platform does. This document describes the controls that exist
because of that.

## Classification

Every finding is classified before it is written to the database.

| Level | Meaning | Examples | Treatment |
|---|---|---|---|
| `PUBLIC` | infrastructure or organisational fact | DNS records, certificates, repository metadata, registrar | stored and shown |
| `PERSONAL` | self-published, but relates to an identifiable person | profile biography, display name, email address, self-declared city | stored, flagged for careful handling |
| `SENSITIVE` | would harm or expose if aggregated | residential address, precise coordinates, date of birth, phone number, breach exposure | reduced and suppressed |
| `RESTRICTED` | must never be stored in full | passwords, tokens, API keys, private keys, payment cards, government identifiers | **value replaced before storage** |

Classification is **monotonic**: a collector's own assessment can be raised by
the filter, never lowered. A collector that marks something `PUBLIC` cannot
override the filter's judgement that it is a credential.

## The filter runs twice

**Before persistence.** `RESTRICTED` values are replaced with `[REDACTED]` and
`SENSITIVE` ones with `[SUPPRESSED: SENSITIVE PERSONAL DATA]` *before the row is
written*. A database dump cannot leak what was never stored. The same filter
runs over raw payloads before they reach the evidence directory, so that
directory can never become a credential store.

**Before export.** A report can apply a stricter policy than storage did — by
default it withholds anything above `PERSONAL` — so a report can be circulated
more widely than the case database. The report states how many findings were
withheld and why.

## Credential detection

`privacy/secrets.py` answers exactly one question — *does this string look like
a credential?* — and deliberately refuses to answer any other. It never
returns, stores, logs or transmits the matched value. A match carries only a
category, character offsets, a confidence and a coarse shape hint
(`alpha:20`).

Detected formats are those whose issuers document them: AWS access keys, GitHub
classic and fine-grained tokens, Slack tokens, Stripe keys, Google and OpenAI
API keys, SendGrid keys, Twilio SIDs, JWTs, Basic and Bearer headers, private
key headers, URLs with embedded credentials, and generic `key = value`
assignments. Obvious placeholders (`your_api_key_here`, `AKIAIOSFODNN7EXAMPLE`)
are suppressed so the platform does not cry wolf.

Payment-card detection additionally requires a valid **Luhn checksum**. Without
it, a pattern loose enough to catch a real card number also catches a DNS SOA
record's serial and timer values — which it did, until a run against real data
showed the SOA record being redacted as a card.

### Secrets found in public repositories

When a credential-shaped string appears in public repository metadata, the
platform records a `POTENTIAL_SECRET_EXPOSURE` finding containing:

```json
{
  "repository": "owner/name",
  "location": "repository.description",
  "secret_type": "AWS access key id",
  "value": "[REDACTED SECRET]",
  "commit": "main",
  "status": "unverified"
}
```

The credential itself is never stored, displayed or transmitted, and it is
never tested against the service it belongs to. The purpose is to let an
investigator responsibly notify the owner — not to hold a live credential.

## What the platform does not collect

Deliberately absent, with the architecture arranged so they cannot be added
quietly:

- login attempts, password-reset or account-recovery probing, session or
  account takeover;
- private-account, paywall or CAPTCHA bypass; authenticated scraping without
  authorisation;
- phone-number or residential-address enrichment on private individuals;
- real-time location, device fingerprinting, or any form of tracking;
- breached credential retrieval — the HIBP integration returns breach *names
  and dates* only;
- face recognition; dark-web credential dumps; private database access;
- doxxing reports of any kind.

`tests/integration/test_security.py::TestCollectionPolicy` inspects the
collector source tree's AST for forbidden imports (SMTP, POP, IMAP, FTP, SSH)
and forbidden URL fragments (`/login`, `/reset`, `/recover`, `/oauth/token`),
so these remain absent as the code changes.

## Identity claims

The platform will not assert that two accounts belong to the same person
because their handles match.

- A shared handle produces a `SAME_USERNAME` edge, never
  `POSSIBLY_SAME_ENTITY`.
- Username evidence is capped at 0.60 (0.40 for short or common handles), so no
  quantity of matches can equal a single self-published link at 0.90.
- Entities merge only on an exact `(type, canonical_value)` key — a lookup, not
  an inference. Anything softer produces a scored, evidence-linked edge for a
  human to accept or reject.
- Every report repeats this in its Limitations section.

## Named people

A personal name is not an identifier, and the platform is built so that no code
path can quietly treat one as though it were.

- **A name is never classified for you.** Free text has no inferable type —
  "Timotheous Samar" and "Example Corporation" read identically — so the API
  refuses it with `ambiguous_target_type` and the investigator states whether it
  is a `PERSON` or an `ORGANIZATION`. Guessing would file a human being under
  the wrong type and run the wrong collectors against them.
- **Only name-appropriate collection runs.** A `PERSON` target schedules the
  search collector alone. No DNS, RDAP, certificate-transparency or
  HTTP-metadata lookup is pointed at a personal name, and no GitHub login is
  guessed by deleting the spaces from one.
- **One query, unqualified.** The search collector asks for the quoted name and
  nothing else. Appending "address", "phone" or "employer" is how a name search
  becomes a dossier, so those queries do not exist in the code.
- **Same-name results stay separate.** Each result becomes its own candidate,
  keyed on the page that mentions the name rather than on the name itself. Ten
  pages about ten different people who share a name produce ten candidates, not
  one merged persona.
- **The link says what it rests on.** A candidate is joined to the subject by
  `POSSIBLY_SAME_ENTITY` carrying the `same_person_name` signal, capped at 0.30
  — far below the 0.95 auto-merge threshold — with
  `basis: name_match_only` and `requires_corroboration: true` on the edge.
  `tests/unit/test_person_targets.py` asserts that no accumulation of name
  matches can reach the merge threshold.
- **Reports say so on their face.** A report for a case with a `PERSON` target
  carries these limitations at the top of its Limitations section.

### Free sources, and what they are allowed to do

PERSON investigations run on public APIs that need no key: ORCID, OpenAlex,
Crossref, Wikidata, GitHub's user search, Reddit's public search, and direct
profile checks. Each is a documented public endpoint queried anonymously — the
same request a logged-out visitor makes. Nothing authenticates, nothing scrapes
behind a login, and no CAPTCHA is answered or avoided. Where a source refuses
anonymous access (Reddit commonly does from server networks) the run is recorded
`SKIPPED` with that reason rather than presented as an empty result.

Two limits are structural rather than advisory:

- **Wikidata candidates must be humans.** Items are filtered on P31=Q5, and only
  employer, education, citizenship and occupation claims are read. Date of
  birth, residence and family are not requested.
- **Handles are checked, never invented.** `person_usernames` probes only
  usernames the investigator supplied. Deriving a handle from a name and probing
  platforms with it would generate leads about whoever actually owns that
  handle.

Investigator-supplied context (usernames, profile URLs, organisations, schools,
city, country) exists to *narrow* a judgement, and the schema admits nothing
finer than a city: a street address is not corroboration. Context is never sent
to a source as an extra search term, and never becomes a finding — the
investigator already knew it.

## Logging and retention

Structured logs carry investigation context and never credentials: a processor
scrubs credential-shaped keys and value patterns from every event. Logs go to
stderr so machine-readable output stays clean on stdout.

Redis holds only short-lived cache entries and job progress, always with a TTL.
The evidence directory holds filtered payloads under their hashes; deleting a
case cascades to its findings, entities, relationships, timeline and evidence
rows.

## Your obligations

The platform gives you controls; it cannot give you a lawful basis. Before an
investigation, be able to say what yours is. Configure the export policy to
match who will read the report. Delete cases when the purpose that justified
them has ended.
