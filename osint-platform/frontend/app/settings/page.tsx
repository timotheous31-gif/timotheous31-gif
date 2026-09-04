"use client";

import { Badge, Card, CardHeader, ErrorNotice, Mono, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { API_BASE, api } from "@/lib/api";

/**
 * Settings is deliberately read-only.
 *
 * Every credential lives in the backend's environment, and the API never
 * returns one. Editing keys through the browser would mean transporting and
 * storing secrets in a place they do not belong.
 */
export default function SettingsPage() {
  const health = useAsync(
    () =>
      fetch(`${API_BASE}/health`).then((response) => {
        if (!response.ok) throw new Error(`Health check failed (${response.status})`);
        return response.json() as Promise<{ status: string; version: string; environment: string }>;
      }),
    [],
  );
  const collectors = useAsync(() => api.collectors(), []);

  const needConfig = collectors.data?.filter((item) => !item.available) ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Configuration lives in the backend&apos;s environment. This page reports what is
          configured; it never displays or accepts a credential.
        </p>
      </header>

      {health.error ? <ErrorNotice error={health.error} retry={health.reload} /> : null}

      <Card>
        <CardHeader title="Backend" />
        {health.loading ? (
          <Spinner />
        ) : health.data ? (
          <dl className="grid gap-3 p-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs uppercase tracking-wide text-muted">API URL</dt>
              <dd className="mt-0.5">
                <Mono>{API_BASE}</Mono>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-muted">Version</dt>
              <dd className="mt-0.5">
                <Mono>{health.data.version}</Mono>
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-muted">Environment</dt>
              <dd className="mt-0.5">
                <Badge tone="SUCCESS">{health.data.environment}</Badge>
              </dd>
            </div>
          </dl>
        ) : null}
      </Card>

      <Card>
        <CardHeader
          title="Optional integrations"
          description="Set these in the backend's environment (see .env.example) and restart it"
        />
        <div className="space-y-3 p-4 text-sm">
          {collectors.loading ? (
            <Spinner />
          ) : needConfig.length === 0 ? (
            <p className="text-muted">Every registered collector can currently run.</p>
          ) : (
            <ul className="space-y-2">
              {needConfig.map((collector) => (
                <li key={collector.name} className="rounded-md border border-line px-3 py-2">
                  <Mono>{collector.name}</Mono>
                  <p className="mt-0.5 text-xs text-muted">{collector.unavailable_reason}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="What this platform will not do" />
        <ul className="list-disc space-y-1 p-4 pl-8 text-sm text-muted">
          <li>Attempt any login, password reset or account-recovery probe.</li>
          <li>Bypass a private account, a paywall or a CAPTCHA.</li>
          <li>Retrieve breached credentials, or store any credential it encounters.</li>
          <li>Aggregate residential addresses, phone numbers or precise locations.</li>
          <li>Track a person&apos;s location, device or activity in real time.</li>
          <li>Assert that two accounts belong to one person because their handles match.</li>
        </ul>
      </Card>
    </div>
  );
}
