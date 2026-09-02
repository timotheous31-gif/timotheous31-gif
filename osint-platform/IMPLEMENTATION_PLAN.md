# OSINT Investigation Platform — Implementation Plan

> A modular, privacy-first platform for **lawful** open-source intelligence work:
> security research, journalism, fraud analysis, threat intelligence, due
> diligence and authorised investigations.

---

## 0. Scope and non-goals

### In scope

Collection, normalisation, correlation and reporting of information that is
**already published** by its owner or by a public registry, using documented
public APIs and protocols (DNS, RDAP, Certificate Transparency, HTTP metadata of
public web pages, public GitHub API, Wayback Machine, configured search-provider
APIs).

### Explicitly out of scope (never implemented)

Credential harvesting/stuffing, login or password-reset probing, session
hijacking, account takeover, private-account or CAPTCHA bypass, authenticated
scraping without authorisation, phone/address aggregation on private persons,
real-time location tracking, stalkerware, device fingerprinting, SIM swapping,
social-engineering or phishing automation, malware or exploit delivery, doxxing
reports, face recognition, dark-web credential dumps, breached-password
retrieval, private database access.

These are not "unimplemented features" — the architecture is designed so that
they cannot be bolted on quietly: every finding passes through a privacy filter
that classifies and redacts before storage-for-display, and every collector must
declare its data sensitivity.

---

## 1. Architecture

```
                       ┌──────────────┐
   CLI (Typer) ──────► │              │
                       │  FastAPI     │◄──── Next.js dashboard
   HTTP client ──────► │  app/api     │
                       └──────┬───────┘
                              │ services/
                              ▼
                    ┌────────────────────┐
                    │ InvestigationEngine│
                    └─────────┬──────────┘
        ┌───────────┬─────────┼──────────┬─────────────┐
        ▼           ▼         ▼          ▼             ▼
   collectors/  correlation/ privacy/  graph/     reporting/
   (registry)   (entity res) (filter)  (NetworkX)  (HTML/MD/JSON)
        │
        ▼
   core/http.py  (single, SSRF-hardened, rate-limited async client)
        │
        ▼
   PostgreSQL (SQLAlchemy 2.0 + Alembic)  •  Redis (cache, rate state, jobs)
        ▲
        └── Celery worker executes the same engine out-of-process
```

### Pipeline

```
INPUT
 → target normalisation      (services/normalization.py)
 → collector planning        (collectors/registry.py: which collectors accept this target type)
 → collection                (async, concurrent, isolated failures)
 → result normalisation      (each collector's normalize())
 → entity extraction         (correlation/extraction.py: findings → entities)
 → entity correlation        (correlation/resolver.py)
 → confidence scoring        (correlation/confidence.py, rule-based + configurable)
 → relationship graph        (graph/, NetworkX backend behind GraphBackend ABC)
 → timeline                  (services/timeline.py)
 → evidence store            (services/evidence.py, SHA-256 content addressing)
 → privacy filter            (privacy/filter.py — runs before persistence-for-display and before export)
 → report                    (reporting/, HTML + Markdown + JSON)
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Sync SQLAlchemy, async collectors | Celery workers and Alembic are sync-native; only network I/O benefits from async. The engine bridges with `asyncio.run`. |
| `GUID` TypeDecorator for PKs | Native `UUID` on PostgreSQL, `CHAR(36)` on SQLite → the whole suite runs without a database server, production still gets real UUIDs. |
| Collector registry + decorator | `@register_collector` is the only step needed to add a source. |
| Single `core/http.py` | One place enforces SSRF guards, timeouts, max response size, redirect policy, rate limits, retries and caching. Collectors cannot bypass it. |
| Graph behind an ABC | `NetworkXBackend` today; a `Neo4jBackend` can be added without touching callers. |
| Confidence rules as data | `ConfidenceRule` objects in a configurable ruleset; scores always carry human-readable reasons. |
| Privacy filter as a hard gate | Findings are stored with a classification; `SENSITIVE`/`RESTRICTED` values are redacted at write time, so a database dump cannot leak what the filter caught. |

---

## 2. Repository layout

```
osint-platform/
  backend/
    app/
      api/            FastAPI routers (cases, targets, runs, findings, entities,
                      graph, timeline, report, jobs, health)
      core/           settings, logging, db, http client, ssrf, ratelimit, cache, errors
      collectors/     base, registry, dns, rdap, http_meta, ctlog, wayback,
                      github, username, search
      correlation/    extraction, confidence, resolver
      graph/          base (ABC), networkx_backend, builder
      models/         SQLAlchemy models
      schemas/        Pydantic v2 schemas
      services/       normalization, cases, evidence, engine, timeline, jobs
      privacy/        classifier, filter, secrets
      reporting/      html, markdown, json, templates/
      workers/        celery app + tasks
      cli/            Typer CLI
    tests/            unit + integration
    alembic/
  frontend/           Next.js 15 App Router, TS, Tailwind, Cytoscape
  docker/             Dockerfiles
  docs/               architecture, collectors, privacy, development, api
  scripts/            seed, dev helpers
  examples/           fixture API responses, sample case
