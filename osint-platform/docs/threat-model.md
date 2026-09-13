# Threat model

Fifteen threats, each with the asset at stake, the attack, what stops it today,
and — the part that matters most — what remains.

The residual-risk column is the point of this document. A threat model whose
every row ends "fully mitigated" is a marketing document.

**Scope:** a private paid pilot. A handful of named users per customer, a
single-tenant deployment behind a reverse proxy, no public sign-up. Out of scope:
a hostile operator with shell access (they own the machine), physical access, and
supply-chain compromise of the dependency tree beyond what CI scanning catches.

---

## 1. Malicious unauthenticated user

**Asset** Every case in the deployment.

**Attack** Call the API directly. Before this change, `GET /cases/{uuid}/report`
returned a complete investigation to anyone who asked.

**Mitigation** Every one of the 55 API routes requires a session. The two
exceptions are `/health` and `/health/ready`, which return a status and a
version. `/docs` is disabled in production.

**Residual** The login endpoint is reachable by definition, so it is the one
place an unauthenticated attacker can spend effort — hence the throttling in §11.
A zero-day in FastAPI's routing or in Starlette's middleware would bypass this
entirely; CI dependency scanning is the only control there, and it is reactive.

---

## 2. Malicious authenticated VIEWER

**Asset** Case integrity: what an investigation concludes.

**Attack** A read-only account deletes a case, re-runs an investigation, imports
a fabricated result, or records an analyst decision that changes a conclusion.

**Mitigation** The permission matrix, enforced server-side on every request. The
interface hides the controls, which is a courtesy; the API refuses them, which is
the control. Tests assert each refusal individually.

**Residual** A VIEWER can still *read* everything in the workspace, including
every report, and export it. That is the role. A customer who needs read access
narrower than "the whole workspace" does not have it: there is no per-case
sharing. **This is the most likely reason a pilot customer asks for a feature
this model does not have.**

---

## 3. Malicious authenticated ANALYST

**Asset** Other members' access; the investigation record.

**Attack** Add an accomplice to the workspace, promote themselves, or delete a
case to destroy evidence of what they did.

**Mitigation** `MEMBERSHIP_MANAGE` and `CASE_DELETE` are both above ANALYST.
Nobody may grant a role above their own. The audit log records what they did do,
and they cannot edit or delete it through any route.

**Residual** An ANALYST can run investigations against any target they like and
export the results — the platform cannot distinguish a legitimate investigation
from an improper one. An ADMIN *can* delete cases, and although the deletion is
audited, the case's contents are gone. There are no backups in the application:
that is the deployment's job, and `pilot-deployment.md` says so.

---

## 4. Cross-workspace access (IDOR)

**Asset** Another customer's entire investigation.

**Attack** Take a case, target, job, finding, entity or membership UUID from one
workspace and use it while signed in to another.

**Mitigation** The containment boundary. `CaseContext` and `JobContext` resolve
the object and the caller's membership in a single query; every listing filters
by workspace in the `WHERE` clause rather than discarding afterwards. A
cross-workspace object answers **404**, so a UUID cannot even be probed for
existence. An adversarial test suite exercises the full matrix — user B against
user A's case, targets, findings, evidence, runs, jobs, reports, imports and
decisions.

**Residual** Isolation is enforced in the application, not by the database. A
SQL-injection bug, or one new route that resolves an object without a context,
would cross it. The first is mitigated by the ORM parameterising everything and
tested; the second is mitigated by there being no dependency that returns a case
without the right to it — but it is a convention, and conventions can be broken
by a determined refactor.

---

## 5. Stolen session

**Asset** Everything the victim can reach.

**Attack** Obtain a session token — a shared machine, a proxy log, a stolen
backup, a browser extension — and replay it.

**Mitigation** The cookie is `HttpOnly` (unreadable by script) and `Secure` in
production (never sent in plaintext). Tokens are stored hashed, so a database
dump yields nothing replayable. Absolute lifetime 12 hours, idle timeout 2 hours.
Logout, password change and removal from a workspace all revoke server-side.

