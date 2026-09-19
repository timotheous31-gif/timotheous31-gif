/**
 * The two-factor flows, kept out of the components that draw them.
 *
 * Everything with a decision in it lives here — which step comes next, what a
 * failure means, when secret material is dropped — so it can be tested in the
 * node environment this project already uses, rather than behind a rendered
 * form. The components are left thin enough to read at a glance.
 *
 * Two rules hold throughout:
 *
 * 1. **Secret material lives in one place and for one step.** The TOTP secret
 *    arrives in the enrolment response and the recovery codes in the
 *    confirmation response; both are held in this module's transient state and
 *    dropped the moment the step that shows them ends. Nothing here writes to
 *    `localStorage`, `sessionStorage`, a cookie, or the console — there is a
 *    test that reads this file and fails if that ever changes.
 * 2. **Nothing here is a security control.** The API refuses a pending session
 *    everything but the challenge and sign-out; this module only decides which
 *    screen to draw. A user who forced the state machine past the challenge
 *    would see an application that 401s on every panel.
 */

import { ApiError, NetworkError, api } from "@/lib/api";
import type { MfaEnabled, MfaEnrollment, MfaStatus, SessionInfo } from "@/types/api";

/** Digits in an authenticator code, matching the backend's `TOTP_DIGITS`. */
export const CODE_LENGTH = 6;

/**
 * The calls the flows need.
 *
 * An interface rather than a direct import of `api` so a test can hand the
 * flows a stub without a module mock or a fetch shim — the same reason the rest
 * of `lib/` takes its data as arguments.
 */
export interface MfaClient {
  status(): Promise<MfaStatus>;
  enroll(password: string): Promise<MfaEnrollment>;
  confirm(code: string): Promise<MfaEnabled>;
  verify(answer: { code?: string; recovery_code?: string }): Promise<SessionInfo>;
  disable(password: string): Promise<void>;
  logout(): Promise<void>;
}

/** The real client, wired to the API module. */
export const liveMfaClient: MfaClient = {
  status: () => api.mfaStatus(),
  enroll: (password) => api.mfaEnroll(password),
  confirm: (code) => api.mfaConfirm(code),
  verify: (answer) => api.mfaVerify(answer),
  disable: (password) => api.mfaDisable(password),
  logout: () => api.logout(),
};

// --- failures ---------------------------------------------------------------

/**
 * What went wrong, in terms the screen can act on.
 *
 * `replayed` and `invalid` are separate states because they call for different
 * things from the user: one means wait a few seconds, the other means check the
 * app. The backend draws that distinction during enrolment but deliberately not
 * during the sign-in challenge, where every wrong answer gets the same reply —
 * "that code was already used" would confirm to a guesser that they had found a
 * real code. So on the challenge the distinction is made here, from what this
 * browser already knows: that it just sent that exact code and was refused.
 */
export type MfaFailureKind =
  | "malformed"
  | "invalid"
  | "replayed"
  | "throttled"
  | "password"
  | "conflict"
  | "not_enabled"
  | "session"
  | "offline"
  | "unknown";

export interface MfaFailure {
  kind: MfaFailureKind;
  /** Ready to show. The backend's own wording is preferred where it has one. */
  message: string;
}

/** Where the failure happened, which changes what a 401 means. */
export type MfaContext = "challenge" | "enrollment" | "password";

const OFFLINE = "Could not reach the API. Check that the backend is running.";

/**
 * Turn a thrown error into a state the screen can render.
 *
 * `authentication_required` is the ambiguous one: on the challenge it is the
 * API saying the code was wrong, everywhere else it is the password being
 * wrong. Both are 401 with the same code, so the context decides — and a
 * session that genuinely expired resolves itself on the next request, which
 * bounces the app back to the login screen.
 */
