"use client";

import { LoginScreen } from "@/components/login";
import { Nav } from "@/components/nav";
import { useSession } from "@/components/session";
import { Spinner } from "@/components/ui/primitives";

/**
 * Chooses between the application and the login screen.
 *
 * This is a *routing* decision, not a security one. Every page inside reaches the
 * API, and the API refuses an unauthenticated request whatever this component
 * renders — so a user who somehow got past here would see empty panels and 401s,
 * not data. What this buys is a sensible experience: one login form instead of a
 * dashboard full of failures.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { session, loading } = useSession();

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <Spinner label="Checking your session" />
      </main>
    );
  }

  if (!session) {
    return <LoginScreen />;
  }

  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <Nav />
      <main className="min-w-0 flex-1 px-5 py-6 lg:px-8">{children}</main>
    </div>
  );
}
