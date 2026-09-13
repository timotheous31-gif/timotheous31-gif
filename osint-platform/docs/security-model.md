# Security model

What protects a private pilot deployment, how each control works, and — with
equal weight — what it does not cover.

This describes the platform as it is, not as it should be. Where a control is
partial, the partial part is named. A security document that reads as though
everything is handled is a document nobody can act on.

---

## Summary

| Concern | Control |
| --- | --- |
| Who you are | Email + password, Argon2id, server-side sessions in an `HttpOnly` cookie |
| What you may do | Four roles, one explicit permission matrix, enforced server-side |
| Whose data you may reach | Workspace containment, checked on every request |
| Cross-site request forgery | Double-submit token bound to the session by HMAC |
| Brute force | Per-account **and** per-address throttling with a bounded cooldown |
| Resource exhaustion | Per-user hourly ceilings on the expensive operations |
| Audit | Append-only ledger, 17 event types, no edit or delete route |
| Outbound requests | Existing SSRF guard, unchanged, `ALLOW_PRIVATE_NETWORKS=false` |
| Secrets | `SecretStr`, scrubbed logs, filtered audit metadata, no secret in any response |
| Unsafe production config | Refuses to start, naming every problem |

---

## Authentication

**Email and password.** No social login, no SSO, no magic links. A private pilot
has a handful of named users and no identity provider to federate with; adding
one would be more attack surface than it removes.

**Argon2id**, via `argon2-cffi`, at the library's own current parameters. A
memory-hard KDF is what makes an offline attack on a stolen password table
expensive on the hardware an attacker actually has. Parameters are not hand-tuned
here — the library tracks the guidance — and `needs_rehash` upgrades a stored
verifier on the next successful sign-in, so raising them later costs nobody a
password reset.

Passwords are **never** stored, logged, echoed, or placed in an audit entry. The
minimum is 12 characters with no composition rules: a passphrase is stronger than
`Password1!` and easier to remember. The maximum is 1024, which is not a strength
rule but a denial-of-service bound on a deliberately slow hash.

**Sign-in discloses nothing.** An unknown address, a wrong password and a
deactivated account return the same status, the same sentence, and the same
latency — the Argon2 verification runs against a fixed dummy verifier when there
is no account, so timing does not answer "does this address have an account
here".

### Sessions

Opaque random tokens, 256 bits from `secrets.token_urlsafe`. The token is
**never stored**: the database holds a SHA-256 of it, so reading `user_sessions`
yields nothing replayable.

| Property | Value | Why |
| --- | --- | --- |
| Cookie | `HttpOnly` | A cross-site-scripting bug cannot read the session |
| | `Secure` in production | Never sent over plaintext HTTP |
| | `SameSite=Lax` | Refuses the classic cross-site POST; lets `:3000` reach `:8000` in development |
| Absolute lifetime | 12 hours | `SESSION_LIFETIME_HOURS` |
| Idle timeout | 120 minutes | `SESSION_IDLE_TIMEOUT_MINUTES` |
| Logout | Revokes the row | Clearing a cookie only asks the browser to forget; revoking stops a copied token |

Three separate conditions are checked on every request — revoked, expired, idle —
because they fail for different reasons. A password change revokes every session
the user holds, including the one that changed it: a change that leaves old
sessions alive does not evict whoever it was made because of. Removing somebody
from a workspace revokes their sessions too.

**Why not bearer tokens in `localStorage`.** It is the common design and it is
worse here: `localStorage` is readable by any script on the origin, so one XSS
becomes a full account takeover with no expiry. The cookie is unreadable, and the
CSRF token — which *is* readable, because it must go in a header — authorises
nothing on its own.

---

## Authorization

### The containment boundary

Every case belongs to exactly one **workspace**. Every other table in the
platform — targets, findings, evidence, entities, relationships, timeline events,
collector runs, jobs, execution observations, social profiles, image evidence,
public contacts, analyst decisions — carries `case_id`. So authorizing the case
authorizes all fourteen at once, in one place, and a table added later inherits
the boundary by carrying `case_id` like its siblings.

