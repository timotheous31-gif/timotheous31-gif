import type { Metadata } from "next";

import "./globals.css";
import { Nav } from "@/components/nav";

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
        <div className="flex min-h-screen flex-col lg:flex-row">
          <Nav />
          <main className="min-w-0 flex-1 px-5 py-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