```

---

## 3. Database design

All tables use UUID primary keys, `created_at`/`updated_at` timestamps and
indexes on foreign keys plus the columns used for filtering.

| Table | Purpose | Notable columns |
|---|---|---|
| `cases` | investigation container | name, description, status(enum), notes |
| `tags` / `case_tags` / `target_tags` | free-form labels | name (unique) |
| `targets` | normalised investigation target | case_id, type(enum), raw_input, normalized_value, status, notes |
| `collector_runs` | one collector × one target | collector, version, status, started/finished, duration_ms, error_type, error_message, stats(JSON) |
| `findings` | normalised atomic result | run_id, target_id, kind, title, data(JSON), classification(enum), confidence, source_url |
| `evidence` | provenance record | finding_id, collector, source_url, retrieved_at, sha256, content_type, size, raw_ref, redacted(bool) |
| `entities` | resolved entity | case_id, type(enum), display_name, canonical_value, aliases(JSON), attributes(JSON), confidence |
| `entity_sources` | entity ← finding provenance | entity_id, finding_id |
| `relationships` | typed edge | case_id, source_entity_id, target_entity_id, type(enum), confidence, reasons(JSON), collector, evidence refs |
| `timeline_events` | chronological item | case_id, occurred_at, kind, title, description, entity_id, finding_id |
| `jobs` | Celery job mirror | case_id, celery_id, state(enum), progress, message, error |

Constraints: unique `(case_id, type, canonical_value)` on entities;
unique `(source_entity_id, target_entity_id, type)` on relationships;
unique `sha256` on evidence (deduplication).

---

## 4. Collector design

```python
@register_collector
class DNSCollector(BaseCollector):
    name = "dns"
    version = "1.0.0"
    description = "Resolves public DNS records"
    supported_targets = [TargetType.DOMAIN, TargetType.URL]
    requires_api_key = False
    rate_limit = RateLimit(requests=20, per_seconds=1.0)
    timeout = 10.0
    default_confidence = 0.95

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> RawResult: ...
    def normalize(self, raw: RawResult) -> list[FindingDraft]: ...