A route never resolves a case itself. It asks for a `CaseContext`, which resolves
the case **and** the caller's membership in one query and hands back both or
refuses. There is no dependency that yields a case without the right to have it,
which is why the check cannot be forgotten at a call site.

Jobs are reached by `job_id` with no case in the path, so they get the same
treatment through `JobContext` — job → case → membership. That route was the most
exposed surface in the platform before this change.

### 404, not 403

A case in another workspace answers **404**. A 403 would confirm that the id
names a real case, which is what an attacker enumerating UUIDs wants to learn.
403 is reserved for the object the caller *can* see but may not act on — a VIEWER
attempting a delete — where the distinction helps them and discloses nothing.

### Roles

| | VIEWER | ANALYST | ADMIN | OWNER |
| --- | :-: | :-: | :-: | :-: |
| Read cases, evidence, reports | ✓ | ✓ | ✓ | ✓ |
| Export reports | ✓ | ✓ | ✓ | ✓ |
| Create / update cases | | ✓ | ✓ | ✓ |
| Add and remove targets | | ✓ | ✓ | ✓ |
| Run / cancel investigations | | ✓ | ✓ | ✓ |
| Import results | | ✓ | ✓ | ✓ |
| Record analyst decisions | | ✓ | ✓ | ✓ |
| Delete cases | | | ✓ | ✓ |
| Manage membership | | | ✓ | ✓ |
| Read the audit log | | | ✓ | ✓ |
| Transfer ownership | | | | ✓ |

Two rules the matrix encodes deliberately. **A VIEWER changes nothing** — which
is what makes the role safe to hand to a client or an auditor. **An ANALYST
manages nobody** — adding a member is how an investigation's data leaves the
people it was scoped to, and that is not an analyst's call.

Two rules enforced beyond the matrix: nobody may grant a role above their own (so
an ADMIN cannot mint an OWNER and act through them), and **only an OWNER
transfers ownership** — checked explicitly, because an ADMIN who could transfer
could promote themselves and demote the owner, making the two roles one.

A workspace always has an owner: removing or demoting the last one is refused.

### The frontend is not a security boundary

`GET /auth/me` returns the caller's permissions so the interface can hide a
control they cannot use. That is a courtesy — a disabled button with no
explanation reads as a broken product. **The backend re-checks every request**,
and a client that ignored the list entirely would gain nothing.

---

## CSRF

The session is a cookie, so state-changing requests need CSRF protection.
`SameSite=Lax` is set and is **not** treated as sufficient: it is one browser's
policy rather than this application's, it does not cover a sibling host under the
same registrable domain, and a customer's investigation data is not the place to
find out where the gaps are.

So `POST`, `PUT`, `PATCH` and `DELETE` must carry an `X-CSRF-Token` header
matching a token derived as `HMAC-SHA256(session_secret, "csrf:" + session_id)`.
The server stores nothing extra, and an attacker who cannot read the session
cannot compute it. Comparison is constant-time.

The check is attached to the router, not to each endpoint, so a new
state-changing route cannot be added without it. One exemption exists and is
written down: `POST /auth/login`, where the caller has no session to bind a token
to. It is defended instead by CORS, `SameSite`, and its own throttle.

---

## Rate limits

Separate from the outbound collector limits in `app/core/ratelimit.py`, which
exist to be a polite client of somebody else's API.

| Operation | Default | Setting |
| --- | --- | --- |
| Failed sign-ins, per account **and** per address | 8 per 15 min, then a 15 min cooldown | `LOGIN_MAX_ATTEMPTS` |
| Case creation | 60/hour/user | `RATE_LIMIT_CASE_CREATE_PER_HOUR` |
| Investigation runs | 60/hour/user | `RATE_LIMIT_INVESTIGATION_PER_HOUR` |
| Manual imports | 300/hour/user | `RATE_LIMIT_IMPORT_PER_HOUR` |
| Report generation | 120/hour/user | `RATE_LIMIT_REPORT_PER_HOUR` |
| Provider search | 120/hour/user | `RATE_LIMIT_RECON_PER_HOUR` |

