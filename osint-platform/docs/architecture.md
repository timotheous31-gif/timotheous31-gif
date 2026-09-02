# Architecture

## Shape of the system

```
   CLI (Typer) ─┐                    ┌── Next.js dashboard (App Router)
                ├──► FastAPI ────────┤
   HTTP client ─┘      │             └── HTML / Markdown / JSON reports
                       ▼
              InvestigationEngine  ── the only place the pipeline is expressed
     ┌──────────┬──────┴─────┬───────────┬──────────┐
     ▼          ▼            ▼           ▼          ▼
 collectors  correlation  privacy      graph    reporting
     │
     ▼
 core/http.py — the single outbound client
     │
     ▼
 PostgreSQL   •   Redis   •   evidence directory (content-addressed)
     ▲
     └── Celery worker runs the same engine out of process
```

The CLI, the API and the worker all call `InvestigationEngine.run()`. There is
no second implementation of the pipeline to drift out of step.

## Pipeline

| Stage | Module | Responsibility |
|---|---|---|
| Normalise the target | `services/normalization.py` | one canonical form per target; type inference |
| Plan | `collectors/registry.py` | which collectors accept this target type, minus exclusions |
| Collect | `collectors/runner.py` | concurrent execution, per-run budget, isolated failures |
| Normalise results | each collector's `normalize()` | raw payload → `FindingDraft` |
| Filter | `privacy/filter.py` | classify, redact, raise classification |
| Persist | `services/engine.py` | findings, runs, evidence, deduplication |
| Extract | `correlation/extraction.py` | findings → entity and relationship drafts |
| Resolve | `correlation/resolver.py` | reconcile with the case, infer bounded links |
| Score | `correlation/confidence.py` | named rules, ceilings, reasons |
| Graph | `graph/` | NetworkX behind a backend ABC |
| Timeline | `services/timeline.py` | dated facts only |
| Report | `reporting/` | one model, three renderers |

Each stage is separately testable and independently resilient. A failing
collector becomes a `FAILED` run row; a failing *stage* is recorded on the
result and does not corrupt what earlier stages wrote.

## Key decisions

**Sync SQLAlchemy, async collectors.** Celery workers and Alembic are
sync-native; only network I/O benefits from async. The engine bridges with
`asyncio.run`, so there is one session model rather than two.

**A portable `GUID` column type.** Native `UUID` on PostgreSQL, `CHAR(36)`
elsewhere. The whole test-suite runs on SQLite with no database server, and
production still gets real UUID columns. The Alembic template renders the type
by name so migrations stay correct for both.

**One outbound HTTP client.** `core/http.py` is the only module that opens a
socket to the outside world. Centralising it means the SSRF guard, the size
ceiling, the rate limiter and the retry policy cannot be bypassed by a
collector that forgets them.

**The graph behind an ABC.** `GraphBackend` defines the operations;
`NetworkXBackend` implements them. A Neo4j backend can be added without
touching a caller.

**Confidence rules as data.** `ConfidenceRule` objects carry a score, a reason
and a ceiling. Scoring is a pure function over signals, so the platform's most
consequential judgements are the easiest part of it to test and to change.

**The database is the source of truth for job state**, not Celery's result
backend, so the dashboard can report progress after a broker restart.

## Data model

```
Case ─┬─ Target ─── CollectorRun ─── Finding ─┬─ (finding_evidence) ─ Evidence
      │                                       └─ (entity_sources) ─── Entity
      ├─ Entity ─── Relationship ─── (relationship_evidence) ─ Finding
      ├─ TimelineEvent
      └─ Job
```

All tables use UUID primary keys, creation and update timestamps, indexes on
foreign keys and on the columns used for filtering, and `ON DELETE CASCADE`
from the case.

Two association tables carry provenance rather than a plain foreign key,
because the relationship is genuinely many-to-many: **one artefact supports
several findings** (a single HTTP response yields page metadata, security
headers and outbound links) and one finding can rest on several artefacts.
The same applies to the findings supporting an entity or an edge.

Uniqueness constraints that matter:

- `targets (case_id, type, normalized_value)` — adding the same target twice is
  a 409, not a duplicate row.
- `entities (case_id, type, canonical_value)` — the entity key. Equal keys are
  the same thing; this is a lookup, not an inference.
- `relationships (source, target, type)` — one edge per typed pair.
- `evidence (case_id, sha256)` — content addressing, so an artefact is stored
  once however many findings cite it.

## Request flow

1. Middleware assigns a request id, enforces the body-size ceiling, and adds
   security headers.
2. The router validates input with Pydantic and resolves the session
   dependency.
3. The service layer does the work; the API and the CLI call the same
   functions, so validation and normalisation happen once.
4. Domain errors map to status codes through one handler, producing a uniform
   envelope with a stable machine-readable `code`.
5. Structured logs carry case, target, collector, request id and duration —
   and never a credential.

## Failure model

- **A collector fails**: recorded as a `FAILED` run with its error type and
  message; the investigation continues. The report's executive summary says
  how many failed, so a partial result is never presented as a complete one.
- **A collector cannot run** (missing key): `SKIPPED` with the reason, surfaced
  in the API, the dashboard and the report.
- **A stage fails**: recorded on the result's `errors`; earlier stages' output
  stands.
- **The broker is unreachable**: the job runs inline and the response says so.
- **Evidence cannot be written**: logged; the hash and the database record
  still stand on their own.
