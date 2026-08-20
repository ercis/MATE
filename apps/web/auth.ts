/**
 * Auth.js v5 configuration.
 *
 * - Sessions use the JWT strategy. By default the encrypted cookie holds
 *   Keycloak's access + refresh tokens. When SESSION_STORE_DIR is set
 *   (production), a custom jwt.encode/decode keeps that payload server-side
 *   (see lib/session-store) and the cookie carries only an opaque session id –
 *   so the cookie stays tiny and can't overflow the upstream proxy's buffer.
 * - The `jwt` callback rotates the access token via Keycloak's `/token`
 *   endpoint when it's within 30 s of expiry. On refresh failure we set
 *   `token.error = "RefreshAccessTokenError"`; the api wrapper picks that up
 *   and triggers a fresh sign-in.
 * - Logout is local-only (clears our cookie). We deliberately do NOT store the
 *   id_token in the session – it would bloat the cookie past the upstream
 *   reverse proxy's header buffer (502 on the callback). The trade-off: the
 *   Keycloak SSO session is not killed on logout, so re-login is silent until
 *   the IdP session times out.
 */

import { randomBytes } from "node:crypto";
import NextAuth, { type DefaultSession } from "next-auth";
import KeycloakProvider from "next-auth/providers/keycloak";
import CredentialsProvider from "next-auth/providers/credentials";
import type { JWT, JWTEncodeParams, JWTDecodeParams } from "next-auth/jwt";
import * as sessionStore from "@/lib/session-store";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
    error?: "RefreshAccessTokenError";
    provider?: string;
    user: {
      id: string;
      /** Keycloak realm roles, surfaced for client-side nav gating. */
      roles?: string[];
      /** Convenience flag – true iff `roles` includes the `admin` realm role. */
      isAdmin?: boolean;
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    provider?: string;
    error?: "RefreshAccessTokenError";
    /** Keycloak realm roles, decoded from the access token (no signature check
     * – we trust our own freshly-minted token; the API re-validates server-side). */
    roles?: string[];
    /** Server-side store key – present only when SESSION_STORE_DIR is set. */
    sid?: string;
  }
}

/** Decode the `realm_access.roles` from a Keycloak access token's payload.
 * No signature verification – this only drives UI affordances; every protected
 * API call is independently validated against the JWKS on the backend. */
function rolesFromAccessToken(accessToken: string | undefined): string[] {
  if (!accessToken) return [];
  const payload = accessToken.split(".")[1];
  if (!payload) return [];
  try {
    const json = Buffer.from(
      payload.replace(/-/g, "+").replace(/_/g, "/"),
      "base64",
    ).toString("utf8");
    const claims = JSON.parse(json) as { realm_access?: { roles?: unknown } };
    const roles = claims.realm_access?.roles;
    return Array.isArray(roles) ? roles.filter((r): r is string => typeof r === "string") : [];
  } catch {
    return [];
  }
}

const KEYCLOAK_ISSUER = process.env.KEYCLOAK_ISSUER ?? "http://localhost:8080/realms/flows-funds";
const KEYCLOAK_CLIENT_ID = process.env.KEYCLOAK_CLIENT_ID ?? "flows-funds-web";
const KEYCLOAK_CLIENT_SECRET = process.env.KEYCLOAK_CLIENT_SECRET ?? "";
// When set to a brokered IdP alias (e.g. "keycloak-oidc"), we pass kc_idp_hint on
// the authorize request so Keycloak skips its own login form and redirects
// straight to that IdP. Leave empty to show Keycloak's local login form (also
// the break-glass path for the admin@flows-funds.local account).
const KEYCLOAK_IDP_HINT = process.env.KEYCLOAK_IDP_HINT ?? "";

// Demo/dev login bypass. When DEMO_MODE is truthy we add a credentials provider
// ("demo") that signs a fixed demo user in with no form and no Keycloak round
// trip, minting DEMO_ACCESS_TOKEN as the session access token. The API accepts
// that exact sentinel as the demo user when its own DEMO_MODE is on (see
// auth/dependencies.DEMO_ACCESS_TOKEN). Both flags must be set, and NEITHER
// belongs in a real production deployment.
export const DEMO_MODE = ["1", "true", "yes", "on"].includes(
  (process.env.DEMO_MODE ?? "").toLowerCase(),
);
// When DEMO_ADMIN is truthy, the demo session is flagged isAdmin so admin-only UI
// shows. The API must also have DEMO_ADMIN on to actually authorise /admin/*.
export const DEMO_ADMIN = ["1", "true", "yes", "on"].includes(
  (process.env.DEMO_ADMIN ?? "").toLowerCase(),
);
const DEMO_ACCESS_TOKEN = "demo-access-token";
const DEMO_USER = { id: "demo-user", name: "Demo User", email: "demo@mate.local" };

// Single-flight guard: several modules (`lib/api`, `lib/ws`, the analytics
// client) each call `getSession()` independently, so after the access token
// expires a burst of activity can fire multiple `jwt` callbacks at once – all
// trying to redeem the *same* refresh token. With Keycloak rotation on, only
// the first redemption is valid; the rest race into `invalid_grant`. We dedupe
// concurrent refreshes per refresh token so one Keycloak call serves them all.
// (In-process only – a multi-instance deployment still relies on Keycloak's
// `refreshTokenMaxReuse` window to absorb cross-instance races.)
const inflightRefreshes = new Map<string, Promise<JWT>>();

