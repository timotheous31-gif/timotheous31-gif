# Search-provider compliance record

One page per search channel, recording what the provider's own documentation
establishes — and, just as carefully, what it does not.

The rule this document exists to enforce: **no right is claimed that the
documentation did not establish.** Where a question matters to a commercial
investigation product and the answer is not documented, the entry says
`PENDING LEGAL/TERMS REVIEW` and stays that way until somebody qualified reads
the contract. An unresolved term recorded as unresolved is a manageable risk; an
unresolved term quietly assumed in our favour is not.

Nothing in this file is legal advice. It is a record of what was read, when, and
what was left open.

---

## Summary

| Channel | Key | State | Commercial storage / caching / redistribution |
| --- | --- | --- | --- |
| No provider | `none` | Active, default | Not applicable — nothing is retrieved |
| Anthropic web search | `anthropic_web_search` | Active, **secondary** | **PENDING LEGAL/TERMS REVIEW** |
| Google Web Search Service | `google_wss` | **PENDING_PARTNER_ACCESS** | **PENDING LEGAL/TERMS REVIEW** |
| Brave Search API | `brave` | Active | Not reviewed in this pass |
| Bing Web Search API | `bing` | Active | Not reviewed in this pass |
| Serper | `serper` | Active | Not reviewed in this pass |

The last three predate this document. They are listed so the table is not
mistaken for a complete review, and they are marked as what they are: not
reviewed. Treat that as an open item, not as approval.

---

## `none` — no search provider

The default, and the one the platform is designed to remain useful under. Every
free structured source (ORCID, OpenAlex, Crossref, Wikidata, GitHub, Reddit,
certificate transparency, RDAP, DNS, Wayback) runs, and the recon plan is
generated for the investigator to run in their own browser.

No credential, no cost, no terms to review. This mode must keep working: a
regression test asserts that a PERSON investigation under `SEARCH_PROVIDER=none`
still produces findings, and that no Anthropic or Google credential is required
to reach it.

---

## `anthropic_web_search` — Anthropic Claude Platform web search

### What is used

- **Tool version:** `web_search_20250305` — the basic version.
- **`allowed_callers`:** `["direct"]`, set explicitly.
- **`max_uses`:** from `ANTHROPIC_WEB_SEARCH_MAX_USES` (default 4).
- **Endpoint:** `POST /v1/messages` with `anthropic-version: 2023-06-01` and
  `x-api-key`. No beta header — the tool reference lists the beta-header column
  for all three web-search versions as `None`.

### Why that version and that configuration

Three versions exist: `web_search_20250305` (basic), `web_search_20260209`
(adds dynamic filtering) and `web_search_20260318` (adds response-inclusion
control). The basic version is pinned deliberately.

**Dynamic filtering runs the search from inside code execution.** On
`web_search_20260209` and later, `allowed_callers` defaults to
`["code_execution_20260120"]`, and model-written code filters the results before
they reach the response. For an investigation platform that is a silent loss of
evidence: results this platform would have stored are discarded by code nobody
reviewed, and the coverage table would report a filtered result set as if it
were the whole one.

**`response_inclusion: "excluded"` removes the blocks entirely.** That is
`web_search_20260318`'s reason to exist, and it is the opposite of what this
platform needs. It is never configured.

**The basic version is the zero-data-retention-eligible one.** Anthropic
documents the `_20260209`+ versions as not ZDR-eligible by default because they
depend on code execution, with eligibility restored by setting
`allowed_callers: ["direct"]`. Pinning the basic version and *also* setting
`["direct"]` explicitly means a future version bump cannot quietly move the
channel into code-execution filtering.

All three versions accept `allowed_callers`, so setting it on the basic version
is supported and is not a no-op in intent: it states the requirement in the
request rather than relying on a default.

### What the results contain

A `web_search_result` carries exactly four fields: `url`, `title`, `page_age`,
`encrypted_content`.

There is **no snippet, no rank, no displayed source label, no result-type
classification and no image URL.** The platform stores `snippet` as `null` for
this channel — distinguishable from an empty description — and never invents one.
`provider_position` is left unset and `position_is_rank` is `false`, because
Anthropic documents no ordering semantics and calling a list index a rank would
be inventing a relevance signal.

`encrypted_content` is **never persisted.** It is opaque, it exists only to be
replayed verbatim on a following turn of the same conversation, and it is held in
memory exactly long enough to continue a `pause_turn`. A regression test asserts
it reaches no finding, no evidence artefact and no report.

Claude's prose answer and its citations are **never evidence.** Only
`web_search_tool_result` blocks are read. Evidence in this platform points at a
public source URL; a model's summary of a page is not the page.

### What leaves the platform

