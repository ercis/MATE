"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";

import { routeProgress } from "@/components/route-progress";

type Router = ReturnType<typeof useRouter>;

/**
 * A `useRouter()` wrapper that nudges the top progress bar on programmatic
 * navigations. The global capture-phase anchor-click listener in
 * `route-progress.tsx` only catches real `<a>`/`<Link>` clicks; `router.push`
 * from a click handler (table rows, cards, cmdk, toasts) would otherwise leave
 * the user with no feedback while the next route's payload resolves.
 *
 * Use this anywhere a click handler navigates to a *different* route. Plain
 * `<Link>` already works — don't swap those. Same-page `replace` (tab/filter
 * URL sync) should keep using the bare `useRouter()` so it doesn't flash the bar.
 */
export function useProgressRouter() {
  const router = useRouter();
  return useMemo(
    () => ({
      ...router,
      push: (href: string, opts?: Parameters<Router["push"]>[1]) => {
        routeProgress.start();
        router.push(href, opts);
      },
      replace: (href: string, opts?: Parameters<Router["replace"]>[1]) => {
        routeProgress.start();
        router.replace(href, opts);
      },
    }),
    [router],
  );
}
