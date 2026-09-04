"use client";

import Link from "next/link";

import { Badge, Card, CardHeader, Empty, ErrorNotice, Spinner, Stat } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export default function DashboardPage() {
  const cases = useAsync(() => api.listCases({ limit: 8 }), []);
  const collectors = useAsync(() => api.collectors(), []);

  const active = cases.data?.items.filter((item) => item.status === "RUNNING").length ?? 0;
  const available = collectors.data?.filter((item) => item.available).length ?? 0;
  const blocked = collectors.data?.filter((item) => !item.available) ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          This platform collects only information that is already published by its owner or by a
          public registry. Every finding is classified by a privacy filter and linked to
          hash-verified evidence.
        </p>
      </header>

      {cases.error ? <ErrorNotice error={cases.error} retry={cases.reload} /> : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Cases" value={cases.data?.total ?? "—"} />
        <Stat label="Running" value={active} />
        <Stat label="Collectors available" value={collectors.data ? available : "—"} />
        <Stat
          label="Needing configuration"
          value={collectors.data ? blocked.length : "—"}
          hint={blocked.length ? "See Collectors" : undefined}
        />
      </div>

      <Card>
        <CardHeader
          title="Recent cases"
          description="The most recently created investigations"
          action={
            <Link href="/cases" className="text-sm text-accent hover:underline">
              All cases →
            </Link>
          }
        />
        {cases.loading ? (
          <Spinner />
        ) : cases.data && cases.data.items.length > 0 ? (
          <ul className="divide-y divide-line">
            {cases.data.items.map((item) => (
              <li key={item.id}>
                <Link
                  href={`/cases/${item.id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-line/40"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{item.name}</span>
                    <span className="block text-xs text-muted">
                      Created {formatDateTime(item.created_at)}
                    </span>
                  </span>
                  <Badge tone={item.status}>{item.status}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="p-4">
            <Empty
              title="No cases yet"
              hint="Create a case to start an investigation."
            />
          </div>
        )}
      </Card>

      {blocked.length > 0 ? (
        <Card>
          <CardHeader
            title="Collectors that cannot run"
            description="These need configuration; the platform reports them rather than failing silently"
          />
          <ul className="divide-y divide-line">
            {blocked.map((collector) => (
              <li key={collector.name} className="px-4 py-3">
                <span className="font-mono text-[13px]">{collector.name}</span>
                <p className="mt-0.5 text-xs text-muted">{collector.unavailable_reason}</p>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
