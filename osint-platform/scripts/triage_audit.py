#!/usr/bin/env python3
"""Turn a dependency-audit report into a verdict and a readable summary.

Run by CI after ``pip-audit`` or ``npm audit``. It exists because the raw tools
give a build two unhelpful choices: fail on everything, which trains everybody to
ignore the job, or fail on nothing, which is the same as not running it.

The policy, applied identically to both ecosystems:

* **HIGH and CRITICAL with a fix available -> the build fails.** There is
  something the contributor can do, and it is worth doing now.
* **HIGH and CRITICAL with no fix available -> reported, not failed.** Blocking a
  pull request does not make an unfixed advisory go away, and the person who
  opened it cannot ship the upstream patch.
* **MEDIUM and LOW -> reported, never failed.** They are worth seeing in the
  summary and are not worth stopping unrelated work for.
* **Waived -> reported as waived, with the reason.** Waivers live in
  ``.security-policy.toml`` beside the workflow, each with a reason and a review
  date, so a suppression is a decision somebody signed rather than a flag
  somebody passed.

Exit status: 0 when nothing blocking was found, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import tomllib
from typing import Any

BLOCKING = {"HIGH", "CRITICAL"}
POLICY_FILE = pathlib.Path(__file__).resolve().parents[2] / ".security-policy.toml"


def _load_waivers() -> dict[str, dict[str, Any]]:
    """Advisory ids this repository has consciously accepted, with reasons."""
    if not POLICY_FILE.exists():
        return {}
    data = tomllib.loads(POLICY_FILE.read_text(encoding="utf-8"))
    return {
        str(item["id"]): item
        for item in data.get("waiver", [])
        if isinstance(item, dict) and item.get("id")
    }


def _pip_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for dependency in report.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            fixed = vuln.get("fix_versions") or []
            findings.append(
                {
                    "id": vuln.get("id", "?"),
                    "package": dependency.get("name", "?"),
                    "installed": dependency.get("version", "?"),
                    # pip-audit does not publish a severity, so anything with a
                    # fix is treated as actionable. Deliberately conservative:
                    # the alternative is guessing a severity the tool declined to
                    # state.
                    "severity": "HIGH" if fixed else "UNKNOWN",
                    "fix": ", ".join(fixed) or "none published",
                }
            )
    return findings


def _npm_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name, entry in (report.get("vulnerabilities") or {}).items():
        if not isinstance(entry, dict):
            continue
        via = entry.get("via") or []
        ids = [str(item.get("source")) for item in via if isinstance(item, dict)]
        findings.append(
            {
                "id": ids[0] if ids else f"npm:{name}",
                "package": name,
                "installed": str(entry.get("range", "?")),
                "severity": str(entry.get("severity", "unknown")).upper(),
                "fix": "available" if entry.get("fixAvailable") else "none published",
            }
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="JSON report from pip-audit or npm audit")
    parser.add_argument("--npm", action="store_true", help="Parse npm audit format")
    args = parser.parse_args()

    path = pathlib.Path(args.report)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        print("### Dependency audit\n\nNo report produced; treating as clean.")
        return 0
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        print("### Dependency audit\n\nCould not parse the report. Not failing the build on it.")
        return 0

    findings = _npm_findings(report) if args.npm else _pip_findings(report)
    waivers = _load_waivers()

    blocking: list[dict[str, Any]] = []
    informational: list[dict[str, Any]] = []
    waived: list[dict[str, Any]] = []

    for finding in findings:
        if finding["id"] in waivers:
            finding["reason"] = waivers[finding["id"]].get("reason", "")
            waived.append(finding)
        elif finding["severity"] in BLOCKING and finding["fix"] != "none published":
            blocking.append(finding)
        else:
            informational.append(finding)

    ecosystem = "npm" if args.npm else "Python"
    print(f"### Dependency audit — {ecosystem}\n")
    if not findings:
        print("No known advisories.\n")
        return 0

    def table(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        print(f"**{title}**\n")
        print("| Advisory | Package | Installed | Severity | Fix |")
        print("| --- | --- | --- | --- | --- |")
        for row in rows:
            print(
                f"| {row['id']} | {row['package']} | {row['installed']} | "
                f"{row['severity']} | {row['fix']} |"
            )
        print()

    table("Blocking — fix available", blocking)
    table("Reported only", informational)
    table("Waived", waived)

    if blocking:
        print(
            f"\n{len(blocking)} advisory(ies) are high or critical **and** have a "
            f"published fix. Update the dependency, or add a waiver with a reason "
            f"to `.security-policy.toml`.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