Enabling this channel sends data to Anthropic, and it is worth being precise about
which data, because "a search provider" understates it: the request carries a
short brief rather than a single query string.

**Sent:** the subject's name and its generated spellings, and the anchor values the
investigator supplied (an employer, a school, a city, a username) — because those
are what the recon queries are built from, and a search for a name plus an employer
is the search. Every query in the brief has already passed the platform's own
policy screen (`app.services.recon.is_permitted`), so a query the platform would
refuse to generate is not sent either. Plus a fixed system prompt that forbids
summarising, forbids inferring nationality, citizenship or residence, and forbids
searching for home addresses, personal phone numbers and personal email addresses.

**Not sent:** case notes, analyst decisions, findings, stored evidence, images, any
other target in the case, any credential other than the Anthropic key itself, and
any investigator identity — no IP, no account, no user agent beyond the platform's
own. Localisation is the coarse `user_location` object and nothing else.

This is the same class of disclosure an investigator makes by typing a name and an
employer into a search engine, and it is not smaller than that. An engagement that
cannot send a subject's name and anchors to a third party should run with
`SEARCH_PROVIDER=none`.

### Determinism

There is no parameter that submits an exact query. Anthropic documents triggering
as steerable through the system prompt, with `max_uses` as the only hard
constraint. The platform therefore sends the recon plan as a brief, records the
query Anthropic actually executed from `server_tool_use.input.query`, and keeps
the planned queries in a separate field. A planned query is never recorded as an
executed one.

### Retrieval channel vs claim origin

Anthropic does not disclose which search index answered a query. The channel is
recorded as `retrieval_channel = anthropic_web_search` and **never** as Google,
Bing or any other named engine. Each result's `claim_origin` is the public URL it
points at.

`anthropic_web_search` is registered as a **relay source** in
`app/correlation/lineage.py`. Two pages reached through it can never amplify each
other's confidence as independent corroboration: a second discovery route is not
a second party.

### Cost and budget

Documented price, verbatim:

> Web search is available on the Claude API for **$10 per 1,000 searches**, plus
> standard token costs for search-generated content.

and:

> Each web search counts as one use, regardless of the number of results
> returned. If an error occurs during web search, the web search will not be
> billed.

Retrieved results are billed as **input tokens**, both within the turn and on
subsequent turns of the same conversation. The platform's cost figure covers the
search tool only and says so everywhere it appears; the token half is named as
excluded rather than omitted. No figure is presented as an invoice.

`max_uses` is the spend cap. Exceeding it returns a `max_uses_exceeded`
tool-result error and Anthropic does not bill it.

### Rate limits

Anthropic publishes no numeric web-search rate limit. The documentation directs
the operator to the Rate limits page in the Claude Console, and notes that the
Batches API throttles web search per organisation. This is an **external
dependency**: the platform cannot discover the limit and does not guess one.

### Errors arrive inside HTTP 200

> When the web search tool encounters an error (such as hitting rate limits), the
> Claude API still returns a 200 (success) response.

Documented codes: `too_many_requests`, `invalid_tool_input`, `max_uses_exceeded`,
`query_too_long`, `request_too_large`, `unavailable`. Also documented, and
load-bearing for this platform:

> A search that succeeds but matches no results returns an empty `content` list,
> not an error.

So an empty list is a real absence in the index and an error object is not. The
platform maps the error codes onto `SKIPPED` / `PARTIAL` / `FAILED` and a
budget-exhausted state, and **never** onto `NO_MATCH_RETURNED`.

### Organisation-level settings — external dependency

> Web search is enabled for your organization unless an administrator has
> disabled it in the Claude Console, where they can also restrict which domains
> it searches. If it's disabled, a request that includes the tool fails with a
> 400 `invalid_request_error`.

Two things therefore sit outside this repository and outside the platform's
control: whether web search is enabled at all, and an organisation-level domain
allow-list that any request-level `allowed_domains` must be a **subset** of. A
deployment whose searches return unexpectedly little should check the Console
before concluding anything about the subject.

### Domain control

`allowed_domains` and `blocked_domains` are mutually exclusive — a request
carrying both is rejected with a 400. The platform refuses the configuration in
`is_available()` and raises again at request-build time, so no request can carry
both even if a caller bypassed the readiness check. Entries are bare domains with
an optional path and no scheme.

### Localisation

Only the documented `user_location` object: `type: "approximate"` plus any of
`city`, `region`, `country` (ISO 3166-1 alpha-2) and `timezone` (IANA). Values
come from settings and from nowhere else. **No investigator IP is derived,
transmitted or stored**, nothing narrower than a city is representable, and the
locality that was sent is recorded in the run's stats rather than held hidden.

### Citation display — the one documented obligation

Quoted verbatim, because it is the only constraint the documentation states about
what may be done with the output:

