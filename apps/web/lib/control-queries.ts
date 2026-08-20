"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  ControlItem,
  ControlItems,
  ControlMode,
  ControlScope,
} from "@/lib/api-types";

/** Admin control framework – list the controllable catalog for a scope and
 *  flip an item between user- and admin-controlled (admin-gated server-side). */

export function useControlItems(scope: ControlScope) {
  return useQuery<ControlItems>({
    queryKey: ["admin", "controls", scope],
    queryFn: () => api<ControlItems>(`/api/v1/admin/controls/items?scope=${scope}`),
    staleTime: 15_000,
  });
}

export function useSetControl(scope: ControlScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { key: string; control_mode: ControlMode; admin_value?: unknown }) =>
      api<ControlItem>(
        `/api/v1/admin/controls/items/${scope}/${encodeURIComponent(input.key)}`,
        {
          method: "PUT",
          json: { control_mode: input.control_mode, admin_value: input.admin_value ?? null },
        },
      ),
    onSuccess: (updated) => {
      // Reconcile the single row in the cached catalog.
      qc.setQueryData<ControlItems>(["admin", "controls", scope], (old) =>
        old
          ? {
              items: old.items.map((it) => (it.key === updated.key ? updated : it)),
            }
          : old,
      );
    },
  });
}
