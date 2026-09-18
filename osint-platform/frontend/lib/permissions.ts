import type { WorkspaceRole, WorkspaceSummary } from "@/types/api";

/**
 * Permission identifiers, matching `app/core/permissions.py` exactly.
 *
 * Duplicated deliberately rather than fetched: the strings are part of the API
 * contract, and a typo here should fail typechecking rather than silently hide a
 * button. The *authority* is the backend — this list only decides what the
 * interface offers.
 */
export const PERMISSION = {
  caseRead: "case:read",
  caseCreate: "case:create",
  caseUpdate: "case:update",
  caseDelete: "case:delete",
  investigationRun: "investigation:run",
  investigationCancel: "investigation:cancel",
  targetWrite: "target:write",
  resultImport: "result:import",
  analystDecide: "analyst:decide",
  reportExport: "report:export",
  auditRead: "audit:read",
  workspaceUpdate: "workspace:update",
  membershipManage: "membership:manage",
  ownershipTransfer: "ownership:transfer",
} as const;

export type Permission = (typeof PERMISSION)[keyof typeof PERMISSION];

/** One sentence per role, for the workspace indicator. */
export const ROLE_SUMMARY: Record<WorkspaceRole, string> = {
  OWNER: "Full control, including transferring the workspace.",
  ADMIN: "Everything except transferring the workspace.",
  ANALYST: "Run investigations, import results, decide and export.",
  VIEWER: "Read-only: cases, evidence and reports.",
};

export function can(workspace: WorkspaceSummary | null, permission: Permission): boolean {
  return workspace?.permissions.includes(permission) ?? false;
}

/**
 * The message shown where a control would be.
 *
 * Says what is missing and who can grant it, rather than hiding the control
 * silently — a disabled button with no explanation reads as a broken product.
 */
export function deniedMessage(
  workspace: WorkspaceSummary | null,
  action: string,
): string {
  if (!workspace) return `Sign in to ${action}.`;
  return `Your role in this workspace (${workspace.role}) cannot ${action}. An owner or admin can change that.`;
}
