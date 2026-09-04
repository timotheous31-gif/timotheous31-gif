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
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export default function TargetsPage() {
  const caseId = useCaseId();
  const targets = useAsync(() => api.listTargets(caseId, { limit: 500 }), [caseId]);
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<{ type: string; normalized_value: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function updatePreview(next: string) {
    setValue(next);
    setError(null);
    if (!next.trim()) {
      setPreview(null);
      return;
    }
    try {
      const result = await api.previewTarget(caseId, next.trim());
      setPreview({ type: result.type, normalized_value: result.normalized_value });
    } catch {
      // An unparseable value simply has no preview; the add attempt will
      // surface the reason.
      setPreview(null);
    }
  }

  async function addTarget(event: React.FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.addTarget(caseId, { value: value.trim() });
      setValue("");
      setPreview(null);
      targets.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  async function removeTarget(targetId: string) {
    try {
      await api.deleteTarget(caseId, targetId);
      targets.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Add a target"
          description="The type is inferred from the shape of what you paste"
        />
        <form onSubmit={addTarget} className="flex flex-wrap items-center gap-2 p-4">
          <Input
            aria-label="Target value"
            placeholder="example.com, @exampleuser, octocat/Hello-World, 203.0.113.7…"
            value={value}
            onChange={(event) => void updatePreview(event.target.value)}
            className="max-w-lg flex-1"
          />
          <Button type="submit" variant="primary" disabled={busy || !value.trim()}>
            {busy ? "Adding…" : "Add target"}
          </Button>
        </form>
        {preview ? (
          <p className="px-4 pb-4 text-xs text-muted">
            Will be stored as <Badge>{preview.type}</Badge>{" "}
            <Mono>{preview.normalized_value}</Mono>
          </p>
        ) : null}
        {error ? (
          <div className="px-4 pb-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}
      </Card>

      <Card>
        <CardHeader title="Targets" description="Everything this case investigates" />
        {targets.loading ? (
          <Spinner />
        ) : targets.data && targets.data.items.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>Type</Th>
                <Th>Normalised value</Th>
                <Th>Original input</Th>
                <Th>Status</Th>
                <Th>Added</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {targets.data.items.map((target) => (
                <tr key={target.id}>
                  <Td>
                    <Badge>{target.type}</Badge>
                  </Td>
                  <Td>
                    <Mono>{target.normalized_value}</Mono>
                  </Td>
                  <Td className="text-muted">
                    <Mono>{target.raw_input}</Mono>
                  </Td>
                  <Td>
                    <Badge tone={target.status}>{target.status}</Badge>
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDateTime(target.created_at)}
                  </Td>
                  <Td>
                    <Button variant="danger" onClick={() => void removeTarget(target.id)}>
                      Remove
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No targets yet" hint="Add one above to begin." />
          </div>
        )}
      </Card>
    </div>
  );
}
