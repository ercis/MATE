import path from "node:path";
import type { NextConfig } from "next";

const webDir = __dirname;
const modulesDir = path.resolve(__dirname, "../../modules");

const config: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // Client Router Cache. Every (platform) page is a client shell whose data
    // freshness is owned by React Query, so the RSC payload of a route is
    // effectively static per session – yet Next 15 defaults `dynamic` to 0,
    // which re-fetches it from the server on EVERY navigation (the top
    // progress bar hanging on trivial pages). Reuse payloads client-side:
    // re-visiting a route within the window is a pure in-memory swap, and
    // hover/viewport prefetches stay usable instead of expiring instantly.
    staleTimes: { dynamic: 60, static: 300 },
    // Rewrite barrel imports (`import { X } from "radix-ui"`) into deep imports
    // so the compiler stops pulling the ENTIRE barrel into a route's module
    // graph. `radix-ui` (every ui/* primitive) and `lucide-react` (86 files)
    // sit in the shared graph, so this cuts first-compile time on EVERY route
    // in dev; the route-specific chart barrels help the heavy pages. Also tree-
    // shakes better in the prod build.
    optimizePackageImports: [
      "radix-ui",
      "lucide-react",
      "recharts",
      "@xyflow/react",
      "framer-motion",
    ],
  },
  // Emit a self-contained build output at .next/standalone so the docker
  // runtime stage stays small (no node_modules). REQUIRED by the Dockerfile's
  // `COPY .next/standalone` — without it `make deploy` fails with "not found".
  output: "standalone",
  outputFileTracingRoot: process.env.OUTPUT_FILE_TRACING_ROOT,
  webpack(config) {
    config.resolve = config.resolve ?? {};
    config.resolve.alias = {
      ...(config.resolve.alias as Record<string, string>),
      "@modules": modulesDir,
    };
    // Module panels live outside apps/web (in modules/<id>/panel/) but import
    // packages like react, recharts, @tanstack/react-query that are installed
    // in apps/web/node_modules. Add it to webpack's resolve.modules so those
    // imports resolve regardless of the importing file's location.
    const existingModules = (config.resolve.modules as string[] | undefined) ?? [];
    config.resolve.modules = [
      path.resolve(webDir, "node_modules"),
      ...existingModules,
      "node_modules",
    ];
    return config;
  },
};

export default config;
