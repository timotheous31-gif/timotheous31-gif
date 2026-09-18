"use client";

import type { FormEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, Card, CardHeader, Input, Mono, Spinner } from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import {
  CODE_LENGTH,
  codesOf,
  createEnrollmentFlow,
  disableMfa,
  liveMfaClient,
  recoveryCodesFile,
  statusSummary,
  type EnrollmentState,
  type MfaFailure,
} from "@/lib/mfa";

/**
 * Two-factor authentication, in the account's own settings.
 *
 * The flow — password, scan, confirm, codes — is in `lib/mfa`; this is the
 * paper it is printed on. The one thing decided here is the QR code, which is
 * drawn in this browser from the provisioning URI rather than fetched as an
 * image: that URI contains the secret, and an `<img src>` pointing at a server
 * would put it through another request, another log and another cache for no
 * benefit.
 *
 * Neither the secret nor the recovery codes are written anywhere. They live in
 * the flow's state for the step that shows them and are gone when it ends —
 * including the rendered QR, which is dropped with them.
 */
export function MfaSettings() {
  const status = useAsync(() => liveMfaClient.status(), []);
  const [state, setState] = useState<EnrollmentState>({
    step: "idle",
    busy: false,
    failure: null,
  });
  const flowRef = useRef(createEnrollmentFlow(liveMfaClient, setState));
  const flow = flowRef.current;

  const enabled = status.data?.enabled ?? false;

  const finish = useCallback(() => {
    flow.finish();
    status.reload();
  }, [flow, status]);

  return (
    <Card>
      <CardHeader
        title="Two-factor authentication"
        description="A code from an authenticator app, in addition to your password."
      />
      <div className="space-y-4 p-4">
        {status.loading && !status.data ? (
          <Spinner />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={enabled ? "SUCCESS" : "SKIPPED"}>{enabled ? "On" : "Off"}</Badge>
            <span className="text-sm text-muted">{statusSummary(status.data)}</span>
          </div>
        )}

        {state.step === "idle" && !enabled ? (
          <div className="space-y-2">
            <p className="text-xs text-muted">
              With this on, signing in needs your password and a code from your phone. A
              stolen password stops being enough on its own.
            </p>
            <Button variant="primary" onClick={() => flow.begin()}>
              Set up two-factor authentication
            </Button>
          </div>
        ) : null}

        {state.step === "password" ? (
          <PasswordStep
            title="Confirm your password"
            hint="Re-entered even though you are signed in: an unlocked browser somebody walked up to should not be enough to change how this account is protected."
            action="Continue"
            busy={state.busy}
            failure={state.failure}
            onSubmit={(password) => void flow.submitPassword(password)}
            onCancel={() => flow.cancel()}
          />
        ) : null}

        {state.step === "scan" ? (
          <ScanStep
            uri={state.enrollment.otpauth_uri}
            secret={state.enrollment.secret}
            busy={state.busy}
            failure={state.failure}
            onConfirm={(code) => void flow.submitCode(code)}
            onCancel={() => flow.cancel()}
          />
        ) : null}

        {state.step === "codes" ? (
          <RecoveryCodesStep codes={codesOf(state)} onDone={finish} />
        ) : null}

        {state.step === "idle" && enabled ? <DisableSection onDisabled={status.reload} /> : null}
      </div>
    </Card>
  );
}

// --- steps ------------------------------------------------------------------

function Failure({ failure }: { failure: MfaFailure | null }) {
  if (!failure) return null;
  return (
    <p role="alert" className="text-xs text-danger">
      {failure.message}
    </p>
  );
}

