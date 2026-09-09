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
  familyPurpose,
  imageEvidence,
  orderedGroups,
  QUICK_ENGINES,
  searchUrl,
  socialResults,
  toImportPayload,
} from "@/lib/recon";
import type { ImportedResult, ReconQuery, Target } from "@/types/api";

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
    () => (targetId ? api.reconQueries(caseId, targetId) : Promise.resolve(null)),
    [caseId, targetId],
  );
  const results = useAsync(() => api.listReconResults(caseId), [caseId]);

  const [engine, setEngine] = useState<EngineKey>("Google");
  const [activeQuery, setActiveQuery] = useState("");
  const [row, setRow] = useState(EMPTY_ROW);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const imported = results.data ?? [];
  const images = imageEvidence(imported);
  const social = socialResults(imported);

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
              {orderedGroups(plan.data.queries).map(([family, queries]) => (
                <QueryGroup
                  key={family}
                  family={family}
                  queries={queries}
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
 * One collapsible family of queries.
 *
 * The page used to be a single vertical wall of every query at once, which is
 * unreadable and buries the anchored ones that actually matter. Families are
 * ordered narrowest-first and the broad ones start closed.
 */
function QueryGroup({
  family,
  queries,
  engine,
  onImport,
}: {
  family: string;
  queries: ReconQuery[];
  engine: EngineKey;
  onImport: (query: string) => void;
}) {
  const [open, setOpen] = useState(EXPANDED_BY_DEFAULT.has(family));

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
          {familyLabel(family)}
          <span className="text-muted">({queries.length})</span>
        </button>
        {open && queries.length > 1 ? (
          // Opens one browser tab per query, in the investigator's own session.
          <span className="ml-auto flex gap-1">
            {queries.slice(0, 8).map((query, index) => (
              <a
                key={query.query}
                href={searchUrl(query.query, engine)}
                target="_blank"
                rel="noreferrer noopener"
                className="sr-only"
              >
                Open query {index + 1}
              </a>
            ))}
            <OpenAll queries={queries} engine={engine} />
          </span>
        ) : null}
      </div>
      {open ? (
        <>
          <p className="px-3 pb-2 text-[11px] text-muted">{familyPurpose(family)}</p>
          <ul className="space-y-1 px-3 pb-3">
            {queries.map((query) => (
              <li
                key={query.query}
                className="flex flex-wrap items-center gap-2 rounded-md border border-line px-3 py-2"
              >
                <Mono>{query.query}</Mono>
                {query.anchors_used.map((anchor) => (
                  <Badge key={anchor} tone="SUCCESS">
                    {anchor}
                  </Badge>
                ))}
                <span className="basis-full text-xs text-muted">{query.rationale}</span>
                <div className="flex flex-wrap gap-2">
                  {QUICK_ENGINES.map((name) => (
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
