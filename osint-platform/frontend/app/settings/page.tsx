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
  const search = collectors.data?.find((item) => item.name === "search") ?? null;
  // Collectors that run, but in a reduced mode because an optional setting is
  // absent — GitHub anonymously, for instance. Worth showing: it explains a
  // rate limit an investigator would otherwise hit without warning.
  const degraded =
    collectors.data?.filter(
      (item) =>
        item.available &&
        item.configuration.optional_settings.length > 0 &&
        !item.configuration.configured,
    ) ?? [];

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
          title="Search"
          description="Which provider the search collector uses, and whether it can run"
        />
        <div className="p-4 text-sm">
          {collectors.loading ? (
            <Spinner />
          ) : search ? (
            <dl className="grid gap-3 sm:grid-cols-3">
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted">Status</dt>
                <dd className="mt-0.5">
                  {search.available ? (
                    <Badge tone="SUCCESS">Operational</Badge>
                  ) : (
                    <Badge tone="SKIPPED">Needs configuration</Badge>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted">Provider</dt>
                <dd className="mt-0.5">
                  <Mono>{search.configuration.mode || "none"}</Mono>
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted">Required settings</dt>
                <dd className="mt-0.5 flex flex-wrap gap-1">
                  {search.configuration.required_settings.map((name) => (
                    <Badge key={name} tone={search.available ? "SUCCESS" : "FAILED"}>
                      {name}
                    </Badge>
                  ))}
                </dd>
              </div>
              <p className="text-xs text-muted sm:col-span-3">
                {search.configuration.detail || search.unavailable_reason}
              </p>
              {!search.available ? (
                <>
                  <p className="text-xs text-muted sm:col-span-3">
                    Set <Mono>SEARCH_PROVIDER</Mono> to <Mono>anthropic_web_search</Mono>,{" "}
                    <Mono>brave</Mono>, <Mono>bing</Mono> or <Mono>serper</Mono> and supply the
                    matching credential (<Mono>ANTHROPIC_API_KEY</Mono>,{" "}
                    <Mono>BRAVE_API_KEY</Mono>, <Mono>BING_API_KEY</Mono> or{" "}
                    <Mono>SERPER_API_KEY</Mono>) in the backend&apos;s environment, then restart
                    it. Investigations still run without search; the collector is recorded as
                    SKIPPED with this reason rather than returning nothing.
                  </p>
                  {/* The Anthropic channel is genuinely different from the others, and an
                      operator choosing between them needs to know how before they enable it. */}
                  <p className="text-xs text-muted sm:col-span-3">
                    <Mono>anthropic_web_search</Mono> is a secondary channel, not a drop-in
                    search API: it chooses its own queries, returns no page description and no
                    ranking, and bills per search (capped by{" "}
                    <Mono>ANTHROPIC_WEB_SEARCH_MAX_USES</Mono>). Whether its results may be
                    stored or displayed in a commercial product is not established by
                    Anthropic&apos;s documentation — read{" "}
                    <Mono>docs/search-provider-compliance.md</Mono> first.
                  </p>
                  <p className="text-xs text-muted sm:col-span-3">
                    <Mono>google_wss</Mono> is configured but not activated
                    (PENDING_PARTNER_ACCESS): it issues no request whatever credentials are set.
                  </p>
                </>
              ) : null}
            </dl>
          ) : (
            <p className="text-muted">The search collector is not registered.</p>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Collector configuration"
          description="Set these in the backend's environment (see .env.example) and restart it"
        />
        <div className="space-y-3 p-4 text-sm">
          {collectors.loading ? (
            <Spinner />
          ) : (
            <>
              {needConfig.length === 0 ? (
                <p className="text-muted">Every registered collector can currently run.</p>
              ) : (
                <ul className="space-y-2">
                  {needConfig.map((collector) => (
                    <li key={collector.name} className="rounded-md border border-line px-3 py-2">
                      <Mono>{collector.name}</Mono> <Badge tone="SKIPPED">Needs configuration</Badge>
                      <p className="mt-0.5 text-xs text-muted">{collector.unavailable_reason}</p>
                      {collector.configuration.required_settings.length > 0 ? (
                        <p className="mt-1 text-xs text-muted">
                          Set: {collector.configuration.required_settings.join(", ")}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {degraded.length > 0 ? (
                <ul className="space-y-2">
                  {degraded.map((collector) => (
                    <li key={collector.name} className="rounded-md border border-line px-3 py-2">
                      <Mono>{collector.name}</Mono> <Badge tone="SUCCESS">Operational</Badge>
                      <p className="mt-0.5 text-xs text-muted">{collector.configuration.detail}</p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
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
          <li>
            Treat two people as the same because they share a name. Name matches are recorded as
            separate candidates with the reason stated.
          </li>
          <li>
            Guess whether a name belongs to a person or an organisation. You are asked instead.
          </li>
        </ul>
      </Card>
    </div>
  );
}
