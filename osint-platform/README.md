# OSINT Investigation Platform

A privacy-first platform for **lawful** open-source intelligence work: security
research, journalism, fraud analysis, threat intelligence, due diligence and
authorised investigations.

It collects only information that is **already published** — by its owner, or by
a public registry — using documented public APIs and protocols. Every finding is
classified by a privacy filter, scored by named rules that carry their reasons,
and linked to a hash-verified artefact you can re-check.

```
INPUT → normalise → plan collectors → collect → normalise results
      → privacy filter → store findings + evidence → extract entities
      → correlate → score → graph → timeline → report
```

---

## What it does — and what it refuses to do

**In scope.** DNS, RDAP registration data, Certificate Transparency logs,
public-page HTTP metadata, the public GitHub API, the Internet Archive,
username presence on a curated set of public publishing platforms, and web
search through a configured provider API.

**Never implemented, by design.** Credential harvesting or stuffing; login,
password-reset or account-recovery probing; session hijacking or account
takeover; private-account, paywall or CAPTCHA bypass; authenticated scraping
without authorisation; phone or address aggregation on private persons;
real-time location tracking; stalkerware; device fingerprinting; SIM swapping;
social-engineering, phishing, malware or exploit automation; doxxing reports;
face recognition; dark-web credential dumps; breached-password retrieval;
private database access.

These are not merely absent. The architecture is built so they cannot be added
quietly, and [`tests/integration/test_security.py`](backend/tests/integration/test_security.py)
asserts each promise:

| Promise | How it is enforced | Test |
|---|---|---|
| No request reaches internal infrastructure | SSRF guard on every URL *and every redirect hop* | `TestSSRF` |
| Credentials are never stored | Privacy filter runs before persistence, on findings and on raw evidence alike | `TestPrivacyGuarantees` |
| Credentials never reach the logs | Structlog processor scrubs keys and value shapes | `test_secrets_never_reach_the_logs` |
| No collector logs in, resets or recovers an account | Collector source tree inspected by AST for forbidden imports and URLs | `test_no_collector_implements_a_prohibited_capability` |
| A shared username never becomes an identity claim | Confidence ceilings; edges are `SAME_USERNAME`, never `POSSIBLY_SAME_ENTITY` | `test_username_confidence_can_never_assert_identity` |
| robots.txt is honoured | Checked before the page is fetched; `Disallow` aborts collection | `test_robots_disallow_stops_collection` |
| Collected HTML cannot execute | Reports autoescape; the dashboard sandboxes the preview | `test_html_in_collected_data_is_escaped_in_the_report` |

---

## Quick start

### Docker (everything)

```bash
cp .env.example .env          # optional: add API keys
make docker-up                # postgres, redis, backend, worker, frontend
```

- Dashboard — <http://localhost:3000>
- API docs — <http://localhost:8000/docs>

### Local development

```bash
make install                  # backend venv + frontend deps
createdb osint                # or point DATABASE_URL at any PostgreSQL
make migrate
make seed                     # optional: a demonstration case, fixture data only
make dev                      # API on :8000, dashboard on :3000
```

Requirements: Python 3.12+, Node 22+, PostgreSQL 16+, Redis 7+ (Redis is
optional in development — the cache falls back to an in-process map and jobs
run inline when no broker is reachable).

### The acceptance workflow

```bash
osint case create "Example Domain Investigation"
osint target add --case CASE_ID --domain example.com
osint investigate --case CASE_ID --domain example.com
osint report --case CASE_ID --format html -o report.html
```

Or through the dashboard: create a case → add `example.com` → **Run
investigation** → read the graph, timeline and evidence → generate the report.

---

## Architecture

```
   CLI (Typer) ─┐                    ┌── Next.js dashboard
                ├──► FastAPI ────────┤
   HTTP client ─┘      │             └── HTML / Markdown / JSON reports
                       ▼
              InvestigationEngine
     ┌──────────┬──────┴─────┬───────────┬──────────┐
     ▼          ▼            ▼           ▼          ▼
 collectors  correlation  privacy      graph    reporting
 (registry)  (entities,   (classify,  (NetworkX
             confidence)   redact)     behind an ABC)
     │
     ▼
 core/http.py — the single outbound client: SSRF, rate limits,
                size caps, retries, caching
     │
     ▼
 PostgreSQL (SQLAlchemy + Alembic)   •   Redis (cache, jobs)
     ▲
     └── Celery worker runs the same engine out of process
```

