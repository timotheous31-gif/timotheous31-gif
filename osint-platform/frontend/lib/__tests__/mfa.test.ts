import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "@/lib/api";
import {
  CODE_LENGTH,
  codesOf,
  createChallengeFlow,
  createEnrollmentFlow,
  describeMfaFailure,
  disableMfa,
  isCompleteCode,
  isCompleteRecoveryCode,
  needsChallenge,
  normaliseCode,
  normaliseRecoveryCode,
  recoveryCodesFile,
  secretOf,
  statusSummary,
  type MfaClient,
} from "@/lib/mfa";
import type { MfaEnabled, MfaEnrollment, MfaStatus, SessionInfo } from "@/types/api";

const ROOT = path.resolve(__dirname, "..", "..");

function session(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    user: {
      id: "u1",
      email: "analyst@example.test",
      display_name: "Analyst",
      is_active: true,
      is_superuser: false,
      created_at: "2026-01-01T00:00:00Z",
    },
    workspaces: [],
    csrf_token: "csrf",
    expires_at: "2026-01-02T00:00:00Z",
    ...overrides,
  } as SessionInfo;
}

function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, { code, message });
}

/**
 * A stand-in for the API that records what it was asked and answers from a
 * script. Nothing here touches fetch, so the flows are exercised exactly as the
 * components drive them.
 */
function stubClient(overrides: Partial<MfaClient> = {}) {
  const calls: Array<{ name: string; argument: unknown }> = [];
  const scripted: MfaClient = {
    status: async () => disabledStatus,
    enroll: async () => enrollment,
    confirm: async () => enabled,
    verify: async () => session({ mfa_required: false }),
    disable: async () => undefined,
    logout: async () => undefined,
    ...overrides,
  };
  const client = Object.fromEntries(
    Object.entries(scripted).map(([name, implementation]) => [
      name,
      async (argument: never) => {
        calls.push({ name, argument });
        return (implementation as (value: never) => unknown)(argument);
      },
    ]),
  ) as unknown as MfaClient;
  return { client, calls, names: () => calls.map((call) => call.name) };
}

const disabledStatus: MfaStatus = {
  enabled: false,
  confirmed_at: null,
  recovery_codes_remaining: 0,
};
const enrollment: MfaEnrollment = {
  secret: "JBSWY3DPEHPK3PXP",
  otpauth_uri:
    "otpauth://totp/OSINT%20Platform:analyst@example.test?secret=JBSWY3DPEHPK3PXP&issuer=OSINT%20Platform",
  confirmed: false,
};
const enabled: MfaEnabled = {
  enabled: true,
  recovery_codes: ["ABCDE-FGHJK", "KLMNP-QRSTV", "WXYZ2-34567"],
};

// --- 1. password login lands on the challenge -------------------------------

describe("routing a session that still owes a second factor", () => {
  it("sends a session flagged mfa_required to the challenge, not the app", () => {
    expect(needsChallenge(session({ mfa_required: true }))).toBe(true);
  });

  it("lets an ordinary session straight through", () => {
    expect(needsChallenge(session())).toBe(false);
    expect(needsChallenge(session({ mfa_required: false }))).toBe(false);
  });

  it("treats no session at all as no challenge — that is the login screen", () => {
    expect(needsChallenge(null)).toBe(false);
    expect(needsChallenge(undefined)).toBe(false);
  });
});

// --- 2. completing the challenge with an authenticator code -----------------

describe("answering the challenge with an authenticator code", () => {
  it("sends the code and returns the now-complete session", async () => {
    const { client, calls } = stubClient();
    const flow = createChallengeFlow(client);

    const result = await flow.submit("123 456");

    expect(calls.filter((call) => call.name === "verify")).toHaveLength(1);
    expect(calls.find((call) => call.name === "verify")?.argument).toEqual({ code: "123456" });
    expect(needsChallenge(result)).toBe(false);
    expect(flow.state.failure).toBeNull();
    expect(flow.state.busy).toBe(false);
  });

  it("refuses to spend an attempt on something that is not six digits", async () => {
    const { client, names } = stubClient();
    const flow = createChallengeFlow(client);

    expect(await flow.submit("12345")).toBeNull();

    expect(names()).not.toContain("verify");
    expect(flow.state.failure?.kind).toBe("malformed");
    expect(flow.state.failure?.message).toContain(String(CODE_LENGTH));
  });
});

// --- 3. completing the challenge with a recovery code -----------------------

