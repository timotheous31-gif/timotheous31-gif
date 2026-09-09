"use client";

import { AnalystDecisionControl } from "@/components/case/analyst-decision";
import { useCaseId } from "@/components/case/shell";
import {
  Badge,
  Card,
  CardHeader,
  Empty,
  ErrorNotice,
  Mono,
  Spinner,
} from "@/components/ui/primitives";
import { useAsync } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import {
  NO_CONTACTS,
  awaitingReview,
  canShowThumbnail,
  classificationLabel,
  classificationTone,
  confidencePercent,
  contactHref,
  contactsByType,
  fetchStateLabel,
  hostOf,
  imagesBySource,
  leadingCaveat,
  nameComparison,
  orderedFacts,
} from "@/lib/social";
import type {
  CandidateGroup,
  ImageEvidenceRecord,
  PublicContactRecord,
  SocialProfileRecord,
} from "@/types/api";

/**
 * Social profiles, public images and analyst review, grouped by candidate.
 *
 * The page keeps two claims visibly apart on every row: the confidence the
 * platform computed, and the decision a human recorded. Blending them would
 * make an analyst's opinion look like evidence.
 *
 * Images are grouped by candidate and by source. Never by how they look — the
 * platform performs no image comparison, and offering that grouping would imply
 * an analysis that does not happen.
 */
