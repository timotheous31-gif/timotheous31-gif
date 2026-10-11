"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { UserMenu } from "@/components/user-menu";

/**
 * The console rail.
 *
 * Four destinations, named for what an operator does rather than for the
 * tables underneath: operations, investigations, sources, system. Routes are
 * unchanged.
 *
 * The active item is marked three ways — a 2px cyan rail on its leading edge,
 * a faint tinted fill, and `aria-current` — so the state survives a glance, a
 * greyscale print and a screen reader. Inactive items are low-contrast text
 * with no box around them: a rail of buttons reads as a toolbar, and this is
 * not a toolbar.
 */
const LINKS = [
  { href: "/", label: "Operations", glyph: "◆" },
  { href: "/cases", label: "Investigations", glyph: "▣" },
  { href: "/collectors", label: "Sources", glyph: "⌁" },
  { href: "/settings", label: "System", glyph: "⚙" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav
      className={clsx(
        "relative z-10 flex shrink-0 flex-col",
        "border-b border-rule bg-command/80 backdrop-blur-sm",
        "lg:h-screen lg:w-[244px] lg:border-b-0 lg:border-r",
      )}
      aria-label="Primary"
    >
      {/* A hairline of light down the rail's inner edge, so it reads as the
          side of a housing rather than a divider. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 hidden w-px bg-gradient-to-b from-accent/25 via-rule to-transparent lg:block"
      />

      <div className="border-b border-rule-quiet px-4 py-[18px]">
        <Link href="/" className="flex items-center gap-3 rounded">
          {/* The insignia: a machined monogram, not a logo. */}
          <span
            aria-hidden="true"
            className="relative grid h-8 w-8 shrink-0 place-items-center rounded-sm border border-accent/35 bg-accent/10 font-mono text-[11px] font-bold tracking-tight text-accent shadow-[inset_0_1px_0_0_rgb(255_255_255/0.06)]"
          >
            OS
            <span className="absolute -right-px -top-px h-1.5 w-1.5 border-r border-t border-accent/70" />
            <span className="absolute -bottom-px -left-px h-1.5 w-1.5 border-b border-l border-accent/70" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13.5px] font-semibold leading-tight tracking-tight text-fg">
              OSINT Platform
            </span>
            <span className="eyebrow mt-1 block truncate !tracking-[0.12em]">Public source</span>
          </span>
        </Link>
      </div>

      <ul className="flex gap-1 overflow-x-auto px-2 py-3 lg:flex-col lg:gap-0.5 lg:overflow-visible">
        {LINKS.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <li key={link.href}>
              <Link
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "group relative flex items-center gap-2.5 whitespace-nowrap py-2 pl-3.5 pr-3",
                  "text-[12.5px] font-medium uppercase tracking-wide transition-colors",
                  active
                    ? "bg-accent/[0.07] text-accent"
                    : "text-faint hover:bg-raised/60 hover:text-fg",
                )}
              >
                {active ? (
                  <span
                    aria-hidden="true"
                    className="absolute inset-y-0 left-0 w-0.5 bg-accent shadow-[0_0_10px_0_rgb(var(--accent)/0.6)]"
                  />
                ) : null}
                <span
                  aria-hidden="true"
                  className={clsx(
                    "text-[10px] transition-colors",
                    active ? "text-accent" : "text-faint/70 group-hover:text-muted",
                  )}
                >
                  {link.glyph}
                </span>
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>

      <UserMenu />
    </nav>
  );
}
