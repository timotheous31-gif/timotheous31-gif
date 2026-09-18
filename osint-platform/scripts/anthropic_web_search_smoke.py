#!/usr/bin/env python3
"""A separately invoked live smoke test for the Anthropic web-search channel.

**Not part of the test suite and not run in CI.** It lives in ``scripts/`` rather
than ``tests/`` deliberately: ``pytest`` collects only ``backend/tests``, so this
file cannot be picked up by accident, and a live call costs money and needs a
credential CI does not have.

Run it by hand, once, after configuring a credential, to confirm that the channel
this platform *believes* it is talking to is the one that actually answers:

    export ANTHROPIC_API_KEY=...      # never committed, never written to disk
    python scripts/anthropic_web_search_smoke.py

What it does, and the limits it keeps:

* **The subject is a harmless public one** — a long-dead scientist — hard-coded
  below. It is never a person under investigation, and the script takes no
  subject argument, so it cannot be pointed at one.
* **One request, at most two searches.** At Anthropic's published price of $10
  per 1,000 searches that is at most $0.02 plus tokens.
* **Nothing is stored.** No database, no evidence store, no file written. It
  prints what came back and exits.
* **The credential is read from the environment only.** It is never a command
  line argument (those land in shell history and process listings), never echoed,
  and the printed output is checked for it before anything is displayed.

What it proves, each line of output answering one question the documentation left
open or worth re-verifying against the live API:

1. Does ``web_search_20250305`` with ``allowed_callers: ["direct"]`` return raw
   ``web_search_tool_result`` blocks?
2. Is there really no description field on a result?
3. Does ``max_uses`` stop the turn, and does the error arrive inside an HTTP 200?
4. Does ``usage.server_tool_use.web_search_requests`` reconcile with the number
   of result blocks?
5. Do ``server_tool_use.id`` and ``web_search_tool_result.tool_use_id`` pair up?
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TOOL_TYPE = "web_search_20250305"

#: A public, uncontroversial, long-dead subject. Hard-coded on purpose: a script
#: that takes a subject argument is a script that will eventually be pointed at a
#: living person from a shell prompt.
SUBJECT = "Claude Shannon information theory"
MAX_USES = 2
MODEL = os.environ.get("ANTHROPIC_WEB_SEARCH_MODEL", "claude-opus-5")


def _request(key: str) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 1024,
        "system": (
            "You are a retrieval tool. Run web searches for the brief and then reply "
            "with the single word DONE. Do not summarise the results."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Run a public web search for: {SUBJECT}\n"
                    f"Then run one more for: {SUBJECT} biography\n"
                    f"Then reply DONE."
                ),
            }
        ],
        "tools": [
            {
                "type": TOOL_TYPE,
                "name": "web_search",
                "max_uses": MAX_USES,
                "allowed_callers": ["direct"],
            }
        ],
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "anthropic-version": API_VERSION,
            "x-api-key": key,
        },
        method="POST",
    )
    try:
        # S310: the URL is the hard-coded Anthropic endpoint (optionally an
        # operator-set base), never user input, and the scheme is fixed https.
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:  # pragma: no cover - live path only
        detail = error.read().decode("utf-8", errors="replace")[:400]
        print(f"HTTP {error.code}. {_scrub(detail, key)}", file=sys.stderr)
        raise SystemExit(2) from error


def _scrub(text: str, key: str) -> str:
    """Never print the credential, whatever the server echoed back."""
    return text.replace(key, "[redacted]") if key else text


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print(
            "ANTHROPIC_API_KEY is not set. This script makes a real, billable call and "
            "will not run without it.",
            file=sys.stderr,
        )
        return 1

    print(f"model={MODEL} tool={TOOL_TYPE} allowed_callers=['direct'] max_uses={MAX_USES}")
    print(f"subject={SUBJECT!r} (a public, long-dead subject; never an investigation target)")
    payload = _request(key)

    calls: dict[str, str] = {}
    results: list[dict] = []
    errors: list[str] = []
    prose: list[str] = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "server_tool_use" and block.get("name") == "web_search":
            calls[str(block.get("id"))] = str((block.get("input") or {}).get("query") or "")
        elif kind == "web_search_tool_result":
            body = block.get("content")
            if isinstance(body, dict) and body.get("type") == "web_search_tool_result_error":
                errors.append(f"{block.get('tool_use_id')}: {body.get('error_code')}")
            elif isinstance(body, list):
                for row in body:
                    if isinstance(row, dict):
                        results.append({**row, "_tool_use_id": block.get("tool_use_id")})
        elif kind == "text":
            prose.append(str(block.get("text") or ""))

    usage = payload.get("usage") or {}
    billed = (usage.get("server_tool_use") or {}).get("web_search_requests")

    print(f"\n1. raw result blocks returned: {'YES' if results or errors else 'NO'}")
    print(f"   searches Anthropic executed: {list(calls.values())}")
    print(f"   stop_reason: {payload.get('stop_reason')}")

    fields = sorted({name for row in results for name in row if not name.startswith("_")})
    print(f"\n2. fields present on a result: {fields}")
    for absent in ("snippet", "description", "rank", "position"):
        print(f"   {absent}: {'PRESENT' if absent in fields else 'absent'}")

    print(f"\n3. tool-result errors inside HTTP 200: {errors or 'none'}")

    print(f"\n4. usage.server_tool_use.web_search_requests = {billed}")
    print(f"   distinct result blocks parsed = {len({row['_tool_use_id'] for row in results})}")
    print(f"   input_tokens={usage.get('input_tokens')} output_tokens={usage.get('output_tokens')}")
    if isinstance(billed, int):
        print(f"   estimated search-tool cost = ${billed * 0.01:.2f} (estimate, not a bill)")

    paired = all(row["_tool_use_id"] in calls for row in results)
    print(f"\n5. every result block pairs with a server_tool_use: {'YES' if paired else 'NO'}")

    print(f"\nurls returned ({len(results)}):")
    for row in results[:10]:
        print(f"   {row.get('url')}  ({row.get('page_age')})")

    print("\nmodel prose (discarded by the platform, shown here only for inspection):")
    print("   " + _scrub(" ".join(prose)[:300], key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
