import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { Button } from "@/components/ui/button";

/**
 * Root not-found boundary. Without one, an unmatched route – or a dead-session
 * request that slips past the reactive redirects in the platform layout / api
 * wrappers – rendered Next's bare "404 – This page could not be found" page.
 *
 * Make it auth-aware: a request with no usable session is almost always an
 * expired login landing on a route it can no longer reach, so bounce to /login
 * instead of showing a dead end. A valid session that genuinely hit a missing
 * route gets a clean, branded 404.
 */
export default async function NotFound() {
  const session = await auth();
  if (!session || session.error === "RefreshAccessTokenError") {
    redirect("/login");
  }
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-6 text-center">
      <div className="space-y-2">
        <p className="text-sm font-medium text-muted-foreground">404</p>
        <h1 className="text-2xl font-semibold">This page could not be found</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          The page you requested does not exist or has moved.
        </p>
      </div>
      <Button asChild>
        <Link href="/processes">Back to app</Link>
      </Button>
    </div>
  );
}
