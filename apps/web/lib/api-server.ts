/**
 * Server-side fetch wrapper. Use from RSCs and route handlers.
 *
 * Reads the bearer token from the encrypted Auth.js cookie via `auth()` and
 * forwards it on the `Authorization` header to the FastAPI backend over the
 * internal docker network (`INTERNAL_API_URL`).
 */

import { auth } from "@/auth";
import { ApiError } from "@/lib/api";

const SERVER_BASE = process.env.INTERNAL_API_URL ?? "http://localhost:8000";

async function bearer(): Promise<string | undefined> {
  const session = await auth();
  if (!session || session.error === "RefreshAccessTokenError") return undefined;
  return session.accessToken;
}

export async function apiServer<T = unknown>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(init.json);
  }
  const token = await bearer();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${SERVER_BASE}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let detail: unknown = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* keep text */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}
