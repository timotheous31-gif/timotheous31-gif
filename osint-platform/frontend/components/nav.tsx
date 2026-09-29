"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { UserMenu } from "@/components/user-menu";

/**
 * The standing navigation rail.
 *
 * Grouped rather than flat, because the four destinations are not four of the
 * same thing: two are where investigations live, two are how the platform is
 * configured. The labels are unchanged — they are the product's own words for
 * these things, and a redesign is not the place to rename them. The active
 * item is marked by a cyan rail on its leading edge and by `aria-current`, so
 * the state survives both a glance and a screen reader.
 */
const GROUPS: { heading: string; links: { href: string; label: string; glyph: string }[] }[] = [
  {
    heading: "Operations",
    links: [
      { href: "/", label: "Dashboard", glyph: "◆" },
      { href: "/cases", label: "Cases", glyph: "▣" },
    ],
  },
  {
    heading: "Platform",
    links: [
      { href: "/collectors", label: "Collectors", glyph: "⚙" },
      { href: "/settings", label: "Settings", glyph: "◈" },
    ],
  },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav
      className={clsx(
        "flex shrink-0 flex-col border-b border-line bg-surface",
        "lg:h-screen lg:w-60 lg:border-b-0 lg:border-r",
      )}
      aria-label="Primary"
    >
      <div className="border-b border-line px-4 py-4">
        <Link href="/" className="flex items-center gap-2.5 rounded">
          <span
            aria-hidden="true"
            className="grid h-7 w-7 shrink-0 place-items-center rounded border border-accent/40 bg-accent/10 font-mono text-[11px] font-bold text-accent"
          >
            OS
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold tracking-tight">
              OSINT Platform
            </span>
            <span className="block truncate text-[10px] uppercase tracking-label text-faint">
              Public-source research
            </span>
          </span>
        </Link>
      </div>

      <div className="flex gap-4 overflow-x-auto px-2 py-3 lg:flex-col lg:gap-3 lg:overflow-visible">
        {GROUPS.map((group) => (
          <div key={group.heading}>
            <p className="hidden px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-label text-faint lg:block">
              {group.heading}
            </p>
            <ul className="flex gap-1 lg:flex-col lg:gap-0.5">
              {group.links.map((link) => {
                const active =
                  link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      aria-current={active ? "page" : undefined}
                      className={clsx(
                        "flex items-center gap-2.5 whitespace-nowrap rounded border-l-2 px-2.5 py-1.5 text-[13px]",
                        "transition-colors",
                        active
                          ? "border-l-accent bg-accent/10 font-medium text-accent"
                          : "border-l-transparent text-muted hover:bg-raised hover:text-fg",
                      )}
                    >
                      <span aria-hidden="true" className="text-[11px] opacity-80">
                        {link.glyph}
                      </span>
                      {link.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <UserMenu />
    </nav>
  );
}
