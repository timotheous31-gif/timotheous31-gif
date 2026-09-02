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
  Mono,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatBytes, formatDateTime } from "@/lib/format";
import type { EvidenceVerification } from "@/types/api";

export default function EvidencePage() {
  const caseId = useCaseId();
  const evidence = useAsync(() => api.listEvidence(caseId, { limit: 500 }), [caseId]);
  const [verification, setVerification] = useState<EvidenceVerification | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function verify() {
    setVerifying(true);
    setError(null);
    try {
      setVerification(await api.verifyEvidence(caseId));
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Evidence integrity"
          description="Re-hashes every stored artefact and compares it with the hash recorded at collection"
          action={
            <Button onClick={verify} disabled={verifying}>
              {verifying ? "Verifying…" : "Verify now"}
            </Button>
          }
        />
        {verification ? (
          <div className="flex flex-wrap items-center gap-2 p-4 text-sm">
            <Badge tone={verification.intact ? "SUCCESS" : "FAILED"}>
              {verification.intact ? "All artefacts intact" : "Mismatch detected"}
            </Badge>
            <span className="text-muted">
              {verification.verified} of {verification.total} verified
              {verification.missing_raw ? `, ${verification.missing_raw} missing on disk` : ""}
              {verification.mismatched.length
                ? `, ${verification.mismatched.length} altered since collection`
                : ""}
            </span>
          </div>
        ) : (
          <p className="p-4 text-sm text-muted">Not verified in this session.</p>
        )}
        {error ? (
          <div className="px-4 pb-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}
      </Card>

      {evidence.error ? <ErrorNotice error={evidence.error} retry={evidence.reload} /> : null}

      <Card>
        <CardHeader
          title="Stored artefacts"
          description="Each raw response is stored under its SHA-256, with credentials removed before writing"
        />
        {evidence.loading ? (
          <Spinner />
        ) : evidence.data && evidence.data.items.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>SHA-256</Th>
                <Th>Collector</Th>
                <Th>Source</Th>
                <Th>Size</Th>
                <Th>Retrieved</Th>
                <Th>Excerpt</Th>
              </tr>
            </thead>
            <tbody>
              {evidence.data.items.map((item) => (
                <tr key={item.id}>
                  <Td>
                    <Mono title={item.sha256}>{item.sha256.slice(0, 16)}…</Mono>
                    {item.redacted ? (
                      <Badge tone="SENSITIVE" className="ml-2">
                        redacted
                      </Badge>
                    ) : null}
                  </Td>
                  <Td>
                    <Mono>{item.collector}</Mono>
                  </Td>
                  <Td className="max-w-xs truncate text-xs text-muted" title={item.source_url ?? ""}>
                    {item.source_url ?? "—"}
                  </Td>
                  <Td className="tabular-nums text-xs text-muted">{formatBytes(item.size_bytes)}</Td>
                  <Td className="whitespace-nowrap text-xs text-muted">
                    {formatDateTime(item.retrieved_at)}
                  </Td>
                  <Td className="max-w-sm text-xs text-muted">
                    <span className="line-clamp-2 break-all">{item.excerpt ?? "—"}</span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="p-4">
            <Empty title="No evidence stored yet" />
          </div>
        )}
      </Card>
    </div>
  );
}