Both login keys are needed: per-account alone lets one attacker spray many
accounts from one host, per-address alone lets a botnet grind one account.
Cooldowns **expire on their own** — a permanent lockout would hand an attacker a
denial-of-service against a real user. A correct password clears both counters,
so one forgotten password does not leave somebody rationed.

Throttle keys are **hashed**: a shared Redis holds digests, not a list of who
tried to sign in. `X-Forwarded-For` is honoured for the address key only, and a
spoofed header buys an attacker their own bucket and nothing else — the value
never reaches the database, the logs, or an audit entry.

Refusals are explicit `429`s with `Retry-After`. **A throttled search never
becomes `NO_MATCH_RETURNED`** in investigation coverage: a search that did not
happen is not a search that found nothing, and the coverage vocabulary has a
state for each.

Backed by Redis where configured, per-process otherwise. Redis being unreachable
allows the request and logs it: refusing everything because the counter store is
down turns a cache outage into a total outage.

---

## Audit log

Append-only. No update route, no delete route, no ORM cascade — deleting a user
or a workspace nulls the foreign key so the record of what they did survives
them.

Recorded: `user_login_success`, `user_login_failure`, `user_logout`,
`user_created`, `membership_added`, `membership_removed`, `role_changed`,
`ownership_transferred`, `case_created`, `case_deleted`,
`investigation_started`, `investigation_cancelled`, `report_generated`,
`report_downloaded`, `analyst_decision_created`, `manual_result_imported`,
`rate_limit_triggered`.

Each entry carries the time, the actor, the workspace, the object, the request id
(joinable against application logs) and filtered metadata.

**Never recorded:** a password, a session token, a CSRF token, an `Authorization`
header, any provider credential, or a raw request body. `app.services.audit`
filters metadata by key — separator-insensitively, so `x-api-key` and `apiKey`
are caught as well as `api_key` — truncates values, bounds the key count, and
records how many fields it dropped so an incomplete entry says so.

A failed audit write is logged and swallowed rather than failing the operation it
records. That trade is stated here rather than discovered later: an audit table
that can take the API down would be a reliability problem wearing a security
badge, and the structured application log still carries the request.

Readable at `GET /workspaces/{id}/audit` by ADMIN and OWNER, scoped to that
workspace. Sign-in events have no workspace of their own — a user may belong to
several — so they are included when the actor is a member of the workspace being
read.

---

## Reports and evidence

Reports are workspace-authorized and need `REPORT_EXPORT`. Every generation is
audited.

**HTML reports are downloads, never pages.** They are built from titles,
snippets and page text collected from the open web — attacker-influenced by
definition. Jinja autoescapes it and a test holds that, but autoescaping is one
mistake away from failing, and the response is served from the API's own origin,
where the session cookie lives. So an HTML report goes out as
`Content-Disposition: attachment` under
`default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox`, and a
templating bug becomes a bad downloaded file rather than a session theft. The
filename is stripped of everything outside `[A-Za-z0-9._-]`, so an
investigator-supplied case name cannot inject a header.

Remote images are **not** embedded by default: a remote image in an exported
document makes the reader's viewer fetch a third-party URL, disclosing when and
from where the report was opened.

**Evidence files are never served over HTTP.** There is no `StaticFiles` mount
and no `FileResponse` anywhere in the application. Raw artefacts stay on disk
under their SHA-256; the API exposes hashes, metadata and privacy-filtered
excerpts, all workspace-authorized.

---

## Outbound requests: the SSRF boundary

**Unchanged by this work, and regression-tested.** Every outbound request goes
through `app.core.http`, which validates the initial URL *and every redirect hop*
against `app.core.ssrf`. Refused: loopback, RFC1918, link-local (including
`169.254.169.254`), `::1`, non-http(s) schemes, and credentials in the authority.

