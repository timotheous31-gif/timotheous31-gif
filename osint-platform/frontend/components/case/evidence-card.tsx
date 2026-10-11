"use client";

import { Badge, Eyebrow, Mono } from "@/components/ui/primitives";
import { formatBytes, formatDateTime } from "@/lib/format";
import type { EvidenceRecord } from "@/types/api";

/**
 * One stored artefact.
 *
 * An evidence row serves two readers at once: an investigator skimming for
 * what was collected and from where, and whoever later has to defend the
 * record — who needs the hash, the exact retrieval time and the source URL
 * verbatim. A table made the second reader's fields unreadable (a 64-character
 * hash in a 120-pixel column) and the first reader's fields invisible.
 *
 * So the human-readable summary leads in ordinary type, and the technical
 * anchors sit beneath it in a quiet recessed strip — present in full, wrapping
 * rather than truncated, but no longer the first thing the eye lands on.
 */
export function EvidenceCard({ item, index }: { item: EvidenceRecord; index?: number }) {
  return (
    <article className="relative border-l-2 border-l-edge px-5 py-4">
      {/* An artefact index, set in brass: editorial, not operational. */}
      {index !== undefined ? (
        <span
          aria-hidden="true"
          className="absolute right-4 top-4 font-mono text-[11px] font-semibold tracking-wide text-brass-dim"
        >
          {String(index).padStart(3, "0")}
        </span>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 pr-10">
        <Eyebrow tone="accent">{item.collector}</Eyebrow>
        {item.redacted ? (
          <Badge
            tone="SENSITIVE"
            title="A privacy filter removed values before this artefact was written to disk."
          >
            redacted
          </Badge>
        ) : null}
      </div>

      {item.excerpt ? (
        <p className="mt-2 text-[13.5px] leading-relaxed text-fg">{item.excerpt}</p>
      ) : (
        <p className="mt-2 text-[13.5px] text-faint">No excerpt was retained for this artefact.</p>
      )}

      {item.source_url ? (
        <a
          href={item.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-2 block break-anywhere font-mono text-[11px] text-muted underline-offset-2 hover:text-accent hover:underline"
        >
          {item.source_url}
        </a>
      ) : (
        <p className="mt-2 text-[11px] text-faint">
          No source URL — this artefact came from a non-HTTP collector.
        </p>
      )}

      <dl className="mt-3 grid gap-x-7 gap-y-2 rounded-sm bg-telemetry px-3.5 py-2.5 shadow-recessed sm:grid-cols-[auto_auto_auto_1fr]">
        <Anchor label="Retrieved">{formatDateTime(item.retrieved_at)}</Anchor>
        <Anchor label="Type">{item.content_type ?? "—"}</Anchor>
        <Anchor label="Size">{formatBytes(item.size_bytes)}</Anchor>
        <Anchor label="Supports">
          {item.finding_ids.length === 0
            ? "No finding yet"
            : `${item.finding_ids.length} finding${item.finding_ids.length === 1 ? "" : "s"}`}
        </Anchor>
        <div className="min-w-0 sm:col-span-4">
          <dt className="eyebrow">Fingerprint · SHA-256</dt>
          {/* In full, and wrapping. A truncated hash cannot be checked against
              anything, which is the only reason to record one. */}
          <dd className="mt-1 break-anywhere font-mono text-[11px] text-muted">{item.sha256}</dd>
        </div>
      </dl>
    </article>
  );
}

function Anchor({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-1 whitespace-nowrap font-mono text-[11px] tabular-nums text-muted">
        {children}
      </dd>
    </div>
  );
}
