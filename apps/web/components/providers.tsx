"use client";

import { useEffect } from "react";
import { ThemeProvider } from "next-themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { useAnalytics } from "@/lib/stores/analytics";
import { AnalyticsProvider } from "@/lib/analytics/provider";
import { ServerStateSync } from "@/components/server-state-sync";
import { makeQueryClient } from "@/lib/get-query-client";

let _client: QueryClient | undefined;
function getQueryClient() {
  // Browser: one client for the tab's lifetime, so SSR-hydrated caches persist
  // across client-side navigations. Server: a throwaway (RSC prefetch uses the
  // request-scoped client in lib/prefetch.ts, never this one).
  if (typeof window === "undefined") return makeQueryClient();
  if (!_client) _client = makeQueryClient();
  return _client;
}

/**
 * The single client-side provider stack. Keeps the root layout
 * server-rendered and dependency-free.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  // Rehydrate persisted UI state after mount so SSR and initial client render
  // both use the same defaults (no hydration mismatch).
  useEffect(() => {
    // ui + viz are now hydrated per-user from the server by <ServerStateSync/>;
    // analytics keeps its local (per-device) persistence.
    useAnalytics.persist.rehydrate();
  }, []);

  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {/* Keep-warm: the Keycloak access token lives 15 min and rotates only as
          a side-effect of GET /api/auth/session (auth.ts jwt callback), so an
          idle tab must poll or its token silently expires. 240s → at least 3
          polls per token lifetime; Keycloak is only hit within 30s of expiry
          (~4 refreshes/h). refetchOnWindowFocus stays on (default), covering
          wake-from-sleep. refetchWhenOffline=false: an offline poll would just
          flicker useSession consumers to null. Side effect: an open tab keeps
          the SSO idle timer (8h) alive, capped by the 24h session max. */}
      <SessionProvider refetchInterval={4 * 60} refetchWhenOffline={false}>
        <ServerStateSync />
        <QueryClientProvider client={getQueryClient()}>
          <TooltipProvider delayDuration={300}>
            <AnalyticsProvider>
              {children}
              <Toaster richColors closeButton position="bottom-right" />
            </AnalyticsProvider>
          </TooltipProvider>
        </QueryClientProvider>
      </SessionProvider>
    </ThemeProvider>
  );
}
