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
    add("## Public profiles and contacts")
    add("")
    add(
        "Public accounts, the professional information they publish about themselves, and the "
        "contact points a source actually printed. Nothing here is derived: no address is built "
        "from a name and a domain, and no image is used to identify anybody — the platform runs "
        "no facial recognition, no biometric analysis and no image comparison of any kind."
    )
    _render_profiles(add, model)
    _render_contacts(add, model)
    _render_images(add, model)

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


def _render_profiles(add: Any, model: ReportModel) -> None:
    """Each public profile, with what it states about itself.

    The declared name is printed beside the searched name rather than instead
    of it. A source's spelling of a name is that source's claim; the name under
    investigation is the one the investigator supplied, and this report never
    silently swaps one for the other.
    """
    add("")
    add("### Social profiles")
    add("")
    if not model.social_profiles:
        add("No public profile was recorded for this case.")
        return
    for profile in model.social_profiles:
        handle = f" @{profile.handle}" if profile.handle else ""
        add(f"#### {profile.platform_label}{handle}")
        add("")
        add(f"<{profile.profile_url}>")
        add("")
        if profile.searched_name or profile.declared_name:
            add(f"- Searched name: {profile.searched_name or '—'}")
            add(f"- Declared name: {profile.declared_name or '—'}")
            if profile.name_relationship:
                add(f"- Name relationship: {profile.name_relationship.get('explanation', '—')}")
        for fact in profile.profile_facts:
            add(f"- {fact.get('label', fact.get('kind'))}: {fact.get('value')}")
            line = fact.get("source_line")
            if line:
                add(f"  - Stated as: \u201c{line}\u201d")
            if fact.get("interpretation"):
                add(f"  - {fact['interpretation']}")
        if profile.detail_source_url:
            add(f"- Source of the statements above: <{profile.detail_source_url}>")
        elif profile.detail_note:
            add(f"- {profile.detail_note}")
        add(f"- Automated confidence: {profile.confidence:.2f} (computed by the platform)")
        add(
            f"- Analyst decision: {profile.analyst_decision or 'none recorded'}"
            + (f" — {profile.analyst_note}" if profile.analyst_note else "")
        )
        if profile.corroborated_by:
            add(f"- Corroborated by: {', '.join(profile.corroborated_by)}")
        for reason in profile.match_reasons:
            add(f"- Why it may match: {reason}")
        for reason in profile.mismatch_reasons:
            add(f"- Why it may not: {reason}")
        if not profile.server_fetchable and profile.fetch_note:
            add(f"- {profile.fetch_note}")
        add("")


def _render_contacts(add: Any, model: ReportModel) -> None:
    add("")
    add("### Public contacts")
    add("")
    if not model.public_contacts:
        add("No verified public contact was found.")
        return
    add("| Kind | Value | Classification | Source | Confidence | Analyst decision |")
    add("| --- | --- | --- | --- | ---: | --- |")
    for contact in model.public_contacts:
        add(
            f"| {contact.contact_type} | {contact.value} | {contact.classification} | "
            f"{contact.source_url or contact.source_name} | {contact.confidence:.2f} | "
            f"{contact.analyst_decision or 'none recorded'} |"
        )
    add("")
    for contact in model.public_contacts:
        if contact.extraction_reason:
            add(f"- `{contact.value}` — {contact.extraction_reason}")


def _render_images(add: Any, model: ReportModel) -> None:
    add("")
    add("### Public image evidence")
    add("")
    if not model.images:
        add("No public image was recorded.")
        return
    add("| Image | Appears on | State | SHA-256 | Analyst decision |")
    add("| --- | --- | --- | --- | --- |")
    for image in model.images:
        add(
            f"| {image.image_url} | {image.source_page_url} | {image.fetch_state} | "
            f"{image.sha256 or '—'} | {image.analyst_decision or 'none recorded'} |"
        )
    add("")
    add(
        "Each image is page context: it appears on a public page associated with a candidate. "
        "That is the whole of the claim. No image here identifies anyone."
    )


RENDERERS: dict[str, Any] = {
    "html": render_html,
    "md": render_markdown,
    "json": render_json,
}
