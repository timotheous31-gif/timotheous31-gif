"""Report renderers: HTML, Markdown and JSON.

All three render the same :class:`ReportModel`, so a reader gets the same
content and the same citations whichever format they take.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.reporting.model import ReportModel

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _environment() -> Environment:
    """Jinja environment with autoescaping on.

    Report content includes text fetched from third-party websites, so
    autoescaping is mandatory: an HTML report must never execute markup that a
    collected page supplied.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml", "html.j2"], default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": str}
    return env


def render_html(model: ReportModel) -> str:
    """Render a self-contained HTML report (no external assets)."""
    return _environment().get_template("report.html.j2").render(model=model)


def render_json(model: ReportModel) -> str:
    """Render the complete model as JSON — the machine-readable export."""
    return json.dumps(model.to_dict(), indent=2, ensure_ascii=False, default=str)


def render_markdown(model: ReportModel) -> str:
    """Render a Markdown report."""
    out: list[str] = []
    add = out.append

    add(f"# {model.case_name}")
    add("")
    add(
        f"OSINT investigation report · case `{model.case_id}` · status {model.case_status} · "
        f"generated {model.generated_at:%Y-%m-%d %H:%M UTC} by osint-platform "
        f"{model.platform_version}"
    )
    if model.case_description:
        add("")
        add(model.case_description)
    if model.case_tags:
        add("")
        add(f"**Tags:** {', '.join(model.case_tags)}")
    add("")
    add(
        "> This report contains only information already published by its owner or by a public "
        "registry. Every claim links to a stored artefact identified by its SHA-256 hash. Read "
        "the Limitations section before acting on anything here."
    )

    add("")
    add("## Case summary")
    add("")
    add("| Metric | Count |")
    add("| --- | ---: |")
    for label, key in (
        ("Targets", "targets"),
        ("Findings", "findings"),
        ("Entities", "entities"),
        ("Relationships", "relationships"),
        ("Evidence artefacts", "evidence"),
        ("Timeline events", "timeline_events"),
        ("Collector runs", "collector_runs"),
    ):
        add(f"| {label} | {model.counts.get(key, 0)} |")

    add("")
    add("## Executive summary")
    add("")
    for line in model.executive_summary:
        add(f"- {line}")

    add("")
    add("## Targets")
    add("")
    if model.targets:
        add("| Type | Normalised value | Original input | Status |")
        add("| --- | --- | --- | --- |")
        for target in model.targets:
            add(
                f"| {target['type']} | `{target['normalized_value']}` | "
                f"`{target['raw_input']}` | {target['status']} |"
            )
    else:
        add("No targets were added to this case.")

    add("")
    add("## Key findings")
    add("")
    if model.key_findings:
        for finding in model.key_findings:
            add(f"### {finding.title}")
            add("")
            observed = f" · observed {finding.observed_at:%Y-%m-%d}" if finding.observed_at else ""
            add(
                f"**{finding.strength.replace('_', ' ')}** ({finding.confidence:.2f}) · "
                f"{finding.classification} · `{finding.kind}` · collector "
                f"`{finding.collector}`{observed}"
            )
            if finding.summary:
                add("")
                add(finding.summary)
            if finding.withheld:
                add("")
                add("> Content withheld by the export privacy policy.")
            elif finding.redacted:
                add("")
                add("> Some values in this finding were redacted before storage.")
            if finding.confidence_reasons:
                add("")
                add("Why this confidence:")
                for reason in finding.confidence_reasons:
                    add(f"- {reason}")
            add("")
            add("```json")
            add(json.dumps(finding.data, indent=2, ensure_ascii=False, default=str))
            add("```")
            add("")
            if finding.source_url:
                add(f"Source: <{finding.source_url}>")
            if finding.evidence:
                citations = ", ".join(
                    f"`sha256:{ref.short_hash}` ({ref.retrieved_at:%Y-%m-%d %H:%M UTC})"
                    for ref in finding.evidence
                )
                add(f"Evidence: {citations}")
            else:
                add("Evidence: *no stored artefact is attached to this finding.*")
            add("")
        if len(model.findings) > len(model.key_findings):
            add(
                f"*Showing the {len(model.key_findings)} highest-confidence of "
                f"{len(model.findings)} findings; the JSON report contains all of them.*"
            )
    else:
        add("No findings met the report's confidence threshold.")

    add("")
    add("## Entities")
    add("")
    if model.entities:
        add("| Type | Name | Canonical value | Confidence | Supported by |")
        add("| --- | --- | --- | ---: | ---: |")
        for entity in model.entities:
            add(
                f"| {entity.type} | {entity.display_name} | `{entity.canonical_value}` | "
                f"{entity.confidence:.2f} | {len(entity.source_finding_ids)} finding(s) |"
            )
    else:
        add("No entities were derived.")

    add("")
    add("## Relationships")
    add("")
    if model.relationships:
        add("| From | Relationship | To | Confidence | Why |")
        add("| --- | --- | --- | ---: | --- |")
        for edge in model.relationships:
            reasons = "; ".join(edge.confidence_reasons)
            add(
                f"| {edge.source_label} | `{edge.type}` | {edge.target_label} | "
                f"{edge.confidence:.2f} | {reasons} |"
            )
    else:
        add("No relationships were derived.")

    add("")
    add("## Confidence assessment")
    add("")
    add(
        "Scores come from named rules and are combined conservatively: several weak signals can "
        "raise a score, but never past what that class of evidence justifies. Mean finding "
        f"confidence is {model.confidence.get('mean_finding_confidence', 0):.2f}."
    )
    add("")
    add("| Band | Range | Findings | Relationships |")
    add("| --- | --- | ---: | ---: |")
    for band, range_text in model.confidence.get("bands", {}).items():
        add(
            f"| {band.replace('_', ' ')} | {range_text} | "
            f"{model.confidence['finding_bands'][band]} | "
            f"{model.confidence['relationship_bands'][band]} |"
        )

    add("")
    add("## Timeline")
    add("")
    if model.timeline:
        add("| Date | Kind | Event | Collector |")
        add("| --- | --- | --- | --- |")
        for event in model.timeline:
            add(
                f"| {event['occurred_at'][:10]} | {event['kind']} | {event['title']} | "
                f"`{event['collector']}` |"
            )
    else:
        add("No dated events were recorded.")

    add("")
    add("## Evidence")
    add("")
    if model.evidence:
        add(
            "Each artefact is stored under its SHA-256 hash. Re-hashing the stored file "
            "reproduces the value below; if it does not, the artefact has been altered since "
            "collection."
        )
        add("")
        add("| SHA-256 | Collector | Source | Retrieved | Redacted |")
        add("| --- | --- | --- | --- | --- |")
        for ref in model.evidence:
            add(
                f"| `{ref.sha256}` | `{ref.collector}` | {ref.source_url or '—'} | "
                f"{ref.retrieved_at:%Y-%m-%d %H:%M UTC} | {'yes' if ref.redacted else 'no'} |"
            )
    else:
        add("No evidence was stored.")

    add("")
    add("## Sources")
    add("")
    if model.sources:
        add("| Collector | Attribution | Runs | OK | Failed | Skipped | Findings |")
        add("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for source in model.sources:
            add(
                f"| `{source.collector}` {source.version} | {source.attribution or '—'} | "
                f"{source.runs} | {source.successes} | {source.failures} | {source.skipped} | "
                f"{source.findings} |"
            )
    else:
        add("No collectors ran.")

    add("")
    add("## Methodology")
    add("")
    for line in model.methodology:
        add(f"- {line}")

    add("")
    add("## Limitations")
    add("")
    for line in model.limitations:
        add(f"- {line}")

    add("")
    add("---")
    add("")
    add(
        f"Generated by osint-platform {model.platform_version} on "
        f"{model.generated_at:%Y-%m-%d %H:%M UTC}. Export policy: content above "
        f"{model.policy.get('max_classification')} withheld "
        f"({model.policy.get('withheld_findings', 0)} finding(s)); minimum confidence "
        f"{model.policy.get('min_confidence')}."
    )
    add("")
    return "\n".join(out)


RENDERERS: dict[str, Any] = {
    "html": render_html,
    "md": render_markdown,
    "json": render_json,
}