The CLI, the API and the worker all call the same `InvestigationEngine`, so an
investigation produces identical results whichever entry point starts it.

See [docs/architecture.md](docs/architecture.md) for the detail.

---

## Collectors

| Collector | Source | API key | Produces |
|---|---|---|---|
| `dns` | system resolvers | — | A, AAAA, MX, TXT, NS, CNAME, SOA |
| `rdap` | rdap.org / IANA bootstrap | — | registrar, dates, statuses, nameservers |
| `http_meta` | the site itself | — | title, headers, redirects, security headers, robots/sitemap |
| `ctlog` | crt.sh | — | certificates, subdomains, issuers |
| `wayback` | Internet Archive CDX | — | first and recent snapshots, coverage |
| `github` | api.github.com | optional | profile, repositories, languages, topics, commit metadata |
| `username` | curated public platforms | — | handle presence (never identity) |
| `search` | Brave / Bing / Serper | **required** | public references |
| `email` | address structure, Gravatar, HIBP | optional | domain, Gravatar hash, breach *names* only |

A collector that cannot run says so — `GET /api/v1/collectors` reports
`available: false` with the variable to set, and the investigation records a
`SKIPPED` run rather than silently producing nothing.

Adding one is a decorator:

```python
@register_collector
class ExampleCollector(BaseCollector):
    name = "example"
    supported_targets = [TargetType.DOMAIN]
    rate_limit = RateLimit(requests=1, per_seconds=2.0)

    async def collect(self, target, ctx) -> CollectorResult: ...
```

See [docs/collectors.md](docs/collectors.md).

---

## Privacy model

Four classifications, applied to every finding before it is written:

| Level | Meaning | Treatment |
|---|---|---|
| `PUBLIC` | infrastructure and organisational facts | stored and shown |
| `PERSONAL` | self-published, but relates to a person | stored, flagged |
| `SENSITIVE` | harmful if aggregated (addresses, coordinates, breach exposure) | reduced and suppressed |
| `RESTRICTED` | credentials, financial and government identifiers | **replaced before storage** |

The filter runs twice: before persistence, so a database dump cannot leak what
was never written; and before export, so a report can apply a stricter policy
than the case database and be circulated more widely.

See [docs/privacy.md](docs/privacy.md).

### Investigating a named person

A PERSON target runs seven key-free collectors (ORCID, OpenAlex, Crossref,
Wikidata, GitHub user search, Reddit, and profile checks for handles you
supply), and generates the public-web searches an investigator would run by
hand. The platform never scrapes a search engine: it hands you the queries, you
run them in your own browser, and you import the public results worth keeping —
which are recorded as *yours*, not as something the platform fetched.

Candidates stay separate, confidence stays below the auto-merge threshold
whatever the anchors say, and image evidence is page context only — there is no
facial recognition anywhere in the platform.

See [docs/person-recon.md](docs/person-recon.md).
- [Operations](docs/operations.md) — deleting a case, and what happens when the queue stops moving

---

## Confidence

Scores come from named rules, and every score carries its reasons.

| Evidence | Score | Ceiling |
|---|---:|---:|
| Reciprocal self-published links | 0.95 | — |
| Site links to a profile (or a profile to a site) | 0.90 | — |
| Stated by the authoritative registry | 0.95 | — |
| Same username + shared website | 0.85 | 0.85 |
| Same username + matching biography | 0.70 | 0.70 |
| Same distinctive username | 0.50 | **0.60** |
| Shared hosting infrastructure | 0.60 | 0.65 |
| Weak name similarity | 0.25 | 0.40 |