`ALLOW_PRIVATE_NETWORKS` stays `false` by default. In production, setting it true
*additionally* requires `PRIVATE_NETWORK_AUTHORIZATION` to name the engagement
that authorised it — otherwise the deployment refuses to start.

Search results are untrusted input and are held to the same standard as a pasted
URL: validated before they are stored and before anything is fetched.

---

## Secrets

- Every credential is a `SecretStr`; a settings `repr` discloses nothing.
- Logs are scrubbed by key pattern and by value shape (`app/core/logging.py`).
- Audit metadata is filtered (above).
- No API response carries a credential — the response models have nowhere to put
  one.
- `.env` and `.env.*` are gitignored; `.env.example` holds placeholders only.
- Docker images copy no credential; configuration arrives as environment
  variables at run time.
- Error bodies from third parties are redacted by exact value and by
  credential shape before being stored on a run record.
- CI runs `gitleaks` over full history, `pip-audit` and `npm audit` on every pull
  request.

---

## Production configuration

`ENVIRONMENT=production` refuses to start, naming every problem at once, when:

- `SESSION_SECRET` is unset, a known placeholder, or shorter than 32 characters;
- `DEBUG` is true;
- `CORS_ORIGINS` contains `*`, a localhost origin, or a plaintext `http://` origin;
- `ALLOW_PRIVATE_NETWORKS` is true without `PRIVATE_NETWORK_AUTHORIZATION`;
- `SESSION_COOKIE_SECURE` is explicitly false, or `SameSite=None` without `Secure`;
- `RATE_LIMIT_ENABLED` is false.

Nothing is repaired automatically. A deployment that quietly fixes its own
dangerous settings teaches its operator that the settings do not matter.

The check runs in `get_settings()`, so the worker and the CLI are held to the
same standard as the web process — a dangerous configuration cannot enter through
a different door.

---

## Known limitations

Named because a pilot customer is entitled to know them.

1. **No multi-factor authentication.** A stolen password is a stolen account
   until the session is revoked. The mitigations are throttling, bounded session
   lifetime, and an audit trail — not a substitute for MFA.
2. **No password reset by email.** A pilot deployment has no outbound mail. An
   administrator resets a password from the shell
   (`python -m app.cli admin reset-password`), which revokes every session.
3. **Per-worker rate limits without Redis.** Several workers and no Redis means
   each enforces its own counters. Logged as a warning in production.
4. **The audit log is not tamper-evident.** It is append-only through the
   application, but a database administrator can still edit the table. Hash
   chaining or shipping to an append-only store is the next step, not this one.
5. **Workspaces share a database and a process.** Isolation is enforced in the
   application, not by the database. A SQL-injection bug would cross it — the ORM
   parameterises everything and a test asserts metacharacters stay data, but the
   boundary is logical, not physical.
6. **No per-field encryption at rest.** Disk encryption is the deployment's job.
7. **No account lockout escalation or anomaly detection.** Throttling is the only
   automated response to a suspicious sign-in pattern.
8. **Session fixation on privilege change** is handled for password changes and
   workspace removal, but a role *downgrade* leaves existing sessions alive; the
   new role applies on the next request, which is correct, but there is no forced
   re-authentication.
9. **Next.js has one moderate unfixed advisory** on the pinned version and a
   nested `postcss` waived with a documented reason. See `.security-policy.toml`.

---

## Deployment assumptions

The model assumes all of the following. Any one of them failing weakens it:

- HTTPS is terminated in front of the API, and `Secure` cookies work.
- The frontend origin is listed explicitly in `CORS_ORIGINS`.
- The database is not reachable from the public internet.
- Redis, when used, is not reachable from the public internet.
- `SESSION_SECRET` is unique per deployment and rotated on suspicion.
- Operators with shell access are trusted — `create-admin` and `reset-password`
  are unauthenticated by design, because a shell already implies that trust.
- `/docs` is disabled in production (`DOCS_ENABLED=false`).

See [pilot-deployment.md](pilot-deployment.md) for how to satisfy them, and
[threat-model.md](threat-model.md) for what each control is actually defending
against.
