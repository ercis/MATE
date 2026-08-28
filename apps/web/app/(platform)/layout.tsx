import { Suspense } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { MateLogo } from "@/components/mate-logo";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { JobsProvider } from "@/components/jobs/jobs-provider";
import { JobsDock } from "@/components/jobs/jobs-dock";
import { JobsDrawer } from "@/components/jobs/jobs-drawer";
import { OnboardingOverlay } from "@/components/onboarding";
import { TourOverlay } from "@/components/tour/tour-overlay";
import { MateAiSidebar } from "@/components/mate-ai/mate-ai-sidebar";
import { RouteProgress } from "@/components/route-progress";
import { SessionGuard } from "@/components/session-guard";
import { AppSplash } from "@/components/app-splash";

// Runs inline BEFORE the boot cover below is parsed, so the decision lands
// before its first paint: splash already shown this browser session (flag also
// written by components/app-splash.tsx — keep the key in sync) → stamp
// `data-ff-splash-done` on <html>, which display:none's the cover (globals.css).
// Otherwise arm a last-resort timeout above AppSplash's 12s hard timeout so a
// dead hydration can never trap the user behind the cover.
const SPLASH_BOOT_SCRIPT = `try{if(sessionStorage.getItem("__ff_splash_shown_v1")){document.documentElement.setAttribute("data-ff-splash-done","")}else{setTimeout(function(){document.documentElement.setAttribute("data-ff-splash-done","")},15000)}}catch(e){}`;

export default async function PlatformLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  // A refresh-failed session is still non-null (valid cookie, flagged error) but
  // has no usable token – treat it as logged-out and redirect server-side, before
  // any dashboard HTML renders. Otherwise the shell paints, then the client api
  // wrapper (lib/api.ts) catches the same error and signs out → a dashboard flash.
  // Mirrors lib/api-server.ts and (auth)/login/page.tsx.
  if (!session || session.error === "RefreshAccessTokenError") {
    redirect("/login");
  }
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Pre-hydration twin of the AppSplash overlay: server-rendered so a new
          session's very first paint is the splash, not a flash of the page.
          AppSplash retires it via `data-ff-splash-done` once its own overlay
          (same pixels) has committed. Kept in the DOM (only display:none'd) so
          RSC re-renders of this layout never reconcile against a removed node. */}
      <script dangerouslySetInnerHTML={{ __html: SPLASH_BOOT_SCRIPT }} />
      <div
        id="ff-splash-boot"
        aria-hidden
        className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-5 bg-background"
      >
        <div className="relative">
          <div className="ff-splash-glow-anim pointer-events-none absolute inset-0 -m-2 rounded-2xl bg-primary/30 blur-2xl" />
          <MateLogo
            animated
            className="relative h-16 w-16 text-foreground duration-700 animate-in fade-in-0 zoom-in-50"
          />
        </div>
        <div className="flex flex-col items-center gap-1 text-center duration-700 animate-in fade-in-0 slide-in-from-bottom-2">
          <span className="text-lg font-semibold tracking-tight">PM-MATE</span>
          <span className="text-xs text-muted-foreground">Caching workspace…</span>
        </div>
        <div className="mt-1 h-0.5 w-40 overflow-hidden rounded-full bg-border">
          <div className="h-full w-[6%] rounded-full bg-primary" />
        </div>
      </div>
      <noscript>
        <style>{`#ff-splash-boot{display:none}`}</style>
      </noscript>
      <SessionGuard />
      <Suspense fallback={null}>
        <RouteProgress />
      </Suspense>
      <Sidebar isAdmin={session.user.isAdmin === true} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Suspense>
          <Topbar />
        </Suspense>
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
      <MateAiSidebar />
      <JobsProvider />
      <JobsDock />
      <JobsDrawer />
      <OnboardingOverlay />
      <TourOverlay />
      <AppSplash isAdmin={session.user.isAdmin === true} />
    </div>
  );
}