export default function SocialReconPage() {
  const caseId = useCaseId();
  const groups = useAsync(() => api.listCandidateGroups(caseId), [caseId]);

  if (groups.error) return <ErrorNotice error={groups.error} retry={groups.reload} />;
  if (groups.loading || !groups.data) return <Spinner label="Loading candidates" />;

  const data = groups.data;
  const queue = awaitingReview(data);
  const totalProfiles = data.reduce((sum, group) => sum + group.social_profiles.length, 0);
  const totalImages = data.reduce((sum, group) => sum + group.images.length, 0);
  const totalContacts = data.reduce((sum, group) => sum + group.public_contacts.length, 0);

  if (totalProfiles === 0 && totalImages === 0 && totalContacts === 0) {
    return (
      <Card>
        <CardHeader title="Social & visual recon" />
        <div className="p-4">
          <Empty
            title="No social profiles or images yet"
            hint="Run the free collectors, or import a public result from the Recon tab."
          />
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader title="Analyst review" description="What still needs a human judgement" />
        <div className="flex flex-wrap gap-4 p-4 text-sm">
          <span>
            <strong>{queue.profiles.length}</strong>{" "}
            <span className="text-muted">profile(s) awaiting review</span>
          </span>
          <span>
            <strong>{queue.images.length}</strong>{" "}
            <span className="text-muted">image(s) awaiting review</span>
          </span>
          <span>
            <strong>{totalContacts}</strong>{" "}
            <span className="text-muted">public contact(s)</span>
          </span>
          <span className="basis-full text-xs text-muted">
            A decision is recorded alongside the automated confidence, never instead of it.
            Nothing here is an identification: the platform performs no facial or biometric
            analysis of any kind.
          </span>
        </div>
      </Card>

      {data.map((group) => (
        <CandidateCard
          key={group.entity_id ?? "unattributed"}
          caseId={caseId}
          group={group}
          onChanged={groups.reload}
        />
      ))}
    </div>
  );
}

function CandidateCard({
  caseId,
  group,
  onChanged,
}: {
  caseId: string;
  group: CandidateGroup;
  onChanged: () => void;
}) {
  return (
    <Card>
      <CardHeader
        title={group.display_name}
        description={
          group.entity_id
            ? "A public record carrying the searched name — a question, not an answer"
            : "Imported evidence not yet attributed to a candidate"
        }
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 pb-3">
        <Badge tone="PARTIAL">Automated confidence {confidencePercent(group.confidence)}</Badge>
        {group.entity_id ? (
          <AnalystDecisionControl
            caseId={caseId}
            subjectType="CANDIDATE"
            subjectId={group.entity_id}
            current={group.decision}
            onChanged={onChanged}
          />
        ) : null}
        {group.entity_id ? (
          <Badge tone="SKIPPED">
            {group.identity_established ? "Identity established" : "Identity not established"}
          </Badge>
        ) : null}
      </div>

      {group.match_reasons.length > 0 || group.mismatch_reasons.length > 0 ? (
        <div className="grid gap-3 border-b border-line p-4 sm:grid-cols-2">
          <Reasons title="Why this may be them" items={group.match_reasons} />
          <Reasons title="Why it may not be" items={group.mismatch_reasons} />
        </div>
      ) : null}

      <section className="border-b border-line p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
          Public contacts ({group.public_contacts.length})
        </h3>
        <p className="mt-0.5 text-[11px] text-muted">
          Published professional and business contact points only. Nothing here is derived
          from a name and a domain.
        </p>
        {group.public_contacts.length === 0 ? (
          <p className="mt-1 text-xs text-muted">{NO_CONTACTS}</p>
        ) : (
          Array.from(contactsByType(group.public_contacts)).map(([kind, contacts]) => (
            <div key={kind} className="mt-2">
              <p className="text-[11px] font-medium text-muted">{kind}</p>
              <ul className="mt-1 space-y-2">
                {contacts.map((contact) => (
                  <ContactRow
                    key={contact.id}
                    caseId={caseId}
                    contact={contact}
                    onChanged={onChanged}
                  />
                ))}
              </ul>
            </div>
          ))
        )}
      </section>

      <section className="border-b border-line p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
          Social profiles ({group.social_profiles.length})
        </h3>
        {group.social_profiles.length === 0 ? (
          <p className="mt-1 text-xs text-muted">None recorded.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {group.social_profiles.map((profile) => (
              <ProfileRow
                key={profile.id}
                caseId={caseId}
                profile={profile}
                onChanged={onChanged}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="p-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
          Public images ({group.images.length})
        </h3>
        <p className="mt-0.5 text-[11px] text-muted">
          Context evidence: where a picture appears. Never a claim about who is in it.
        </p>
        {group.images.length === 0 ? (
          <p className="mt-1 text-xs text-muted">None recorded.</p>
        ) : (
          Array.from(imagesBySource(group.images)).map(([source, images]) => (
            <div key={source} className="mt-3">
              <p className="text-[11px] font-medium text-muted">{source}</p>
              <ul className="mt-1 space-y-2">
                {images.map((image) => (
                  <ImageRow key={image.id} caseId={caseId} image={image} onChanged={onChanged} />
                ))}
              </ul>
            </div>
          ))
        )}
      </section>
    </Card>
  );
}

function ContactRow({
  caseId,
  contact,
  onChanged,
}: {
  caseId: string;
  contact: PublicContactRecord;
  onChanged: () => void;
}) {
  const href = contactHref(contact);
  return (
    <li className="space-y-1 rounded-md border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={classificationTone(contact.classification)}>
          {classificationLabel(contact.classification)}
        </Badge>
        {href ? (
          <a href={href} className="text-sm text-accent underline" rel="noreferrer noopener">
            {contact.value}
          </a>
        ) : (
          <span className="text-sm">{contact.value}</span>
        )}
        <Badge tone="PARTIAL">Automated {confidencePercent(contact.confidence)}</Badge>
        <AnalystDecisionControl
          caseId={caseId}
          subjectType="CONTACT"
          subjectId={contact.id}
          current={contact.decision}
          onChanged={onChanged}
        />
      </div>
      {contact.confidence_reasons.map((reason) => (
        <p key={reason} className="text-xs text-muted">
          {reason}
        </p>
      ))}
      {contact.extraction_reason ? (
        <p className="text-[11px] text-muted">{contact.extraction_reason}</p>
      ) : null}
      <p className="text-[11px] text-muted">
        Source: {contact.source_name} · {contact.evidence_class}
        {contact.retrieved_at ? ` · ${formatDateTime(contact.retrieved_at)}` : ""}
      </p>
      {contact.source_url ? (
        <a
          href={contact.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="block truncate text-[11px] text-accent underline"
        >
          Open source page — {hostOf(contact.source_url) || contact.source_url}
        </a>
      ) : null}
    </li>
  );
}

function Reasons({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs font-medium">{title}</h4>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ProfileRow({
  caseId,
  profile,
  onChanged,
}: {
  caseId: string;
  profile: SocialProfileRecord;
  onChanged: () => void;
}) {
  const caveat = leadingCaveat(profile);
  return (
    <li className="space-y-1 rounded-md border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{profile.platform_label}</Badge>
        {profile.handle ? <Mono>{profile.handle}</Mono> : null}
        <Badge tone="PARTIAL">Automated {confidencePercent(profile.confidence)}</Badge>
        <AnalystDecisionControl
          caseId={caseId}
          subjectType="SOCIAL_PROFILE"
          subjectId={profile.id}
          current={profile.decision}
          onChanged={onChanged}
        />
      </div>
      <a
        href={profile.profile_url}
        target="_blank"
        rel="noreferrer noopener"
        className="block text-xs text-accent underline"
      >
        Open source page — {profile.profile_url}
      </a>
      <ProfileDetail profile={profile} />
      {profile.match_reasons.map((reason) => (
        <p key={reason} className="text-xs">
          ✓ {reason}
        </p>
      ))}
      {caveat ? <p className="text-xs text-muted">✗ {caveat}</p> : null}
      {!profile.server_fetchable && profile.fetch_note ? (
        <p className="text-[11px] text-muted">{profile.fetch_note}</p>
      ) : null}
      <p className="text-[11px] text-muted">
        Source: {profile.collector} · {profile.evidence_class}
        {profile.retrieved_at ? ` · ${formatDateTime(profile.retrieved_at)}` : ""}
      </p>
    </li>
  );
}

/**
 * What a public profile states about itself.
 *
 * Two things this deliberately does not do. It does not replace the searched
 * name with the declared one — both are shown, with the relationship spelled
 * out, because a source's spelling of a name is a claim by that source. And it
 * prints a country as a public geographic association, never as nationality or
 * citizenship: those are legal statuses, and naming a place asserts neither.
 */
function ProfileDetail({ profile }: { profile: SocialProfileRecord }) {
  const comparison = nameComparison(profile);
  const facts = orderedFacts(profile);
  if (!comparison && facts.length === 0) return null;

  return (
    <div className="mt-2 space-y-1 rounded-md border border-line bg-panel p-2">
      {comparison ? (
        <div className="text-xs">
          <p>
            <span className="text-muted">Searched name:</span> {comparison.searched}
          </p>
          <p>
            <span className="text-muted">Declared name:</span> {comparison.declared}
          </p>
          <p className="text-[11px] text-muted">{comparison.explanation}</p>
        </div>
      ) : null}
      {facts.length > 0 ? (
        <dl className="space-y-1 text-xs">
          {facts.map((fact) => (
            <div key={`${fact.kind}:${fact.value}`}>
              <dt className="text-muted">{fact.label}</dt>
              <dd>{fact.value}</dd>
              <dd className="text-[11px] text-muted">Stated as “{fact.source_line}”</dd>
              {fact.interpretation ? (
                <dd className="text-[11px] text-muted">{fact.interpretation}</dd>
              ) : null}
            </div>
          ))}
        </dl>
      ) : null}
      {profile.detail_source_url ? (
        <a
          href={profile.detail_source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="block text-[11px] text-accent underline"
        >
          Source of the statements above
        </a>
      ) : null}
    </div>
  );
}

function ImageRow({
  caseId,
  image,
  onChanged,
}: {
  caseId: string;
  image: ImageEvidenceRecord;
  onChanged: () => void;
}) {
  return (
    <li className="flex gap-3 rounded-md border border-line p-3">
      {canShowThumbnail(image) ? (
        // Only ever an image the platform fetched itself. Rendering an unfetched
        // URL would make the browser retrieve it from a page nobody vetted, and
        // would look like verification that did not happen.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={image.final_url || image.image_url}
          alt={image.caption || "Public image recorded as page context"}
          className="h-16 w-16 shrink-0 rounded object-cover"
        />
      ) : (
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded border border-dashed border-line text-[10px] text-muted">
          no preview
        </div>
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={image.fetch_state === "FETCHED" ? "SUCCESS" : "SKIPPED"}>
            {fetchStateLabel(image.fetch_state)}
          </Badge>
          {image.platform ? <Badge>{image.platform}</Badge> : null}
          <AnalystDecisionControl
            caseId={caseId}
            subjectType="IMAGE"
            subjectId={image.id}
            current={image.decision}
            onChanged={onChanged}
          />
        </div>
        {image.caption ? <p className="text-xs">{image.caption}</p> : null}
        <a
          href={image.source_page_url}
          target="_blank"
          rel="noreferrer noopener"
          className="block truncate text-xs text-accent underline"
        >
          Open source page — {hostOf(image.source_page_url) || image.source_page_url}
        </a>
        {image.sha256 ? (
          <p className="text-[11px] text-muted">
            SHA-256 <Mono>{image.sha256.slice(0, 12)}</Mono>
            {image.byte_length ? ` · ${image.byte_length} bytes` : ""}
            {image.width && image.height ? ` · ${image.width}×${image.height}` : ""}
          </p>
        ) : (
          <p className="text-[11px] text-muted">
            {image.fetch_note || "Recorded by reference; the bytes were not downloaded."}
          </p>
        )}
        <p className="text-[11px] text-muted">
          No facial or biometric analysis is performed. This records where a picture appears,
          not who is in it.
        </p>
      </div>
    </li>
  );
}
