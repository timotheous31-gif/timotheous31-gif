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
  "supported_targets": ["DOMAIN", "ORGANIZATION", "USERNAME", "EMAIL", "URL"],
  "requires_api_key": true,
  "rate_limit": "1/1s (concurrency 1)",
  "available": false,
  "unavailable_reason": "No search provider is configured. Set SEARCH_PROVIDER to brave, bing or serper and supply the matching API key."
}]
```

A collector that cannot run says so here, and records a `SKIPPED` run during an
investigation, rather than silently producing nothing.

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
