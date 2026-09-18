# Collectors

A collector is a small adapter over one public data source. It declares what it
accepts, fetches through the shared HTTP client, and turns raw payloads into
findings.

## The contract

```python
@register_collector
class ExampleCollector(BaseCollector):
    name = "example"                       # registry key and rate-limit bucket
    version = "1.0.0"
    description = "What this collects, in one line."
    supported_targets = [TargetType.DOMAIN]
    requires_api_key = False
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=1)
    timeout = 20.0                         # per request
    run_timeout = 60.0                     # per whole run; None uses the global default
    retry = RetryPolicy(attempts=2, base_delay=1.0)
    default_confidence = 0.9
    source_attribution = "Example public API"

    def is_available(self) -> tuple[bool, str]:
        """Return (False, reason) when a credential is missing."""
        return True, ""

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        response = await http.get(url, provider=self.name, timeout=self.timeout)
        payload = RawPayload(source_url=url, content=response.json())
        result = CollectorResult()
        for draft in self.normalize(payload, target):
            result.add(draft, payload)
        return result

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        """Pure: fixture in, findings out. This is what the unit tests exercise."""
```

Two rules are structural rather than advisory:

1. **A collector never persists anything.** It returns drafts. The engine
   classifies them through the privacy filter, stores the evidence and writes
   the rows.
2. **A collector never suppresses its own failure.** It raises; the runner
   records the failure on the run row and the investigation continues with the
   collectors that did work.

Splitting `normalize()` out of `collect()` is what makes a collector testable
against a fixture with no network at all.

## Registration

`@register_collector` records the class in a permanent **catalogue** and puts it
in the live **registry** that investigations plan from. The split matters
because the decorator fires at module import, and a module imports only once
per process: without the catalogue, `load_builtin_collectors()` would be a
no-op on its second call and could never restore the registry after
`reset_registry()`.

`load_builtin_collectors()` is therefore idempotent and restorative — it
re-applies registration from the catalogue on every call — and it is invoked at
one deterministic point per entry point: FastAPI's startup lifespan, the CLI's
callback, and engine construction. Nothing registers collectors lazily in the
middle of serving a request; doing so once meant that rendering a report
changed which collectors the next investigation would plan.

## What the runner guarantees

- Each collector runs as its own task with its own run budget.
- Domain errors, unexpected exceptions and timeouts all become typed
  `RunOutcome` records — one failure never aborts an investigation.
- A missing credential produces `SKIPPED` with the reason, not a failure.
- Concurrency is bounded globally and per provider; every collector's declared
  rate limit is registered with the shared HTTP client.
- Cancellation is polled between collectors.

## Built-in collectors

| Name | Source | Key | Notes |
|---|---|---|---|
| `dns` | system resolvers | — | Concurrent per record type; a failure for one type is a note, not a lost run. |
| `rdap` | rdap.org | — | Detects privacy-protected registrations and records only *that* they are protected. |
| `http_meta` | the site itself | — | One page. robots.txt honoured; `Disallow` aborts before fetching. |
| `ctlog` | crt.sh | — | Hostnames scoped to the registrable domain. CT proves issuance, not liveness. |
| `wayback` | Internet Archive CDX | — | Bounded limit, collapsed timestamps. Never a bulk download. |
| `github` | api.github.com | optional | Public only. Commit *metadata*; no message bodies, no diffs. |
| `username` | curated platforms | — | Presence only; confidence capped at 0.60. |
| `search` | Anthropic web search / Brave / Bing / Serper | required | Provider APIs only — never engine HTML scraping. `google_wss` is configured but PENDING_PARTNER_ACCESS. See [search-provider-compliance.md](search-provider-compliance.md). |
| `email` | structure, Gravatar, HIBP | optional | Breach *names and dates* only. |

## The username platform list

An entry belongs there only if all four hold:

1. the profile page is served to anonymous visitors — no login, no bypass;
2. a documented, stable URL shape identifies a profile;
3. checking it takes one unauthenticated request;
4. it is a *publishing* platform (code, writing, discussion), not a private
   social network or a dating, health or financial service, where mere presence
   is itself sensitive information about a person.

The list is deliberately small. Breadth trades directly against politeness and
against becoming a profile-aggregation tool.

## Adding a collector

1. Create `app/collectors/<name>.py` and implement `BaseCollector`.
2. Decorate it with `@register_collector`.
3. Add its module name to `BUILTIN_MODULES` in `collectors/registry.py`.
4. Capture a fixture in `examples/fixtures/` and unit-test `normalize()`
   against it; mock the transport with `respx` for `collect()`.
5. If it needs a credential, add the setting to `Settings` and `.env.example`,
   and implement `is_available()` so the platform reports the gap rather than
   producing nothing.

Before adding a source, check its terms of service and what its data means for
the people it describes. A source that requires authentication you were not
granted, or that exposes private individuals, does not belong here.

## Rate limiting and politeness

Each collector declares a `RateLimit`; the shared client enforces it with a
token bucket and a concurrency semaphore per provider. Retries use exponential
backoff with full jitter and honour `Retry-After`, and only for transient
statuses (408, 425, 429, 5xx) and transport errors. A 4xx is never retried.
