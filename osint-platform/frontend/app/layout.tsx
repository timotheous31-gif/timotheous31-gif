import type { Metadata } from "next";

import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session";

export const metadata: Metadata = {
  title: "OSINT Investigation Platform",
  description:
    "A privacy-first platform for lawful open-source intelligence work: security research, " +
    "journalism, fraud analysis, threat intelligence and due diligence.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
