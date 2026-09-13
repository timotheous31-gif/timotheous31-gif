"use client";

import { useState } from "react";

import { useSession } from "@/components/session";
import { Badge } from "@/components/ui/primitives";
import { ROLE_SUMMARY } from "@/lib/permissions";

/**
 * Who is signed in, which workspace they are in, and the way out.
 *
 * The role is shown next to the workspace rather than buried in a settings page:
 * a VIEWER who cannot see why a button is missing will file it as a bug, and a
 * visible "VIEWER" answers that before it is asked.
 */
export function UserMenu() {
  const { session, workspace, selectWorkspace, signOut } = useSession();
  const [busy, setBusy] = useState(false);

  if (!session) return null;
  const workspaces = session.workspaces;

  return (
    <div className="border-t border-line px-4 py-3 lg:mt-auto">
      {workspaces.length === 0 ? (
        <p className="text-[11px] text-danger">
          You do not belong to a workspace yet. An owner or admin has to invite you
          before you can see any cases.
        </p>
      ) : (
        <>
          <label className="block text-[11px] text-muted">
            Workspace
            {workspaces.length > 1 ? (
              <select
                value={workspace?.workspace.id ?? ""}
                onChange={(event) => selectWorkspace(event.target.value)}
                className="mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-xs text-fg"
              >
                {workspaces.map((item) => (
                  <option key={item.workspace.id} value={item.workspace.id}>
                    {item.workspace.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className="mt-0.5 block truncate text-xs font-medium text-fg">
                {workspace?.workspace.name}
              </span>
            )}
          </label>
          {workspace ? (
            <p className="mt-1.5 flex items-center gap-1.5">
              <Badge tone={workspace.role === "VIEWER" ? "SKIPPED" : "SUCCESS"}>
                {workspace.role}
              </Badge>
              <span className="text-[10px] text-muted">{ROLE_SUMMARY[workspace.role]}</span>
            </p>
          ) : null}
        </>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[11px] text-muted" title={session.user.email}>
          {session.user.display_name || session.user.email}
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void signOut().finally(() => setBusy(false));
          }}
          className="shrink-0 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-line/60"
        >
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}
