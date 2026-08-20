"use client";

import { useEffect } from "react";
import Link from "next/link";

import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";

/**
 * Route-segment error boundary (covers everything under the root layout). The
 * common auth case (a client `api()` call that 401s) is surfaced as "session
 * expired" with a hard link to /login; anything else gets a retry + back-to-app.
 *
 * Server-thrown errors arrive opaque (Next strips the message in prod), so the
 * 401 detection only fires for client-thrown `ApiError`s – the auth-aware
 * not-found boundary, the api 401 interceptor and the session guard catch the
 * server-render paths before they reach here.
 */
export default function RouteError({
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
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-6 text-center">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">
          {isAuth ? "Your session has expired" : "Something went wrong"}
        </h1>
        <p className="max-w-md text-sm text-muted-foreground">
          {isAuth
            ? "Please sign in again to continue."
            : "An unexpected error occurred. Try again, or head back to the app."}
        </p>
      </div>
      <div className="flex gap-3">
        {isAuth ? (
          <Button asChild>
            <a href="/login">Sign in</a>
          </Button>
        ) : (
          <>
            <Button onClick={reset}>Try again</Button>
            <Button variant="outline" asChild>
              <Link href="/processes">Back to app</Link>
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
