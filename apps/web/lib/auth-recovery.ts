/**
 * sessionStorage flag shared between the login page's auto-recovery and the
 * in-app session guard.
 *
 * Set when the login page auto-submits a hands-free recovery sign-in once
 * (`app/(auth)/login/recovery-auto-retry.tsx`); cleared the moment a healthy
 * session is observed (`components/session-guard.tsx`). The one-shot semantics
 * stop an OAuth round-trip that keeps returning to /login errored from looping
 * forever – after one auto-attempt we fall back to a manual button.
 */
export const LOGIN_RECOVERY_KEY = "ff-login-recovery-attempted";
