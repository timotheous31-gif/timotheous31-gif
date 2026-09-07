# Operations

How the platform behaves when part of it is down, and what deleting a case
actually deletes.

## Deleting a case

`DELETE /api/v1/cases/{case_id}`

| Response | Meaning |
|---|---|
| `204` | Deleted |
| `404` | No such case |
| `409` | A job is QUEUED or RUNNING — cancel it first |

### What is removed

Everything the case owns: targets, findings, entities, relationships, evidence
records, timeline events, collector runs, jobs, and imported recon results.
Deletion leans on the schema rather than hand-written cleanup — every
case-scoped table declares `case_id ... ondelete="CASCADE"` and every `Case`
collection declares `cascade="all, delete-orphan"` — so a table added later is
covered automatically as long as it follows that pattern.

The evidence store is content-addressed on disk at
`<EVIDENCE_DIR>/<case_id>/<aa>/<sha256>.json`, so a case owns a whole subtree.
That subtree is removed after the database flush. If the unlink fails it is
logged, not raised: the rows are already gone, and leftover bytes are a cleanup
problem rather than a reason to fail a request that succeeded.

### What is *not* removed

**Tags.** They are shared between cases. Only the `case_tags` links go; a tag
another case still uses survives, and that case keeps it.

### Why an active job is a 409 rather than a cancel-then-delete

Cancellation here is *cooperative*: the worker checks
`jobs.is_cancelled()` between collectors and stops when it notices. Nothing can
force it to stop mid-collector. So a cancel-then-delete flow would be deleting
rows while a worker may still be holding its own session and about to insert
findings into a case that no longer exists — a foreign-key error in the worker
at best, orphaned rows at worst.

Requiring the caller to cancel, watch the job leave QUEUED/RUNNING, and then
delete closes that window without the platform having to guess when a worker is
safely stopped. The 409 body names the active jobs and their states.

## When the queue stops moving

The failure worth understanding: **a reachable broker is not a working system.**

If Redis is healthy but the `celery-worker` container has exited, `apply_async`
*succeeds*. The task lands in a queue nobody is consuming, and the job sits at
`QUEUED`, 0%, forever. Nothing raises, so a `try/except` around dispatch cannot
see it. Only an active probe can tell the two apart.

### What the platform does

1. **Before dispatch**, `jobs.worker_status()` pings for a live worker. If none
   answers, the job runs inline instead of being handed to a queue nobody
   reads. This is the same fallback that already existed for an unreachable
   broker, extended to the case that actually happens.

2. **For jobs already queued** when a worker dies, the stored state stays
   `QUEUED` on purpose — the row is a standing instruction, and a worker that
   comes back must still be able to run it. Rewriting it to a terminal state to
   make the UI honest would destroy that recovery.

   So the *reporting* changes instead. `JobRead` carries:

   - `effective_state` — `PROCESSING_UNAVAILABLE` for a `QUEUED` job with no
     worker, otherwise the stored state;
   - `processing_available` — whether any worker answered.

   The UI shows "Processing unavailable" and names the container to check,
   rather than a progress bar stuck at 0%.

The broker is probed once per response, and only when something is actually
`QUEUED`, so polling a page of finished jobs costs nothing.

### Job states

| State | Stored | Meaning |
|---|---|---|
| `NOT_RUN` | no | No job exists for this case yet |
| `QUEUED` | yes | Waiting for a worker |
| `RUNNING` | yes | A worker is collecting |
| `COMPLETE` | yes | Finished |
| `FAILED` | yes | Stopped on an error |
| `CANCELLED` | yes | Stopped at the investigator's request |
| `PROCESSING_UNAVAILABLE` | **no** | Derived: queued, but nothing is consuming |

`PROCESSING_UNAVAILABLE` is deliberately not a stored state. It is a fact about
the deployment at the moment you asked, not about the job, and it stops being
true the instant a worker starts.

### Redis databases

Unchanged, and worth stating because they are easy to conflate:

| Setting | Database | Used for |
|---|---|---|
| `REDIS_URL` | 0 | Application cache |
| `CELERY_BROKER_URL` | 1 | Task queue |
| `CELERY_RESULT_BACKEND` | 2 | Task results |

### If the worker is down

```bash
docker compose ps                  # is celery-worker healthy?
docker compose logs celery-worker  # why did it stop?
docker compose up -d celery-worker
```

All services carry `restart: unless-stopped`, so a crashed worker comes back by
itself. `unless-stopped` rather than `always` so a service an operator
deliberately stopped stays stopped. The worker's healthcheck runs
`celery inspect ping`, which asks the worker to answer *over the broker* —
green means it is genuinely consuming, not merely that the process exists.

## Privacy filtering and extraction

The privacy filter substitutes bracketed markers (`[REDACTED]`,
`[SUPPRESSED: SENSITIVE PERSONAL DATA]`). A bracketed netloc is IPv6-literal
syntax to `urllib.parse.urlsplit`, so a redacted URL used to parse as an IP
address and raise, and extraction recorded a working privacy policy as a
malformed finding.

Redacted values are now recognised (`app.privacy.is_redacted`) before any typed
parser sees them. A finding whose identity-bearing field was redacted is logged
as `extraction.finding_redacted` at info — a deliberate privacy outcome, kept
separate from `extraction.finding_skipped`, which still means malformed data
worth investigating.
