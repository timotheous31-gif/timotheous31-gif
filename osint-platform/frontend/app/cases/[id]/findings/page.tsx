"use client";

import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  Empty,
  ErrorNotice,
  Input,
  Mono,
  Select,
  Spinner,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { confidenceBand, formatConfidence, formatDate, humanise } from "@/lib/format";
import type { Classification, Finding } from "@/types/api";

const CLASSIFICATIONS: Classification[] = ["PUBLIC", "PERSONAL", "SENSITIVE", "RESTRICTED"];

export default function FindingsPage() {
  const caseId = useCaseId();
  const [kind, setKind] = useState("");
  const [classification, setClassification] = useState("");
  const [minConfidence, setMinConfidence] = useState(0);

  const findings = useAsync(
    () =>
      api.listFindings(caseId, {
        kind: kind || undefined,
        classification: classification || undefined,
        min_confidence: minConfidence || undefined,
        limit: 500,
      }),
    [caseId, kind, classification, minConfidence],
  );

  const kinds = Array.from(new Set(findings.data?.items.map((item) => item.kind) ?? [])).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Filter by kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {kinds.map((item) => (
            <option key={item} value={item}>
              {humanise(item)}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by classification"
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
        >
          <option value="">All classifications</option>
          {CLASSIFICATIONS.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-2 text-xs text-muted">
          Minimum confidence
          <Input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minConfidence}
            onChange={(e) => setMinConfidence(Number(e.target.value))}
            className="w-40"
            aria-label="Minimum confidence"
          />
          <span className="tabular-nums">{minConfidence.toFixed(2)}</span>
        </label>
        <span className="ml-auto text-xs text-muted">
          {findings.data ? `${findings.data.total} finding(s)` : ""}
        </span>
      </div>

      {findings.error ? <ErrorNotice error={findings.error} retry={findings.reload} /> : null}

      {findings.loading ? (
        <Spinner label="Loading findings" />
      ) : findings.data && findings.data.items.length > 0 ? (
        <ul className="space-y-3">
          {findings.data.items.map((finding) => (
            <li key={finding.id}>
              <FindingCard finding={finding} />
            </li>
          ))}
        </ul>
      ) : (
        <Empty
          title="No findings match"
          hint="Loosen the filters, or run the investigation to collect data."
        />
      )}
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false);
  const band = confidenceBand(finding.confidence);

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-sm font-medium">{finding.title}</h3>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={band} title={`Confidence ${formatConfidence(finding.confidence)}`}>
            {formatConfidence(finding.confidence)}
          </Badge>
          <Badge tone={finding.classification}>{finding.classification}</Badge>
          <Badge>{humanise(finding.kind)}</Badge>
        </div>
      </div>

      {finding.summary ? <p className="mt-1.5 text-sm text-muted">{finding.summary}</p> : null}

      <p className="mt-2 text-xs text-muted">
        Collector <Mono>{finding.collector}</Mono>
        {finding.observed_at ? ` · observed ${formatDate(finding.observed_at)}` : null}
        {finding.redacted ? " · some values were redacted before storage" : null}
      </p>

      {finding.confidence_reasons.length > 0 ? (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted">
          {finding.confidence_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 border-t border-line pt-2 text-xs text-muted">
        {finding.source_url ? (
          <p className="truncate">
            Source:{" "}
            <a
              href={finding.source_url}
              rel="noreferrer nofollow noopener"
              target="_blank"
              className="text-accent hover:underline"
            >
              {finding.source_url}
            </a>
          </p>
        ) : null}
        {finding.evidence.length > 0 ? (
          <p className="mt-1">
            Evidence:{" "}
            {finding.evidence.map((item) => (
              <Mono key={item.id} className="mr-2" title={item.sha256}>
                sha256:{item.sha256.slice(0, 12)}
              </Mono>
            ))}
          </p>
        ) : (
          <p className="mt-1 italic">No stored artefact is attached to this finding.</p>
        )}
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mt-2 text-accent hover:underline"
          aria-expanded={open}
        >
          {open ? "Hide data" : "Show data"}
        </button>
      </div>

      {open ? (
        <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-line bg-bg p-3 text-[12px]">
          {JSON.stringify(finding.data, null, 2)}
        </pre>
      ) : null}
    </Card>
  );
}
