#!/usr/bin/env node
/**
 * Bundle every `modules/<id>/panel/index.{ts,tsx}` (and declared widgets)
 * into `modules/<id>/.dist/panel.js` as a CJS module with all runtime
 * externals preserved as `require(...)` calls. The frontend's module loader
 * intercepts each require() and resolves it against `window.__FF_RUNTIME__`
 * (see `apps/web/lib/module-panels.tsx`).
 *
 * Usage:
 *   node scripts/bundle-modules.mjs            # build once for all modules
 *   node scripts/bundle-modules.mjs --watch    # watch each panel + widget tree
 *   node scripts/bundle-modules.mjs <id> ...   # build only the listed module ids
 */
import { build, context } from "esbuild";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(WEB_ROOT, "..", "..");

// Modules dir + runtime externals are env-overridable so the script works
// both from the repo root (dev – `pnpm dev`) and from a flat install dir
// inside the production Docker image (the runner stage drops the script at
// /opt/ff-bundler/ alongside its own node_modules, with no apps/web parent).
const MODULES_DIR = process.env.FF_MODULES_DIR
  ? resolve(process.env.FF_MODULES_DIR)
  : resolve(REPO_ROOT, "modules");

function resolveExternalsPath() {
  if (process.env.FF_RUNTIME_EXTERNALS) return resolve(process.env.FF_RUNTIME_EXTERNALS);
  const adjacent = resolve(__dirname, "runtime-externals.json");
  if (existsSync(adjacent)) return adjacent;
  return resolve(WEB_ROOT, "lib", "runtime-externals.json");
}

const RUNTIME_EXTERNALS = JSON.parse(readFileSync(resolveExternalsPath(), "utf8"));

// esbuild resolves a bare import (e.g. `bpmn-js`, bundled and intentionally NOT
// externalised) by walking up the directory tree from the importing module
// source. In the production image the module tree (/app/modules) and the
// bundler's own deps (/opt/ff-bundler/node_modules) live under separate roots,
// so that walk never reaches the bundler's deps. Mirror NODE_PATH (which
// esbuild ignores from the environment by design) via the nodePaths option:
// list the bundler's node_modules as a resolve fallback. Only consulted when
// normal resolution fails, so it never perturbs the dev tree-walk.
const NODE_PATHS = [
  resolve(__dirname, "node_modules"),
  resolve(WEB_ROOT, "node_modules"),
].filter((p) => existsSync(p));

const argv = process.argv.slice(2);
const WATCH = argv.includes("--watch");
const ONLY_IDS = new Set(argv.filter((a) => !a.startsWith("--")));

function* iterModuleFolders() {
  if (!existsSync(MODULES_DIR)) return;
  for (const name of readdirSync(MODULES_DIR)) {
    if (name.startsWith(".") || name.startsWith("_")) continue;
    const folder = join(MODULES_DIR, name);
    const stat = statSync(folder, { throwIfNoEntry: false });
    if (!stat?.isDirectory()) continue;
    if (!existsSync(join(folder, "manifest.yaml"))) continue;
    yield folder;
  }
}

function readManifest(folder) {
  const text = readFileSync(join(folder, "manifest.yaml"), "utf8");
  return parseYaml(text);
}

function entriesForModule(folder, manifest) {
  // The platform's manifest schema (§5.6) declares:
  //   frontend:
  //     panel: ./panel/index.tsx
  //     widgets:
  //       - id: throughput-chart
  //         entry: ./widgets/Throughput.tsx
  const out = [];
  const fe = manifest.frontend ?? {};
  if (typeof fe.panel === "string") {
    out.push({ name: "panel", path: resolve(folder, fe.panel) });
  }
  if (Array.isArray(fe.widgets)) {
    for (const w of fe.widgets) {
      if (w && typeof w.entry === "string" && typeof w.id === "string") {
        out.push({ name: `widget-${w.id}`, path: resolve(folder, w.entry) });
      }
    }
  }
  return out.filter((e) => existsSync(e.path));
}

/** Aliases mirror apps/web/tsconfig.json – module source can use `@/...`. */
const ALIAS = {
  "@/": resolve(WEB_ROOT) + "/",
};

