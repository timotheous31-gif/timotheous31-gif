/**
 * The component vocabulary the console is built from.
 *
 * Four rules run through all of it:
 *
 *  - Importance is carried by **material**, not by size alone. A surface is
 *    one of four things — command, instrument, sheet, telemetry — and which
 *    one it is says how much it matters before a word is read.
 *  - State is never communicated by colour alone. Every badge carries a label,
 *    and the operational ones carry a glyph.
 *  - Edges mean something. A quiet separator, a structural edge, a control
 *    boundary and a lit rail are four different weights, not one.
 *  - Cyan is intelligence and system; brass is prestige and editorial
 *    emphasis. They are not used heavily together in one component.
 *
 * The export surface is deliberately stable: pages consume these by name, so
 * the material can change without touching a route.
 */

import clsx from "clsx";
import Link from "next/link";
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export type Material = "command" | "instrument" | "sheet" | "telemetry";

const MATERIAL: Record<Material, string> = {
  command: "surface-command",
  instrument: "surface-instrument",
  sheet: "surface-sheet",
  telemetry: "surface-telemetry",
};

/**
 * A surface of a declared material.
 *
 * `sheet` is the default because most of the product is reading matter: a
 * finding, a record, an artefact. Panels that read a live value ask for
 * `instrument`; logs and run history ask for `telemetry`; only the two
 * surfaces that establish where you are ask for `command`.
 */
export function Surface({
  material = "sheet",
  notch = false,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { material?: Material; notch?: boolean }) {
  return (
    <div className={clsx(MATERIAL[material], className)} {...props}>
      {/* The command material already spends its ::after on the lit top edge,
          so its machined corner has to be its own element. */}
      {notch ? <span aria-hidden="true" className="notch-piece" /> : null}
      {children}
    </div>
  );
}

/** Kept so the ~40 existing `<Card>` call sites keep working, as sheets. */
export function Card({
  material = "sheet",
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { material?: Material }) {
  return <Surface material={material} className={className} {...props} />;
}

/**
 * A surface's header.
 *
 * The title is a section role, not body text, and it sits above a quiet rule
 * rather than inside a boxed band — a dossier's section head, not a card
 * chrome strip.
 */
export function CardHeader({
  title,
  description,
  action,
  index,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** An editorial section number, set in brass. Optional and sparing. */
  index?: string;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-rule-quiet px-4 py-3">
      <div className="min-w-0">
        <h2 className="flex items-baseline gap-2 text-section text-fg">
          {index ? (
            <span className="font-mono text-[11px] font-semibold tracking-wide text-brass-dim">
              {index}
            </span>
          ) : null}
          {title}
        </h2>
        {description ? <p className="mt-1 text-meta text-muted">{description}</p> : null}
      </div>
      {/* `shrink-0` keeps a short action from being squeezed; `max-w-full`
          plus the row's `flex-wrap` lets a wide one drop to its own line
          rather than pushing the page sideways on a phone. */}
      {action ? <div className="max-w-full shrink-0">{action}</div> : null}
    </div>
  );
}

/** The small-caps label that names an operational region. */
export function Eyebrow({
  children,
  tone = "muted",
  className,
}: {
  children: ReactNode;
  tone?: "muted" | "accent" | "brass";
  className?: string;
}) {
  return (
    <p
      className={clsx(
        "eyebrow",
        tone === "accent" && "eyebrow-accent",
        tone === "brass" && "eyebrow-brass",
        className,
      )}
    >
      {children}
    </p>
  );
}

/** A horizontal division inside a dossier surface. */
export function Rule({ strong = false }: { strong?: boolean }) {
  return (
    <hr
      className={clsx("border-0 border-t", strong ? "border-rule-strong" : "border-rule-quiet")}
    />
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  // Near-black on the accent, not white: the accent is a mid-tone cyan, and
  // white on it measures about 2.5:1.
  primary:
    "border-accent/60 bg-accent text-bg shadow-[0_0_18px_-8px_rgb(var(--accent)/0.8)] hover:brightness-110",
  secondary: "border-edge bg-raised text-fg hover:border-accent/60 hover:bg-instrument",
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
        "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5",
        "text-[12px] font-semibold uppercase tracking-wide",
        "transition-[color,background-color,border-color,box-shadow,transform]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        BUTTON_STYLES[variant],
        className,
      )}
      {...props}
    />
  );
}

const FIELD =
  "rounded-md border border-edge bg-telemetry px-3 py-1.5 text-body text-fg " +
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
 * Badge tones: text colour, a hairline, and a wash.
 *
 * The wash is what makes a status readable at a glance in a dense table
 * without shouting; the text and the glyph are what make it readable without
 * colour at all.
 */
