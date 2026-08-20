import { redirect } from "next/navigation";

import { auth, signIn, DEMO_MODE } from "@/auth";
import { ThemeToggleButton } from "@/components/theme-toggle-button";
import { Button } from "@/components/ui/button";
import { clearAuthCookies } from "@/lib/clear-session";
import { DemoAutoSignIn } from "./demo-auto-signin";
import { RecoveryAutoRetry } from "./recovery-auto-retry";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string }>;
}) {
  const session = await auth();
  const params = await searchParams;
  const callbackUrl = params.callbackUrl || "/processes";

  // A healthy session → straight into the app.
  if (session && session.error !== "RefreshAccessTokenError") {
    redirect(callbackUrl);
  }

  // A refresh-failed session keeps a valid-looking cookie, so `auth()` (here and
  // in the platform layout) keeps returning it and bouncing back to /login – and
  // a server `redirect()` can't delete the cookie, so it never recovers (the
  // lock-in). Break it: the login action wipes the stale cookie before a fresh
  // OAuth round-trip, and `prompt=login` forces Keycloak → the university IdP to
  // re-authenticate rather than silently reissue a session tied to the dead one.
  const staleSession = session?.error === "RefreshAccessTokenError";

  async function startKeycloakLogin() {
    "use server";
    await clearAuthCookies();
    await signIn(
      "keycloak",
      { redirectTo: callbackUrl },
      staleSession ? { prompt: "login" } : undefined,
    );
  }

  return (
    <div className="relative w-full max-w-sm space-y-6 rounded-2xl border border-border bg-card p-8 shadow-sm">
      <ThemeToggleButton className="absolute right-4 top-4 h-8 w-8 cursor-pointer text-muted-foreground" />
      <div className="space-y-2 text-center">
        <h1 className="text-2xl font-semibold">Mate</h1>
        <p className="text-sm text-muted-foreground">
          {DEMO_MODE
            ? "Demo mode – signing you in…"
            : staleSession
              ? "Your session expired – signing you back in…"
              : "Sign in with your workspace account to continue."}
        </p>
      </div>
      {DEMO_MODE ? (
        <>
          <DemoAutoSignIn callbackUrl={callbackUrl} />
          <form
            action={async () => {
              "use server";
              await signIn("demo", { redirectTo: callbackUrl });
            }}
          >
            <Button type="submit" className="w-full" size="lg">
              Enter demo workspace
            </Button>
          </form>
        </>
      ) : (
        <>
          {/* Dead refresh token → auto-submit once to recover hands-free; the
              one-shot guard inside stops a failing OAuth from looping and leaves
              the manual button as the fallback. */}
          {staleSession ? <RecoveryAutoRetry formId="login-form" /> : null}
          <form id="login-form" action={startKeycloakLogin}>
            <Button type="submit" className="w-full" size="lg">
              {staleSession ? "Sign in again" : "Login with university account"}
            </Button>
          </form>
        </>
      )}
    </div>
  );
}
