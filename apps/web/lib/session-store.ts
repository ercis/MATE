/**
 * File-backed server-side session store (Option 3 – small auth cookie).
 *
 * When `SESSION_STORE_DIR` is set, `auth.ts` keeps the Auth.js session JWT
 * (which holds the Keycloak access + refresh tokens) here on disk – one JSON
 * file per session – and the browser cookie carries only an opaque session id.
 * That keeps the cookie at ~64 B so it can't overflow the upstream reverse
 * proxy's response-header buffer (the 502 on /api/auth/*).
 *
 * Node-only (uses `node:fs`). Never import this from Edge middleware.
 * Single-instance only: a horizontally scaled deployment would need a shared
 * store (Redis/DB) instead of the local filesystem.
 */
import { randomBytes } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type { JWT } from "next-auth/jwt";

const DIR = process.env.SESSION_STORE_DIR;

/** True when a server-side store is configured (prod); false → cookie-only (dev). */
export const sessionStoreEnabled = Boolean(DIR);

type Entry = { token: JWT; exp: number };

function fileFor(sid: string): string {
  // sid is our own 256-bit hex id; reject anything else to avoid path escapes.
  if (!/^[a-f0-9]{32,128}$/.test(sid)) throw new Error("invalid session id");
  return path.join(DIR as string, `${sid}.json`);
}

export async function putSession(sid: string, token: JWT, ttlSeconds: number): Promise<void> {
  await fs.mkdir(DIR as string, { recursive: true });
  const entry: Entry = { token, exp: Date.now() + ttlSeconds * 1000 };
  const target = fileFor(sid);
  // Unique per write: concurrent encodes for the same sid (a burst of parallel
  // authed requests) used to collide on a pid+ms tmp name, so the first rename
  // won and the rest hit ENOENT (JWTSessionError). A random suffix avoids that;
  // rename stays atomic and last-writer-wins is fine for a session store.
  const tmp = `${target}.${randomBytes(8).toString("hex")}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(entry), { mode: 0o600 });
  await fs.rename(tmp, target); // atomic within the same filesystem
}

/** Remove a session entry (server-side logout). Unknown/invalid sids are a
 * no-op – the route calling this treats the cookie value as untrusted input. */
export async function deleteSession(sid: string): Promise<void> {
  let target: string;
  try {
    target = fileFor(sid);
  } catch {
    return;
  }
  await fs.unlink(target).catch(() => {});
}

export async function readSession(sid: string): Promise<JWT | null> {
  let target: string;
  try {
    target = fileFor(sid);
  } catch {
    return null;
  }
  let raw: string;
  try {
    raw = await fs.readFile(target, "utf8");
  } catch {
    return null; // missing → no session
  }
  let entry: Entry;
  try {
    entry = JSON.parse(raw) as Entry;
  } catch {
    return null;
  }
  if (!entry.exp || entry.exp < Date.now()) {
    fs.unlink(target).catch(() => {}); // lazily evict expired
    return null;
  }
  return entry.token;
}
