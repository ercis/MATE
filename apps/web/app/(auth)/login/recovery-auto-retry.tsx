"use client";

import { useEffect, useRef } from "react";

import { LOGIN_RECOVERY_KEY } from "@/lib/auth-recovery";

/**
 * Hands-free recovery from a dead refresh token. The login page renders this
 * only when the current session is flagged `RefreshAccessTokenError`; on mount
 * it navigates to /login/start once so the user re-authenticates without
 * clicking anything. A document navigation (not a form/fetch), so it works
 * even when a content blocker / stale service worker intercepts fetches.
 *
 * The one-shot `sessionStorage` guard is the loop-breaker: if the OAuth
 * round-trip comes back and we're STILL on /login errored, we don't auto-retry
 * again – the page falls back to the manual "Sign in again" link. The flag is
 * cleared the moment a healthy session is seen (`components/session-guard.tsx`),
 * so a later expiry can auto-recover afresh.
 */
export function RecoveryAutoRetry({ href }: { href: string }) {
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    try {
      if (sessionStorage.getItem(LOGIN_RECOVERY_KEY)) return; // already auto-tried
      sessionStorage.setItem(LOGIN_RECOVERY_KEY, "1");
    } catch {
      return; // storage blocked → leave the manual link, don't auto-retry
    }
    window.location.assign(href);
  }, [href]);
  return null;
}