> When displaying API outputs directly to end users, citations must be included
> to the original source. If you are making modifications to API outputs,
> including by reprocessing or combining them with your own material before
> displaying them to end users, display citations as appropriate based on
> consultation with your legal team.

How the platform stands against it: every stored result keeps its source `url`
and `title`, every finding carries `source_url`, and every report section that
shows a result shows the URL it came from. The platform also reprocesses output —
it correlates results against anchors and combines them with its own collectors'
findings — which is precisely the second sentence's case, and the second sentence
directs that to counsel.

### Commercial storage, caching and redistribution

**PENDING LEGAL/TERMS REVIEW.**

Anthropic's web-search documentation does not address whether search results may
be stored, cached, displayed or redistributed in a commercial investigation
product. The only stated constraint is the citation-display requirement quoted
above.

`https://www.anthropic.com/legal/commercial-terms` **could not be read** during
this investigation: the environment's egress proxy blocked it
(`EGRESS_BLOCKED`). Nothing has been inferred from it.

Consequences, which are deliberate:

- This channel is implemented as a **secondary** provider. `none` remains the
  default and the free structured sources remain the primary path.
- Nothing in the codebase asserts a right to store, cache or redistribute these
  results. This entry is the record that the question is open.
- Before enabling `anthropic_web_search` for commercial work, read the commercial
  terms and the usage policy and replace this section with what they say.

### Retention and privacy

- Basic `web_search_20250305` is documented as ZDR-eligible and HIPAA-eligible.
- `_20260209` and `_20260318` are not ZDR-eligible by default (they depend on
  code execution); `allowed_callers: ["direct"]` restores eligibility. The
  platform pins the basic version *and* sets `["direct"]`.
- Independently of any retention arrangement, content flagged by safety systems
  may be retained for up to 2 years.
- No end-user or investigator IP is sent. See *Localisation*.

### Anthropic's `web_fetch` tool — deliberately not used

Anthropic also offers a server-side `web_fetch` tool, which is free of tool
charges and would recover the page text this channel does not return. It is
**not used**, and that is a decision rather than an omission: its commercial,
storage and security behaviour has not been separately audited, and it would put
a second fetch path outside the platform's SSRF guard, redirect validation, byte
ceiling and robots handling.

Where page text is needed, the platform fetches the page itself through
`app.core.http` — the one guarded client every collector uses — capped at
`MAX_RESULT_ENRICHMENTS_PER_INVESTIGATION` pages per investigation. See
`app/services/enrichment.py`.

### Documentation read for this entry

- Web search tool — `https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool`
- Tool reference — `https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference`
- Server tools, including ZDR and `allowed_callers`
- Web fetch tool
- API and data retention

Read on 2026-09-12. Re-read before relying on any quotation above.

---

## `google_wss` — Google Web Search Service

**State: `PENDING_PARTNER_ACCESS`.** Enforced in code, not left to a comment:
`is_available()` returns `False` whatever credentials are present, and both entry
points raise or report unavailable rather than attempting a call.

### Blockers

1. Partner credentials (`GOOGLE_WSS_API_KEY`, `GOOGLE_WSS_CLIENT_ID`) have not
   been issued.
2. The service endpoint is not published, so `GOOGLE_WSS_ENDPOINT` has no
   documented value to default to.
3. The request and response schemas are under partner agreement and are not
   public, so the result mapping cannot be finalised. `normalise()` in
   `app/services/providers/google_wss.py` isolates the single function that has
   to change once they are known.
4. Commercial terms for storing and displaying results in an investigation
   product have not been reviewed for this service. **PENDING LEGAL/TERMS
   REVIEW.**

### What is explicitly not done

- **No live request is attempted** without credentials.
- **No faked access.** There are no stubbed results and no sample data presented
  as a live index. An unavailable channel reports itself unavailable and the
  coverage table says so.
- **No Google HTML scraping, ever.** Parsing Google's result pages violates its
  terms and is out of scope for this platform. There is no fallback path from
  this provider to a scrape and there must never be one.

---

## Standing rules for adding a channel

1. Register it in `app/services/providers/` and in
   `app/correlation/lineage.py`'s `RELAY_SOURCES`. A retrieval channel
   originates nothing, and a new provider must not be able to create
   corroboration that did not exist.
2. Declare `runs_requested_query`, `supplies_snippet` and `position_is_rank`
   truthfully. The ingestion layer reads them; a false declaration there becomes
   a false statement in a report.
3. Never fill an absent field. `None` means the provider publishes no such field.
4. Add an entry to this document before the provider can be activated, including
   an explicit answer — or an explicit `PENDING LEGAL/TERMS REVIEW` — for
   storage, caching, display and redistribution.
5. All fetching goes through `app.core.http`. No second HTTP path.
