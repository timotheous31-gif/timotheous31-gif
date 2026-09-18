"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, api, setUnauthenticatedHandler } from "@/lib/api";
import type { SessionInfo, WorkspaceSummary } from "@/types/api";

/**
 * Who is signed in, and which workspace they are working in.
 *
 * The session itself lives in an `HttpOnly` cookie the browser sends on every
 * request; this context holds only what the interface needs to *render* — the
 * user's name, their workspaces, and the permissions the backend computed for
 * each. None of it is a credential, and none of it is trusted for authorization:
 * every request is re-checked server-side, so a tampered value in here changes
 * what the page draws and nothing about what the API allows.
 */
interface SessionState {
  session: SessionInfo | null;
  workspace: WorkspaceSummary | null;
  /** True until the first `/auth/me` has resolved, so the app does not flash. */
  loading: boolean;
  selectWorkspace: (id: string) => void;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

const WORKSPACE_KEY = "osint.workspace";

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const info = await api.me();
      setSession(info);
    } catch (error) {
      // A 401 here is the ordinary "not signed in" case, not a failure worth
      // surfacing: the login screen is the answer to it.
      if (!(error instanceof ApiError && error.status === 401)) {
        console.error("Could not load the session", error);
      }
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    // The API client calls this when any request answers 401 — an expired or
    // revoked session. Clearing the state swaps the app for the login screen
    // rather than leaving a page of failed panels.
    setUnauthenticatedHandler(() => setSession(null));
    return () => setUnauthenticatedHandler(null);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setWorkspaceId(window.localStorage.getItem(WORKSPACE_KEY));
    } catch {
      // Private mode, or storage disabled. The first workspace is used instead.
    }
  }, []);

  const workspace = useMemo(() => {
    if (!session || session.workspaces.length === 0) return null;
    return (
      session.workspaces.find((item) => item.workspace.id === workspaceId) ??
      session.workspaces[0] ??
      null
    );
  }, [session, workspaceId]);

  const selectWorkspace = useCallback((id: string) => {
    setWorkspaceId(id);
    try {
      window.localStorage.setItem(WORKSPACE_KEY, id);
    } catch {
      // Remembering the choice is a convenience, not a requirement.
    }
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const info = await api.login(email, password);
    setSession(info);
    setLoading(false);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Clear locally even if the call failed: the user asked to be signed out,
      // and the server-side revocation is the part that actually matters.
      setSession(null);
    }
  }, []);

  const value = useMemo(
    () => ({ session, workspace, loading, selectWorkspace, signIn, signOut, refresh }),
    [session, workspace, loading, selectWorkspace, signIn, signOut, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error("useSession must be used inside a SessionProvider");
  }
  return context;
}
