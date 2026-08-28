"use client";

import { useEffect, useState } from "react";
import { useFormStatus } from "react-dom";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Login CTA with instant click feedback. The navigation MUST stay a plain <a>
 * (GET /login/start) — a fetch()-based sign-in dies under fetch-layer
 * interception (content blockers / stale service workers, the Safari login
 * loop; see page.tsx). This wrapper only swaps the label for a spinner while
 * the browser does the full-document round-trip to Keycloak, which otherwise
 * shows no sign the click registered. `pageshow` resets the state so a bfcache
 * restore (back button from the IdP) doesn't resurrect a stale spinner.
 */
export function LoginCta({ href, label, pendingLabel }: {
  href: string;
  label: string;
  pendingLabel: string;
}) {
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const reset = () => setPending(false);
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  return (
    <Button asChild className="w-full" size="lg">
      <a href={href} aria-busy={pending} onClick={() => setPending(true)}>
        {pending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            {pendingLabel}
          </>
        ) : (
          label
        )}
      </a>
    </Button>
  );
}

/** Demo-mode submit button: server-action form, pending via useFormStatus. */
export function DemoSubmitButton({ label, pendingLabel }: {
  label: string;
  pendingLabel: string;
}) {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="w-full" size="lg" disabled={pending} aria-busy={pending}>
      {pending ? (
        <>
          <Loader2 className="h-4 w-4 animate-spin" />
          {pendingLabel}
        </>
      ) : (
        label
      )}
    </Button>
  );
}
