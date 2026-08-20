"use client";

import { useEffect, useRef } from "react";

import { LOGIN_RECOVERY_KEY } from "@/lib/auth-recovery";

/**
 * Hands-free recovery from a dead refresh token. The login page renders this
 * only when the current session is flagged `RefreshAccessTokenError`; on mount
 * it submits the (cookie-clearing) login form once so the user re-authenticates
 * without clicking anything.
 *
 * The one-shot `sessionStorage` guard is the loop-breaker: if the OAuth
 * round-trip comes back and we're STILL on /login errored, we don't auto-submit
 * again – the page falls back to a manual "Sign in again" button. The flag is
 * cleared the moment a healthy session is seen (`components/session-guard.tsx`),
 * so a later expiry can auto-recover afresh.
 */
export function RecoveryAutoRetry({ formId }: { formId: string }) {
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    try {
      if (sessionStorage.getItem(LOGIN_RECOVERY_KEY)) return; // already auto-tried
      sessionStorage.setItem(LOGIN_RECOVERY_KEY, "1");
    } catch {
      return; // storage blocked → leave the manual button, don't auto-retry
    }
    const form = document.getElementById(formId);
    if (form instanceof HTMLFormElement) form.requestSubmit();
  }, [formId]);
  return null;
}
