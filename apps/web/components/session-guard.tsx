"use client";

import { useEffect, useRef } from "react";
import { useSession } from "next-auth/react";

import { logoutToLogin } from "@/lib/api";
import { LOGIN_RECOVERY_KEY } from "@/lib/auth-recovery";

/**
 * Watches the Auth.js session and signs the user out to /login the moment the
 * refresh token dies (`error === "RefreshAccessTokenError"`). SessionProvider
 * refetches on window focus, so a tab left open past the IdP idle timeout bounces
 * to login as soon as the user returns – instead of the next click 401-ing or a
 * navigation hitting a dead route. Reuses `logoutToLogin` so it shares the
 * single-flight / on-/login guard with the api wrapper. Renders nothing.
 */
export function SessionGuard() {
  const { data: session } = useSession();
  const fired = useRef(false);
  useEffect(() => {
    if (session?.error !== "RefreshAccessTokenError") {
      fired.current = false;
      // Healthy session → re-arm the login page's one-shot auto-recovery so a
      // later refresh-token death can recover hands-free again.
      if (session) {
        try {
          sessionStorage.removeItem(LOGIN_RECOVERY_KEY);
        } catch {
          /* storage blocked – nothing to clear */
        }
      }
      return;
    }
    if (fired.current) return;
    fired.current = true;
    void logoutToLogin();
  }, [session]);
  return null;
}
