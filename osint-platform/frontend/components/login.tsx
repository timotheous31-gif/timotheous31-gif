"use client";

import type { FormEvent } from "react";
import { useState } from "react";

import { Button, Card, Input } from "@/components/ui/primitives";
import { useSession } from "@/components/session";
import { ApiError } from "@/lib/api";

/**
 * The sign-in screen.
 *
 * One message for every failure, matching the backend: "those credentials are
 * not valid" whether the address is unknown, the password is wrong, or the
 * account is deactivated. Telling the three apart would turn this form into a
 * tool for discovering who has an account here.
 */
export function LoginScreen() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setError(cause.message);
      } else if (cause instanceof ApiError) {
        setError("Those credentials are not valid.");
      } else {
        setError("Could not reach the API. Check that the backend is running.");
      }
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <Card className="w-full max-w-sm">
        <form onSubmit={submit} className="space-y-4 p-5">
          <div>
            <h1 className="text-sm font-semibold">OSINT Platform</h1>
            <p className="mt-0.5 text-[11px] text-muted">
              Sign in to reach your workspace.
            </p>
          </div>

          <label className="block text-xs">
            <span className="text-muted">Email</span>
            <Input
              type="email"
              name="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setEmail(event.target.value)}
              className="mt-0.5"
            />
          </label>

          <label className="block text-xs">
            <span className="text-muted">Password</span>
            <Input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setPassword(event.target.value)}
              className="mt-0.5"
            />
          </label>

          {error ? (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          ) : null}

          <Button type="submit" variant="primary" disabled={busy} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>

          <p className="text-[11px] text-muted">
            No account yet? The first one is created from the server:{" "}
            <code className="font-mono">python -m app.cli admin create-admin</code>. After
            that, an owner or admin invites the rest of the team.
          </p>
        </form>
      </Card>
    </main>
  );
}
