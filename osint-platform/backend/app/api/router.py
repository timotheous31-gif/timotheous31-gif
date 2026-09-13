"""Aggregate API router.

Routers are imported lazily inside :func:`build_api_router` so importing this
module (for example from the CLI) does not pull in the whole service layer.

**CSRF is applied here, once, to everything except sign-in.** Attaching it per
endpoint would mean a new state-changing route could be added without it, and
that omission would be invisible in review. Attaching it to the router means the
default is protection and an exemption has to be written down — which, for
sign-in, it is: a caller with no session yet has no session-bound token to send,
and the route is defended instead by CORS, ``SameSite`` and its own throttle.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import enforce_csrf


def build_api_router() -> APIRouter:
    """Assemble the versioned API router."""
    from app.api import (
        auth,
        cases,
        collectors,
        entities,
        findings,
        jobs,
        recon,
        reports,
        social,
        targets,
        workspaces,
    )

    router = APIRouter()

    # Sign-in and "who am I" sit outside the CSRF dependency: the first has no
    # session to bind a token to, and the second is a safe method. ``logout`` and
    # ``password`` call the same check explicitly, so nothing state-changing in
    # that router is unprotected.
    router.include_router(auth.router)

    guarded = APIRouter(dependencies=[Depends(enforce_csrf)])
    guarded.include_router(workspaces.router)
    guarded.include_router(cases.router)
    guarded.include_router(targets.router)
    guarded.include_router(jobs.run_router)
    guarded.include_router(findings.router)
    guarded.include_router(entities.router)
    guarded.include_router(recon.router)
    guarded.include_router(social.router)
    guarded.include_router(reports.router)
    guarded.include_router(jobs.router)
    guarded.include_router(collectors.router)
    router.include_router(guarded)
    return router