**Residual** **No MFA.** Within its lifetime a stolen token is the user, and the
platform cannot tell. There is no device binding, no IP pinning (deliberately —
it breaks mobile networks and VPNs more often than it stops an attacker), and no
"active sessions" screen for a user to review. An administrator can end all of a
user's sessions by resetting their password.

---

## 6. Credential leakage

**Asset** The provider API keys, the database password, the session secret.

**Attack** Read a credential out of a log, an error response, a report, an audit
entry, a Docker image layer, or the git history.

**Mitigation** `SecretStr` everywhere; log scrubbing by key pattern and value
shape; audit metadata filtered separator-insensitively; no response model with a
field for a credential; third-party error bodies redacted before storage;
`.env` gitignored; no credential copied into an image; `gitleaks` over full
history in CI.

**Residual** A credential passed as a *value* in an unexpected place — inside a
case name, say — would be stored as the user typed it. The platform redacts
credential-shaped strings collected from third parties, but it does not police
what an investigator types into a free-text field.

---

## 7. Hostile search result

**Asset** The investigation record; the analyst reading it.

**Attack** Publish a page whose title or snippet contains markup, and get it
returned by a search provider so it lands in a finding and then in a report.

**Mitigation** React escapes by default and there is no `dangerouslySetInnerHTML`
anywhere in the frontend. Jinja autoescapes the report, with a test asserting
`<script>` survives as `&lt;script&gt;`. HTML reports are downloads under a
`default-src 'none'; sandbox` policy, so even a templating failure cannot run
script on the API origin.

**Residual** A hostile result can still *mislead* — a page that looks
authoritative and says something false about the subject. That is what the
confidence model, the source-coverage table and the analyst-decision record exist
for, and none of them is a security control.

---

## 8. Hostile URL / SSRF

**Asset** The internal network; cloud instance metadata.

**Attack** Get the platform to fetch `http://169.254.169.254/`, a loopback
address, an RFC1918 host, or a redirect chain that ends at one.

**Mitigation** Unchanged from before this work and regression-tested: every
request goes through one guarded client that validates the initial URL and every
redirect hop. Non-http(s) schemes and credentials in the authority are refused.
`ALLOW_PRIVATE_NETWORKS` defaults false and, in production, additionally requires
a named authorisation.

**Residual** **DNS rebinding.** The guard resolves and checks at validation time;
a name that resolves to a public address then to a private one on the connection
could slip through. Closing it requires pinning the resolved address into the
connection, which the current HTTP client does not support. The platform's own
fetches are bounded (one request per page, capped bytes, no crawling), which
limits the value of the window rather than closing it.

---

## 9. Cross-site scripting

**Asset** The session cookie; the analyst's browser.

**Attack** Inject script through any field the interface renders: titles,
snippets, URLs, captions, analyst notes, organisation names, profile metadata.

**Mitigation** React escaping, no `dangerouslySetInnerHTML`, a
`default-src 'none'` CSP on API responses, and — the layer that matters if the
others fail — an `HttpOnly` session cookie, so a script that runs still cannot
read the session.

**Residual** The CSP has one exception, `/docs`, which loads Swagger UI from
jsdelivr with an inline bootstrap. It is bounded three ways: that path only, a
named CDN rather than a wildcard, and `DOCS_ENABLED=false` removes it entirely —
which is the recommended production setting. The frontend is a separate origin
and sets no CSP of its own; Next.js's defaults apply.

---

## 10. Cross-site request forgery

**Asset** Any state-changing operation.

**Attack** A page the user visits while signed in submits a request to the API
with their cookie attached.

**Mitigation** `SameSite=Lax`, explicit credentialed CORS against a listed origin
set, **and** a double-submit token bound to the session by HMAC on every
`POST`/`PUT`/`PATCH`/`DELETE`. The check is attached to the router, so a new
route cannot omit it.

