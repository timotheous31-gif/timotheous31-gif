"use client";

import { useState } from "react";

import { useCaseId } from "@/components/case/shell";
import { Button, Card, CardHeader, ErrorNotice, Select, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { Classification } from "@/types/api";

type Format = "html" | "md" | "json";

const CLASSIFICATIONS: Classification[] = ["PUBLIC", "PERSONAL", "SENSITIVE", "RESTRICTED"];

export default function ReportPage() {
  const caseId = useCaseId();
  const [format, setFormat] = useState<Format>("md");
  const [maxClassification, setMaxClassification] = useState<Classification>("PERSONAL");

  const report = useAsync(
    () =>
      fetch(
        `${api.reportUrl(caseId, format)}&max_classification=${maxClassification}`,
      ).then((response) => {
        if (!response.ok) throw new Error(`Report request failed (${response.status})`);
        return response.text();
      }),
    [caseId, format, maxClassification],
  );

  const downloadUrl = `${api.reportUrl(caseId, format)}&max_classification=${maxClassification}`;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Report"
          description="Every claim cites the stored artefact that supports it"
          action={
            <a href={downloadUrl} target="_blank" rel="noreferrer">
              <Button variant="primary">Open / download</Button>
            </a>
          }
        />
        <div className="flex flex-wrap items-center gap-3 p-4">
          <label className="flex items-center gap-2 text-xs text-muted">
            Format
            <Select value={format} onChange={(event) => setFormat(event.target.value as Format)}>
              <option value="md">Markdown</option>
              <option value="html">HTML</option>
              <option value="json">JSON</option>
            </Select>
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            Withhold content above
            <Select
              value={maxClassification}
              onChange={(event) => setMaxClassification(event.target.value as Classification)}
            >
              {CLASSIFICATIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </label>
          <p className="text-xs text-muted">
            A stricter policy lets the report be circulated more widely than the case database.
          </p>
        </div>
      </Card>

      {report.error ? <ErrorNotice error={report.error} retry={report.reload} /> : null}

      <Card>
        {report.loading ? (
          <Spinner label="Rendering report" />
        ) : format === "html" ? (
          <iframe
            // The report contains text collected from third-party sites. It is
            // escaped at render time, and sandboxed here as a second defence.
            sandbox=""
            srcDoc={report.data ?? ""}
            title="Investigation report preview"
            className="h-[40rem] w-full rounded-lg bg-white"
          />
        ) : (
          <pre className="max-h-[40rem] overflow-auto p-4 text-[12px] leading-relaxed">
            {report.data}
          </pre>
        )}
      </Card>
    </div>
  );
}
