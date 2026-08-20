import { QueryClient } from "@tanstack/react-query";

/**
 * Single source of truth for QueryClient defaults, shared by the browser
 * provider (`components/providers.tsx`) and the server prefetch layer
 * (`lib/prefetch.ts`).
 *
 * `staleTime` is > 0 on purpose: SSR-prefetched data is hydrated with a fresh
 * `dataUpdatedAt`, so a > 0 window means the client does NOT immediately refetch
 * on mount (the classic SSR double-fetch). 0 would negate every prefetch.
 *
 * This file is intentionally pure (no `cache()` / `next/headers`) so it is safe
 * to import from client code. The request-scoped server client lives in
 * `lib/prefetch.ts`.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,
        refetchOnWindowFocus: false,
        retry: (count, err: unknown) => {
          // Don't retry on 4xx – they're our fault, not the network's.
          const status = (err as { status?: number } | null)?.status;
          if (status && status >= 400 && status < 500) return false;
          return count < 2;
        },
      },
    },
  });
}
