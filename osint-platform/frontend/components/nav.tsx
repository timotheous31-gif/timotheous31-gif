"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Dashboard", glyph: "▤" },
  { href: "/cases", label: "Cases", glyph: "▣" },
  { href: "/collectors", label: "Collectors", glyph: "⚙" },
  { href: "/settings", label: "Settings", glyph: "◈" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav
      className="shrink-0 border-b border-line bg-panel lg:h-screen lg:w-56 lg:border-b-0 lg:border-r"
      aria-label="Primary"
    >
      <div className="px-4 py-4">
        <Link href="/" className="block">
          <span className="text-sm font-semibold">OSINT Platform</span>
          <span className="mt-0.5 block text-[11px] text-muted">Lawful public-source research</span>
        </Link>
      </div>
      <ul className="flex gap-1 overflow-x-auto px-2 pb-3 lg:flex-col lg:gap-0.5 lg:overflow-visible">
        {LINKS.map((link) => {
          const active =
            link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <li key={link.href}>
              <Link
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm",
                  active ? "bg-accent/10 font-medium text-accent" : "text-fg hover:bg-line/60",
                )}
              >
                <span aria-hidden="true" className="text-xs">
                  {link.glyph}
                </span>
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