describe("answering the challenge with a recovery code", () => {
  it("sends it as a recovery code, normalised the way the backend compares it", async () => {
    const { client, calls } = stubClient();
    const flow = createChallengeFlow(client);

    flow.use("recovery");
    const result = await flow.submit(" abcde-fghjk ");

    expect(calls.find((call) => call.name === "verify")?.argument).toEqual({
      recovery_code: "ABCDEFGHJK",
    });
    expect(result).not.toBeNull();
    expect(flow.state.failure).toBeNull();
  });

  it("clears an authenticator failure when the user switches to a recovery code", async () => {
    const { client } = stubClient({
      verify: async () => {
        throw apiError(401, "authentication_required", "That code is not valid.");
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("000000");
    expect(flow.state.failure?.kind).toBe("invalid");

    flow.use("recovery");
    expect(flow.state.failure).toBeNull();
    expect(flow.state.method).toBe("recovery");
  });
});

// --- 4. enrolment, confirmation, and the one-time recovery codes ------------

describe("enrolling an authenticator", () => {
  it("asks for the password, hands back the secret, then the codes", async () => {
    const { client, calls } = stubClient();
    const seen: string[] = [];
    const flow = createEnrollmentFlow(client, (state) => seen.push(state.step));

    expect(flow.begin().step).toBe("password");

    const scanning = await flow.submitPassword("hunter2-hunter2");
    expect(scanning.step).toBe("scan");
    expect(secretOf(scanning)).toBe(enrollment.secret);
    expect(calls.find((call) => call.name === "enroll")?.argument).toBe("hunter2-hunter2");

    const showing = await flow.submitCode("123456");
    expect(showing.step).toBe("codes");
    expect(codesOf(showing)).toEqual(enabled.recovery_codes);

    expect(seen).toEqual(["password", "password", "scan", "scan", "codes"]);
  });

  it("drops the secret the moment the scan step ends", async () => {
    const { client } = stubClient();
    const flow = createEnrollmentFlow(client);

    flow.begin();
    const scanning = await flow.submitPassword("hunter2-hunter2");
    expect(secretOf(scanning)).toBe(enrollment.secret);

    const showing = await flow.submitCode("123456");
    expect(secretOf(showing)).toBeNull();
    expect(secretOf(flow.state)).toBeNull();
  });

  it("drops the recovery codes when the user leaves the one-time display", async () => {
    const { client } = stubClient();
    const flow = createEnrollmentFlow(client);

    flow.begin();
    await flow.submitPassword("hunter2-hunter2");
    await flow.submitCode("123456");
    expect(codesOf(flow.state)).toHaveLength(3);

    const after = flow.finish();
    expect(after.step).toBe("idle");
    expect(codesOf(after)).toEqual([]);
    expect(codesOf(flow.state)).toEqual([]);
    expect(JSON.stringify(flow.state)).not.toContain("ABCDE");
  });

  it("keeps a wrong confirmation code on the scan step so the secret is still scannable", async () => {
    const { client } = stubClient({
      confirm: async () => {
        throw apiError(400, "validation_error", "That code is not valid. Check your authenticator app and try again.");
      },
    });
    const flow = createEnrollmentFlow(client);

    flow.begin();
    await flow.submitPassword("hunter2-hunter2");
    const after = await flow.submitCode("000000");

    expect(after.step).toBe("scan");
    expect(after.failure?.kind).toBe("invalid");
    expect(secretOf(after)).toBe(enrollment.secret);
  });

  it("tells a user who reused a code to wait for the next one", async () => {
    const { client } = stubClient({
      confirm: async () => {
        throw apiError(
          400,
          "validation_error",
          "That code has already been used. Wait for your authenticator app to show the next one.",
        );
      },
    });
    const flow = createEnrollmentFlow(client);

    flow.begin();
    await flow.submitPassword("hunter2-hunter2");
    const after = await flow.submitCode("123456");

    expect(after.failure?.kind).toBe("replayed");
    expect(after.failure?.message).toMatch(/next one/i);
  });

  it("falls back to the password step when the password is refused", async () => {
    const { client, names } = stubClient({
      enroll: async () => {
        throw apiError(401, "authentication_required", "Your password is not correct");
      },
    });
    const flow = createEnrollmentFlow(client);

    flow.begin();
    const after = await flow.submitPassword("wrong");

    expect(after.step).toBe("password");
    expect(after.failure?.kind).toBe("password");
    expect(names()).not.toContain("confirm");
  });

  it("never asks the API for a secret without a password", async () => {
    const { client, names } = stubClient();
    const flow = createEnrollmentFlow(client);

    flow.begin();
    const after = await flow.submitPassword("");

    expect(names()).not.toContain("enroll");
    expect(after.step).toBe("password");
    expect(after.failure?.kind).toBe("password");
  });

  it("abandons an enrolment without leaving the secret behind", async () => {
    const { client } = stubClient();
    const flow = createEnrollmentFlow(client);

    flow.begin();
    await flow.submitPassword("hunter2-hunter2");
    const after = flow.cancel();

    expect(after.step).toBe("idle");
    expect(secretOf(after)).toBeNull();
    expect(JSON.stringify(flow.state)).not.toContain(enrollment.secret);
  });
});

// --- 5. turning the factor off ----------------------------------------------

describe("disabling two-factor authentication", () => {
  it("re-authenticates and reports success", async () => {
    const { client, calls } = stubClient();

    const result = await disableMfa(client, "hunter2-hunter2");

    expect(result).toEqual({ disabled: true, failure: null });
    expect(calls.find((call) => call.name === "disable")?.argument).toBe("hunter2-hunter2");
  });

  it("does not call the API without a password", async () => {
    const { client, names } = stubClient();

    const result = await disableMfa(client, "");

    expect(result.disabled).toBe(false);
    expect(result.failure?.kind).toBe("password");
    expect(names()).not.toContain("disable");
  });

  it("reports a refused password without disabling anything", async () => {
    const { client } = stubClient({
      disable: async () => {
        throw apiError(401, "authentication_required", "Your password is not correct");
      },
    });

    const result = await disableMfa(client, "wrong");

    expect(result.disabled).toBe(false);
    expect(result.failure?.kind).toBe("password");
  });

  it("reports an account that had no factor to remove", async () => {
    const { client } = stubClient({
      disable: async () => {
        throw apiError(404, "not_found", "Two-factor authentication is not enabled on this account.");
      },
    });

    const result = await disableMfa(client, "hunter2-hunter2");

    expect(result.failure?.kind).toBe("not_enabled");
  });
});

// --- 6. invalid and throttled challenges ------------------------------------

describe("a challenge that fails", () => {
  it("shows the API's refusal and stays on the challenge", async () => {
    const { client } = stubClient({
      verify: async () => {
        throw apiError(401, "authentication_required", "That code is not valid.");
      },
    });
    const flow = createChallengeFlow(client);

    const result = await flow.submit("000000");

    expect(result).toBeNull();
    expect(flow.state.failure?.kind).toBe("invalid");
    expect(flow.state.lockedOut).toBe(false);
    expect(flow.state.busy).toBe(false);
  });

  it("recognises a resubmitted code without spending another attempt", async () => {
    let attempts = 0;
    const { client } = stubClient({
      verify: async () => {
        attempts += 1;
        throw apiError(401, "authentication_required", "That code is not valid.");
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("000000");
    expect(attempts).toBe(1);

    await flow.submit("000000");
    expect(attempts).toBe(1);
    expect(flow.state.failure?.kind).toBe("replayed");

    await flow.submit("111111");
    expect(attempts).toBe(2);
  });

  it("locks the form when the API throttles, and keeps the API's wording", async () => {
    const { client } = stubClient({
      verify: async () => {
        throw apiError(429, "rate_limited", "Too many codes tried. Wait a few minutes and try again.");
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("000000");

    expect(flow.state.failure?.kind).toBe("throttled");
    expect(flow.state.failure?.message).toMatch(/wait a few minutes/i);
    expect(flow.state.lockedOut).toBe(true);
  });

  it("separates an unreachable API from a rejected code", async () => {
    const { client } = stubClient({
      verify: async () => {
        throw new NetworkError(new Error("connection refused"));
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("000000");

    expect(flow.state.failure?.kind).toBe("offline");
    expect(flow.state.lockedOut).toBe(false);
  });

  it("does not treat an unreachable API as a code worth remembering", async () => {
    let attempts = 0;
    const { client } = stubClient({
      verify: async () => {
        attempts += 1;
        throw new NetworkError(new Error("connection refused"));
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("123456");
    await flow.submit("123456");

    expect(attempts).toBe(2);
  });
});

// --- 7. leaving from the challenge ------------------------------------------

describe("signing out of the challenge", () => {
  it("is available from the challenge state", async () => {
    const { client, names } = stubClient();
    const flow = createChallengeFlow(client);

    await flow.signOut();

    expect(names()).toContain("logout");
  });

  it("is still available after the form has been locked out", async () => {
    const { client, names } = stubClient({
      verify: async () => {
        throw apiError(429, "rate_limited", "Too many codes tried.");
      },
    });
    const flow = createChallengeFlow(client);

    await flow.submit("000000");
    expect(flow.state.lockedOut).toBe(true);

    await flow.signOut();
    expect(names()).toContain("logout");
  });
});

// --- failure mapping --------------------------------------------------------

describe("describeMfaFailure", () => {
  it("reads 401 as a wrong code on the challenge and a wrong password elsewhere", () => {
    const error = apiError(401, "authentication_required", "nope");
    expect(describeMfaFailure(error, "challenge").kind).toBe("invalid");
    expect(describeMfaFailure(error, "password").kind).toBe("password");
    expect(describeMfaFailure(error, "enrollment").kind).toBe("password");
  });

  it("maps every code the MFA routes can raise", () => {
    const cases: Array<[string, string]> = [
      ["rate_limited", "throttled"],
      ["mfa_required", "session"],
      ["conflict", "conflict"],
      ["not_found", "not_enabled"],
      ["csrf_failed", "session"],
    ];
    for (const [code, kind] of cases) {
      expect(describeMfaFailure(apiError(400, code, "message"), "enrollment").kind).toBe(kind);
    }
  });

  it("splits validation errors into malformed, replayed and invalid", () => {
    expect(describeMfaFailure(apiError(400, "validation_error", "A code is 6 digits."), "enrollment").kind).toBe(
      "malformed",
    );
    expect(
      describeMfaFailure(apiError(400, "validation_error", "That code has already been used."), "enrollment").kind,
    ).toBe("replayed");
    expect(describeMfaFailure(apiError(400, "validation_error", "That code is not valid."), "enrollment").kind).toBe(
      "invalid",
    );
  });

  it("does not pretend to understand something that is not an ApiError", () => {
    expect(describeMfaFailure(new Error("boom"), "challenge")).toEqual({
      kind: "unknown",
      message: "boom",
    });
    expect(describeMfaFailure("boom", "challenge").kind).toBe("unknown");
  });
});

// --- small helpers ----------------------------------------------------------

describe("code handling", () => {
  it("strips whatever an authenticator app puts between the digits", () => {
    expect(normaliseCode("123 456")).toBe("123456");
    expect(normaliseCode("123-456")).toBe("123456");
    expect(isCompleteCode(" 123456 ")).toBe(true);
    expect(isCompleteCode("12345")).toBe(false);
  });

  it("compares recovery codes the way the backend does", () => {
    expect(normaliseRecoveryCode("abcde-fghjk")).toBe("ABCDEFGHJK");
    expect(normaliseRecoveryCode("ABCDE FGHJK")).toBe("ABCDEFGHJK");
    expect(isCompleteRecoveryCode("abcde-fghjk")).toBe(true);
    expect(isCompleteRecoveryCode("abc")).toBe(false);
  });
});

describe("statusSummary", () => {
  it("says plainly when a password is the only thing protecting the account", () => {
    expect(statusSummary(disabledStatus)).toMatch(/password alone/i);
  });

  it("counts the recovery codes that are left", () => {
    expect(
      statusSummary({ enabled: true, confirmed_at: "2026-01-01T00:00:00Z", recovery_codes_remaining: 1 }),
    ).toMatch(/1 recovery code\b/);
    expect(
      statusSummary({ enabled: true, confirmed_at: "2026-01-01T00:00:00Z", recovery_codes_remaining: 4 }),
    ).toMatch(/4 recovery codes/);
  });

  it("warns when the last recovery code has been spent", () => {
    expect(
      statusSummary({ enabled: true, confirmed_at: "2026-01-01T00:00:00Z", recovery_codes_remaining: 0 }),
    ).toMatch(/no recovery codes are left/i);
  });
});

describe("recoveryCodesFile", () => {
  it("contains every code and says what they are for", () => {
    const file = recoveryCodesFile(enabled.recovery_codes);
    for (const code of enabled.recovery_codes) expect(file).toContain(code);
    expect(file).toMatch(/works once/i);
  });
});

// --- the standing rules -----------------------------------------------------

/**
 * These read the source rather than the behaviour on purpose.
 *
 * "The secret is never written to storage" is not a property any single call
 * can demonstrate — it is a property of every line in the files that touch one.
 * Reading them is the only check that stays true when somebody adds a
 * convenience later.
 */
describe("secret material never leaves the page", () => {
  const files = ["lib/mfa.ts", "components/mfa-challenge.tsx", "components/mfa-settings.tsx"];

  /** The file with its comments removed, so prose about storage is not a hit. */
  function code(file: string): string {
    return readFileSync(path.join(ROOT, file), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
  }

  it("never reaches for browser storage or a cookie", () => {
    for (const file of files) {
      expect(code(file), `${file} uses browser storage`).not.toMatch(
        /localStorage|sessionStorage|indexedDB/,
      );
      expect(code(file), `${file} touches a cookie`).not.toMatch(/document\.cookie/);
    }
  });

  it("never logs from the code that handles secrets", () => {
    for (const file of files) {
      expect(code(file), `${file} logs`).not.toMatch(/console\.(log|info|warn|debug|error)/);
    }
  });

  it("keeps the secret out of any URL the browser would send somewhere", () => {
    for (const file of files) {
      // A QR fetched from a third party, or a secret in a query string, would
      // put it through a log this project does not control.
      expect(code(file), `${file} builds a remote URL`).not.toMatch(
        /https?:\/\/(?!localhost)[a-z0-9.-]*\/[^"'`]*\$\{/i,
      );
    }
  });
});
