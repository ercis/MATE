"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  JobsInsights,
  StorageInsights,
  UsageInsights,
  UsersInsights,
} from "@/lib/api-types";

/** Admin metric groups for the admin dashboard (admin-gated server-side). The
 *  caller passes a day window mirroring the overview range selector. */

export function useUsersInsights(days: number) {
  return useQuery<UsersInsights>({
    queryKey: ["admin", "insights", "users", days],
    queryFn: () => api<UsersInsights>(`/api/v1/admin/insights/users?days=${days}`),
    staleTime: 30_000,
  });
}

export function useStorageInsights(days: number, includeDisk = false) {
  return useQuery<StorageInsights>({
    queryKey: ["admin", "insights", "storage", days, includeDisk],
    queryFn: () =>
      api<StorageInsights>(
        `/api/v1/admin/insights/storage?days=${days}${includeDisk ? "&include_disk=1" : ""}`,
      ),
    staleTime: 30_000,
  });
}

export function useJobsInsights(days: number) {
  return useQuery<JobsInsights>({
    queryKey: ["admin", "insights", "jobs", days],
    queryFn: () => api<JobsInsights>(`/api/v1/admin/insights/jobs?days=${days}`),
    // Live runtime snapshot – refresh while the page is open.
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
}

export function useUsageInsights(days: number) {
  return useQuery<UsageInsights>({
    queryKey: ["admin", "insights", "usage", days],
    queryFn: () => api<UsageInsights>(`/api/v1/admin/insights/usage?days=${days}`),
    staleTime: 30_000,
  });
}
