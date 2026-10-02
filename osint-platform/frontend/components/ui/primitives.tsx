/**
 * The component vocabulary the whole console is built from.
 *
 * Three rules run through all of them, and all three matter for an
 * investigation tool:
 *
 *  - State is never communicated by colour alone. Every badge carries a label,
 *    and the ones that mean something operational carry a glyph too.
 *  - Every interactive element is reachable and visible with a keyboard.
 *  - Depth comes from surface + hairline, never from a drop shadow. Four
 *    surface tokens stack in a fixed order (bg → surface → panel → raised), so
 *    a panel inside a panel still reads as one.
 *
 * The export surface here is deliberately stable: pages consume these by name,
 * so the look can change without touching a single route.
 */

import clsx from "clsx";
import Link from "next/link";
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
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold uppercase tracking-label text-fg">{title}</h2>
        {description ? <p className="mt-1 text-xs text-muted">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** A small caps rule used to separate regions inside a panel. */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-4 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-label text-faint">
      {children}
    </p>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  // Near-black on the accent, not white. The accent is a mid-tone cyan: white
  // on it measures about 2.4:1, which is not readable; this is about 11:1.
  primary: "border-transparent bg-accent text-bg hover:brightness-110",
  secondary: "border-line-strong bg-raised text-fg hover:border-accent/60 hover:bg-line/60",
  ghost: "border-transparent bg-transparent text-muted hover:bg-raised hover:text-fg",
  danger: "border-danger/40 bg-transparent text-danger hover:bg-danger/10",
};

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded border px-3 py-1.5 text-[13px] font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_STYLES[variant],
        className,
      )}
      {...props}
    />
  );
}

const FIELD =
  "rounded border border-line-strong bg-surface px-3 py-1.5 text-sm text-fg " +
  "transition-colors placeholder:text-faint hover:border-accent/60";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx(FIELD, "w-full", className)} {...props} />;
}

/**
 * Intentionally *not* full width.
 *
 * A select sized to its content is a control; a select stretched across the
 * panel is a banner that happens to be clickable. Callers that want it wide
 * pass `w-full` themselves.
 */
export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={clsx(FIELD, "pr-8", className)} {...props}>
      {children}
    </select>
  );
}

/**
 * Badge tones.
 *
 * Each entry is text colour, hairline and a 10% wash. The wash is what makes a
 * status readable at a glance in a dense table without shouting; the text and
 * the glyph are what make it readable without colour at all.
 */
const BAND_STYLES: Record<string, string> = {
  LIKELY_MATCH: "text-likely border-likely/40 bg-likely/10",
  PROBABLE_MATCH: "text-probable border-probable/40 bg-probable/10",
  POSSIBLE_MATCH: "text-possible border-possible/40 bg-possible/10",
  WEAK_ASSOCIATION: "text-weak border-weak/40 bg-weak/10",
  PUBLIC: "text-muted border-line bg-raised",
  PERSONAL: "text-possible border-possible/40 bg-possible/10",
  SENSITIVE: "text-danger border-danger/40 bg-danger/10",
  RESTRICTED: "text-danger border-danger/50 bg-danger/15",
  SUCCESS: "text-ok border-ok/40 bg-ok/10",
  PARTIAL: "text-caution border-caution/40 bg-caution/10",
  FAILED: "text-danger border-danger/40 bg-danger/10",
  TIMEOUT: "text-danger border-danger/40 bg-danger/10",
  SKIPPED: "text-muted border-line bg-raised",
  COMPLETE: "text-ok border-ok/40 bg-ok/10",
  RUNNING: "text-info border-info/40 bg-info/10",
  QUEUED: "text-muted border-line bg-raised",
  CANCELLED: "text-muted border-line bg-raised",
  NEW: "text-accent border-accent/40 bg-accent/10",
  PAUSED: "text-caution border-caution/40 bg-caution/10",
  ARCHIVED: "text-faint border-line bg-transparent",
  // Generic tones, for states that are not one of the stored enums.
  OK: "text-ok border-ok/40 bg-ok/10",
  CAUTION: "text-caution border-caution/40 bg-caution/10",
  ALERT: "text-danger border-danger/40 bg-danger/10",
  INFO: "text-info border-info/40 bg-info/10",
  NEUTRAL: "text-muted border-line bg-raised",
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
  RUNNING: "▸",
  PAUSED: "‖",
  QUEUED: "·",
  OK: "✓",
  CAUTION: "!",
  ALERT: "✕",
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
        "inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5",
        "text-[10px] font-semibold uppercase tracking-label",
        tone ? (BAND_STYLES[tone] ?? BAND_STYLES.NEUTRAL) : BAND_STYLES.NEUTRAL,
        className,
      )}
    >
      {glyph ? <span aria-hidden="true">{glyph}</span> : null}
      {children}
    </span>
  );
}

/**
 * A single readout.
 *
 * `tone` colours the number only when the number means something is wrong —
 * a caution or an alert. A count that is merely a count stays in the
 * foreground colour, so a coloured figure on this screen always carries
 * information.
 */
export function Stat({
  label,
  value,
  hint,
  tone,
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "OK" | "CAUTION" | "ALERT" | "INFO";
  href?: string;
}) {
  const colour =
    tone === "ALERT"
      ? "text-danger"
      : tone === "CAUTION"
        ? "text-caution"
        : tone === "OK"
          ? "text-ok"
          : tone === "INFO"
            ? "text-info"
            : "text-fg";

  const body = (
    <>
      <div className="text-[10px] font-semibold uppercase tracking-label text-faint">{label}</div>
      <div className={clsx("mt-2 font-mono text-[26px] leading-none tabular-nums", colour)}>
        {value}
      </div>
      {hint ? <div className="mt-2 text-xs text-muted">{hint}</div> : null}
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        className="block rounded-lg border border-line bg-panel px-4 py-3 transition-colors hover:border-line-strong hover:bg-raised"
      >
        {body}
      </Link>
    );
  }
  return <Card className="px-4 py-3">{body}</Card>;
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-muted">{title}</p>
      {hint ? <p className="mt-1 text-xs text-faint">{hint}</p> : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted" role="status">
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
      <p className="text-sm font-semibold text-danger">Something went wrong</p>
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
        "border-b border-line bg-surface px-3 py-2 text-left text-[10px] font-semibold",
        "uppercase tracking-label text-faint",
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
    <span title={title} className={clsx("font-mono text-[12.5px]", className)}>
      {children}
    </span>
  );
}
