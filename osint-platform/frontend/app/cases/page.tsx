"use client";

import Link from "next/link";
import { useState } from "react";

import { DeleteCase } from "@/components/case/delete-case";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Input,
  Select,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { CaseStatus } from "@/types/api";

const STATUSES: CaseStatus[] = ["NEW", "RUNNING", "PAUSED", "COMPLETE", "ARCHIVED"];

export default function CasesPage() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string>("");
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<Error | null>(null);

  const cases = useAsync(
    () => api.listCases({ q: query || undefined, status: status || undefined, limit: 100 }),
    [query, status],
  );

  async function createCase(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      await api.createCase({ name: name.trim() });
      setName("");
      cases.reload();
    } catch (cause) {
      setCreateError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Cases</h1>
        <p className="mt-1 text-sm text-muted">
          Investigations group targets, findings, entities and evidence.
        </p>
      </header>

      <Card>
        <CardHeader
          title="New case"
          description="Give the investigation a name you will recognise later"
        />
        <form onSubmit={createCase} className="flex flex-wrap items-center gap-2 p-4">
          <Input
            aria-label="Case name"
            placeholder="Example Domain Investigation"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="max-w-md flex-1"
          />
          <Button type="submit" variant="primary" disabled={creating || !name.trim()}>
            {creating ? "Creating…" : "Create case"}
          </Button>
        </form>
        {createError ? (
          <div className="px-4 pb-4">
            <ErrorNotice error={createError} />
          </div>
        ) : null}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Search cases"
          placeholder="Search by name…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-xs"
        />
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">All statuses</option>
          {STATUSES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </Select>
      </div>

      {cases.error ? <ErrorNotice error={cases.error} retry={cases.reload} /> : null}

      <Card>
        {cases.loading ? (
          <Spinner />
        ) : cases.data && cases.data.items.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Tags</Th>
                <Th>Created</Th>
                <Th className="w-10 text-right">{""}</Th>
              </tr>
            </thead>
            <tbody>
              {cases.data.items.map((item) => (
                <tr key={item.id} className="hover:bg-line/40">
                  <Td>
                    <Link
                      href={`/cases/${item.id}`}
                      className="font-medium text-accent hover:underline"
                    >
                      {item.name}
                    </Link>
                    {item.description ? (
                      <p className="mt-0.5 text-xs text-muted">{item.description}</p>
                    ) : null}
                  </Td>
                  <Td>
                    <Badge tone={item.status}>{item.status}</Badge>
                  </Td>
                  <Td className="text-xs text-muted">
                    {item.tags.length ? item.tags.map((tag) => tag.name).join(", ") : "—"}
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDateTime(item.created_at)}
                  </Td>
                  <Td className="text-right">
                    <DeleteCase
                      caseId={item.id}
                      caseName={item.name}
                      onDeleted={cases.reload}
                    />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No cases match" hint="Adjust the filters, or create a case above." />
          </div>
        )}
      </Card>
    </div>
  );
}
