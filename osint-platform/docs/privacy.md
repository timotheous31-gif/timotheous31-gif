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