const aliasPlugin = {
  name: "ff-alias",
  setup(b) {
    for (const [prefix, target] of Object.entries(ALIAS)) {
      b.onResolve({ filter: new RegExp(`^${prefix.replace(/[/]/g, "\\/")}`) }, (args) => {
        // Workspace alias hits – but if the requested path matches one of
        // the runtime externals, we let esbuild's `external` list handle it
        // (which it will, since externals are matched before plugins).
        if (RUNTIME_EXTERNALS.includes(args.path)) {
          return { path: args.path, external: true };
        }
        return { path: resolve(target, args.path.slice(prefix.length)) };
      });
    }
  },
};

function commonOptions(entry, outdir) {
  return {
    entryPoints: [{ in: entry.path, out: entry.name }],
    outdir,
    bundle: true,
    format: "cjs",
    platform: "browser",
    target: "es2022",
    jsx: "automatic",
    jsxImportSource: "react",
    sourcemap: "inline",
    legalComments: "none",
    logLevel: WATCH ? "info" : "warning",
    // Runtime externals stay as require() calls; the frontend loader resolves
    // them against window.__FF_RUNTIME__.
    external: RUNTIME_EXTERNALS,
    // Fallback resolve roots for bundled (non-externalised) npm deps – see
    // NODE_PATHS above. Empty in dev (normal resolution already succeeds).
    nodePaths: NODE_PATHS,
    plugins: [aliasPlugin],
    define: {
      "process.env.NODE_ENV": JSON.stringify(process.env.NODE_ENV || "development"),
    },
    loader: { ".js": "jsx", ".ts": "ts", ".tsx": "tsx" },
  };
}

async function bundleModule(folder) {
  const manifest = readManifest(folder);
  const moduleId = manifest.id;
  if (ONLY_IDS.size && !ONLY_IDS.has(moduleId)) return [];
  const entries = entriesForModule(folder, manifest);
  if (!entries.length) {
    if (manifest.frontend) {
      console.warn(`[bundle-modules] ${moduleId}: manifest declares frontend but no entry found.`);
    }
    return [];
  }
  const outdir = join(folder, ".dist");
  const tasks = [];
  for (const entry of entries) {
    const opts = commonOptions(entry, outdir);
    if (WATCH) {
      const ctx = await context(opts);
      await ctx.watch();
      tasks.push(ctx);
      console.log(`[bundle-modules] watch ${moduleId}/${entry.name}`);
    } else {
      await build(opts);
      const rel = relative(REPO_ROOT, join(outdir, `${entry.name}.js`));
      console.log(`[bundle-modules] built ${moduleId}/${entry.name} -> ${rel}`);
    }
  }
  return tasks;
}

async function main() {
  const folders = [...iterModuleFolders()];
  if (!WATCH) {
    // Surface the resolve fallbacks + whether the bundled (non-externalised)
    // bpmn deps are actually present in this image. If bpmn-js shows NOT FOUND
    // here, the runner image was built without them (stale/cached build) – the
    // discovery panel will then fail to bundle.
    const bpmnProbe = NODE_PATHS.map((p) => join(p, "bpmn-js")).find((p) => existsSync(p));
    console.log(
      `[bundle-modules] nodePaths=${JSON.stringify(NODE_PATHS)} ` +
        `bpmn-js=${bpmnProbe ?? "NOT FOUND – discovery panel will fail to bundle"}`,
    );
  }
  const allTasks = [];
  for (const folder of folders) {
    try {
      const tasks = await bundleModule(folder);
      allTasks.push(...tasks);
    } catch (err) {
      console.error(`[bundle-modules] failed for ${folder}:`, err);
      if (!WATCH) process.exitCode = 1;
    }
  }
  if (!WATCH) return;
  // Keep the process alive while watch contexts run. A never-resolving promise
  // alone does NOT keep Node's event loop alive – Node 22 then exits with code
  // 13 ("unsettled top-level await") once no active handles remain, which
  // crash-loops the dev container. Hold an explicit ref'd timer handle so the
  // esbuild watchers keep running.
  setInterval(() => {}, 2 ** 30);
  await new Promise(() => {});
}

await main();
