"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  AdminApiTokenInfo,
  ApiTokenInfo,
  CreateTokenResponse,
  McpAdminConfig,
  McpAdminUpdate,
  McpConsentState,
  McpInfo,
} from "@/lib/api-types";

const TOKENS_KEY = ["api-tokens"] as const;
const MCP_INFO_KEY = ["mcp-info"] as const;
const CONSENT_KEY = ["mcp-consent"] as const;
const ADMIN_CFG_KEY = ["mcp-admin-config"] as const;
const ADMIN_TOKENS_KEY = ["mcp-admin-tokens"] as const;

export function useApiTokens() {
  return useQuery({ queryKey: TOKENS_KEY, queryFn: () => api<ApiTokenInfo[]>("/api/v1/api-tokens") });
}

export function useMcpInfo() {
  return useQuery({ queryKey: MCP_INFO_KEY, queryFn: () => api<McpInfo>("/api/v1/api-tokens/mcp-info") });
}

export function useMcpConsent() {
  return useQuery({
    queryKey: CONSENT_KEY,
    queryFn: () => api<McpConsentState>("/api/v1/api-tokens/consent"),
  });
}

export function useSetConsent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (consented: boolean) =>
      api<McpConsentState>("/api/v1/api-tokens/consent", { method: "PUT", json: { consented } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CONSENT_KEY });
      void qc.invalidateQueries({ queryKey: MCP_INFO_KEY });
    },
  });
}

export function useCreateApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; scopes?: string[]; expires_in_days?: number | null }) =>
      api<CreateTokenResponse>("/api/v1/api-tokens", { method: "POST", json: body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: TOKENS_KEY }),
  });
}

export function useRevokeApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean }>(`/api/v1/api-tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: TOKENS_KEY }),
  });
}

// ── Admin (gated): the query simply errors with 403 for non-admins, so callers
// treat `isSuccess` as "is admin" and hide the section otherwise. ──────────────

export function useMcpAdminConfig() {
  return useQuery({
    queryKey: ADMIN_CFG_KEY,
    queryFn: () => api<McpAdminConfig>("/api/v1/system/mcp"),
    retry: false,
  });
}

export function useUpdateMcpAdminConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: McpAdminUpdate) =>
      api<McpAdminConfig>("/api/v1/system/mcp", { method: "PUT", json: body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ADMIN_CFG_KEY });
      // read_only surfaces in the user-facing mcp-info panel too.
      void qc.invalidateQueries({ queryKey: MCP_INFO_KEY });
    },
  });
}

export function useAdminApiTokens(enabled: boolean) {
  return useQuery({
    queryKey: ADMIN_TOKENS_KEY,
    queryFn: () => api<AdminApiTokenInfo[]>("/api/v1/admin/api-tokens"),
    enabled,
    retry: false,
  });
}

export function useAdminRevokeToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean }>(`/api/v1/admin/api-tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ADMIN_TOKENS_KEY }),
  });
}
