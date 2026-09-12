# API reference

Base URL `http://localhost:8000`, versioned under `/api/v1`. Interactive
documentation is generated from the code at `/docs` (Swagger) and `/redoc`.

Every response is JSON except reports, which honour the requested format.
Every request carries an `X-Request-ID` back in the response; supply your own
to correlate with your logs.

## Errors

One envelope for every handled failure:

```json
{
  "code": "not_found",
  "message": "Case 0e1f... does not exist",
  "detail": null,
  "request_id": "7c2f-..."
}
```

| `code` | Status | Meaning |
|---|---|---|
| `validation_error` | 422 | Request body or a target value failed validation |
| `not_found` | 404 | The resource does not exist |
| `conflict` | 409 | Duplicate target, or a case already has an active job |
| `policy_refused` | 403 | robots.txt or a privacy rule refuses the action |
| `ssrf_blocked` | 400 | The URL resolves to a blocked address |
| `too_many_redirects` | 502 | An upstream redirect chain exceeded its budget |
| `configuration_error` | 503 | A required credential or provider is not configured |
| `request_too_large` | 413 | The body exceeds `MAX_REQUEST_BYTES` |
| `internal_error` | 500 | Unexpected failure; details are logged, never returned |

## Health

```
GET /health          → {"status": "ok", "version": "0.1.0", "environment": "..."}
GET /health/ready    → per-dependency checks; 503 when degraded
```

## Cases

```http
POST /api/v1/cases
{"name": "Example Domain Investigation", "description": "...", "tags": ["demo"]}
→ 201 Case
```

```
GET    /api/v1/cases?status=&q=&tag=&limit=&offset=   → Page<Case>
GET    /api/v1/cases/{id}                             → Case
GET    /api/v1/cases/{id}/summary                     → counters for the overview
PATCH  /api/v1/cases/{id}                             → Case
DELETE /api/v1/cases/{id}                             → 204 (cascades)
```

## Targets

```http
POST /api/v1/cases/{id}/targets
{"value": "  Example.COM. "}
→ 201 {"type": "DOMAIN", "normalized_value": "example.com", "raw_input": "Example.COM.", ...}
```

The type is inferred from the input's shape unless you supply `type`. Adding
the same normalised target twice returns `409 conflict`.

```
POST   /api/v1/cases/{id}/targets/bulk      # skips duplicates rather than failing
POST   /api/v1/cases/{id}/targets/preview   # shows normalisation, persists nothing
GET    /api/v1/cases/{id}/targets?type=
PATCH  /api/v1/cases/{id}/targets/{target_id}
DELETE /api/v1/cases/{id}/targets/{target_id}
```

## Running an investigation

```http
POST /api/v1/cases/{id}/run
{"collectors": ["dns"], "exclude_collectors": [], "target_ids": []}
→ 202 {"job": {...}, "dispatch": {"dispatched": "celery", "celery_id": "..."}}
```

`dispatch` says whether the job went to a worker or ran inline, so a caller
never has to guess. A case may have only one active job; a second returns
`409`.

```
GET  /api/v1/jobs/{job_id}          → state, progress (0–1), message, result
POST /api/v1/jobs/{job_id}/cancel   → requests cancellation; checked between collectors
GET  /api/v1/cases/{id}/jobs
GET  /api/v1/cases/{id}/runs        → every collector execution, including failures
```

## Reading results

```
GET /api/v1/cases/{id}/findings?kind=&classification=&collector=&min_confidence=
GET /api/v1/cases/{id}/entities?type=&min_confidence=
GET /api/v1/cases/{id}/relationships?type=&min_confidence=
GET /api/v1/cases/{id}/timeline?kind=&order=asc|desc
GET /api/v1/cases/{id}/evidence
GET /api/v1/cases/{id}/evidence/verify
```

A finding carries its score, the reasons behind it, its classification, and the
evidence supporting it:

```json
{
  "kind": "DNS_RECORD",
  "title": "DNS A for example.com",
  "confidence": 0.95,
  "confidence_reasons": ["Resolved directly from public DNS, which is authoritative for this fact"],
  "classification": "PUBLIC",
  "redacted": false,
  "evidence": [{"sha256": "9f86d0...", "retrieved_at": "2026-05-01T12:00:00Z", "collector": "dns"}]
}
```

`evidence/verify` re-hashes every stored artefact:

```json
{"total": 11, "verified": 11, "missing_raw": 0, "mismatched": [], "intact": true}
```

## Graph

```
GET /api/v1/cases/{id}/graph?min_confidence=0.5&types=DOMAIN&relationship_types=RESOLVES_TO
```

Returns `nodes`, `edges`, `stats` and `summary`, shaped for Cytoscape or React
Flow. Filtering edges never removes nodes, so a filter cannot invent isolation.

## Reports

```
GET /api/v1/cases/{id}/report?format=html|md|json
                            &max_classification=PUBLIC|PERSONAL|SENSITIVE|RESTRICTED
                            &min_confidence=0.5
```

`max_classification` withholds anything above the given level, so a report can
be circulated more widely than the case database; the report states how many
findings it withheld. Every claim cites the artefact behind it.

## Collectors

```
GET /api/v1/collectors
```

