"use client";

import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Input,
  Mono,
  Spinner,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import {
  EXPANDED_BY_DEFAULT,
  SEARCH_ENGINES,
  type EngineKey,
  evidenceClassLabel,
  familyLabel,
  capabilitySummary,
  IMAGE_ENGINES,
  isImageQuery,
  needsCorroboration,
  providerStatus,
  QUERIES_SHOWN,
  variantTone,
  imageEvidence,
  QUICK_ENGINES,
  searchUrl,
  socialResults,
  toImportPayload,
} from "@/lib/recon";
import type {
  ImportedResult,
  ReconQuery,
  ReconStage,
  SearchIngestResult,
  StagedReconPlan,
  Target,
} from "@/types/api";

const EMPTY_ROW = {
  url: "",
  title: "",
  displayName: "",
  handle: "",
  snippet: "",
  imageUrl: "",
  caption: "",
  notes: "",
};

/**
 * Manual search recon.
 *
 * The platform generates queries and accepts back whatever public results the
 * investigator chose to keep. It never submits a query and never scrapes a
 * result page — the "Open" links below go to the investigator's own browser
 * session, and everything that comes back is marked as theirs, not ours.
 */
export default function ReconPage() {
  const caseId = useCaseId();
  const targets = useAsync(() => api.listTargets(caseId, { type: "PERSON" }), [caseId]);
  const people = targets.data?.items ?? [];
  const [selected, setSelected] = useState<string>("");
  const targetId = selected || people[0]?.id || "";

  const plan = useAsync(
    () => (targetId ? api.reconPlan(caseId, targetId) : Promise.resolve(null)),
    [caseId, targetId],
  );
  const [searching, setSearching] = useState(false);
  const [ingest, setIngest] = useState<SearchIngestResult | null>(null);
  const results = useAsync(() => api.listReconResults(caseId), [caseId]);

  const [engine, setEngine] = useState<EngineKey>("Google");
  const [activeQuery, setActiveQuery] = useState("");
  const [row, setRow] = useState(EMPTY_ROW);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const imported = results.data ?? [];
  const images = imageEvidence(imported);
  const social = socialResults(imported);

  async function runSearch() {
    if (!targetId) return;
    setSearching(true);
    setError(null);
    try {
      setIngest(await api.runProviderSearch(caseId, targetId));
      results.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSearching(false);
    }
  }

  async function importResult(event: React.FormEvent) {
    event.preventDefault();
    const payload = toImportPayload(activeQuery, engine, row);
    if (!payload || !targetId) return;
    setBusy(true);
    setError(null);
    try {
      await api.importReconResults(caseId, targetId, [payload]);
      setRow(EMPTY_ROW);
      results.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  if (targets.loading) return <Spinner />;
  if (people.length === 0) {
    return (
      <Card>
        <CardHeader title="Reconnaissance" />
        <div className="p-4">
          <Empty
            title="No PERSON target in this case"
            hint="Add one on the Targets tab to generate reconnaissance queries."
          />
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Reconnaissance queries"
          description="Run these in your own browser, then import what is relevant"
        />
        {plan.data ? <ProviderBanner plan={plan.data} busy={searching} onRun={runSearch} /> : null}
        {plan.data && plan.data.variants.length > 0 ? (
          <div className="border-b border-line px-4 py-3">
            <h3 className="text-xs font-medium">Name variants</h3>
            <p className="mt-0.5 text-[11px] text-muted">
              Canonical: <strong className="text-fg">{plan.data.canonical}</strong>. Public
              sources may publish any of the spellings below; each is searched, and a hit on a
              shorter one is a lead rather than a match. The name under investigation never
              changes.
            </p>
            <ul className="mt-2 space-y-1">
              {plan.data.variants.map((variant) => (
                <li key={variant.search_variant} className="flex flex-wrap items-center gap-2">
                  <Mono>{variant.search_variant}</Mono>
                  <Badge tone={variantTone(variant.variant_type)}>{variant.variant_label}</Badge>
                  {needsCorroboration(variant.variant_type) ? (
                    <Badge tone="SKIPPED">needs corroboration</Badge>
                  ) : null}
                  <span className="basis-full text-[11px] text-muted">
                    {variant.variant_generation_reason}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {plan.data && plan.data.discovered_anchors.length > 0 ? (
          <div className="border-b border-line px-4 py-3">
            <h3 className="text-xs font-medium">Anchors the investigation discovered</h3>
            <p className="mt-0.5 text-[11px] text-muted">
              Claims a public source actually published, each with the page that published it.
              Nothing here is inferred.
            </p>
            <ul className="mt-2 space-y-1">
              {plan.data.discovered_anchors.map((anchor) => (
                <li key={`${anchor.kind}:${anchor.value}`} className="text-xs">
                  <Badge>{anchor.kind}</Badge> {anchor.value}{" "}
                  <a
                    href={anchor.source_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-accent underline"
                  >
                    source
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {ingest ? <IngestSummary ingest={ingest} /> : null}
        {plan.data && plan.data.also_known_as.length > 0 ? (
          <p className="border-b border-line px-4 pt-3 text-xs text-muted">
            A public profile in this case declares{" "}
            <strong className="text-fg">{plan.data.also_known_as.join(", ")}</strong>. Searches for
            the fuller name are included below. The name under investigation is unchanged.
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
          <select
            aria-label="Person"
            value={targetId}
            onChange={(event) => setSelected(event.target.value)}
            className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
          >
            {people.map((person: Target) => (
              <option key={person.id} value={person.id}>
                {String(person.attributes?.["display_name"] ?? person.normalized_value)}
              </option>
            ))}
          </select>
          <select
            aria-label="Search engine"
            value={engine}
            onChange={(event) => setEngine(event.target.value as EngineKey)}
            className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
          >
            {SEARCH_ENGINES.map((item) => (
              <option key={item.key} value={item.key}>
                {item.key}
              </option>
            ))}
          </select>
        </div>

        {plan.data ? (
          <>
            <p className="px-4 pt-3 text-xs text-muted">{plan.data.execution}</p>
            {plan.data.anchors_used.length > 0 ? (
              <p className="px-4 pt-1 text-xs text-muted">
                Built from your anchors: {plan.data.anchors_used.join(" · ")}
              </p>
            ) : (
              <p className="px-4 pt-1 text-xs text-muted">
                No anchors supplied. Adding known usernames, an organisation or an ORCID on
                the target produces far narrower queries.
              </p>
            )}
            <div className="space-y-2 p-4">
              {plan.data.stages
                .filter((stage) => stage.queries.length > 0)
                .map((stage) => (
                  <StageGroup
                    key={stage.stage}
                    stage={stage}
                    engine={engine}
                    onImport={setActiveQuery}
                  />
                ))}
            </div>
          </>
        ) : (
          <Spinner />
        )}
      </Card>

      {plan.data && plan.data.capabilities.length > 0 ? (
        <Card>
          <CardHeader
            title="What this tool can and cannot check"
            description="A platform that refuses automated access is a boundary respected, not a gap"
          />
          <ul className="divide-y divide-line">
            {plan.data.capabilities.map((platform) => (
              <li key={platform.platform} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <span className="text-xs font-medium">{platform.display_name}</span>
                <Badge tone={platform.handle_check_supported ? "SUCCESS" : "SKIPPED"}>
                  {platform.handle_check_supported ? "checked directly" : "manual search"}
                </Badge>
                {platform.public_api_available ? <Badge tone="PARTIAL">public API</Badge> : null}
                {platform.image_reference_supported ? <Badge>profile image</Badge> : null}
                <span className="basis-full text-[11px] text-muted">
                  {capabilitySummary(platform)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="Import a public result"
          description="Paste what you found. It is recorded as yours, not as something the platform fetched"
        />
        <form onSubmit={importResult} className="space-y-2 p-4">
          <label className="block text-xs">
            <span className="text-muted">Query that produced it</span>
            <Input
              aria-label="Query"
              value={activeQuery}
              onChange={(event) => setActiveQuery(event.target.value)}
              placeholder="Pick a query above, or type the one you ran"
              className="mt-0.5"
            />
          </label>
          <div className="grid gap-2 sm:grid-cols-2">
            {(
              [
                ["url", "Result URL", "https://www.linkedin.com/in/example"],
                ["title", "Title", "Example Person — LinkedIn"],
                ["displayName", "Displayed name (optional)", "Example Person"],
                ["handle", "Handle (optional)", "example-person"],
                ["snippet", "Snippet", "Researcher at Example University"],
                ["imageUrl", "Image URL (optional)", "https://example.org/photo.jpg"],
                ["caption", "Caption (optional)", "Speakers at the 2024 conference"],
                ["notes", "Your notes (optional)", "Same employer as the anchor"],
              ] as const
            ).map(([field, label, placeholder]) => (
              <label key={field} className="block text-xs">
                <span className="text-muted">{label}</span>
                <Input
                  aria-label={label}
                  value={row[field]}
                  placeholder={placeholder}
                  onChange={(event) => setRow({ ...row, [field]: event.target.value })}
                  className="mt-0.5"
                />
              </label>
            ))}
          </div>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || !row.url.trim() || !activeQuery.trim()}
          >
            {busy ? "Importing…" : "Import result"}
          </Button>
          {error ? <ErrorNotice error={error} /> : null}
        </form>
      </Card>

      <Card>
        <CardHeader
          title="Social profiles"
          description="Public profile pages found so far. A URL is not an identification"
        />
        {social.length === 0 ? (
          <div className="p-4">
            <Empty title="No social profiles yet" hint="Import a profile URL above." />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {social.map((result) => (
              <ResultRow key={result.id} result={result} />
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Images / visual evidence"
          description="Context evidence only — no facial recognition is performed, ever"
        />
        {images.length === 0 ? (
          <div className="p-4">
            <Empty
              title="No image evidence yet"
              hint="Import a result with an image URL to record it here."
            />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {images.map((result) => (
              <li key={result.id} className="space-y-1 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{result.title}</span>
                  <Badge tone="SKIPPED">context evidence</Badge>
                </div>
                {result.caption ? <p className="text-xs">{result.caption}</p> : null}
                <p className="text-xs text-muted">
                  Appears on{" "}
                  <a
                    href={result.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="underline"
                  >
                    {result.url}
                  </a>
                  , found by searching <Mono>{result.query}</Mono>.
                </p>
                <p className="text-xs text-muted">
                  The platform performs no facial or biometric analysis and makes no claim
                  that anyone depicted is the subject. This records where a picture appears,
                  not who is in it.
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader title="All imported results" />
        {results.loading ? (
          <Spinner />
        ) : imported.length === 0 ? (
          <div className="p-4">
            <Empty title="Nothing imported yet" hint="Run a query above and import a result." />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {imported.map((result) => (
              <ResultRow key={result.id} result={result} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/**
 * What the public-web channel can do right now.
 *
 * The distinction this banner exists to make: "no provider configured" is not
 * "nothing found". An investigator must be able to see which one they are
 * looking at before concluding anything from an empty result list.
 */
function ProviderBanner({
  plan,
  busy,
  onRun,
}: {
  plan: StagedReconPlan;
  busy: boolean;
  onRun: () => void;
}) {
  const status = providerStatus(plan);
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
      <Badge tone={status.tone}>{status.label}</Badge>
      <span className="basis-full text-[11px] text-muted sm:basis-auto sm:flex-1">
        {status.detail}
      </span>
      {plan.search_provider_configured ? (
        <Button variant="primary" onClick={onRun} disabled={busy}>
          {busy ? "Searching…" : "Search the public web"}
        </Button>
      ) : null}
    </div>
  );
}

/** What one automated ingestion run did, in the investigator's terms. */
function IngestSummary({ ingest }: { ingest: SearchIngestResult }) {
  if (!ingest.configured) {
    return (
      <div className="border-b border-line px-4 py-2 text-xs text-muted">
        {ingest.reason || "No search provider is configured, so nothing was searched."}
      </div>
    );
  }
  return (
    <div className="border-b border-line px-4 py-2 text-xs">
      <p>
        {ingest.queries_run} query(ies) through {ingest.provider}: {ingest.results_stored} new
        result(s) stored, {ingest.duplicates} already known, {ingest.rejected_urls} rejected as
        unsafe or irrelevant.
      </p>
      {ingest.results_stored === 0 && ingest.failures.length === 0 ? (
        <p className="mt-0.5 text-muted">
          The provider searched and returned nothing usable. That is an absence in this
          provider&apos;s index — not a finding about the subject.
        </p>
      ) : null}
      {ingest.failures.length > 0 ? (
        <p className="mt-0.5 text-danger">
          {ingest.failures.length} query(ies) failed. A failure is a gap in coverage, not a
          negative result.
        </p>
      ) : null}
    </div>
  );
}

/**
 * One stage of the plan.
 *
 * Staged rather than flat because an investigator works outward — the name, the
 * spellings, what they know, what was discovered, then the sweeps — and a single
 * list of forty queries is a wall rather than a workflow. Only the first few
 * queries show until asked, for the same reason.
 */
function StageGroup({
  stage,
  engine,
  onImport,
}: {
  stage: ReconStage;
  engine: EngineKey;
  onImport: (query: string) => void;
}) {
  const [open, setOpen] = useState(stage.stage <= 3);
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? stage.queries : stage.queries.slice(0, QUERIES_SHOWN);

  return (
    <section className="rounded-md border border-line">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex items-center gap-1.5 text-xs font-medium"
        >
          <span aria-hidden="true" className="text-muted">
            {open ? "▾" : "▸"}
          </span>
          Stage {stage.stage} — {stage.title}
          <span className="text-muted">({stage.queries.length})</span>
        </button>
        {open && stage.queries.length > 1 ? (
          <span className="ml-auto">
            <OpenAll queries={stage.queries} engine={engine} />
          </span>
        ) : null}
      </div>
      {open ? (
        <>
          <p className="px-3 pb-2 text-[11px] text-muted">{stage.purpose}</p>
          <ul className="space-y-1 px-3 pb-3">
            {shown.map((query) => (
              <li
                key={query.query}
                className="flex flex-wrap items-center gap-2 rounded-md border border-line px-3 py-2"
              >
                <Mono>{query.query}</Mono>
                <Badge>{familyLabel(query.family)}</Badge>
                {query.variant_type !== "EXACT_NAME" ? (
                  <Badge tone={variantTone(query.variant_type)}>{query.variant_label}</Badge>
                ) : null}
                {query.anchors_used.map((anchor) => (
                  <Badge key={anchor} tone="SUCCESS">
                    {anchor}
                  </Badge>
                ))}
                <span className="basis-full text-xs text-muted">{query.rationale}</span>
                <span className="basis-full text-[11px] text-muted">
                  Spelling searched: {query.name_variant}
                </span>
                <div className="flex flex-wrap gap-2">
                  {(isImageQuery(query.family) ? IMAGE_ENGINES : QUICK_ENGINES).map((name) => (
                    <a
                      key={name}
                      href={searchUrl(query.query, name)}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="rounded-md border border-line px-2 py-1 text-xs hover:bg-line"
                    >
                      {name}
                    </a>
                  ))}
                  <Button onClick={() => onImport(query.query)}>Import a result</Button>
                </div>
              </li>
            ))}
          </ul>
          {stage.queries.length > shown.length ? (
            <button
              type="button"
              onClick={() => setShowAll(true)}
              className="mx-3 mb-3 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-line"
            >
              Show all {stage.queries.length}
            </button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function OpenAll({ queries, engine }: { queries: ReconQuery[]; engine: EngineKey }) {
  return (
    <button
      type="button"
      onClick={() => {
        // Capped: a browser blocks a burst of popups, and thirty tabs is not a
        // workflow. Each opens the investigator's own search session.
        for (const query of queries.slice(0, 8)) {
          window.open(searchUrl(query.query, engine), "_blank", "noreferrer,noopener");
        }
      }}
      className="rounded-md border border-line px-2 py-0.5 text-[11px] text-muted hover:bg-line"
      title={`Opens up to 8 tabs in your own browser. Nothing is submitted by the platform.`}
    >
      Open all in {engine}
    </button>
  );
}

function ResultRow({ result }: { result: ImportedResult }) {
  return (
    <li className="space-y-1 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{result.title || result.url}</span>
        {result.platform_label ? <Badge>{result.platform_label}</Badge> : null}
        {result.url_kind ? <Badge>{result.url_kind}</Badge> : null}
        <Badge tone="SKIPPED">{evidenceClassLabel(result.evidence_class)}</Badge>
      </div>
      <a
        href={result.url}
        target="_blank"
        rel="noreferrer noopener"
        className="block text-xs text-muted underline"
      >
        {result.url}
      </a>
      {result.snippet ? <p className="text-xs">{result.snippet}</p> : null}
      <p className="text-xs text-muted">
        Found by searching <Mono>{result.query}</Mono> in {result.engine}
        {result.handle ? (
          <>
            {" "}
            · handle <Mono>{result.handle}</Mono>
          </>
        ) : null}
      </p>
      {result.evidence_sha256.length > 0 ? (
        <p className="text-xs text-muted">
          Stored artefact <Mono>{result.evidence_sha256[0]?.slice(0, 12)}</Mono>
        </p>
      ) : null}
    </li>
  );
}
