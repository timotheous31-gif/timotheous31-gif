"use client";

import { LoginScreen } from "@/components/login";
import { MfaChallenge } from "@/components/mfa-challenge";
import { Nav } from "@/components/nav";
import { useSession } from "@/components/session";
import { Spinner } from "@/components/ui/primitives";
import { needsChallenge } from "@/lib/mfa";

/**
 * Chooses between the application and the login screen.
 *
 * This is a *routing* decision, not a security one. Every page inside reaches the
 * API, and the API refuses an unauthenticated request whatever this component
 * renders — so a user who somehow got past here would see empty panels and 401s,
 * not data. What this buys is a sensible experience: one login form instead of a
 * dashboard full of failures.
 *
 * The frame: on a wide screen the rail is fixed and only the content column
 * scrolls, so the workspace, the role and the way out never leave the screen
 * during a long evidence table. Below `lg` it collapses to a horizontal strip
 * above the content, which is the layout that was already here.
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

  // The password was right but the second factor is still owed. The API refuses
  // this session everything but the challenge and sign-out, so rendering the
  // application here would draw a dashboard of 401s, not data.
  if (needsChallenge(session)) {
    return <MfaChallenge />;
  }

  return (
    <div className="flex min-h-screen flex-col lg:h-screen lg:flex-row lg:overflow-hidden">
      <Nav />
      <main className="min-w-0 flex-1 px-5 py-6 lg:overflow-y-auto lg:px-8 lg:py-7">
        {children}
      </main>
    </div>
  );
}
