"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type {
  ScanResponse,
  WatchedFolderDetail,
  WatchedFolderSummary,
  WatchedFolderUpdatePayload,
} from "@/lib/api-types";

export const watchedKeys = {
  all: () => ["watched-folders"] as const,
  detail: (id: string) => ["watched-folders", id] as const,
};

export function useWatchedFolders() {
  return useQuery({
    queryKey: watchedKeys.all(),
    queryFn: () => api<WatchedFolderSummary[]>("/api/v1/watched-folders"),
  });
}

export function useWatchedFolder(id: string | null) {
  return useQuery({
    queryKey: id ? watchedKeys.detail(id) : ["watched-folders", "noop"],
    queryFn: () => api<WatchedFolderDetail>(`/api/v1/watched-folders/${id}`),
    enabled: Boolean(id),
  });
}

export function useUpdateWatchedFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; patch: WatchedFolderUpdatePayload }) =>
      api<WatchedFolderSummary>(`/api/v1/watched-folders/${input.id}`, {
        method: "PATCH",
        json: input.patch,
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: watchedKeys.all() });
      qc.invalidateQueries({ queryKey: watchedKeys.detail(vars.id) });
    },
  });
}

export function useDeleteWatchedFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/api/v1/watched-folders/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: watchedKeys.all() });
    },
  });
}

export function useScanWatchedFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<ScanResponse>(`/api/v1/watched-folders/${id}/scan`, { method: "POST" }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: watchedKeys.all() });
      qc.invalidateQueries({ queryKey: watchedKeys.detail(id) });
      // New logs may have landed in the destination folder.
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}