async function refreshAccessToken(token: JWT): Promise<JWT> {
  if (!token.refreshToken) return { ...token, error: "RefreshAccessTokenError" };
  const key = token.refreshToken;
  const existing = inflightRefreshes.get(key);
  if (existing) return existing;
  const pending = doRefreshAccessToken(token).finally(() => {
    inflightRefreshes.delete(key);
  });
  inflightRefreshes.set(key, pending);
  return pending;
}

async function doRefreshAccessToken(token: JWT): Promise<JWT> {
  const refreshToken = token.refreshToken;
  if (!refreshToken) return { ...token, error: "RefreshAccessTokenError" };
  try {
    const resp = await fetch(`${KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: KEYCLOAK_CLIENT_ID,
        client_secret: KEYCLOAK_CLIENT_SECRET,
        refresh_token: refreshToken,
      }),
    });
    const data = (await resp.json()) as {
      access_token?: string;
      refresh_token?: string;
      id_token?: string;
      expires_in?: number;
      error?: string;
    };
    if (!resp.ok || !data.access_token || !data.expires_in) {
      return { ...token, error: "RefreshAccessTokenError" };
    }
    return {
      ...token,
      accessToken: data.access_token,
      refreshToken: data.refresh_token ?? token.refreshToken,
      // Auth.js v5 expects seconds – not ms. Don't multiply by 1000.
      expiresAt: Math.floor(Date.now() / 1000) + data.expires_in,
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshAccessTokenError" };
  }
}

const SESSION_MAX_AGE = 60 * 60 * 24 * 30; // 30 days, in seconds

// Option 3: when a server-side store is configured, override how the session
// JWT is (de)serialized – persist the full token to disk under a random id and
// hand the browser only that id. Without the store, Auth.js's default
// cookie-based JWT encoding is used unchanged (local dev).
const jwtOverride = sessionStore.sessionStoreEnabled
  ? {
      async encode(params: JWTEncodeParams<JWT>): Promise<string> {
        const token = params.token;
        if (!token) return "";
        const sid = token.sid ?? randomBytes(32).toString("hex");
        token.sid = sid;
        await sessionStore.putSession(sid, token, params.maxAge ?? SESSION_MAX_AGE);
        return sid;
      },
      async decode(params: JWTDecodeParams): Promise<JWT | null> {
        if (!params.token) return null;
        return sessionStore.readSession(params.token);
      },
    }
  : undefined;

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    KeycloakProvider({
      clientId: KEYCLOAK_CLIENT_ID,
      clientSecret: KEYCLOAK_CLIENT_SECRET,
      issuer: KEYCLOAK_ISSUER,
      // Forward kc_idp_hint so Keycloak redirects straight to the brokered IdP
      // (no Keycloak login page). Only added when KEYCLOAK_IDP_HINT is set; we
      // re-declare the default OIDC scope here so adding `params` doesn't drop
      // it.
      ...(KEYCLOAK_IDP_HINT
        ? {
            authorization: {
              params: { scope: "openid email profile", kc_idp_hint: KEYCLOAK_IDP_HINT },
            },
          }
        : {}),
    }),
    // Demo bypass – only registered when DEMO_MODE is on. No credentials are
    // checked; it always returns the same fixed demo user.
    ...(DEMO_MODE
      ? [
          CredentialsProvider({
            id: "demo",
            name: "Demo",
            credentials: {},
            authorize: () => DEMO_USER,
          }),
        ]
      : []),
  ],
  session: { strategy: "jwt", maxAge: SESSION_MAX_AGE },
  ...(jwtOverride ? { jwt: jwtOverride } : {}),
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        if (account.provider === "demo") {
          // Fixed demo session – sentinel token the API maps to the demo user.
          // No refresh token; keep it valid for the full session lifetime.
          token.accessToken = DEMO_ACCESS_TOKEN;
          token.provider = "demo";
          token.expiresAt = Math.floor(Date.now() / 1000) + SESSION_MAX_AGE;
          token.refreshToken = undefined;
          token.error = undefined;
          return token;
        }
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token;
        token.expiresAt = account.expires_at;
        token.provider = account.provider;
        return token;
      }
      // The demo session is non-expiring and has no refresh token – never try
      // to rotate it (that would fail and flag RefreshAccessTokenError).
      if (token.provider === "demo") return token;
      const now = Math.floor(Date.now() / 1000);
      if (token.expiresAt && now < token.expiresAt - 30) {
        return token;
      }
      return refreshAccessToken(token);
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken;
      session.error = token.error;
      session.provider = token.provider;
      if (token.sub) session.user.id = token.sub;
      // The demo sentinel token isn't a JWT, so roles can't be decoded from it –
      // derive them from DEMO_ADMIN instead (mirrors the API's demo_admin flag).
      if (token.provider === "demo") {
        session.user.roles = DEMO_ADMIN ? ["admin"] : [];
        session.user.isAdmin = DEMO_ADMIN;
        return session;
      }
      // Decode roles from the current access token (re-decoded each read so a
      // rotated token's roles stay correct). Drives only UI gating.
      const roles = rolesFromAccessToken(token.accessToken);
      session.user.roles = roles;
      session.user.isAdmin = roles.includes("admin");
      return session;
    },
  },
});
