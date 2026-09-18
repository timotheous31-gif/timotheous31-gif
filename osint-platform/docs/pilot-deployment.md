# Private pilot deployment

How to run this platform for one customer, safely enough for paid work.

Written for the person who will be on the phone when something breaks. Every
command is one you can copy; every value you must choose is called out.

---

## Before you start

You need: a host with Docker, a DNS name, a TLS certificate (Let's Encrypt is
fine), and somewhere to put backups that is not the same disk.

You do **not** need: an identity provider, an SMTP server, a paid search
provider, or an Anthropic key. The platform runs at £0 of third-party cost with
`SEARCH_PROVIDER=none`.

---

## 1. Generate the secrets

```bash
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
```

`SESSION_SECRET` keys the CSRF derivation. It is **per deployment** — never
shared between customers, never committed, never a placeholder. Production
refuses to start with a known placeholder or anything under 32 characters.

---

## 2. Write the environment file

```bash
cp .env.example .env
chmod 600 .env
```

The settings that decide whether this deployment is safe:

```ini
ENVIRONMENT=production
DEBUG=false

# Per deployment, from step 1. Never a placeholder.
SESSION_SECRET=...

# Only the deployed frontend. No localhost, no http://, no "*".
CORS_ORIGINS=https://osint.customer.example

DATABASE_URL=postgresql+psycopg://osint:<password>@postgres:5432/osint
REDIS_URL=redis://redis:6379/0

# The interactive API docs are the one page on the API origin that runs a
# script, and the only reason the CSP has an exception. Off in production.
DOCS_ENABLED=false

# Defaults, listed so you know they exist.
SESSION_LIFETIME_HOURS=12
SESSION_IDLE_TIMEOUT_MINUTES=120
RATE_LIMIT_ENABLED=true
ALLOW_PRIVATE_NETWORKS=false
```

> **A per-hour limit of `0` means "no limit configured", not "forbidden".**
> `RATE_LIMIT_CASE_CREATE_PER_HOUR=0` and its siblings switch that particular
> ceiling *off*. To turn inbound throttling off deliberately, set
> `RATE_LIMIT_ENABLED=false` — which production refuses to start with. To stop
> provider searches happening at all, leave `SEARCH_PROVIDER=none`.

**The platform will refuse to start** if any of these is unsafe, and will name
every problem at once:

```
Refusing to start: ENVIRONMENT=production with unsafe configuration.

  1. SESSION_SECRET is not set. Generate one with:
    python -c "import secrets; print(secrets.token_urlsafe(48))"

  2. CORS_ORIGINS still contains development origins (http://localhost:3000).
     List only the deployed frontend origins.

Nothing here has been adjusted for you — a production deployment that silently
repairs its own security settings is worse than one that will not start.
```

That is the intended behaviour. Fix the lines it names.

---

## 3. HTTPS in front

The API sets `Secure` cookies in production, so **it will not work over plain
HTTP**. Terminate TLS in a reverse proxy and forward the client address:

```nginx
server {
    listen 443 ssl http2;
    server_name osint.customer.example;

    ssl_certificate     /etc/letsencrypt/live/osint.customer.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/osint.customer.example/privkey.pem;

    # Same origin for the app and the API is the simplest safe arrangement: the
    # session cookie is same-site by construction and CORS never comes into it.
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
    }
}

server {
    listen 80;
    server_name osint.customer.example;
    return 301 https://$host$request_uri;
}
```

`X-Forwarded-For` matters: without it every caller looks like the proxy and the
per-address sign-in throttle becomes one shared bucket. It is used **only** for
throttling — never stored, never logged, never audited — and is hashed before it
becomes a key.

With this arrangement the frontend and the API share an origin, so `CORS_ORIGINS`
is belt-and-braces rather than load-bearing. If you serve them from different
hosts, list the frontend origin explicitly.

---

## 4. Start it

```bash
docker compose up -d postgres redis
docker compose run --rm api alembic upgrade head
docker compose up -d
docker compose ps          # every service healthy
curl -fsS https://osint.customer.example/health
```

For a lean production image without the test suite or dev dependencies:

```bash
docker compose build --build-arg INSTALL_DEV=false api
```

Both images already run as unprivileged users (uid 10001 and 10002).

---

## 5. Create the first administrator

There is **no default account and no bootstrap endpoint**. The first account is
created by somebody with a shell on the machine — a privilege they already have —
and the password is prompted, never an argument:

```bash
docker compose exec api python -m app.cli admin create-admin
```

