/**
 * Delete stale Auth.js cookies (session + OAuth check cookies) before starting a
 * fresh sign-in. Server Action / Route Handler only – a Server Component render
 * cannot mutate cookies, which is why a wedged auth cookie can't clear itself.
 *
 * Two failure modes this defends against:
 *  - A refresh-failed session cookie (`RefreshAccessTokenError`) that `auth()`
 *    keeps returning, bouncing the user to /login forever.
 *  - A leftover OAuth *check* cookie (PKCE `code_verifier`, `state`, `nonce`)
 *    from an earlier/aborted attempt. At the callback Auth.js reads the stale
 *    one and throws `InvalidCheck: pkceCodeVerifier value could not be parsed`.
 *    Wiping them first guarantees the fresh sign-in's cookie is the only one.
 */
import { cookies } from "next/headers";

// Auth.js cookie base names. `__Secure-` is prepended under HTTPS (prod); a
// session cookie larger than ~4KB is split into numbered chunks (`<name>.0`, …).
const AUTH_COOKIE_BASES = [
  "authjs.session-token",
  "authjs.pkce.code_verifier",
  "authjs.state",
  "authjs.nonce",
  "authjs.callback-url",
];

export function isAuthCookie(name: string): boolean {
  const bare = name.startsWith("__Secure-") ? name.slice("__Secure-".length) : name;
  return AUTH_COOKIE_BASES.some((base) => bare === base || bare.startsWith(`${base}.`));
}

export async function clearAuthCookies(): Promise<void> {
  const jar = await cookies();
  for (const cookie of jar.getAll()) {
    if (!isAuthCookie(cookie.name)) continue;
    // Overwrite-and-expire with the attributes Auth.js sets, so the browser
    // actually drops it (a `__Secure-` cookie is only deleted by a Secure
    // Set-Cookie on a matching path).
    jar.set(cookie.name, "", {
      path: "/",
      maxAge: 0,
      httpOnly: true,
      sameSite: "lax",
      secure: cookie.name.startsWith("__Secure-"),
    });
  }
}