```

* `CollectorRunner` executes each collector in its own task with a hard timeout
  and `return_exceptions=True`, so one failure never aborts an investigation; the
  failure is recorded as a `collector_runs` row with `status=FAILED`.
* Retries: exponential backoff with jitter for 429/5xx/timeouts only.
* Every `FindingDraft` carries `source_url`, `collector`, `confidence`,
  `confidence_reasons` and the raw payload used to produce it.

### Phase-1..4 collectors

| Collector | Source | Key |
|---|---|---|
| `dns` | dnspython over system resolvers | no |
| `rdap` | rdap.org / IANA bootstrap | no |
| `http_meta` | the target site itself (single polite request + robots/sitemap HEAD) | no |
| `ctlog` | crt.sh JSON API | no |
| `wayback` | Wayback CDX + availability API | no |
| `github` | api.github.com (unauthenticated or PAT) | optional |
| `username` | curated public platform list, HEAD/GET on profile URLs | no |
| `search` | pluggable provider (Brave / Bing / Serper) | yes |
| `email` | domain + MX + Gravatar hash + optional HIBP breach *names only* | optional |

---

## 5. API design

```
GET    /health           /health/ready
POST   /api/v1/cases                      GET /api/v1/cases      GET /api/v1/cases/{id}
PATCH  /api/v1/cases/{id}                 DELETE /api/v1/cases/{id}
POST   /api/v1/cases/{id}/targets         GET /api/v1/cases/{id}/targets
POST   /api/v1/cases/{id}/run             → Job
GET    /api/v1/cases/{id}/findings        ?kind=&classification=&min_confidence=
GET    /api/v1/cases/{id}/entities        GET /api/v1/cases/{id}/relationships
GET    /api/v1/cases/{id}/graph           ?min_confidence=&types=
GET    /api/v1/cases/{id}/timeline
GET    /api/v1/cases/{id}/evidence
GET    /api/v1/cases/{id}/report          ?format=html|md|json
GET    /api/v1/jobs/{id}                  POST /api/v1/jobs/{id}/cancel
GET    /api/v1/collectors
```

Fully described by FastAPI's OpenAPI schema; every response model is a Pydantic
v2 model.

---

## 6. Security model

* **SSRF**: every outbound URL is validated — scheme allowlist (`http`/`https`),
  DNS resolution of the host, rejection of loopback/private/link-local/reserved/
  multicast addresses and cloud metadata endpoints, re-validation on every
  redirect hop, capped redirect count. Overridable only by an explicit
  `ALLOW_PRIVATE_NETWORKS=true` for authorised internal engagements.
* **Response limits**: streamed reads with a hard byte cap and a total timeout.
* **Injection**: SQLAlchemy parameter binding only; no string-built SQL.
* **XSS**: reports escape all interpolated values; HTML fetched from targets is
  never re-rendered, only parsed for metadata.
* **Secrets**: settings via Pydantic Settings from environment; `.env.example`
  documents every key; secrets are never logged (a logging filter scrubs
  known patterns) and never returned by the API.
* **Privacy**: `PrivacyFilter` classifies every finding value as
  `PUBLIC | PERSONAL | SENSITIVE | RESTRICTED` and redacts the latter two.

---

## 7. Testing strategy

* `pytest` + `pytest-asyncio`; SQLite via the `GUID` decorator for fast,
  hermetic DB tests; `respx` to mock every outbound HTTP call; fixture JSON in
  `examples/fixtures/` captured from public documentation examples.
* Unit tests: normalisation, each collector's `normalize()`, SSRF guard, rate
  limiter, privacy classifier, secret detector, confidence rules, resolver
  thresholds, graph builder, report renderers.
* Integration tests: full `InvestigationEngine` run against mocked collectors
  and the real API through `httpx.ASGITransport`.
* Only fictional/reserved targets: `example.com`, `example.org`,
  `test.invalid`, mock usernames.

---

## 8. Phases

| Phase | Content | Exit criteria |
|---|---|---|
| 1 | skeleton, settings, logging, DB, FastAPI, health, Docker, first tests | `pytest` green, `ruff`/`black`/`mypy` clean |
| 2 | models, migrations, case/target CRUD API, CLI skeleton | CRUD tests green |
| 3 | collector framework, http core, SSRF, rate limit, DNS/RDAP/HTTP collectors | collector unit tests green |
| 4 | CT logs, Wayback, GitHub, username, search provider abstraction | mocked collector tests green |
| 5 | entity extraction, graph, correlation, confidence | correlation tests green |
| 6 | privacy filter, secret detection, evidence provenance | privacy tests green |
| 7 | Celery, Redis, job tracking, progress | job tests green |
| 8 | Next.js dashboard | `next build` clean |
| 9 | graph view, timeline UI | build clean |
| 10 | HTML/MD/JSON reports | report tests green |
| 11 | security hardening pass | security tests green |
| 12 | integration tests, docs, compose | end-to-end acceptance test green |

Each phase ends with: tests, lint, fix, file list, summary, technical-debt note,
commit. A phase is not left with failing core tests.

---

## 9. What actually shipped, and where it diverged

All twelve phases are complete: 547 backend tests (93% statement coverage) and
26 frontend tests pass; ruff, black, mypy, `tsc --noEmit` and `next build` are
clean. Deviations from the plan above, and why:

**Ordering.** Reporting (phase 10) was built before the frontend (phases 8–9),
because the dashboard consumes the report endpoint. Core HTTP, SSRF, rate
limiting and caching moved from phase 3 into phase 1, since they are
infrastructure the collectors merely use.

**Evidence is many-to-many with findings.** The plan gave `evidence` a
`finding_id` foreign key. That is wrong: a single HTTP response yields page
metadata, security headers *and* outbound links, so one artefact supports
several findings. The acceptance test caught findings silently citing no
evidence. Replaced with a `finding_evidence` join table, which keeps
content-addressed deduplication and complete provenance.

**Per-request and per-run timeouts are separate.** `timeout` is the per-request
budget passed to the HTTP client; `run_timeout` bounds a whole collector run. A
collector that makes many requests keeps a small per-request timeout and a
larger run budget.

**Confidence combination has ceilings, not just scores.** Noisy-OR alone would
let twenty username matches out-score a self-published link. Each rule declares
a ceiling for its class of evidence, and the combined score clamps to the
highest ceiling present.

**The test suite is hermetic by construction.** An autouse fixture answers name
resolution from a fixed table and raises otherwise. This was added after the
engine tests were found reaching the live network through the real collector
registry.

### Defects found by running the real thing

Each was found by exercising the system rather than by reading it, and each is
now covered by a test:

| Found by | Defect |
|---|---|
| Real run against PostgreSQL | Payment-card classifier matched any long digit run, redacting a DNS SOA record's serial as a card number. Now requires a card-shaped grouping *and* a Luhn checksum. |
| Real run against PostgreSQL | structlog wrote to stdout, corrupting `--json` output. Logs go to stderr. |
| Test-ordering failure | Binding `sys.stderr` at configure time captured pytest's temporary stream; later logging wrote to a closed file. The stream is now resolved per write. |
| Browser verification | `NEXT_PUBLIC_API_URL` is inlined at build time, so the compose runtime variable could never have worked. Now a Docker build arg. |
| Browser verification | CORS allowed only `localhost:3000`, so `127.0.0.1` failed opaquely. |
| Browser screenshot | `example.com`'s null MX (`0 .`) became an empty hostname and a blank graph node. Null MX is now recorded as the fact it is, and extraction refuses an entity with no canonical value. |
| Security test | The redirect limit was unenforceable — the loop returned the final 302 as content and the raise after it was unreachable. Now a typed `TooManyRedirects`. |
| Acceptance test | Evidence could cite only one finding (see above). |

### Known limitations and technical debt

- **`registrable_domain()` uses a small suffix heuristic**, not the Public
  Suffix List. It is only ever a grouping hint, never an assertion, but a
  bundled PSL would be more accurate.
- **The graph backend is NetworkX only.** The `GraphBackend` ABC exists so a
  Neo4j backend can be added, but none is written.
- **Job progress is coarse** — one step per target plus correlation and
  timeline. Per-collector progress events would be finer.
- **No authentication or multi-tenancy.** The API assumes a trusted network or
  an authenticating reverse proxy. Anyone who can reach it can read every case.
- **Docker images were not built during development** (no daemon available);
  the compose file validates and the Dockerfiles were reviewed statically.
- **PDF reports are not implemented**, as planned; HTML prints cleanly.
- **The frontend has no component tests** — the API client and formatters are
  unit-tested, and the routes are verified end to end in a real browser, but
  rendering logic is not covered by unit tests.