export function describeMfaFailure(error: unknown, context: MfaContext): MfaFailure {
  if (error instanceof NetworkError) return { kind: "offline", message: OFFLINE };
  if (!(error instanceof ApiError)) {
    return {
      kind: "unknown",
      message: error instanceof Error && error.message ? error.message : "Something went wrong.",
    };
  }

  switch (error.code) {
    case "rate_limited":
      return { kind: "throttled", message: error.message };
    case "mfa_required":
      return { kind: "session", message: error.message };
    case "conflict":
      return { kind: "conflict", message: error.message };
    case "not_found":
      return { kind: "not_enabled", message: error.message };
    case "csrf_failed":
      return {
        kind: "session",
        message: "Your session could not be verified. Reload the page and try again.",
      };
    case "authentication_required":
      return context === "challenge"
        ? { kind: "invalid", message: error.message }
        : { kind: "password", message: error.message };
    case "validation_error":
      if (/already been used/i.test(error.message)) {
        return { kind: "replayed", message: error.message };
      }
      if (/\bdigits\b/i.test(error.message)) {
        return { kind: "malformed", message: error.message };
      }
      return { kind: "invalid", message: error.message };
    default:
      return { kind: "unknown", message: error.message };
  }
}

// --- codes ------------------------------------------------------------------

/** An authenticator code with the spaces some apps display stripped out. */
export function normaliseCode(raw: string): string {
  return Array.from(raw ?? "")
    .filter((character) => character >= "0" && character <= "9")
    .join("");
}

/**
 * A recovery code as the backend compares it: upper case, no separators.
 *
 * Mirrors `normalize_recovery_code` server-side. Doing it here too means the
 * "looks complete" check below agrees with what will actually be accepted,
 * rather than refusing to submit a code somebody typed without the dash.
 */
export function normaliseRecoveryCode(raw: string): string {
  return Array.from((raw ?? "").toUpperCase())
    .filter((character) => /[A-Z0-9]/.test(character))
    .join("");
}

/** Whether an authenticator code is worth spending an attempt on. */
export function isCompleteCode(raw: string): boolean {
  return normaliseCode(raw).length === CODE_LENGTH;
}

/** Whether a recovery code is long enough to be one at all. */
export function isCompleteRecoveryCode(raw: string): boolean {
  return normaliseRecoveryCode(raw).length >= 8;
}

// --- routing ----------------------------------------------------------------

/**
 * Whether this session still owes its second factor.
 *
 * The one place the app decides between the challenge screen and the
 * application. It reads a flag the API set; it does not compute one. If the
 * flag were stripped, the app would render its panels and the API would refuse
 * every one of them — the gate is server-side and this is the signpost.
 */
export function needsChallenge(session: SessionInfo | null | undefined): boolean {
  return session?.mfa_required === true;
}

// --- the sign-in challenge --------------------------------------------------

export type ChallengeMethod = "totp" | "recovery";

export interface ChallengeState {
  method: ChallengeMethod;
  busy: boolean;
  failure: MfaFailure | null;
  /** True once the API has throttled us; the form stays disabled. */
  lockedOut: boolean;
}

export interface ChallengeFlow {
  readonly state: ChallengeState;
  /** Swap between the authenticator and a recovery code. Clears the error. */
  use(method: ChallengeMethod): ChallengeState;
  /** Answer the challenge. Resolves with the session on success. */
  submit(value: string): Promise<SessionInfo | null>;
  /** Always available, including while locked out. */
  signOut(): Promise<void>;
}

/**
 * The screen a session sees between its password and its second factor.
 *
 * Sign-out is a method here rather than something the component reaches for
 * separately because it is the one action that must never be gated: a user who
 * cannot produce a code — wrong phone, wrong account, a shared machine they
 * want to leave — has to be able to get out without closing the browser.
 */
