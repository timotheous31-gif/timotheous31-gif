"use client";

import { useState } from "react";

import { useSession } from "@/components/session";
import { Badge, Select } from "@/components/ui/primitives";
import { ROLE_SUMMARY } from "@/lib/permissions";

/**
 * Who is signed in, which workspace they are in, and the way out.
 *
 * The role is shown next to the workspace rather than buried in a settings
 * page: a VIEWER who cannot see why a button is missing will file it as a bug,
 * and a visible "VIEWER" answers that before it is asked. The role's one-line
 * summary moved into the badge's tooltip — it was competing with the identity
 * line for the same two square inches and losing to it.
 */
export function UserMenu() {
  const { session, workspace, selectWorkspace, signOut } = useSession();
  const [busy, setBusy] = useState(false);

  if (!session) return null;
  const workspaces = session.workspaces;
  const initials = (session.user.display_name || session.user.email)
    .split(/[\s.@]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");

  return (
    <div className="border-t border-line bg-bg px-3 py-3 lg:mt-auto">
      {workspaces.length === 0 ? (
        <p className="rounded border border-danger/40 bg-danger/5 px-2.5 py-2 text-[11px] text-danger">
          You do not belong to a workspace yet. An owner or admin has to invite you before you
          can see any cases.
        </p>
      ) : (
        <>
          <p className="pb-1.5 text-[10px] font-semibold uppercase tracking-label text-faint">
            Workspace
          </p>
          {workspaces.length > 1 ? (
            <Select
              aria-label="Workspace"
              value={workspace?.workspace.id ?? ""}
              onChange={(event) => selectWorkspace(event.target.value)}
              className="w-full text-[12px]"
            >
              {workspaces.map((item) => (
                <option key={item.workspace.id} value={item.workspace.id}>
                  {item.workspace.name}
                </option>
              ))}
            </Select>
          ) : (
            <p className="truncate text-[13px] font-medium text-fg">
              {workspace?.workspace.name}
            </p>
          )}
          {workspace ? (
            <p className="mt-2">
              <Badge
                tone={workspace.role === "VIEWER" ? "NEUTRAL" : "OK"}
                title={ROLE_SUMMARY[workspace.role]}
              >
                {workspace.role}
              </Badge>
            </p>
          ) : null}
        </>
      )}

      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
        <span
          aria-hidden="true"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-line bg-raised font-mono text-[10px] font-semibold text-muted"
        >
          {initials}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted" title={session.user.email}>
          {session.user.display_name || session.user.email}
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void signOut().finally(() => setBusy(false));
          }}
          className="shrink-0 rounded border border-line-strong px-2 py-1 text-[11px] text-muted transition-colors hover:border-accent/60 hover:text-fg disabled:opacity-50"
        >
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}