```json
[{
  "name": "search",
  "supported_targets": ["DOMAIN", "ORGANIZATION", "PERSON", "USERNAME", "EMAIL", "URL"],
  "requires_api_key": true,
  "rate_limit": "1/1s (concurrency 1)",
  "available": false,
  "unavailable_reason": "No search provider is configured. Set SEARCH_PROVIDER to anthropic_web_search, brave, bing or serper and supply the matching credential. Every free structured source and the manual search workflow keep working with no provider at all.",
  "configuration": {
    "required_settings": ["SEARCH_PROVIDER"],
    "optional_settings": [],
    "configured": false,
    "mode": "none",
    "detail": "No search provider is configured. Set SEARCH_PROVIDER to anthropic_web_search, brave, bing or serper and supply the matching credential. Every free structured source and the manual search workflow keep working with no provider at all."
  }
}]
```

A collector that cannot run says so here, and records a `SKIPPED` run during an
investigation, rather than silently producing nothing.

`configuration` names the settings a collector reads and whether they are
present. It carries setting *names* and status only — no endpoint on this
platform returns a credential value, or anything derived from one. Once a
provider is selected, `required_settings` names its specific key
(`["SEARCH_PROVIDER", "BRAVE_API_KEY"]`), so "search was skipped" can be
answered without guessing which of three keys was wanted.

## Target types

Types that identify themselves by shape are inferred: `DOMAIN`, `URL`, `IP`,
`EMAIL`, `REPOSITORY`, `SOCIAL_PROFILE` and `USERNAME`. Free text is not one of
those — "Timotheous Samar" and "Example Corporation" read identically — so it is
never guessed:

```
POST /api/v1/cases/{case_id}/targets   {"value": "Timotheous Samar"}
→ 422 {"code": "ambiguous_target_type",
       "detail": {"candidates": ["PERSON", "ORGANIZATION"]}}
```

Resend with `"type": "PERSON"` or `"type": "ORGANIZATION"`. The preview endpoint
reports the same state without erroring, so a UI can offer the choice:

```
POST /api/v1/cases/{case_id}/targets/preview   {"value": "Timotheous Samar"}
→ 200 {"ambiguous": true, "type": null,
       "candidates": ["PERSON", "ORGANIZATION"], "message": "…"}
```

A `PERSON` target schedules only name-appropriate collectors. No DNS, RDAP,
certificate-transparency or HTTP-metadata lookup is pointed at a personal name,
and each result is stored as its own candidate: records naming two different
people who share a name stay two entities, linked to the subject by
`POSSIBLY_SAME_ENTITY` at a ceiling far below the auto-merge threshold.

### Free person sources

Seven collectors run for a `PERSON` and none of them needs an API key, so a
person investigation costs nothing:

| Collector | Source | What a candidate is |
|---|---|---|
| `orcid` | ORCID public API | A researcher record carrying the name |
| `openalex` | OpenAlex (CC0) | An author record, often with an institution and ORCID iD |
| `crossref` | Crossref REST API | A publication crediting the name |
| `wikidata` | Wikidata / Wikipedia | An item about a *human* (P31=Q5) carrying the name |
| `github_people` | GitHub user search | A public account whose profile name matches |
| `reddit` | Reddit public search | A public account, where Reddit allows anonymous access |
| `person_usernames` | Platform profile pages | A profile that exists for a handle *you supplied* |

`search` is the only paid collector, and nothing depends on it. With
`SEARCH_PROVIDER=none` it is recorded `SKIPPED` with its reason and the free
sources still run.

### Person context

A `PERSON` target accepts optional `context` that narrows the judgement — never
the search:

```json
POST /api/v1/cases/{case_id}/targets
{
  "value": "Timotheous Samar",
  "type": "PERSON",
  "context": {
    "known_usernames": ["exampleuser"],
    "profile_urls": ["https://github.com/exampleuser"],
    "organizations": ["Example Ltd"],
    "schools": ["Example University"],
    "city": "Delft",
    "country": "Netherlands"
  }
}
```

Context is only ever used to corroborate or rule out a candidate a public source
already returned. It is never sent to a source as an extra search term, and
`person_usernames` checks only the handles it was given — it never derives one
from a name. Every candidate records `match_reasons`, `mismatch_reasons` and
`corroborated_by`, so the reasoning is auditable in the API, the graph and the
report. Corroboration raises a candidate's confidence but cannot reach the
auto-merge threshold: a candidate stays a candidate.

## Example: a full investigation with curl

```bash
API=http://localhost:8000/api/v1

CASE=$(curl -s -X POST $API/cases -H 'content-type: application/json' \
  -d '{"name":"Example Domain Investigation"}' | jq -r .id)

curl -s -X POST $API/cases/$CASE/targets -H 'content-type: application/json' \
  -d '{"value":"example.com"}' > /dev/null

curl -s -X POST $API/cases/$CASE/run -H 'content-type: application/json' -d '{}' | jq .dispatch

curl -s "$API/cases/$CASE/findings?min_confidence=0.8" | jq '.items[].title'
curl -s "$API/cases/$CASE/evidence/verify" | jq .intact
curl -s "$API/cases/$CASE/report?format=md" -o report.md
```
