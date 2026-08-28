/**
 * Fetch-free sign-in entry point (GET /login/start).
 *
 * The login CTA links here instead of posting a server action: after
 * hydration, server actions are fetch() calls, and the environments that
 * break the session fetch (content blocker / privacy extension / stale
 * service worker – the Safari login loop) break those the same way. As a
 * plain navigation this works even when every fetch on the page is
 * intercepted: wipe stale auth cookies (dead session + leftover PKCE/state
 * check cookies), then hand off to the Keycloak authorize redirect.
 *
 * `?prompt=login` (set by the login page for a refresh-failed session) forces
 * Keycloak → the university IdP to re-authenticate instead of silently
 * reissuing a session tied to the dead one.
 */
import type { NextRequest } from "next/server";

import { signIn } from "@/auth";
import { clearAuthCookies } from "@/lib/clear-session";

export async function GET(req: NextRequest): Promise<Response> {
  const raw = req.nextUrl.searchParams.get("callbackUrl") ?? "/processes";
  // Relative-path-only guard (no `//host` or absolute URLs → no open redirect).
  const callbackUrl = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/processes";
  const forceLogin = req.nextUrl.searchParams.get("prompt") === "login";

  await clearAuthCookies();
  // Throws NEXT_REDIRECT to the Keycloak authorize URL (Auth.js sets the fresh
  // PKCE/state cookies on that redirect response).
  await signIn(
    "keycloak",
    { redirectTo: callbackUrl },
    forceLogin ? { prompt: "login" } : undefined,
  );
  // signIn always redirects; reaching here means it didn't – fall back to the
  // login form instead of rendering an empty 200.
  return new Response(null, { status: 303, headers: { Location: "/login" } });
}