export function createChallengeFlow(
  client: MfaClient,
  onChange?: (state: ChallengeState) => void,
): ChallengeFlow {
  let state: ChallengeState = {
    method: "totp",
    busy: false,
    failure: null,
    lockedOut: false,
  };
  // The last answer this browser sent and had refused. Held only to recognise a
  // resubmission of the same code, and overwritten by the next attempt.
  let refused: string | null = null;

  function set(next: Partial<ChallengeState>): ChallengeState {
    state = { ...state, ...next };
    onChange?.(state);
    return state;
  }

  return {
    get state() {
      return state;
    },

    use(method) {
      refused = null;
      return set({ method, failure: null });
    },

    async submit(value) {
      if (state.busy) return null;
      const recovery = state.method === "recovery";
      const answer = recovery ? normaliseRecoveryCode(value) : normaliseCode(value);

      if (!answer) {
        set({
          failure: {
            kind: "malformed",
            message: recovery
              ? "Enter one of your recovery codes."
              : "Enter the code your authenticator app is showing.",
          },
        });
        return null;
      }
      if (!recovery && answer.length !== CODE_LENGTH) {
        set({
          failure: { kind: "malformed", message: `A code is ${CODE_LENGTH} digits.` },
        });
        return null;
      }
      if (answer === refused) {
        // The API would refuse this identically and spend an attempt against
        // the lockout doing it. Saying so here is a nicety, not a control.
        set({
          failure: {
            kind: "replayed",
            message: recovery
              ? "That recovery code was already refused. Each code works once."
              : "That code was already tried. Wait for your app to show the next one.",
          },
        });
        return null;
      }

      set({ busy: true, failure: null });
      try {
        const session = await client.verify(recovery ? { recovery_code: answer } : { code: answer });
        refused = null;
        set({ busy: false, failure: null });
        return session;
      } catch (error) {
        const failure = describeMfaFailure(error, "challenge");
        refused = failure.kind === "invalid" ? answer : null;
        set({ busy: false, failure, lockedOut: failure.kind === "throttled" });
        return null;
      }
    },

    async signOut() {
      await client.logout();
    },
  };
}

// --- enrolment --------------------------------------------------------------

/**
 * Where an enrolment has got to.
 *
 * `scan` is the only state that carries the secret and `codes` the only one
 * that carries the recovery codes. Leaving either — by finishing, cancelling,
 * or failing back to the start — replaces the whole state object, so there is
 * no step in this machine from which the previous step's secret can be read.
 */
export type EnrollmentState =
  | { step: "idle"; busy: boolean; failure: MfaFailure | null }
  | { step: "password"; busy: boolean; failure: MfaFailure | null }
  | { step: "scan"; busy: boolean; failure: MfaFailure | null; enrollment: MfaEnrollment }
  | { step: "codes"; busy: boolean; failure: MfaFailure | null; codes: string[] };

export type EnrollmentStep = EnrollmentState["step"];

export interface EnrollmentFlow {
  readonly state: EnrollmentState;
  /** Ask for the password that has to precede an enrolment. */
  begin(): EnrollmentState;
  /** Re-authenticate and fetch the secret. */
  submitPassword(password: string): Promise<EnrollmentState>;
  /** Prove the authenticator holds it, and turn the factor on. */
  submitCode(code: string): Promise<EnrollmentState>;
  /** Leave the one-time recovery-code display. The codes are dropped here. */
  finish(): EnrollmentState;
  /** Abandon the enrolment at any point. */
  cancel(): EnrollmentState;
}

/**
 * Enrolment: password, then scan, then confirm, then the codes — once.
 *
 * The order is the backend's and is not negotiable from here: the factor is
 * inactive until `/auth/mfa/confirm` accepts a code, so a user who scans the QR
 * and then loses the phone still signs in with their password.
 */
