"use client";

import { useCallback, useState } from "react";

import { useCaseId } from "@/components/case/shell";
import { Button, Card, CardHeader, ErrorNotice, Select, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api, saveDocument } from "@/lib/api";
import type { ReportRenderFormat } from "@/lib/api";
import type { Classification } from "@/types/api";

type Format = ReportRenderFormat;

/** What each format is, in the words an investigator chooses between. */
const FORMATS: { value: Format; label: string; hint: string }[] = [
  {
    value: "dossier",
    label: "Investigation report",
    hint: "Client-facing document, print/PDF-ready. Every statement labelled with its basis.",
  },
  { value: "md", label: "Markdown", hint: "Plain text, for working notes." },
  { value: "html", label: "HTML", hint: "The full case, ordered by data structure." },
  { value: "json", label: "JSON", hint: "Machine-readable export. Complete." },
];

/** Formats that are a rendered document rather than source text. */
const RENDERED: ReadonlySet<Format> = new Set<Format>(["dossier", "html"]);

const CLASSIFICATIONS: Classification[] = ["PUBLIC", "PERSONAL", "SENSITIVE", "RESTRICTED"];

export default function ReportPage() {
  const caseId = useCaseId();
  const [format, setFormat] = useState<Format>("dossier");
  const [maxClassification, setMaxClassification] = useState<Classification>("PERSONAL");

  // Through the authenticated client, like every other panel in this app.
  //
  // This used to be a bare `fetch` of a URL string. `fetch` defaults to
  // `credentials: "same-origin"`, so across the :3000/:8000 split it sent no
  // session cookie and a working endpoint answered 401 — while the rest of the
  // page, which went through `api`, loaded fine.
  const report = useAsync(
    () => api.report(caseId, format, { max_classification: maxClassification }),
    [caseId, format, maxClassification],
  );

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<Error | null>(null);

  // Downloading re-fetches through the same authenticated path and saves the
  // result, rather than pointing an anchor at the API. A plain `<a href>` leaves
  // the app entirely, and on a cross-site deployment a `SameSite=lax` cookie
  // does not follow it — the same 401, arriving as a browser tab full of JSON.
  const download = useCallback(async () => {
    setDownloading(true);
    setDownloadError(null);
    try {
      saveDocument(
        await api.report(caseId, format, { max_classification: maxClassification }),
      );
    } catch (cause) {
      setDownloadError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setDownloading(false);
    }
  }, [caseId, format, maxClassification]);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Report"
          description="Every claim cites the stored artefact that supports it"
          action={
            <Button variant="primary" disabled={downloading} onClick={() => void download()}>
              {downloading ? "Preparing…" : "Download"}
            </Button>
          }
        />
        <div className="flex flex-wrap items-center gap-3 p-4">
          <label className="flex items-center gap-2 text-xs text-muted">
            Format
            <Select value={format} onChange={(event) => setFormat(event.target.value as Format)}>
              {FORMATS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
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
            {FORMATS.find((item) => item.value === format)?.hint} A stricter policy lets the
            report be circulated more widely than the case database.
          </p>
        </div>
      </Card>

      {report.error ? <ErrorNotice error={report.error} retry={report.reload} /> : null}
      {downloadError ? <ErrorNotice error={downloadError} retry={() => void download()} /> : null}

      <Card>
        {report.loading ? (
          <Spinner label="Rendering report" />
        ) : RENDERED.has(format) ? (
          <iframe
            // The report contains text collected from third-party sites. It is
            // escaped at render time, and sandboxed here as a second defence.
            sandbox=""
            srcDoc={report.data?.body ?? ""}
            title="Investigation report preview"
            className="h-[40rem] w-full rounded-lg bg-white"
          />
        ) : (
          <pre className="max-h-[40rem] overflow-auto p-4 text-[12px] leading-relaxed">
            {report.data?.body}
          </pre>
        )}
      </Card>
    </div>
  );
}
