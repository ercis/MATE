import { ProcessesClient } from "./processes-client";

// Server shell only — no data fetch here, so the route stays prefetchable and
// paints instantly. The list loads client-side (async) inside ProcessesClient,
// warmed ahead of time by the sidebar/row intent-prefetch (lib/client-prefetch).
export default function ProcessesPage() {
  return <ProcessesClient />;
}