export function createEnrollmentFlow(
  client: MfaClient,
  onChange?: (state: EnrollmentState) => void,
): EnrollmentFlow {
  let state: EnrollmentState = { step: "idle", busy: false, failure: null };

  function set(next: EnrollmentState): EnrollmentState {
    state = next;
    onChange?.(state);
    return state;
  }

  function fail(error: unknown, context: MfaContext): EnrollmentState {
    const failure = describeMfaFailure(error, context);
    if (state.step === "scan") {
      return set({ ...state, busy: false, failure });
    }
    // Anywhere else, drop back to the password step rather than leaving a
    // half-started enrolment on screen.
    return set({ step: "password", busy: false, failure });
  }

  return {
    get state() {
      return state;
    },

    begin() {
      return set({ step: "password", busy: false, failure: null });
    },

    async submitPassword(password) {
      if (state.busy) return state;
      if (!password) {
        return set({
          step: "password",
          busy: false,
          failure: { kind: "password", message: "Enter your password to continue." },
        });
      }
      set({ step: "password", busy: true, failure: null });
      try {
        const enrollment = await client.enroll(password);
        return set({ step: "scan", busy: false, failure: null, enrollment });
      } catch (error) {
        return fail(error, "password");
      }
    },

    async submitCode(code) {
      if (state.step !== "scan" || state.busy) return state;
      const answer = normaliseCode(code);
      if (answer.length !== CODE_LENGTH) {
        return set({
          ...state,
          failure: { kind: "malformed", message: `A code is ${CODE_LENGTH} digits.` },
        });
      }
      set({ ...state, busy: true, failure: null });
      try {
        const enabled = await client.confirm(answer);
        // The secret-bearing state is replaced, not amended: from here on there
        // is nowhere left in this flow holding it.
        return set({ step: "codes", busy: false, failure: null, codes: enabled.recovery_codes });
      } catch (error) {
        return fail(error, "enrollment");
      }
    },

    finish() {
      return set({ step: "idle", busy: false, failure: null });
    },

    cancel() {
      return set({ step: "idle", busy: false, failure: null });
    },
  };
}

/** The secret a `scan` state is showing, or null anywhere else. */
export function secretOf(state: EnrollmentState): string | null {
  return state.step === "scan" ? state.enrollment.secret : null;
}

/** The codes a `codes` state is showing, or an empty list anywhere else. */
export function codesOf(state: EnrollmentState): string[] {
  return state.step === "codes" ? state.codes : [];
}

// --- disabling --------------------------------------------------------------

export interface DisableResult {
  disabled: boolean;
  failure: MfaFailure | null;
}

/**
 * Turn the factor off, after the same re-authentication that turned it on.
 *
 * A single function rather than a flow: there is one step, and the password
 * prompt is the whole of it.
 */
export async function disableMfa(client: MfaClient, password: string): Promise<DisableResult> {
  if (!password) {
    return {
      disabled: false,
      failure: { kind: "password", message: "Enter your password to continue." },
    };
  }
  try {
    await client.disable(password);
    return { disabled: true, failure: null };
  } catch (error) {
    return { disabled: false, failure: describeMfaFailure(error, "password") };
  }
}

// --- presentation -----------------------------------------------------------

/** One line describing the account's current protection. */
export function statusSummary(status: MfaStatus | null): string {
  if (!status) return "Checking…";
  if (!status.enabled) {
    return "Your account is protected by a password alone.";
  }
  if (status.recovery_codes_remaining === 0) {
    return "Two-factor authentication is on. No recovery codes are left — turn it off and on again to issue a new set.";
  }
  const codes = status.recovery_codes_remaining;
  return `Two-factor authentication is on. ${codes} recovery ${codes === 1 ? "code" : "codes"} unused.`;
}

/**
 * The recovery codes as a file the user can keep.
 *
 * Plain text, no account identifier beyond what they already know: a file
 * naming the service and the codes together is a slightly worse thing to find
 * on a shared drive than one holding codes alone.
 */
export function recoveryCodesFile(codes: string[]): string {
  return [
    "Recovery codes",
    "",
    "Each of these works once, in place of your authenticator app.",
    "Store them somewhere you can reach without this account.",
    "",
    ...codes,
    "",
  ].join("\n");
}
