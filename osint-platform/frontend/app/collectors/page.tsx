"use client";

import {
  Badge,
  Card,
  CardHeader,
  ErrorNotice,
  Mono,
  Spinner,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";

export default function CollectorsPage() {
  const collectors = useAsync(() => api.collectors(), []);

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Collectors</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Each collector is an adapter over one public source. A collector that cannot run says so
          here rather than silently producing nothing.
        </p>
      </header>

      {collectors.error ? <ErrorNotice error={collectors.error} retry={collectors.reload} /> : null}

      <Card>
        <CardHeader title="Registered collectors" />
        {collectors.loading ? (
          <Spinner />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Accepts</Th>
                <Th>Configuration</Th>
                <Th>Rate limit</Th>
                <Th>Source</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {collectors.data?.map((collector) => (
                <tr key={collector.name}>
                  <Td>
                    <Mono>{collector.name}</Mono>
                    <span className="ml-1 text-xs text-muted">{collector.version}</span>
                    <p className="mt-0.5 max-w-md text-xs text-muted">{collector.description}</p>
                  </Td>
                  <Td className="text-xs">
                    <div className="flex flex-wrap gap-1">
                      {collector.supported_targets.map((target) => (
                        <Badge key={target}>{target}</Badge>
                      ))}
                    </div>
                  </Td>
                  <Td className="max-w-xs text-xs">
                    {collector.configuration.required_settings.length === 0 &&
                    collector.configuration.optional_settings.length === 0 ? (
                      <span className="text-muted">No configuration needed</span>
                    ) : (
                      <>
                        <div className="flex flex-wrap gap-1">
                          {collector.configuration.required_settings.map((name) => (
                            <Badge key={name} tone={collector.available ? "SUCCESS" : "FAILED"}>
                              {name}
                            </Badge>
                          ))}
                          {collector.configuration.optional_settings.map((name) => (
                            <Badge key={name}>{name} (optional)</Badge>
                          ))}
                        </div>
                        {collector.configuration.detail ? (
                          <p className="mt-1 text-xs text-muted">{collector.configuration.detail}</p>
                        ) : null}
                      </>
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-muted">{collector.rate_limit}</Td>
                  <Td className="max-w-xs text-xs text-muted">
                    {collector.source_attribution || "—"}
                  </Td>
                  <Td>
                    {collector.available ? (
                      <>
                        <Badge tone="SUCCESS">Operational</Badge>
                        {collector.configuration.mode ? (
                          <p className="mt-1 text-xs text-muted">
                            Mode: <Mono>{collector.configuration.mode}</Mono>
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <Badge tone="SKIPPED">Needs configuration</Badge>
                        <p className="mt-1 max-w-xs text-xs text-muted">
                          {collector.unavailable_reason}
                        </p>
                      </>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