function PasswordStep({
  title,
  hint,
  action,
  busy,
  failure,
  destructive = false,
  onSubmit,
  onCancel,
}: {
  title: string;
  hint: string;
  action: string;
  busy: boolean;
  failure: MfaFailure | null;
  destructive?: boolean;
  onSubmit: (password: string) => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(password);
    // Cleared whether or not it was accepted: the field has served its purpose
    // and a password sitting in a mounted input is a password on screen.
    setPassword("");
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3 rounded-md border border-line p-3">
      <div>
        <h3 className="text-xs font-semibold">{title}</h3>
        <p className="mt-0.5 text-[11px] text-muted">{hint}</p>
      </div>
      <label className="block text-xs">
        <span className="text-muted">Password</span>
        <Input
          type="password"
          name="password"
          autoComplete="current-password"
          required
          autoFocus
          value={password}
          onChange={(event: React.ChangeEvent<HTMLInputElement>) => setPassword(event.target.value)}
          className="mt-0.5"
        />
      </label>
      <Failure failure={failure} />
      <div className="flex gap-2">
        <Button type="submit" variant={destructive ? "danger" : "primary"} disabled={busy}>
          {busy ? "Working…" : action}
        </Button>
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ScanStep({
  uri,
  secret,
  busy,
  failure,
  onConfirm,
  onCancel,
}: {
  uri: string;
  secret: string;
  busy: boolean;
  failure: MfaFailure | null;
  onConfirm: (code: string) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const qr = useQrCode(uri);

  function submit(event: FormEvent) {
    event.preventDefault();
    onConfirm(code);
    setCode("");
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border border-line p-3">
      <div>
        <h3 className="text-xs font-semibold">Scan this with your authenticator app</h3>
        <p className="mt-0.5 text-[11px] text-muted">
          Nothing changes about signing in until you enter a code below. If you stop here,
          your password still works on its own.
        </p>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="rounded-md border border-line bg-white p-2">
          {qr ? (
            // A data URI generated in this tab. It is not fetched, not cached,
            // and is dropped with the rest of the step.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qr} alt="" width={160} height={160} />
          ) : (
            <div className="flex h-[160px] w-[160px] items-center justify-center">
              <Spinner label="Drawing the code" />
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-[11px] text-muted">
            Cannot scan? Enter this key in the app by hand:
          </p>
          <Mono className="block break-all text-xs">{secret}</Mono>

          <label className="block text-xs">
            <span className="text-muted">Code from the app</span>
            <Input
              name="code"
              autoComplete="one-time-code"
              inputMode="numeric"
              required
              maxLength={16}
              value={code}
              onChange={(event: React.ChangeEvent<HTMLInputElement>) => setCode(event.target.value)}
              className="mt-0.5 max-w-[10rem] font-mono tracking-[0.2em]"
              placeholder={"0".repeat(CODE_LENGTH)}
            />
          </label>
        </div>
      </div>

      <Failure failure={failure} />

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "Checking…" : "Turn it on"}
        </Button>
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function RecoveryCodesStep({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
    } catch {
      // Clipboard access can be refused — an insecure origin, or a permission
      // the user declined. The codes are on screen either way.
      setCopied(false);
    }
  }, [codes]);

  const download = useCallback(() => {
    const blob = new Blob([recoveryCodesFile(codes)], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "recovery-codes.txt";
    anchor.click();
    // Revoked immediately: the object URL is a readable handle on the codes for
    // as long as it exists, and the download has already taken its copy.
    URL.revokeObjectURL(url);
  }, [codes]);

  return (
    <div className="space-y-3 rounded-md border border-accent/50 bg-accent/5 p-3">
      <div>
        <h3 className="text-xs font-semibold">Save these recovery codes now</h3>
        <p className="mt-0.5 text-[11px] text-muted">
          This is the only time they are shown. Each one works once, in place of your
          authenticator app — they are how you get back in if you lose your phone. Store
          them somewhere you can reach <em>without</em> this account.
        </p>
      </div>

      <ul className="grid gap-1 font-mono text-xs sm:grid-cols-2">
        {codes.map((code) => (
          <li key={code} className="rounded border border-line bg-bg px-2 py-1">
            {code}
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button type="button" onClick={download}>
          Download
        </Button>
      </div>

      <label className="flex items-start gap-2 text-[11px] text-muted">
        <input
          type="checkbox"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
          className="mt-0.5"
        />
        <span>I have saved these codes somewhere I can reach without this account.</span>
      </label>

      <Button type="button" variant="primary" disabled={!saved} onClick={onDone}>
        Done
      </Button>
    </div>
  );
}

function DisableSection({ onDisabled }: { onDisabled: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<MfaFailure | null>(null);

  if (!asking) {
    return (
      <div className="border-t border-line pt-3">
        <Button type="button" variant="danger" onClick={() => setAsking(true)}>
          Turn off two-factor authentication
        </Button>
        <p className="mt-1 text-[11px] text-muted">
          This also destroys your recovery codes. Turning it on again issues a new set.
        </p>
      </div>
    );
  }

  return (
    <div className="border-t border-line pt-3">
      <PasswordStep
        title="Confirm your password to turn it off"
        hint="Removing a factor is a change to how this account is protected, so it asks for the same proof that adding one did."
        action="Turn it off"
        destructive
        busy={busy}
        failure={failure}
        onCancel={() => {
          setAsking(false);
          setFailure(null);
        }}
        onSubmit={(password) => {
          setBusy(true);
          setFailure(null);
          void disableMfa(liveMfaClient, password)
            .then((result) => {
              setFailure(result.failure);
              if (result.disabled) {
                setAsking(false);
                onDisabled();
              }
            })
            .finally(() => setBusy(false));
        }}
      />
    </div>
  );
}

// --- the QR code ------------------------------------------------------------

/**
 * Render `uri` as a data URI, in this browser.
 *
 * Loaded on demand so the encoder is not in the bundle every page pays for, and
 * cleared on unmount: the image encodes the secret, so it is dropped with the
 * step that showed it rather than left in a closure.
 */
function useQrCode(uri: string): string | null {
  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void import("qrcode")
      .then((qrcode) =>
        qrcode.toDataURL(uri, { margin: 1, width: 320, errorCorrectionLevel: "M" }),
      )
      .then((result) => {
        if (!cancelled) setDataUrl(result);
      })
      .catch(() => {
        // No QR, but the key is printed beside it and can be typed in by hand.
        if (!cancelled) setDataUrl(null);
      });
    return () => {
      cancelled = true;
      setDataUrl(null);
    };
  }, [uri]);

  return dataUrl;
}
