"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { ExportFacets, ExportFilters, ExportPreview } from "@/lib/api-types";

/**
 * Read-only TanStack Query hooks for the admin behaviour-export filter UI.
 * Mirrors ``lib/analytics-queries.ts``. No mutations here – the actual export
 * is a streamed download (``downloadBlob``), not a query.
 */

const KEYS = {
  facets: ["admin", "export", "facets"] as const,
  preview: (filters: ExportFilters) =>
    ["admin", "export", "preview", filters] as const,
};

/** Drop empty/undefined filter fields and serialise to a query string. */
export function exportQueryString(filters: ExportFilters): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v == null || v === "") continue;
    params.set(k, String(v));
  }
  return params.toString();
}

export function useExportFacets() {
  return useQuery<ExportFacets>({
    queryKey: KEYS.facets,
    queryFn: () => api<ExportFacets>("/api/v1/admin/export/facets"),
    staleTime: 60_000,
  });
}

export function useExportPreview(filters: ExportFilters) {
  const qs = exportQueryString(filters);
  return useQuery<ExportPreview>({
    queryKey: KEYS.preview(filters),
    queryFn: () =>
      api<ExportPreview>(
        `/api/v1/admin/export/preview${qs ? `?${qs}` : ""}`,
      ),
    // Keep the prior counts on screen while the next filter set loads so the
    // preview panel doesn't flash empty on every keystroke.
    placeholderData: keepPreviousData,
    staleTime: 5_000,
  });
}
