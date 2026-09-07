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
import {
  EMPTY_PERSON_CONTEXT,
  SELECTABLE_TYPES,
  TYPE_HELP,
  ambiguityChoices,
  buildPersonContext,
  canSubmitTarget,
  requiresExplicitType,
} from "@/lib/targets";
import type { NormalizationPreview, TargetType } from "@/types/api";

export default function TargetsPage() {
  const caseId = useCaseId();
  const targets = useAsync(() => api.listTargets(caseId, { limit: 500 }), [caseId]);
  const [value, setValue] = useState("");
  const [chosenType, setChosenType] = useState<TargetType | "">("");
  const [preview, setPreview] = useState<NormalizationPreview | null>(null);
  const [context, setContext] = useState(EMPTY_PERSON_CONTEXT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // Context only means anything for a person, so the fields only appear once
  // the target is known to be one.
  const isPerson = chosenType === "PERSON" || preview?.type === "PERSON";

  // The backend refuses to guess between PERSON and ORGANIZATION for a bare
  // name, so the form has to collect that decision before it can submit.
  const needsChoice = requiresExplicitType(preview, chosenType);
  const choices = ambiguityChoices(preview);

  async function refreshPreview(nextValue: string, nextType: TargetType | "") {
    setError(null);
    if (!nextValue.trim()) {
      setPreview(null);
      return;
    }
    try {
      setPreview(await api.previewTarget(caseId, nextValue.trim(), nextType || null));
    } catch {
      // An unparseable value simply has no preview; the add attempt will
      // surface the reason.
      setPreview(null);
    }
  }

  function onValueChange(next: string) {
    setValue(next);
    void refreshPreview(next, chosenType);
  }

  function onTypeChange(next: TargetType | "") {
    setChosenType(next);
    void refreshPreview(value, next);
  }

  async function addTarget(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmitTarget(value, preview, chosenType, busy)) return;
    setBusy(true);
    setError(null);
    try {
      await api.addTarget(caseId, {
        value: value.trim(),
        type: chosenType || null,
        context: isPerson ? buildPersonContext(context) : null,
      });
      setValue("");
      setChosenType("");
      setContext(EMPTY_PERSON_CONTEXT);
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
          description="Structured input is detected automatically; a name has to be classified by you"
        />
        <form onSubmit={addTarget} className="flex flex-wrap items-center gap-2 p-4">
          <Input
            aria-label="Target value"
            placeholder="example.com, @exampleuser, octocat/Hello-World, 203.0.113.7, a name…"
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            className="max-w-lg flex-1"
          />
          <select
            aria-label="Target type"
            value={chosenType}
            onChange={(event) => onTypeChange(event.target.value as TargetType | "")}
            className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm"
          >
            <option value="">Detect automatically</option>
            {SELECTABLE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <Button
            type="submit"
            variant="primary"
            disabled={!canSubmitTarget(value, preview, chosenType, busy)}
          >
            {busy ? "Adding…" : "Add target"}
          </Button>
        </form>

        {needsChoice ? (
          <div className="mx-4 mb-4 rounded-md border border-line bg-bg p-3">
            <p className="text-sm font-medium">Is this a person or an organisation?</p>
            <p className="mt-0.5 text-xs text-muted">
              {preview?.message ?? "This reads as a name, and a name alone does not say which."}{" "}
              The platform will not guess, because the two are investigated differently.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {choices.map((candidate) => (
                <Button key={candidate} onClick={() => onTypeChange(candidate)}>
                  {candidate}
                </Button>
              ))}
            </div>
            {choices.map((candidate) =>
              TYPE_HELP[candidate] ? (
                <p key={candidate} className="mt-1.5 text-xs text-muted">
                  <span className="font-medium">{candidate}</span> — {TYPE_HELP[candidate]}
                </p>
              ) : null,
            )}
          </div>
        ) : null}

        {isPerson ? (
          <div className="mx-4 mb-4 rounded-md border border-line p-3">
            <p className="text-sm font-medium">Optional context</p>
            <p className="mt-0.5 text-xs text-muted">
              Anchors you already know. They are never used to search for new personal
              information — only to judge which of the same-name records public sources
              return could be this person, and which can be ruled out. Exact identifiers
              (ORCID, GitHub username, a profile URL) are worth far more than a city or a
              job title. Lists are comma-separated.
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["knownUsernames", "Known usernames", "octocat, example_user"],
                  ["profileUrls", "Known profile URLs", "https://github.com/octocat"],
                  ["websites", "Known websites", "https://example.com"],
                  ["githubUsername", "GitHub username", "octocat"],
                  ["orcid", "ORCID iD", "0000-0002-1825-0097"],
                  ["organizations", "Organisations", "Example Ltd"],
                  ["schools", "Schools or universities", "Example University"],
                  ["occupation", "Occupation", "researcher"],
                  ["city", "City", "Delft"],
                  ["country", "Country", "Netherlands"],
                ] as const
              ).map(([field, label, placeholder]) => (
                <label key={field} className="block text-xs">
                  <span className="text-muted">{label}</span>
                  <Input
                    aria-label={label}
                    placeholder={placeholder}
                    value={context[field]}
                    onChange={(event) =>
                      setContext({ ...context, [field]: event.target.value })
                    }
                    className="mt-0.5"
                  />
                </label>
              ))}
            </div>
          </div>
        ) : null}

        {preview && !preview.ambiguous ? (
          <p className="px-4 pb-4 text-xs text-muted">
            Will be stored as <Badge>{preview.type}</Badge> <Mono>{preview.normalized_value}</Mono>
            {preview.type === "PERSON" ? (
              <span className="ml-1">
                — searched by name only. Results are candidates, not identifications.
              </span>
            ) : null}
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