const BAND_STYLES: Record<string, string> = {
  LIKELY_MATCH: "text-likely border-likely/35 bg-likely/10",
  PROBABLE_MATCH: "text-probable border-probable/35 bg-probable/10",
  POSSIBLE_MATCH: "text-possible border-possible/35 bg-possible/10",
  WEAK_ASSOCIATION: "text-weak border-weak/35 bg-weak/10",
  PUBLIC: "text-muted border-rule bg-raised",
  PERSONAL: "text-possible border-possible/35 bg-possible/10",
  SENSITIVE: "text-danger border-danger/35 bg-danger/10",
  RESTRICTED: "text-danger border-danger/50 bg-danger/15",
  SUCCESS: "text-ok border-ok/35 bg-ok/10",
  PARTIAL: "text-caution border-caution/35 bg-caution/10",
  FAILED: "text-danger border-danger/35 bg-danger/10",
  TIMEOUT: "text-danger border-danger/35 bg-danger/10",
  SKIPPED: "text-muted border-rule bg-raised",
  COMPLETE: "text-ok border-ok/35 bg-ok/10",
  RUNNING: "text-accent border-accent/40 bg-accent/10",
  QUEUED: "text-muted border-rule bg-raised",
  CANCELLED: "text-muted border-rule bg-raised",
  NEW: "text-blue border-blue/35 bg-blue/10",
  PAUSED: "text-caution border-caution/35 bg-caution/10",
  ARCHIVED: "text-faint border-rule-quiet bg-transparent",
  OK: "text-ok border-ok/35 bg-ok/10",
  CAUTION: "text-caution border-caution/35 bg-caution/10",
  ALERT: "text-danger border-danger/35 bg-danger/10",
  INFO: "text-blue border-blue/35 bg-blue/10",
  NEUTRAL: "text-muted border-rule bg-raised",
  /** Editorial emphasis, not a status. Used for a selected artefact. */
  BRASS: "text-brass border-brass/35 bg-brass/10",
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
  glyph: override,
}: {
  tone?: string;
  children: ReactNode;
  title?: string;
  className?: string;
  /**
   * A glyph more specific than the tone's own — or `null` to suppress it.
   *
   * Tones are coarse: four candidate review states map onto three tones, and
   * "needs review" deserves its own mark rather than borrowing the one caution
   * uses for everything.
   */
  glyph?: string | null;
}) {
  const glyph =
    override === undefined ? (tone ? BAND_GLYPHS[tone] : undefined) : (override ?? undefined);
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-[3px]",
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
 * A numeric readout.
 *
 * Three sizes, because a hero figure and a panel figure are not the same
 * claim. Tone colours the number only when the number means something is
 * wrong or something is live — a count that is merely a count stays in the
 * foreground colour, so a coloured figure on this screen always carries
 * information.
 */
export function Readout({
  label,
  value,
  hint,
  tone,
  size = "md",
  href,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "OK" | "CAUTION" | "ALERT" | "INFO" | "BRASS";
  size?: "sm" | "md" | "lg";
  href?: string;
  className?: string;
}) {
  const colour =
    tone === "ALERT"
      ? "text-danger"
      : tone === "CAUTION"
        ? "text-caution"
        : tone === "OK"
          ? "text-ok"
          : tone === "INFO"
            ? "text-accent"
            : tone === "BRASS"
              ? "text-brass"
              : "text-fg";
  const scale =
    size === "lg" ? "text-readout-lg" : size === "sm" ? "text-readout-sm" : "text-readout";

  const body = (
    <>
      <span className={clsx("readout block font-mono", scale, colour)}>{value}</span>
      <Eyebrow className="mt-2">{label}</Eyebrow>
      {hint ? <span className="mt-1.5 block text-meta text-muted">{hint}</span> : null}
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        className={clsx(
          "block transition-colors hover:text-accent [&_.eyebrow]:hover:text-accent-dim",
          className,
        )}
      >
        {body}
      </Link>
    );
  }
  return <div className={className}>{body}</div>;
}

/** Kept for call sites that still want a bordered tile. */
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
  return (
    <Surface material="instrument" className="px-4 py-3.5">
      <Readout label={label} value={value} hint={hint} tone={tone} href={href} size="sm" />
    </Surface>
  );
}

/**
 * An empty state, in console register.
 *
 * Calm and factual. Normal emptiness is not a warning, and dressing it as one
 * teaches an operator to ignore the real ones.
 */
export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-6 py-9 text-center">
      <p className="eyebrow eyebrow-accent">{title}</p>
      {hint ? <p className="mx-auto mt-2 max-w-sm text-meta text-muted">{hint}</p> : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-6 text-meta text-muted" role="status">
      <span
        aria-hidden="true"
        className="h-3 w-3 animate-spin rounded-full border-2 border-rule border-t-accent"
      />
      {label}…
    </div>
  );
}

export function ErrorNotice({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <div
      className="rounded-lg border border-danger/40 bg-danger/5 px-4 py-3 shadow-raised"
      role="alert"
      style={{ boxShadow: "inset 2px 0 0 0 rgb(var(--alert))" }}
    >
      <Eyebrow className="!text-danger">Fault</Eyebrow>
      <p className="mt-1.5 text-body text-fg">{error.message}</p>
      {retry ? (
        <Button className="mt-3" onClick={retry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={clsx("w-full border-collapse text-body", className)}>{children}</table>
    </div>
  );
}

export function Th({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <th
      className={clsx(
        "border-b border-rule bg-telemetry/60 px-3 py-2 text-left",
        "text-[10px] font-semibold uppercase tracking-label text-faint",
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
    <td
      title={title}
      className={clsx("border-b border-rule-quiet px-3 py-2 align-top", className)}
    >
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
    <span title={title} className={clsx("font-mono text-[12px]", className)}>
      {children}
    </span>
  );
}

/** A live indicator. Pulses only while something genuinely is. */
export function LiveDot({ live = true, className }: { live?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={clsx(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
        live ? "live-dot bg-accent" : "bg-faint",
        className,
      )}
    />
  );
}
