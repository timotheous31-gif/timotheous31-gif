"""Entity and relationship extraction.

Turns normalised findings into the graph: which *things* an investigation has
seen, and how they are connected. Extraction is pure — it takes findings and
returns drafts — so every rule here is directly testable.

The rules that matter most are the ones that refuse to over-claim:

* a shared username produces ``SAME_USERNAME``, never ``POSSIBLY_SAME_ENTITY``;
* a self-published link (site → profile, profile → site) is the only signal
  strong enough to assert ownership;
* infrastructure that many parties share (an IP, a nameserver) produces
  ``HOSTED_ON``/``RESOLVES_TO`` with an explicit low ceiling.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.correlation.anchors import ANCHOR_RULES
from app.correlation.confidence import ConfidenceSignal, default_engine
from app.correlation.sources import evidence_class, reference_signal
from app.models.enums import EntityType, FindingKind, RelationshipType
from app.services.normalization import registrable_domain

log = get_logger(__name__)

#: An entity's identity within a case.
EntityKey = tuple[EntityType, str]


@dataclass(slots=True)
class EntityDraft:
    """A candidate entity, before it is reconciled with what the case holds."""

    type: EntityType
    canonical_value: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    signals: list[ConfidenceSignal] = field(default_factory=list)
    source_finding_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def key(self) -> EntityKey:
        return (self.type, self.canonical_value)

    def merge(self, other: EntityDraft) -> None:
        """Fold ``other`` into this draft (same key, more evidence)."""
        for alias in other.aliases:
            if alias not in self.aliases:
                self.aliases.append(alias)
        for name, value in other.attributes.items():
            self.attributes.setdefault(name, value)
        seen = {signal.key for signal in self.signals}
        self.signals.extend(signal for signal in other.signals if signal.key not in seen)
        for finding_id in other.source_finding_ids:
            if finding_id not in self.source_finding_ids:
                self.source_finding_ids.append(finding_id)
        if len(other.display_name) > len(self.display_name):
            self.display_name = other.display_name


@dataclass(slots=True)
class RelationshipDraft:
    """A candidate edge, before scoring and persistence."""

    source: EntityKey
    target: EntityKey
    type: RelationshipType
    signals: list[ConfidenceSignal] = field(default_factory=list)
    evidence_finding_ids: list[uuid.UUID] = field(default_factory=list)
    collector: str = "correlation"
    source_url: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[EntityKey, EntityKey, RelationshipType]:
        return (self.source, self.target, self.type)

    def merge(self, other: RelationshipDraft) -> None:
        seen = {signal.key for signal in self.signals}
        self.signals.extend(signal for signal in other.signals if signal.key not in seen)
        for finding_id in other.evidence_finding_ids:
            if finding_id not in self.evidence_finding_ids:
                self.evidence_finding_ids.append(finding_id)
        self.attributes.update(other.attributes)


@dataclass(slots=True)
class ExtractionResult:
    """Everything extraction produced for a set of findings."""

    entities: dict[EntityKey, EntityDraft] = field(default_factory=dict)
    relationships: dict[tuple, RelationshipDraft] = field(default_factory=dict)

    def add_entity(self, draft: EntityDraft) -> EntityDraft:
        # An entity with no canonical value has no identity: it would collide
        # with every other empty one and appear in the graph as a blank node.
        if not draft.canonical_value.strip():
            raise ValueError(f"{draft.type} entity has an empty canonical value")
        existing = self.entities.get(draft.key)
        if existing is None:
            self.entities[draft.key] = draft
            return draft
        existing.merge(draft)
        return existing

    def add_relationship(self, draft: RelationshipDraft) -> RelationshipDraft:
        # An edge between an entity and itself carries no information.
        if draft.source == draft.target:
            return draft
        existing = self.relationships.get(draft.key)
        if existing is None:
            self.relationships[draft.key] = draft
            return draft
        existing.merge(draft)
        return existing


class FindingLike:
    """Structural type: what extraction needs from a finding."""

    id: uuid.UUID
    kind: FindingKind
    data: dict[str, Any]
    collector: str
    source_url: str | None
    confidence: float


def extract(findings: Iterable[Any]) -> ExtractionResult:
    """Extract entities and relationships from ``findings``."""
    result = ExtractionResult()
    for finding in findings:
        handler = _HANDLERS.get(finding.kind)
        if handler is None:
            continue
        try:
            handler(finding, result)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # A malformed finding must not stop the rest of the extraction,
            # but it is reported rather than silently dropped.
            log.warning(
                "extraction.finding_skipped",
                finding_kind=str(finding.kind),
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return result


# ----------------------------------------------------------------- builders


def _domain_entity(host: str, finding: Any, signal_key: str = "dns_resolution") -> EntityDraft:
    host = host.lower().strip(".")
    return EntityDraft(
        type=EntityType.DOMAIN,
        canonical_value=host,
        display_name=host,
        attributes={"registrable_domain": registrable_domain(host)},
        signals=[default_engine.signal(signal_key)],
        source_finding_ids=[finding.id],
    )


def _ip_entity(address: str, finding: Any) -> EntityDraft:
    return EntityDraft(
        type=EntityType.IP_ADDRESS,
        canonical_value=address,
        display_name=address,
        signals=[default_engine.signal("dns_resolution")],
        source_finding_ids=[finding.id],
    )


def _website_entity(url: str, finding: Any, signal_key: str) -> EntityDraft:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or url).lower()
    canonical = f"{parts.scheme or 'https'}://{host}{parts.path or '/'}".rstrip("/") or (
        f"https://{host}"
    )
    return EntityDraft(
        type=EntityType.WEBSITE,
        canonical_value=canonical,
        display_name=host,
        attributes={"host": host, "url": url},
        signals=[default_engine.signal(signal_key)],
        source_finding_ids=[finding.id],
    )


def _username_entity(username: str, finding: Any, signal_key: str) -> EntityDraft:
    return EntityDraft(
        type=EntityType.USERNAME,
        canonical_value=username.lower(),
        display_name=username,
        signals=[default_engine.signal(signal_key)],
        source_finding_ids=[finding.id],
    )


def _social_entity(platform: str, handle: str, url: str, finding: Any) -> EntityDraft:
    return EntityDraft(
        type=EntityType.SOCIAL_ACCOUNT,
        canonical_value=f"{platform}:{handle.lower()}",
        display_name=f"{handle} on {platform}",
        attributes={"platform": platform, "handle": handle, "profile_url": url},
        signals=[default_engine.signal("same_unique_username")],
        source_finding_ids=[finding.id],
    )


# ----------------------------------------------------------------- handlers


def _handle_dns(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    hostname = str(data.get("hostname", "")).lower()
    if not hostname:
        return
    host_entity = result.add_entity(_domain_entity(hostname, finding))

    record_type = str(data.get("record_type", ""))
    records = [str(record) for record in data.get("records", [])]

    if record_type in {"A", "AAAA"}:
        for address in records:
            ip = result.add_entity(_ip_entity(address, finding))
            result.add_relationship(
                RelationshipDraft(
                    source=host_entity.key,
                    target=ip.key,
                    type=RelationshipType.RESOLVES_TO,
                    signals=[default_engine.signal("dns_resolution")],
                    evidence_finding_ids=[finding.id],
                    collector=finding.collector,
                    source_url=finding.source_url,
                    attributes={"record_type": record_type},
                )
            )
    elif record_type == "MX":
        for mail_host in data.get("mail_hosts", []):
            mail_entity = result.add_entity(_domain_entity(str(mail_host), finding))
            result.add_relationship(
                RelationshipDraft(
                    source=host_entity.key,
                    target=mail_entity.key,
                    type=RelationshipType.HAS_MX,
                    signals=[default_engine.signal("dns_resolution")],
                    evidence_finding_ids=[finding.id],
                    collector=finding.collector,
                    source_url=finding.source_url,
                )
            )
    elif record_type == "NS":
        for nameserver in data.get("nameservers", []):
            ns_entity = result.add_entity(_domain_entity(str(nameserver), finding))
            result.add_relationship(
                RelationshipDraft(
                    source=host_entity.key,
                    target=ns_entity.key,
                    type=RelationshipType.HOSTED_ON,
                    # Nameservers are shared by very many unrelated domains.
                    signals=[default_engine.signal("shared_infrastructure")],
                    evidence_finding_ids=[finding.id],
                    collector=finding.collector,
                    source_url=finding.source_url,
                    attributes={"role": "nameserver"},
                )
            )
    elif record_type == "CNAME":
        for alias_target in records:
            alias_entity = result.add_entity(_domain_entity(alias_target, finding))
            result.add_relationship(
                RelationshipDraft(
                    source=host_entity.key,
                    target=alias_entity.key,
                    type=RelationshipType.RESOLVES_TO,
                    signals=[default_engine.signal("dns_resolution")],
                    evidence_finding_ids=[finding.id],
                    collector=finding.collector,
                    source_url=finding.source_url,
                    attributes={"record_type": "CNAME"},
                )
            )


def _handle_registration(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    handle = str(data.get("handle", "")).lower().strip(".")
    if not handle:
        return
    domain = result.add_entity(_domain_entity(handle, finding, "authoritative_record"))
    domain.attributes.setdefault("registrar", data.get("registrar"))
    domain.attributes.setdefault("created_at", data.get("created_at"))
    domain.attributes.setdefault("expires_at", data.get("expires_at"))
    domain.attributes.setdefault("privacy_protected", data.get("privacy_protected"))

    organisation = data.get("registrant_organization")
    if organisation:
        org = result.add_entity(
            EntityDraft(
                type=EntityType.ORGANIZATION,
                canonical_value=str(organisation).strip().lower(),
                display_name=str(organisation),
                signals=[default_engine.signal("authoritative_record")],
                source_finding_ids=[finding.id],
            )
        )
        result.add_relationship(
            RelationshipDraft(
                source=org.key,
                target=domain.key,
                type=RelationshipType.OWNS_DOMAIN,
                signals=[default_engine.signal("authoritative_record")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
            )
        )


def _handle_http_metadata(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    if data.get("relation") == "site_links_to_profile":
        site = result.add_entity(_website_entity(str(data["from"]), finding, "site_links_profile"))
        link = str(data["to"])
        host = (urlsplit(link).hostname or "").lower().removeprefix("www.")
        from app.services.normalization import SOCIAL_HOSTS

        platform = SOCIAL_HOSTS.get(host, host)
        handle = _handle_from_url(link)
        if handle:
            profile = result.add_entity(_social_entity(platform, handle, link, finding))
            result.add_relationship(
                RelationshipDraft(
                    source=site.key,
                    target=profile.key,
                    type=RelationshipType.LINKS_TO,
                    # The site owner published this link themselves.
                    signals=[default_engine.signal("site_links_profile")],
                    evidence_finding_ids=[finding.id],
                    collector=finding.collector,
                    source_url=finding.source_url,
                )
            )
        return

    url = data.get("final_url") or data.get("url")
    if not url:
        return
    site = result.add_entity(_website_entity(str(url), finding, "authoritative_record"))
    for attribute in ("title", "description", "generator", "site_name", "status_code"):
        if data.get(attribute) is not None:
            site.attributes.setdefault(attribute, data[attribute])

    host = str(site.attributes.get("host", ""))
    if host:
        domain = result.add_entity(_domain_entity(host, finding, "authoritative_record"))
        result.add_relationship(
            RelationshipDraft(
                source=domain.key,
                target=site.key,
                type=RelationshipType.LINKS_TO,
                signals=[default_engine.signal("authoritative_record")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
                attributes={"relation": "domain_serves_site"},
            )
        )


def _handle_subdomain(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    hostname = str(data.get("hostname", "")).lower()
    parent = str(data.get("parent_domain", "")).lower()
    if not hostname or not parent:
        return
    child = result.add_entity(_domain_entity(hostname, finding, "certificate_covers_host"))
    parent_entity = result.add_entity(_domain_entity(parent, finding, "certificate_covers_host"))
    result.add_relationship(
        RelationshipDraft(
            source=child.key,
            target=parent_entity.key,
            type=RelationshipType.SUBDOMAIN_OF,
            signals=[default_engine.signal("certificate_covers_host")],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
        )
    )


def _handle_certificate(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    common_name = str(data.get("common_name", "")).lower()
    if not common_name:
        return
    certificate = result.add_entity(
        EntityDraft(
            type=EntityType.CERTIFICATE,
            canonical_value=f"cert:{data.get('crtsh_id') or common_name}",
            display_name=f"Certificate for {common_name}",
            attributes={
                "common_name": common_name,
                "issuer": data.get("issuer"),
                "not_before": data.get("not_before"),
                "not_after": data.get("not_after"),
            },
            signals=[default_engine.signal("certificate_covers_host")],
            source_finding_ids=[finding.id],
        )
    )
    for hostname in data.get("san_entries", []) or [common_name]:
        clean = str(hostname).lower().lstrip("*.")
        if not clean:
            continue
        host_entity = result.add_entity(_domain_entity(clean, finding, "certificate_covers_host"))
        result.add_relationship(
            RelationshipDraft(
                source=certificate.key,
                target=host_entity.key,
                type=RelationshipType.ISSUED_FOR,
                signals=[default_engine.signal("certificate_covers_host")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
            )
        )


def _handle_archive(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    original = data.get("original_url")
    if not original:
        return
    site = result.add_entity(_website_entity(str(original), finding, "archive_record"))
    document = result.add_entity(
        EntityDraft(
            type=EntityType.DOCUMENT,
            canonical_value=str(data.get("archived_url", "")),
            display_name=f"Archive snapshot {data.get('timestamp', '')}",
            attributes={"timestamp": data.get("timestamp"), "source": "internet_archive"},
            signals=[default_engine.signal("archive_record")],
            source_finding_ids=[finding.id],
        )
    )
    result.add_relationship(
        RelationshipDraft(
            source=site.key,
            target=document.key,
            type=RelationshipType.ARCHIVED_AS,
            signals=[default_engine.signal("archive_record")],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
        )
    )


def _handle_code_profile(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    login = str(data.get("login", ""))
    if not login:
        return

    if data.get("relation") == "profile_links_to_site":
        account = result.add_entity(
            _social_entity("github", login, f"https://github.com/{login}", finding)
        )
        site = result.add_entity(_website_entity(str(data["url"]), finding, "profile_links_site"))
        result.add_relationship(
            RelationshipDraft(
                source=account.key,
                target=site.key,
                type=RelationshipType.LINKS_TO,
                signals=[default_engine.signal("profile_links_site")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
            )
        )
        return

    account = result.add_entity(
        _social_entity("github", login, str(data.get("html_url", "")), finding)
    )
    account.attributes.setdefault("account_type", data.get("account_type"))
    account.attributes.setdefault("created_at", data.get("created_at"))
    if data.get("name"):
        account.aliases.append(str(data["name"]))

    username = result.add_entity(_username_entity(login, finding, "same_unique_username"))
    result.add_relationship(
        RelationshipDraft(
            source=username.key,
            target=account.key,
            type=RelationshipType.USES_USERNAME,
            signals=[default_engine.signal("authoritative_record")],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
        )
    )


def _handle_repository(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    full_name = str(data.get("full_name", ""))
    owner = str(data.get("owner", ""))
    if not full_name:
        return
    repository = result.add_entity(
        EntityDraft(
            type=EntityType.REPOSITORY,
            canonical_value=f"github:{full_name.lower()}",
            display_name=full_name,
            attributes={
                "language": data.get("language"),
                "topics": data.get("topics"),
                "stars": data.get("stars"),
                "created_at": data.get("created_at"),
                "html_url": data.get("html_url"),
            },
            signals=[default_engine.signal("authoritative_record")],
            source_finding_ids=[finding.id],
        )
    )
    if owner:
        account = result.add_entity(
            _social_entity("github", owner, f"https://github.com/{owner}", finding)
        )
        result.add_relationship(
            RelationshipDraft(
                source=account.key,
                target=repository.key,
                type=RelationshipType.CONTRIBUTED_TO,
                signals=[default_engine.signal("authoritative_record")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
                attributes={"role": "owner"},
            )
        )
    homepage = data.get("homepage")
    if homepage:
        site = result.add_entity(_website_entity(str(homepage), finding, "profile_links_site"))
        result.add_relationship(
            RelationshipDraft(
                source=repository.key,
                target=site.key,
                type=RelationshipType.LINKS_TO,
                signals=[default_engine.signal("profile_links_site")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
            )
        )


def _handle_membership(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    login = str(data.get("login", ""))
    organisation = str(data.get("organization", ""))
    if not login or not organisation:
        return
    account = result.add_entity(
        _social_entity("github", login, f"https://github.com/{login}", finding)
    )
    org = result.add_entity(
        EntityDraft(
            type=EntityType.ORGANIZATION,
            canonical_value=organisation.lower(),
            display_name=organisation,
            attributes={"platform": "github", "html_url": data.get("html_url")},
            signals=[default_engine.signal("self_declared_membership")],
            source_finding_ids=[finding.id],
        )
    )
    result.add_relationship(
        RelationshipDraft(
            source=account.key,
            target=org.key,
            type=RelationshipType.MEMBER_OF,
            signals=[default_engine.signal("self_declared_membership")],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
        )
    )


def _handle_commit_activity(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    repository_name = str(data.get("repository", ""))
    if not repository_name:
        return
    repository = result.add_entity(
        EntityDraft(
            type=EntityType.REPOSITORY,
            canonical_value=f"github:{repository_name.lower()}",
            display_name=repository_name,
            signals=[default_engine.signal("authoritative_record")],
            source_finding_ids=[finding.id],
        )
    )
    for author, commits in (data.get("authors") or {}).items():
        account = result.add_entity(
            _social_entity("github", str(author), f"https://github.com/{author}", finding)
        )
        result.add_relationship(
            RelationshipDraft(
                source=account.key,
                target=repository.key,
                type=RelationshipType.CONTRIBUTED_TO,
                signals=[default_engine.signal("commit_authorship")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
                attributes={"commits": commits},
            )
        )


def _handle_username_presence(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    username_value = str(data.get("username", ""))
    platform = str(data.get("platform", ""))
    if not username_value or not platform:
        return

    username = result.add_entity(
        _username_entity(username_value, finding, _username_signal_key(username_value))
    )
    account = result.add_entity(
        _social_entity(platform, username_value, str(data.get("profile_url", "")), finding)
    )
    result.add_relationship(
        RelationshipDraft(
            source=username.key,
            target=account.key,
            # Deliberately SAME_USERNAME, not POSSIBLY_SAME_ENTITY: the handle
            # exists on this platform, which says nothing about who holds it.
            type=RelationshipType.SAME_USERNAME,
            signals=[default_engine.signal(_username_signal_key(username_value))],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
            attributes={"platform": platform},
        )
    )


def _handle_email(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    address = str(data.get("address", ""))
    domain = str(data.get("domain", ""))
    if not address:
        return
    email_entity = result.add_entity(
        EntityDraft(
            type=EntityType.EMAIL,
            canonical_value=address.lower(),
            display_name=address,
            attributes={"domain": domain},
            signals=[default_engine.signal("authoritative_record")],
            source_finding_ids=[finding.id],
        )
    )
    if domain:
        domain_entity = result.add_entity(_domain_entity(domain, finding, "authoritative_record"))
        result.add_relationship(
            RelationshipDraft(
                source=email_entity.key,
                target=domain_entity.key,
                type=RelationshipType.REFERENCED_BY,
                signals=[default_engine.signal("authoritative_record")],
                evidence_finding_ids=[finding.id],
                collector=finding.collector,
                source_url=finding.source_url,
                attributes={"relation": "email_domain"},
            )
        )


def _handle_search_result(finding: Any, result: ExtractionResult) -> None:
    data = finding.data
    url = str(data.get("url", ""))
    if not url:
        return
    site = result.add_entity(_website_entity(url, finding, "search_reference"))
    site.attributes.setdefault("title", data.get("title"))
    site.attributes.setdefault("discovered_via", "search")


#: Corroboration kinds a PERSON collector may report, and the confidence rule
#: each one fires. Anything not in this table is ignored rather than trusted.
#:
#: Imported from the anchor model rather than restated here: a second copy would
#: drift, and a kind missing from it fails silently — the anchor matches, the
#: score is right on the finding, and the entity quietly loses it.
_CORROBORATION = ANCHOR_RULES


def _handle_person_candidate(finding: Any, result: ExtractionResult) -> None:
    """Turn one name-matching page into a candidate, kept apart from the subject.

    Two entities, never one. The *subject* is the person the case is about. Each
    *candidate* is keyed on the page that mentions the name, so ten pages about
    ten different people who share a name produce ten candidates rather than one
    merged persona. They are joined by a ``POSSIBLY_SAME_ENTITY`` edge carrying
    the ``same_person_name`` signal, whose ceiling (0.30) sits far below the
    auto-merge threshold (0.95) — so no accumulation of name matches can ever
    cause the resolver to fuse them. Only independent evidence, arriving through
    another rule, can raise the association.
    """
    data = finding.data
    url = str(data.get("url", ""))
    subject_value = str(data.get("subject_value", "")).strip()
    subject_name = str(data.get("subject_name", "")).strip() or subject_value
    if not url or not subject_value:
        return

    subject = result.add_entity(
        EntityDraft(
            type=EntityType.PERSONA,
            canonical_value=f"person:{subject_value}",
            display_name=subject_name,
            attributes={"role": "subject", "name": subject_name},
            source_finding_ids=[finding.id],
        )
    )

    # Corroboration recorded by the collector, translated back into the named
    # confidence rules. Only keys the engine knows are honoured, so a collector
    # cannot invent a signal by writing an arbitrary string into a finding.
    corroborated = [
        str(item) for item in (data.get("corroborated_by") or []) if str(item) in _CORROBORATION
    ]
    signals = [default_engine.signal("same_person_name")]
    signals.extend(default_engine.signal(_CORROBORATION[key]) for key in corroborated)

    host = str(data.get("host", "")) or (urlsplit(url).hostname or "")
    candidate = result.add_entity(
        EntityDraft(
            type=EntityType.PERSONA,
            # Keyed on the page, not the name: that is what keeps same-name
            # candidates from collapsing into each other.
            canonical_value=f"person-candidate:{url}",
            display_name=f"{subject_name} (candidate on {host})" if host else subject_name,
            attributes={
                "role": "candidate",
                "name_as_searched": subject_name,
                "candidate_name": data.get("candidate_name") or data.get("title"),
                "reference_url": url,
                "reference_title": data.get("title"),
                "host": host,
                # Which free source produced this, so the graph and the report
                # can say where a candidate came from without re-reading the
                # finding.
                "source": data.get("source") or finding.collector,
                "source_label": data.get("source_label"),
                "evidence_class": data.get("evidence_class")
                or evidence_class(str(data.get("source") or finding.collector)),
                "platform": data.get("platform"),
                "url_kind": data.get("url_kind"),
                "handle": data.get("handle"),
                "identifiers": data.get("identifiers") or {},
                "affiliations": data.get("affiliations") or [],
                "locations": data.get("locations") or [],
                # Both sides of the judgement travel with the node: an
                # investigator ruling a candidate out needs the reasons against
                # it as much as the reasons for it.
                "match_reasons": data.get("match_reasons") or [],
                "mismatch_reasons": data.get("mismatch_reasons") or [],
                "corroborated_by": corroborated,
                # The rules that produced this node's score, so a later
                # corroboration pass can rescore without losing the anchors.
                "signal_keys": [signal.key for signal in signals],
                # Nothing here establishes identity; say so on the node itself
                # so it survives into the graph view and the report.
                "identity_established": False,
            },
            signals=signals,
            source_finding_ids=[finding.id],
        )
    )

    page = result.add_entity(_website_entity(url, finding, "search_reference"))
    page.attributes.setdefault("title", data.get("title"))
    page.attributes.setdefault("discovered_via", "search")

    source = str(data.get("source") or finding.collector)
    result.add_relationship(
        RelationshipDraft(
            source=candidate.key,
            target=page.key,
            type=RelationshipType.REFERENCED_BY,
            # Worded for the source that actually produced this. Attributing an
            # ORCID registry record to "a search provider" is not a wording nit:
            # it misstates the evidence a reviewer is being asked to weigh.
            signals=[
                reference_signal(
                    source,
                    query=str(data.get("query") or "") or None,
                    engine=str(data.get("engine") or "") or None,
                )
            ],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
            attributes={
                "rank": data.get("rank"),
                "provider": data.get("provider") or source,
                "source": source,
                "evidence_class": data.get("evidence_class") or evidence_class(source),
            },
        )
    )
    result.add_relationship(
        RelationshipDraft(
            source=subject.key,
            target=candidate.key,
            type=RelationshipType.POSSIBLY_SAME_ENTITY,
            signals=[
                default_engine.signal(
                    "same_person_name",
                    detail=f"found by searching {subject_name!r}",
                ),
                *(default_engine.signal(_CORROBORATION[key]) for key in corroborated),
            ],
            evidence_finding_ids=[finding.id],
            collector=finding.collector,
            source_url=finding.source_url,
            attributes={
                "basis": (
                    "+".join(["name_match", *corroborated]) if corroborated else "name_match_only"
                ),
                # Corroborated or not, this stays a candidate: the resolver
                # merges only at 0.95, which none of these rules can reach.
                "requires_corroboration": not corroborated,
                "corroborated_by": corroborated,
                "source": data.get("source") or finding.collector,
            },
        )
    )


def _handle_image_evidence(finding: Any, result: ExtractionResult) -> None:
    """Attach an imported public image to the page it appears on.

    Image evidence is *page* evidence. What it establishes is that a picture
    appears on a page associated with the searched name — nothing about who is
    depicted. No comparison of any kind is performed between images, and the
    node records that explicitly so the claim cannot drift as the data is passed
    around.
    """
    data = finding.data
    page_url = str(data.get("source_page_url") or data.get("url") or "")
    if not page_url:
        return

    # The imported record is also a candidate: a page that names the subject.
    _handle_person_candidate(finding, result)

    page = result.add_entity(_website_entity(page_url, finding, "search_reference"))
    page.attributes.setdefault("title", data.get("title"))
    page.attributes.setdefault("discovered_via", data.get("source") or finding.collector)
    images = list(page.attributes.get("images") or [])
    images.append(
        {
            "image_url": data.get("image_url"),
            "thumbnail_url": data.get("thumbnail_url"),
            "caption": data.get("caption"),
            "query": data.get("query"),
            "engine": data.get("engine"),
            # Restated on the node, not only on the finding: whatever reads this
            # attribute must see the limit alongside the data.
            "analysis": "none",
            "biometric_matching": False,
        }
    )
    page.attributes["images"] = images


def _username_signal_key(username: str) -> str:
    """Distinctive handles are worth more than short, common ones."""
    return "same_unique_username" if len(username) >= 6 else "same_common_username"


def _handle_from_url(url: str) -> str | None:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    skip = {"u", "user", "users", "profile", "people", "in"}
    for part in parts:
        cleaned = part.lstrip("@")
        if part.lower() in skip or not cleaned:
            continue
        return cleaned
    return None


_HANDLERS = {
    FindingKind.DNS_RECORD: _handle_dns,
    FindingKind.DOMAIN_REGISTRATION: _handle_registration,
    FindingKind.HTTP_METADATA: _handle_http_metadata,
    FindingKind.SUBDOMAIN: _handle_subdomain,
    FindingKind.CERTIFICATE: _handle_certificate,
    FindingKind.ARCHIVE_SNAPSHOT: _handle_archive,
    FindingKind.CODE_PROFILE: _handle_code_profile,
    FindingKind.REPOSITORY: _handle_repository,
    FindingKind.ORGANIZATION_MEMBERSHIP: _handle_membership,
    FindingKind.COMMIT_ACTIVITY: _handle_commit_activity,
    FindingKind.USERNAME_PRESENCE: _handle_username_presence,
    FindingKind.EMAIL_DOMAIN: _handle_email,
    FindingKind.SEARCH_RESULT: _handle_search_result,
    FindingKind.PERSON_CANDIDATE: _handle_person_candidate,
    FindingKind.MANUAL_SEARCH_RESULT: _handle_person_candidate,
    FindingKind.IMAGE_EVIDENCE: _handle_image_evidence,
}
