import path from "node:path";
import type { NextConfig } from "next";

const webDir = __dirname;
const modulesDir = path.resolve(__dirname, "../../modules");

const config: NextConfig = {
  reactStrictMode: true,
  // Emit a self-contained build output at .next/standalone so the docker
  // image stays small (no node_modules in the runtime stage).
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
