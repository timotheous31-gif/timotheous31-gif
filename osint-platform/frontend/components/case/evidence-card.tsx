"use client";

import { Badge, Mono } from "@/components/ui/primitives";
import { formatBytes, formatDateTime } from "@/lib/format";
import type { EvidenceRecord } from "@/types/api";

/**
 * One stored artefact.
 *
 * An evidence row has to serve two readers at once: an investigator skimming
 * for what was collected and from where, and whoever later has to defend the
 * record — who needs the hash, the exact retrieval time and the source URL
 * verbatim. A table made the second reader's fields unreadable (a 64-character
 * hash in a 120-pixel column) and the first reader's fields invisible. So:
 * provenance on top in plain words, the technical anchors kept in full
 * underneath, in monospace, wrapping rather than truncated.
 */
export function EvidenceCard({ item }: { item: EvidenceRecord }) {
  return (
    <article className="border-l-2 border-l-line-strong bg-panel px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="font-semibold text-fg">{item.collector}</Mono>
        <Badge tone="NEUTRAL" title="When the platform read this artefact.">
          {formatDateTime(item.retrieved_at)}
        </Badge>
        {item.content_type ? <Badge tone="NEUTRAL">{item.content_type}</Badge> : null}
        {item.redacted ? (
          <Badge
            tone="SENSITIVE"
            title="A privacy filter removed values before this artefact was written to disk."
          >
            redacted
          </Badge>
        ) : null}
        <span className="ml-auto font-mono text-[11.5px] tabular-nums text-faint">
          {formatBytes(item.size_bytes)}
        </span>
      </div>

      {item.source_url ? (
        <a
          href={item.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-1.5 block break-anywhere font-mono text-[11.5px] text-muted underline-offset-2 hover:text-accent hover:underline"
        >
          {item.source_url}
        </a>
      ) : (
        <p className="mt-1.5 text-[11.5px] text-faint">
          No source URL — this artefact came from a non-HTTP collector.
        </p>
      )}

      {item.excerpt ? (
        <p className="mt-2 line-clamp-3 break-anywhere rounded border border-line bg-surface px-3 py-2 text-xs text-muted">
          {item.excerpt}
        </p>
      ) : null}

      <dl className="mt-2.5 flex flex-wrap gap-x-6 gap-y-1.5 border-t border-line pt-2.5">
        <div className="min-w-0">
          <dt className="text-[10px] font-semibold uppercase tracking-label text-faint">
            SHA-256
          </dt>
          {/* In full, and wrapping. A truncated hash cannot be checked against
              anything, which is the only reason to record one. */}
          <dd className="break-anywhere font-mono text-[11px] text-muted">{item.sha256}</dd>
        </div>
        <div>
          <dt className="text-[10px] font-semibold uppercase tracking-label text-faint">
            Supports
          </dt>
          <dd className="font-mono text-[11px] text-muted">
            {item.finding_ids.length === 0
              ? "No finding yet"
              : `${item.finding_ids.length} finding${item.finding_ids.length === 1 ? "" : "s"}`}
          </dd>
        </div>
      </dl>
    </article>
  );
}