**Residual** `POST /auth/login` is exempt — a caller with no session has no
session-bound token. Login CSRF (forcing somebody into *your* account) is
therefore possible in principle; the impact is low and it is written down rather
than implied away. A same-site subdomain under the customer's control could also
set a cookie, which `SameSite` would not stop — the CSRF token does.

---

## 11. Brute-force sign-in

**Asset** An account, and everything behind it.

**Attack** Guess a password online, either against one account from many hosts or
against many accounts from one.

**Mitigation** Throttling on **both** keys, 8 attempts per 15 minutes each, then a
bounded cooldown. Uniform failure responses and uniform timing, so the attacker
cannot even enumerate which addresses exist. Argon2id makes each guess expensive
server-side. Every failure is audited.

**Residual** The cooldown is bounded on purpose, so a patient attacker gets ~32
guesses an hour per account — irrelevant against a passphrase, not irrelevant
against a weak one. Without Redis, limits are per worker. A distributed attack
from many addresses against many accounts is throttled per account, which is the
binding constraint.

---

## 12. Resource exhaustion

**Asset** Availability, and the customer's money on a paid provider.

**Attack** Start hundreds of investigations, render hundreds of reports, or
import a flood of results.

**Mitigation** Per-user hourly ceilings on all five expensive operations, a 1 MB
request-body cap, bounded page enrichment, and the outbound collector rate limits
that already existed. `429`s carry `Retry-After`.

**Residual** Limits are per user, so N compromised accounts get N times the
allowance. There is no global ceiling and no queue-depth limit. A single
investigation against a target with an enormous public footprint is bounded by
the collectors' own limits, not by anything here.

---

## 13. Malicious imported metadata

**Asset** The investigation record.

**Attack** Import a hand-crafted "search result" with a `javascript:` URL, a
`file://` URL, credentials in the authority, or a 10 MB snippet.

**Mitigation** Every imported URL passes the same SSRF and scheme validation a
collected one does. Schema bounds every field. Imports are filed under their own
collector identity so nothing later mistakes them for something a provider
returned, and every import is audited.

**Residual** An ANALYST can import content that is simply *false*. The platform
records who imported it and when; it cannot tell whether it is true.

---

## 14. Insider with database access

**Asset** Everything.

**Attack** Read or modify the database directly.

**Mitigation** Passwords are Argon2id verifiers and session tokens are SHA-256
digests, so neither yields a usable credential. The audit log records application
activity.

**Residual** **Substantial, and largely unmitigated by design.** Case data is not
encrypted at rest by the application; the audit log is not tamper-evident and can
be edited by anybody who can write to the table. Disk encryption, database access
control and backup custody are the deployment's responsibility.

---

## 15. Compromised dependency

**Asset** Everything the process can reach.

**Attack** A malicious or vulnerable package in the Python or npm tree.

**Mitigation** `pip-audit`, `npm audit` and `gitleaks` on every pull request,
with a documented severity policy: high and critical **with a published fix**
fail the build; everything else is reported. Waivers live in
`.security-policy.toml` with a reason and a review date. Both images run as
non-root.

**Residual** Scanning is reactive — it finds what is already published. There is
no lockfile attestation, no SBOM, and no pinning of transitive dependencies
beyond the lockfile. One advisory is currently waived (a `postcss` copy nested
inside Next.js, unreachable because no user-supplied CSS exists) and one moderate
Next.js advisory is unfixed on the pinned version.

---

## What would change this model most

In the order a pilot customer is most likely to ask for it:

1. **MFA**, which is the single largest residual (§5).
2. **Per-case sharing**, so a client can be given one case rather than a
   workspace (§2).
3. **Tamper-evident audit** — hash chaining, or shipping to an append-only store
   (§14).
4. **DNS-rebinding-proof fetching**, by pinning the resolved address into the
   connection (§8).
5. **A global resource ceiling** in addition to the per-user ones (§12).
