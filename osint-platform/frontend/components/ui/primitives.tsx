/**
 * Small, dependency-free UI primitives.
 *
 * Two rules run through all of them, and both matter for an investigation
 * tool: state is never communicated by colour alone (every badge carries a
 * label or an icon glyph), and every interactive element is reachable and
 * visible with a keyboard.
 */

import clsx from "clsx";
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx("rounded-lg border border-line bg-panel", className)}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary: "bg-accent text-white hover:opacity-90 border-transparent",
  secondary: "bg-panel text-fg hover:bg-line border-line",
  ghost: "bg-transparent text-fg hover:bg-panel border-transparent",
  danger: "bg-transparent text-danger hover:bg-danger/10 border-danger/40",
};

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_STYLES[variant],
        className,
      )}
      {...props}
    />
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        "w-full rounded-md border border-line bg-bg px-3 py-1.5 text-sm",
        "placeholder:text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "rounded-md border border-line bg-bg px-2 py-1.5 text-sm",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

const BAND_STYLES: Record<string, string> = {
  LIKELY_MATCH: "text-likely border-likely/50",
  PROBABLE_MATCH: "text-probable border-probable/50",
  POSSIBLE_MATCH: "text-possible border-possible/50",
  WEAK_ASSOCIATION: "text-weak border-weak/50",
  PUBLIC: "text-muted border-line",
  PERSONAL: "text-possible border-possible/50",
  SENSITIVE: "text-danger border-danger/50",
  RESTRICTED: "text-danger border-danger/50",
  SUCCESS: "text-likely border-likely/50",
  PARTIAL: "text-probable border-probable/50",
  FAILED: "text-danger border-danger/50",
  TIMEOUT: "text-danger border-danger/50",
  SKIPPED: "text-muted border-line",
  COMPLETE: "text-likely border-likely/50",
  RUNNING: "text-accent border-accent/50",
  QUEUED: "text-muted border-line",
  CANCELLED: "text-muted border-line",
  NEW: "text-accent border-accent/50",
  PAUSED: "text-probable border-probable/50",
  ARCHIVED: "text-muted border-line",
};

/** Glyphs so status is never carried by colour alone. */
const BAND_GLYPHS: Record<string, string> = {
  LIKELY_MATCH: "●●●",
  PROBABLE_MATCH: "●●○",
  POSSIBLE_MATCH: "●○○",
  WEAK_ASSOCIATION: "○○○",
  SENSITIVE: "!",
  RESTRICTED: "!!",
  FAILED: "✕",
  TIMEOUT: "✕",
  SUCCESS: "✓",
  COMPLETE: "✓",
  SKIPPED: "–",
};

export function Badge({
  tone,
  children,
  title,
  className,
}: {
  tone?: string;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  const glyph = tone ? BAND_GLYPHS[tone] : undefined;
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5",
        "text-[11px] font-medium",
        tone ? (BAND_STYLES[tone] ?? "text-muted border-line") : "text-muted border-line",
        className,
      )}
    >
      {glyph ? <span aria-hidden="true">{glyph}</span> : null}
      {children}
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wide text-muted">{label}</div>
      {hint ? <div className="mt-1 text-xs text-muted">{hint}</div> : null}
    </Card>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-6 text-sm text-muted" role="status">
      <span
        aria-hidden="true"
        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent"
      />
      {label}…
    </div>
  );
}

export function ErrorNotice({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <div className="rounded-lg border border-danger/40 bg-danger/5 px-4 py-3" role="alert">
      <p className="text-sm font-medium text-danger">Something went wrong</p>
      <p className="mt-1 text-xs text-muted">{error.message}</p>
      {retry ? (
        <Button className="mt-3" onClick={retry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={clsx("w-full border-collapse text-sm", className)}>{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <th
      className={clsx(
        "border-b border-line px-3 py-2 text-left text-[11px] font-semibold",
        "uppercase tracking-wide text-muted",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <td title={title} className={clsx("border-b border-line px-3 py-2 align-top", className)}>
      {children}
    </td>
  );
}

export function Mono({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span title={title} className={clsx("font-mono text-[13px]", className)}>
      {children}
    </span>
  );
}
