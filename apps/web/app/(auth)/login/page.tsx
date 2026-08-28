import { redirect } from "next/navigation";

import { auth, signIn, DEMO_MODE } from "@/auth";
import { ThemeToggleButton } from "@/components/theme-toggle-button";
import { MateLogo } from "@/components/mate-logo";
import { BorderBeam } from "@/components/glass/border-beam";
import { DemoAutoSignIn } from "./demo-auto-signin";
import { LoginCta, DemoSubmitButton } from "./login-cta";
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
  // lock-in). Break it: /login/start wipes the stale cookie before a fresh
  // OAuth round-trip, and `prompt=login` forces Keycloak → the university IdP to
  // re-authenticate rather than silently reissue a session tied to the dead one.
  //
  // The CTA is a plain link (GET /login/start), not a server action: after
  // hydration a server action submits over fetch(), which dies under the same
  // fetch-layer interception (content blocker / stale service worker) that
  // strands Safari in the login loop. A navigation always works.
  const staleSession = session?.error === "RefreshAccessTokenError";
  const startUrl = `/login/start?callbackUrl=${encodeURIComponent(callbackUrl)}${
    staleSession ? "&prompt=login" : ""
  }`;

  return (
    <div className="relative z-10 w-full max-w-sm space-y-6 rounded-2xl border border-white/15 [border-top-color:var(--glass-refraction-top)] bg-card/70 p-8 shadow-xl backdrop-blur-2xl backdrop-saturate-150 supports-[backdrop-filter]:bg-card/60">
      <BorderBeam className="rounded-2xl" />
      <ThemeToggleButton className="absolute right-4 top-4 h-8 w-8 cursor-pointer text-muted-foreground" />
      <div className="space-y-2 text-center">
        <MateLogo className="mx-auto h-11 w-11 text-foreground" />
        <h1 className="text-2xl font-semibold">PM-MATE</h1>
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
            <DemoSubmitButton label="Enter demo workspace" pendingLabel="Entering…" />
          </form>
        </>
      ) : (
        <>
          {/* Dead refresh token → auto-navigate once to recover hands-free; the
              one-shot guard inside stops a failing OAuth from looping and leaves
              the manual link as the fallback. */}
          {staleSession ? <RecoveryAutoRetry href={startUrl} /> : null}
          <LoginCta
            href={startUrl}
            label={staleSession ? "Sign in again" : "Login with university account"}
            pendingLabel="Signing in…"
          />
        </>
      )}
    </div>
  );
}
