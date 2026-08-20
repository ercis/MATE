"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";

/**
 * Scoped error boundary body for `(platform)` route segments. Renders INSIDE
 * the app shell (sidebar/topbar stay alive) so a single failed query degrades
 * to a contained, retryable message instead of bubbling to the root
 * `app/error.tsx` and blanking the whole app.
 *
 * Use from a segment `error.tsx`: `export default function Error(p) { return
 * <SegmentError {...p} /> }`.
 */
export function SegmentError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const isAuth = error instanceof ApiError && error.status === 401;

  return (
    <PageContainer>
      <EmptyState
        icon={AlertTriangle}
        title={isAuth ? "Your session has expired" : "Couldn't load this page"}
        description={
          isAuth
            ? "Please sign in again to continue."
            : "Something went wrong loading this section. Try again, or reload the page."
        }
        primaryAction={
          isAuth ? (
            <Button asChild className="cursor-pointer">
              <a href="/login">Sign in</a>
            </Button>
          ) : (
            <Button onClick={reset} className="cursor-pointer">
              Try again
            </Button>
          )
        }
      />
    </PageContainer>
  );
}
