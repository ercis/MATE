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
 *
 * `?local=1` omits kc_idp_hint so Keycloak renders its OWN login form instead
 * of bouncing to the university IdP - the only way in for accounts that live in
 * the realm itself (service/test accounts, external collaborators, the
 * break-glass admin). Requires the browser flow's Identity Provider Redirector
 * to have NO default provider, or Keycloak redirects anyway, hint or not.
 */
import type { NextRequest } from "next/server";

import { signIn, KEYCLOAK_IDP_HINT } from "@/auth";
import { clearAuthCookies } from "@/lib/clear-session";

export async function GET(req: NextRequest): Promise<Response> {
  const raw = req.nextUrl.searchParams.get("callbackUrl") ?? "/processes";
  // Relative-path-only guard (no `//host` or absolute URLs → no open redirect).
  const callbackUrl = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/processes";
  const forceLogin = req.nextUrl.searchParams.get("prompt") === "login";
  const localAccount = req.nextUrl.searchParams.get("local") === "1";

  const authParams: Record<string, string> = {};
  if (forceLogin) authParams.prompt = "login";
  if (!localAccount && KEYCLOAK_IDP_HINT) authParams.kc_idp_hint = KEYCLOAK_IDP_HINT;

  await clearAuthCookies();
  // Throws NEXT_REDIRECT to the Keycloak authorize URL (Auth.js sets the fresh
  // PKCE/state cookies on that redirect response).
  await signIn(
    "keycloak",
    { redirectTo: callbackUrl },
    Object.keys(authParams).length > 0 ? authParams : undefined,
  );
  // signIn always redirects; reaching here means it didn't – fall back to the
  // login form instead of rendering an empty 200.
  return new Response(null, { status: 303, headers: { Location: "/login" } });
}