```
Create the first administrator
This account owns a new workspace and can invite the rest of the team.

Email address: lead@customer.example
Display name [lead]: Investigations Lead
Workspace name [Investigations]: Customer Investigations
Password (at least 12 characters): ********
Repeat for confirmation: ********

✓ Created lead@customer.example as OWNER of workspace 'customer-investigations'.
```

A passphrase of several unrelated words beats a short password with
substitutions. There are no composition rules.

That administrator then invites the rest of the team through the interface, or:

```
POST /api/v1/workspaces/{workspace_id}/users
{"email": "analyst@customer.example", "password": "...", "role": "ANALYST"}
```

Pass the initial password to them out of band; they change it at
`POST /auth/password`, which revokes every session including the one created from
the initial password.

Roles: **OWNER** (everything), **ADMIN** (everything but ownership transfer),
**ANALYST** (runs investigations, no member management), **VIEWER** (read-only —
the role to give a client or an auditor).

---

## 6. Upgrading an existing installation

**Read this before upgrading a deployment that already holds cases.**

Cases created before workspaces existed have no owner. The migration deliberately
does **not** guess one: attaching a customer's whole back catalogue to whoever
happens to run `create-admin` first would be a worse default than admitting the
gap. Those cases are **unclaimed** — present in the database, invisible to the
API — until an operator adopts them.

```bash
# 1. Back up first. This is a schema change.
docker compose exec postgres pg_dump -U osint osint > backup-before-upgrade.sql

# 2. Apply the migration. No investigation data is read or rewritten;
#    the only change to an existing table is a new nullable column on `cases`.
docker compose run --rm api alembic upgrade head

# 3. Create the first account and workspace.
docker compose exec api python -m app.cli admin create-admin

# 4. See what is unclaimed. This changes nothing.
docker compose exec api python -m app.cli admin list-unclaimed

# 5. Adopt them. Shows what it will do and asks before doing it.
docker compose exec api python -m app.cli admin claim-cases --workspace customer-investigations
```

Everyone in that workspace will then be able to read those cases, subject to
their role. If different historical cases belong to different teams, create the
workspaces first and move the cases between them afterwards — `claim-cases` is
all-or-nothing by design, because a partial adopt driven by a pattern match is
how the wrong case ends up in the wrong workspace.

The operation is reversible: setting `workspace_id` back to `NULL` returns a case
to unclaimed. The downgrade drops the column entirely and every case is reachable
again by the pre-upgrade code.

---

## 7. Backups

The application has none. This is the deployment's job, and an ADMIN deleting a
case is audited but not recoverable without one.

```bash
# Database — everything except the raw evidence files.
docker compose exec -T postgres pg_dump -U osint osint | gzip > "osint-$(date +%F).sql.gz"

# Evidence artefacts — hash-addressed files under EVIDENCE_DIR.
docker compose exec -T api tar -cz -C /srv/data evidence > "evidence-$(date +%F).tar.gz"
```

Back up **both**. The database holds each artefact's SHA-256; without the files,
`GET /cases/{id}/evidence/verify` cannot confirm that evidence is unaltered.

A backup contains every investigation in plaintext. Encrypt it, store it away
from the host, and restrict who can read it exactly as you would the database.

### Restore

```bash
docker compose down
docker compose up -d postgres
gunzip -c osint-2026-09-13.sql.gz | docker compose exec -T postgres psql -U osint osint
docker compose exec -T api tar -xz -C /srv/data < evidence-2026-09-13.tar.gz
docker compose run --rm api alembic upgrade head   # in case the backup predates a migration
docker compose up -d
docker compose exec api python -m app.cli admin list-unclaimed   # sanity check
```

Practise this before you need it. A backup nobody has restored is a hypothesis.

### Redis

Redis holds the response cache and the rate-limit counters. **Nothing in it needs
to survive a restart**: losing it means a cold cache and reset counters, not lost
data. No persistence configuration is required. It must not be reachable from the
internet.

---

## 8. Credential rotation

| Credential | How | Effect |
| --- | --- | --- |
| `SESSION_SECRET` | Replace, restart | Every outstanding CSRF token becomes invalid; users reload the page once. Sessions themselves survive. |
| A user's password | `python -m app.cli admin reset-password --email ...` | Revokes every session that user holds. |
| `POSTGRES_PASSWORD` | Change in Postgres and in `.env`, restart | Brief downtime. |
| A provider key | Replace in `.env`, restart | The collector reports itself unavailable until it is set. |

Rotate `SESSION_SECRET` and every password if a host compromise is suspected, and
read the audit log for the period in question:

