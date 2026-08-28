"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { LogModel } from "@/lib/api-types";

/**
 * Dashboard sharing + admin teams data layer.
 *
 * Mirrors `apps/api/.../schemas/sharing.py`. A share grants read-only access to
 * one dashboard for one target (a co-member, or a whole team). Teams + members
 * are admin-managed (`/admin/*`); the per-user views (`/sharing/*`,
 * `/dashboards/{id}/shares`) are available to every authenticated user.
 */

export type ShareKind = "user" | "team";

export interface ShareTarget {
  kind: ShareKind;
  id: string;
  label: string;
  sublabel: string | null;
}

export interface DashboardShare {
  id: string;
  dashboard_id: string;
  kind: ShareKind;
  target_id: string;
  label: string;
  created_at: string;
}

export interface SharedDashboard {
  id: string;
  name: string;
  description: string | null;
  event_log_id: string | null;
  log_model: LogModel;
  card_count: number;
  owner_label: string;
  updated_at: string;
}

export interface AdminUser {
  id: string;
  email: string | null;
  preferred_username: string | null;
  name: string | null;
}

export interface Team {
  id: string;
  name: string;
  member_count: number;
  created_at: string;
}

export interface TeamMember {
  user_id: string;
  role: string;
  email: string | null;
  preferred_username: string | null;
  name: string | null;
  created_at: string;
}

export interface AdminShare {
  id: string;
  dashboard_id: string;
  dashboard_name: string;
  owner_label: string;
  target_kind: ShareKind;
  target_label: string;
  created_at: string;
}

export const sharingKeys = {
  sharedWithMe: () => ["sharing", "shared-with-me"] as const,
  targets: () => ["sharing", "targets"] as const,
  dashboardShares: (id: string) => ["sharing", "dashboard", id] as const,
  adminUsers: () => ["admin", "users"] as const,
  adminTeams: () => ["admin", "teams"] as const,
  teamMembers: (id: string) => ["admin", "team", id, "members"] as const,
  adminShares: () => ["admin", "dashboard-shares"] as const,
};

// ── User-facing ─────────────────────────────────────────────────────────────

export function useSharedWithMe() {
  return useQuery({
    queryKey: sharingKeys.sharedWithMe(),
    queryFn: () => api<SharedDashboard[]>("/api/v1/sharing/shared-with-me"),
  });
}

export function useShareTargets(enabled = true) {
  return useQuery({
    queryKey: sharingKeys.targets(),
    queryFn: () => api<ShareTarget[]>("/api/v1/sharing/targets"),
    enabled,
    staleTime: 60_000,
  });
}

/** True once we know the user has nobody to share with (not in any team –
 * `/sharing/targets` returns `[]` exactly then). Drives the disabled state of
 * Share buttons: they stay visible but grayed with a "join a team" tooltip.
 * While loading (or on error) this is false, so the button stays usable and
 * the dialog's own empty-state acts as the fallback explanation. */
export function useNoShareTargets(enabled = true): boolean {
  const targets = useShareTargets(enabled);
  return targets.isSuccess && targets.data.length === 0;
}

export function useDashboardShares(dashboardId: string | null) {
  return useQuery({
    queryKey: dashboardId ? sharingKeys.dashboardShares(dashboardId) : ["sharing", "noop"],
    queryFn: () => api<DashboardShare[]>(`/api/v1/dashboards/${dashboardId}/shares`),
    enabled: !!dashboardId,
  });
}

export function useAddShare(dashboardId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (target: { target_user_id?: string; target_team_id?: string }) =>
      api<DashboardShare>(`/api/v1/dashboards/${dashboardId}/shares`, {
        method: "POST",
        json: target,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.dashboardShares(dashboardId) });
    },
  });
}

export function useRemoveShare(dashboardId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shareId: string) =>
      api<void>(`/api/v1/dashboards/${dashboardId}/shares/${shareId}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.dashboardShares(dashboardId) });
    },
  });
}

// ── Admin ───────────────────────────────────────────────────────────────────

export function useAdminUsers() {
  return useQuery({
    queryKey: sharingKeys.adminUsers(),
    queryFn: () => api<AdminUser[]>("/api/v1/admin/users"),
    staleTime: 30_000,
  });
}

export function useAdminTeams() {
  return useQuery({
    queryKey: sharingKeys.adminTeams(),
    queryFn: () => api<Team[]>("/api/v1/admin/teams"),
  });
}

export function useCreateTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<Team>("/api/v1/admin/teams", { method: "POST", json: { name } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.adminTeams() });
    },
  });
}

export function useUpdateTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, name }: { teamId: string; name: string }) =>
      api<Team>(`/api/v1/admin/teams/${teamId}`, { method: "PATCH", json: { name } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.adminTeams() });
    },
  });
}

export function useDeleteTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (teamId: string) =>
      api<void>(`/api/v1/admin/teams/${teamId}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.adminTeams() });
      void qc.invalidateQueries({ queryKey: sharingKeys.adminShares() });
    },
  });
}

export function useTeamMembers(teamId: string | null) {
  return useQuery({
    queryKey: teamId ? sharingKeys.teamMembers(teamId) : ["admin", "team", "noop"],
    queryFn: () => api<TeamMember[]>(`/api/v1/admin/teams/${teamId}/members`),
    enabled: !!teamId,
  });
}

export function useAddTeamMember(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api<TeamMember>(`/api/v1/admin/teams/${teamId}/members`, {
        method: "POST",
        json: { user_id: userId },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.teamMembers(teamId) });
      void qc.invalidateQueries({ queryKey: sharingKeys.adminTeams() });
    },
  });
}

export function useRemoveTeamMember(teamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api<void>(`/api/v1/admin/teams/${teamId}/members/${userId}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.teamMembers(teamId) });
      void qc.invalidateQueries({ queryKey: sharingKeys.adminTeams() });
    },
  });
}

export function useAdminShares() {
  return useQuery({
    queryKey: sharingKeys.adminShares(),
    queryFn: () => api<AdminShare[]>("/api/v1/admin/dashboard-shares"),
  });
}

export function useRevokeAdminShare() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (shareId: string) =>
      api<void>(`/api/v1/admin/dashboard-shares/${shareId}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.adminShares() });
    },
  });
}
