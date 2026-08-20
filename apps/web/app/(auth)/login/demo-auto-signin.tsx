"use client";

import { useEffect, useRef } from "react";
import { signIn } from "next-auth/react";

/**
 * Demo mode only: fires the `demo` credentials sign-in on mount so the user
 * lands in the app without clicking anything. The visible "Enter demo
 * workspace" button on the login page is the manual fallback.
 */
export function DemoAutoSignIn({ callbackUrl }: { callbackUrl: string }) {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void signIn("demo", { callbackUrl });
  }, [callbackUrl]);
  return null;
}
