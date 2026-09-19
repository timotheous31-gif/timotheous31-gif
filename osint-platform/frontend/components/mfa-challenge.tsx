"use client";

import type { FormEvent } from "react";
import { useCallback, useMemo, useRef, useState } from "react";

import { Button, Card, Input } from "@/components/ui/primitives";
import { useSession } from "@/components/session";
import {
  CODE_LENGTH,
  createChallengeFlow,
  liveMfaClient,
  type ChallengeState,
} from "@/lib/mfa";

/**
 * The screen between a correct password and a signed-in session.
 *
 * The decisions — which method, what a refusal means, when the form locks —
 * live in `lib/mfa`; this draws them. Two things are deliberate:
 *
 * * **Sign out is always reachable**, including while the account is locked
 *   out. Somebody at a shared machine who cannot produce a code must be able to
 *   leave without closing the browser, and an account under attack is exactly
 *   when its owner most wants the session gone.
 * * **Nothing here is the gate.** The API refuses this session everything but
 *   this challenge and sign-out. Rendering the dashboard instead would show
 *   empty panels and 401s, not data.
 */
export function MfaChallenge() {
  const { completeMfa, signOut } = useSession();
  const [value, setValue] = useState("");
  const [state, setState] = useState<ChallengeState>({
    method: "totp",
    busy: false,
    failure: null,
    lockedOut: false,
  });
  const [leaving, setLeaving] = useState(false);

  // One flow for the life of the screen: it remembers which answer was just
  // refused, which is how a resubmitted code is recognised without spending an
  // attempt against the lockout.
  const flowRef = useRef(createChallengeFlow(liveMfaClient, setState));
  const flow = flowRef.current;

  const recovery = state.method === "recovery";

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const session = await flow.submit(value);
      if (session) {
        setValue("");
        completeMfa(session);
      }
    },
    [completeMfa, flow, value],
  );

  const switchMethod = useCallback(() => {
    setValue("");
    flow.use(recovery ? "totp" : "recovery");
  }, [flow, recovery]);

  const hint = useMemo(() => {
    if (recovery) {
      return "Enter one of the recovery codes you saved when you turned this on. Each works once.";
    }
    return `Enter the ${CODE_LENGTH}-digit code your authenticator app is showing.`;
  }, [recovery]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <Card className="w-full max-w-sm">
        <form onSubmit={submit} className="space-y-4 p-5">
          <div>
            <h1 className="text-sm font-semibold">Two-factor authentication</h1>
            <p className="mt-0.5 text-[11px] text-muted">{hint}</p>
          </div>

          <label className="block text-xs">
            <span className="text-muted">{recovery ? "Recovery code" : "Authenticator code"}</span>
            <Input
              // `one-time-code` lets a phone offer the SMS/authenticator code;
              // a recovery code is typed off paper, so it gets neither that nor
              // a numeric keypad.
              key={state.method}
              name={recovery ? "recovery-code" : "code"}
              autoComplete={recovery ? "off" : "one-time-code"}
              inputMode={recovery ? "text" : "numeric"}
              autoFocus
              required
              maxLength={recovery ? 32 : 16}
              value={value}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setValue(event.target.value)}
              className="mt-0.5 font-mono tracking-[0.2em]"
            />
          </label>

          {state.failure ? (
            <p role="alert" className="text-xs text-danger">
              {state.failure.message}
            </p>
          ) : null}

          <Button
            type="submit"
            variant="primary"
            disabled={state.busy || state.lockedOut}
            className="w-full"
          >
            {state.busy ? "Checking…" : "Continue"}
          </Button>

          <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
            <button
              type="button"
              onClick={switchMethod}
              className="rounded-md px-1 py-1 text-[11px] text-muted underline-offset-2 hover:underline"
            >
              {recovery ? "Use my authenticator app" : "Use a recovery code"}
            </button>
            {/*
              Never disabled by `lockedOut`: leaving is the one thing a user who
              cannot answer the challenge has to be able to do.
            */}
            <button
              type="button"
              disabled={leaving}
              onClick={() => {
                setLeaving(true);
                setValue("");
                void signOut().finally(() => setLeaving(false));
              }}
              className="rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-line/60"
            >
              {leaving ? "Signing out…" : "Sign out"}
            </button>
          </div>

          {state.lockedOut ? (
            <p className="text-[11px] text-muted">
              Too many attempts. The lockout clears on its own; signing out and back in
              does not shorten it.
            </p>
          ) : null}
        </form>
      </Card>
    </main>
  );
}
