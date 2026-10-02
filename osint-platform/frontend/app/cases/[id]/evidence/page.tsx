"use client";

import { useState } from "react";

import { EvidenceCard } from "@/components/case/evidence-card";
import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Spinner,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
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
          <ul className="divide-y divide-line">
            {evidence.data.items.map((item) => (
              <li key={item.id}>
                <EvidenceCard item={item} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="p-4">
            <Empty title="No evidence stored yet" />
          </div>
        )}
      </Card>
    </div>
  );
}
