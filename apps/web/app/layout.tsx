import type { Metadata } from "next";
import "@/app/globals.css";
// bpmn-js + diagram-js-minimap CSS – loaded here (host app) rather than
// inside the discovery module because the module bundler (esbuild) has no
// loaders for .woff / .ttf / .eot / .svg font assets.
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
import "diagram-js-minimap/assets/diagram-js-minimap.css";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "MATE Hub",
  description: "Local-first process analysis platform.",
};

// Defensive cleanup for any rogue service worker left over from a previous
// app on http://localhost:3000 (browsers scope SWs per origin, so a stale
// worker survives across projects). The platform itself ships no SW; this
// script unregisters whatever's installed and clears its caches before
// React hydrates, preventing CSP-blocked fetches and hydration mismatches.
const KILL_ROGUE_SW = `
(function () {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.getRegistrations().then(function (regs) {
    if (regs && regs.length) {
      regs.forEach(function (r) { r.unregister(); });
      if (typeof caches !== 'undefined') {
        caches.keys().then(function (ks) { ks.forEach(function (k) { caches.delete(k); }); });
      }
      // Reload once so the page is no longer controlled by the dead worker.
      if (!sessionStorage.getItem('__sw_cleanup_done__')) {
        sessionStorage.setItem('__sw_cleanup_done__', '1');
        window.location.reload();
      }
    }
  }).catch(function () {});
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: KILL_ROGUE_SW }} />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
