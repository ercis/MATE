"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { sharingKeys } from "@/lib/sharing-queries";

/**
 * Admin user-management data layer. Mirrors `routes/admin_users.py`
 * (`AdminUserDetail`, `DeleteUserResponse`). The all-users LIST hook lives in
 * `sharing-queries.ts` (`useAdminUsers`); this file adds the drill-down detail
 * and the destructive full-purge delete.
 */

export interface UserEventLogBrief {
  id: string;
  name: string;
  status: string;
  log_model: string;
  events_count: number | null;
  cases_count: number | null;
  created_at: string;
  deleted_at: string | null;
}

export interface UserWatchedFolderBrief {
  id: string;
  name: string;
  status: string;
  mode: string;
}

export interface UserDashboardBrief {
  id: string;
  name: string;
  event_log_id: string | null;
}

export interface UserTeamBrief {
  team_id: string;
  name: string;
  role: string;
}

export interface UserApiTokenBrief {
  id: string;
  name: string;
  token_prefix: string;
  revoked: boolean;
  last_used_at: string | null;
}

export interface UserModuleBrief {
  module_id: string;
  /** True when a delete would tear the shared artifact down (sole owner, not a default). */
  last_owner: boolean;
}

export interface UserJobCounts {
  by_status: Record<string, number>;
  active: number;
}

export interface AdminUserDetail {
  id: string;
  email: string | null;
  preferred_username: string | null;
  name: string | null;
  label: string;
  created_at: string;
  last_seen_at: string;
  event_logs: UserEventLogBrief[];
  folders_count: number;
  watched_folders: UserWatchedFolderBrief[];
  dashboards: UserDashboardBrief[];
  shares_created: number;
  shares_received: number;
  jobs: UserJobCounts;
  modules: UserModuleBrief[];
  teams: UserTeamBrief[];
  api_tokens: UserApiTokenBrief[];
  analytics_sessions: number;
  analytics_events: number;
  storage_bytes: number | null;
}

export interface DeleteUserResponse {
  deleted: boolean;
  jobs_cancelled: number;
  modules_torn_down: number;
  keycloak_deleted: boolean;
  keycloak_skipped_reason: string | null;
  warnings: string[];
}

export const userKeys = {
  detail: (id: string, includeDisk: boolean) =>
    ["admin", "user-detail", id, includeDisk] as const,
};

export function useAdminUserDetail(userId: string, includeDisk = false) {
  return useQuery({
    queryKey: userKeys.detail(userId, includeDisk),
    queryFn: () =>
      api<AdminUserDetail>(
        `/api/v1/admin/users/${encodeURIComponent(userId)}${
          includeDisk ? "?include_disk=1" : ""
        }`,
      ),
    enabled: !!userId,
    staleTime: 15_000,
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api<DeleteUserResponse>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sharingKeys.adminUsers() });
    },
  });
}