```
GET /api/v1/workspaces/{workspace_id}/audit?limit=500
```

---

## 9. Logs

Structured JSON on stdout, scrubbed of anything credential-shaped:

```bash
docker compose logs -f api | jq 'select(.event | startswith("audit"))'
```

Ship them somewhere the host cannot rewrite. The audit table is the record of
*what was done*; these logs are the record of *what was requested*, and the
`request_id` field joins the two.

---

## 10. Routine upgrades

```bash
git pull
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d
docker compose ps
```

Back up first if the release contains a migration. `alembic upgrade head` is
idempotent; running it when there is nothing to do is safe.

---

## 11. Before you call it live

- [ ] `curl -fsS https://.../health` returns 200 over HTTPS.
- [ ] `curl http://.../health` redirects to HTTPS.
- [ ] Signing in sets a cookie marked `Secure` and `HttpOnly` (check dev tools).
- [ ] `https://.../docs` is 404 (`DOCS_ENABLED=false`).
- [ ] An anonymous `GET /api/v1/cases` returns 401.
- [ ] A VIEWER account cannot see the delete or run controls, and the API refuses
      them if called directly.
- [ ] A second workspace's case id returns 404, not 403.
- [ ] `pg_dump` runs, and you have restored it once somewhere else.
- [ ] The audit log shows your own sign-in.
- [ ] `.env` is `chmod 600` and not in git.

---

## What to tell the customer

Honesty here is worth more than a feature list. From
[threat-model.md](threat-model.md), the four they are most likely to ask about:

- **No multi-factor authentication.** A stolen password is a stolen account until
  the session is revoked or expires.
- **Access is per workspace, not per case.** A VIEWER sees every case in the
  workspace. There is no way to share one case alone.
- **The audit log is append-only through the application**, but anybody with
  direct database access can edit it.
- **Backups and disk encryption are the deployment's responsibility**, not the
  application's.

[security-model.md](security-model.md) has the full list.

---

## Downgrading destroys accounts and the audit trail

**Read this before running `alembic downgrade`.** Investigation data is safe;
everything that controls *access* to it is not.

| Revision | Downgrading past it destroys |
| --- | --- |
| `90102014feb7` (MFA) | Every enrolled TOTP secret and every unused recovery code. Enrolled users fall back to a password alone and must re-enrol. Recoverable. |
| `dbeccf60fc7e` (auth) | **Users, workspaces, memberships, sessions and the entire audit history.** Irrecoverable. |

Verified, not assumed: migrating an installation up, adopting its cases, then
downgrading and re-upgrading leaves every investigation table byte-identical —
and leaves `users`, `workspaces`, `workspace_memberships`, `user_sessions` and
`audit_events` with zero rows. The cases become unclaimed again, because
`cases.workspace_id` is dropped with the column.

There is no recovery path in the application for this. If you must downgrade past
`dbeccf60fc7e`, take a database dump first and understand that restoring it is
the only way back.

## Rate limiting in production: two supported modes

Inbound throttling counts in one process unless it is told otherwise. Behind a
proxy with four workers, in-memory counting is four separate counters — a limit
of eight sign-in attempts is really thirty-two, and nothing says so.

Production therefore refuses to start unless one of these is true:

```bash
# Either: every worker counts into the same place.
REDIS_URL=redis://redis:6379/0        # must be reachable at start-up

# Or: an explicit statement that there is exactly one worker.
SINGLE_WORKER_DEPLOYMENT=true
```

A configured-but-unreachable Redis is refused too. That is the case that would
otherwise degrade silently: the connection fails at start-up, the process falls
back to per-worker counting, and the deployment looks healthy while its limits
are worth a fraction of their number.

## Two-factor authentication

Off until a user enrols. No deployment-wide switch turns it on for everybody: a
factor forced onto an account whose owner has not yet scanned a QR code locks
them out of their own account.

- **Enrolment** requires the password again, then a code from the authenticator
  before anything changes. A scanned-but-unconfirmed secret gates nothing.
- **Recovery codes** are issued once, shown once, stored as Argon2 verifiers, and
  each works exactly once.
- **`admin reset-password` does not clear the second factor.** Shell access is not
  account takeover; a user who has lost both password and phone needs the
  recovery code, or a deliberate database change an operator can be held to.
- **Rate limited** at `MFA_MAX_ATTEMPTS` (default 5) per `MFA_ATTEMPT_WINDOW_SECONDS`.
  Bounded, like the sign-in cooldown, so it cannot be used to lock somebody out.

