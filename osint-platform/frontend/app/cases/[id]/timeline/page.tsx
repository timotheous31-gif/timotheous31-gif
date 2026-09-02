"use client";

import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Select,
  Spinner,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { confidenceBand, formatConfidence, formatDate, humanise } from "@/lib/format";

export default function TimelinePage() {
  const caseId = useCaseId();
  const [kind, setKind] = useState("");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  const timeline = useAsync(
    () => api.timeline(caseId, { kind: kind || undefined, order }),
    [caseId, kind, order],
  );

  const kinds = Object.keys(timeline.data?.summary.kinds ?? {}).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Filter by event kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All event kinds</option>
          {kinds.map((item) => (
            <option key={item} value={item}>
              {humanise(item)}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Sort order"
          value={order}
          onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
        >
          <option value="asc">Oldest first</option>
          <option value="desc">Newest first</option>
        </Select>
        {timeline.data && timeline.data.summary.event_count > 0 ? (
          <span className="ml-auto text-xs text-muted">
            {timeline.data.summary.event_count} event(s) ·{" "}
            {formatDate(timeline.data.summary.first_event)} →{" "}
            {formatDate(timeline.data.summary.last_event)}
          </span>
        ) : null}
      </div>

      {timeline.error ? <ErrorNotice error={timeline.error} retry={timeline.reload} /> : null}

      <Card>
        <CardHeader
          title="Investigation timeline"
          description="Built only from findings that carry a real observation date"
        />
        {timeline.loading ? (
          <Spinner />
        ) : timeline.data && timeline.data.events.length > 0 ? (
          <ol className="relative space-y-0 p-4">
            {timeline.data.events.map((event) => (
              <li key={event.id} className="relative flex gap-4 pb-5 last:pb-0">
                <div className="flex flex-col items-center">
                  <span
                    aria-hidden="true"
                    className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full border-2 border-accent bg-bg"
                  />
                  <span aria-hidden="true" className="mt-1 w-px flex-1 bg-line" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono className="text-muted">{formatDate(event.occurred_at)}</Mono>
                    <Badge>{humanise(event.kind)}</Badge>
                    <Badge tone={confidenceBand(event.confidence)}>
                      {formatConfidence(event.confidence)}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm font-medium">{event.title}</p>
                  {event.description ? (
                    <p className="mt-0.5 text-xs text-muted">{event.description}</p>
                  ) : null}
                  {event.source_url ? (
                    <a
                      href={event.source_url}
                      target="_blank"
                      rel="noreferrer nofollow noopener"
                      className="mt-1 block truncate text-xs text-accent hover:underline"
                    >
                      {event.source_url}
                    </a>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <div className="p-4">
            <Empty
              title="No dated events"
              hint="Registrations, certificates, archive snapshots and commits contribute dates."
            />
          </div>
        )}
      </Card>
    </div>
  );
}