Signals combine with a noisy-OR — several weak signals can raise a score — then
clamp to the highest ceiling present. The practical consequence, asserted by
test: *no quantity of username matches can ever equal a single self-published
link*, and nothing merges two entities on inference alone.

Bands: `≥0.90` likely · `0.70–0.89` probable · `0.50–0.69` possible · `<0.50`
weak association.

---

## Evidence

Every raw response is stored under its SHA-256, after credential removal.
Content addressing gives deduplication and tamper-evidence together, and one
artefact can support several findings.

```bash
curl localhost:8000/api/v1/cases/CASE_ID/evidence/verify
# {"total": 11, "verified": 11, "missing_raw": 0, "mismatched": [], "intact": true}
```

Every claim in a report cites the artefact behind it, or states plainly that no
artefact is attached.

---

## API

Full OpenAPI at `/docs`. The main routes:

```
POST   /api/v1/cases                     GET /api/v1/cases[/{id}]
PATCH  /api/v1/cases/{id}                DELETE /api/v1/cases/{id}
GET    /api/v1/cases/{id}/summary
POST   /api/v1/cases/{id}/targets        GET /api/v1/cases/{id}/targets
POST   /api/v1/cases/{id}/targets/bulk   POST /api/v1/cases/{id}/targets/preview
POST   /api/v1/cases/{id}/run            GET /api/v1/cases/{id}/jobs
GET    /api/v1/cases/{id}/findings       GET /api/v1/cases/{id}/runs
GET    /api/v1/cases/{id}/entities       GET /api/v1/cases/{id}/relationships
GET    /api/v1/cases/{id}/graph          GET /api/v1/cases/{id}/timeline
GET    /api/v1/cases/{id}/evidence       GET /api/v1/cases/{id}/evidence/verify
GET    /api/v1/cases/{id}/report?format=html|md|json
GET    /api/v1/jobs/{id}                 POST /api/v1/jobs/{id}/cancel
GET    /api/v1/collectors
```

See [docs/api.md](docs/api.md).

---

## CLI

```bash
osint case create NAME [--tag t]        osint case list|show|delete
osint target add --case ID --domain example.com
osint investigate --domain example.com [--collector dns] [--exclude-collector ctlog]
osint report --case ID --format html|md|json [-o file]
osint collectors                        osint normalize "@ExampleUser"
```

Every command accepts `--json` for pipelines; logs go to stderr, so stdout
carries only the payload.

---

## Configuration

All configuration is environment-based; see [`.env.example`](.env.example) for
every key. Nothing is hard-coded, no credential is ever returned by the API,
and the settings object does not render secrets in its `repr`.

The one setting that deserves care:

```bash
ALLOW_PRIVATE_NETWORKS=false   # only ever true for an AUTHORISED internal engagement
```

Cloud metadata endpoints stay blocked either way.

---

## Testing

```bash
make test        # 547 backend tests + 26 frontend tests
make lint        # ruff, black --check, mypy
make cov         # coverage report
```

The suite is hermetic: name resolution is answered from a fixed table and
anything else raises, so a collector that slips past its mock fails loudly
instead of quietly contacting a real host. All sample data uses reserved
documentation domains (`example.com`, RFC 5737 addresses) and invented handles.

---

## Documentation

- [docs/architecture.md](docs/architecture.md) — components, data model, request flow
- [docs/collectors.md](docs/collectors.md) — the collector contract and how to add one
- [docs/person-recon.md](docs/person-recon.md) — PERSON reconnaissance: anchors, the
  zero-cost manual-search workflow, correlation, and image-evidence limits
- [docs/privacy.md](docs/privacy.md) — classification, redaction, retention
- [docs/development.md](docs/development.md) — setup, workflow, conventions
- [docs/api.md](docs/api.md) — endpoint reference and examples

---

## Legal and ethical use

This platform is for lawful investigation with a legitimate basis: authorised
security testing, journalism, fraud analysis, threat intelligence, and due
diligence. You remain responsible for complying with the law that applies to
you, with the terms of every source you configure, and with data-protection
obligations toward the people whose published information you process.

If you cannot articulate a lawful basis for an investigation, do not run it.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
