// Central page-meta registry. Single source of truth for the page name (shown
// centered in the topbar) and its one-line description (revealed by the topbar
// info button). Pages no longer render their own title/description – the topbar
// resolves them from the current pathname here.
//
// Static routes map directly. Dynamic routes (a specific process, dashboard, or
// module) resolve their title from the loaded resource name, falling back to a
// generic label until the data lands. Keep this in sync with the routes under
// apps/web/app/(platform).

import type { ManifestArtifact, ManifestSource } from "@/lib/api-types";

/** Rich module "About" payload — manifest-sourced, rendered by the topbar ⓘ. */
export interface ModuleAbout {
  name: string;
  description?: string | null;
  about?: string | null;
  /** Cited works (max 20) — title + full citation + optional DOI link. */
  sources?: ManifestSource[] | null;
  /** Named links (max 20) — code repo, dataset, demo, released model. */
  artifacts?: ManifestArtifact[] | null;
  license?: string | null;
  version?: string | null;
}

export interface PageMeta {
  title: string;
  description?: string;
  /** When set, the topbar ⓘ shows the full module "About" card instead of the
   *  plain title + description (process → module view). */
  module?: ModuleAbout;
}

export interface ResolvePageCtx {
  logNames?: Map<string, string>;
  dashboardNames?: Map<string, string>;
  /** Module id → about payload, for both /modules/:id and …/processes/:id/modules/:id. */
  modules?: Map<string, ModuleAbout>;
}

// Anything that looks like a UUID (log / dashboard id). Module ids are slugs
// (e.g. "cv4cdd"), so they are matched positionally instead.
const UUID_RE = /^[0-9a-f-]{8,}$/i;

const STATIC: Record<string, PageMeta> = {
  "/processes": {
    title: "Processes",
    description: "Imported event logs. Drop a XES, XES.gz, or CSV here to start mining.",
  },
  "/processes/import": {
    title: "Import event log",
    description:
      "Upload a XES, CSV, XML, JSON, or OCEL file, check how its columns map, then import.",
  },
  "/processes/watched": {
    title: "Watched folders",
    description:
      "Storage locations scanned for new event-log files. New files are imported automatically.",
  },
  "/dashboards": {
    title: "Dashboards",
    description: "Compose cards from any module into a saved, reopenable board.",
  },
  "/modules": {
    title: "Modules",
    description: "Enable or disable modules, or open one to configure it.",
  },
  "/modules/import": {
    title: "Install a module",
    description:
      "Upload a module archive. The platform unpacks it, resolves its dependencies, and registers it without a restart.",
  },
  "/profile": {
    title: "Profile",
    description: "Your account, session, and sign-in details.",
  },
  "/settings/general": {
    title: "General",
    description: "Appearance, notifications, process proficiency, and import defaults.",
  },
  "/settings/privacy": {
    title: "Privacy",
    description: "Control product-usage analytics and what data is shared.",
  },
  "/settings/ai": {
    title: "AI",
    description: "Configure MATE AI providers, models, and how much data they may see.",
  },
  "/settings/api": {
    title: "API & MCP",
    description: "Personal access tokens and the read-only MCP endpoint.",
  },
  "/settings/about": {
    title: "About",
    description: "Version, license, and platform information.",
  },
  "/admin/overview": {
    title: "Overview",
    description: "Platform activity across every user.",
  },
  "/admin/logs": {
    title: "Event logs",
    description: "Every user's imported event logs.",
  },
  "/admin/jobs": {
    title: "Jobs",
    description: "Cross-user job monitor and control.",
  },
  "/admin/controls": {
    title: "Controls",
    description: "Per-card module setting locks and access policies.",
  },
  "/admin/modules": {
    title: "Modules",
    description: "Default modules and platform-wide install control.",
  },
  "/admin/teams": {
    title: "Teams",
    description: "Teams and dashboard-sharing membership.",
  },
  "/admin/system": {
    title: "System",
    description: "Live host CPU & memory and the job runtime.",
  },
  "/admin/export": {
    title: "Data export",
    description: "Export platform data for backup or migration.",
  },
};

function prettify(seg: string): string {
  if (UUID_RE.test(seg)) return seg;
  if (seg.toLowerCase() === "ai") return "AI";
  return seg.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function resolvePageMeta(pathname: string, ctx: ResolvePageCtx = {}): PageMeta {
  const clean = pathname.replace(/\/+$/, "") || "/";
  const staticMeta = STATIC[clean];
  if (staticMeta) return staticMeta;

  const parts = clean.split("/").filter(Boolean);

  // /processes/:logId[/…]
  if (parts[0] === "processes" && parts.length >= 2 && UUID_RE.test(parts[1])) {
    const logId = parts[1];
    if (parts[2] === "variants") {
      return {
        title: "Variant",
        description: "A single process variant — its cases, durations, and attribute breakdowns.",
      };
    }
    if (parts[2] === "activities") {
      return {
        title: "Activity",
        description:
          "One activity — its frequency, position in cases, and the variants that contain it.",
      };
    }
    if (parts[2] === "modules" && parts[3]) {
      const mod = ctx.modules?.get(parts[3]);
      return {
        title: mod?.name ?? "Module",
        description: mod?.description ?? "Module output and settings for this process.",
        module: mod,
      };
    }
    return {
      title: ctx.logNames?.get(logId) ?? "Process",
      description: "Overview, variants, and module output for this event log.",
    };
  }

  // /dashboards/:dashboardId
  if (parts[0] === "dashboards" && parts.length >= 2 && UUID_RE.test(parts[1])) {
    return {
      title: ctx.dashboardNames?.get(parts[1]) ?? "Dashboard",
      description: "A saved board of module cards bound to one event log.",
    };
  }

  // /modules/:moduleId (slug; /modules and /modules/import are static above)
  if (parts[0] === "modules" && parts.length >= 2) {
    const mod = ctx.modules?.get(parts[1]);
    return {
      title: mod?.name ?? "Module",
      description: mod?.description ?? "Module details, configuration, and per-user install.",
      module: mod,
    };
  }

  // Section roots that only redirect – give them a sane label just in case.
  if (parts[0] === "settings") return { title: "Settings" };
  if (parts[0] === "admin") return { title: "Admin" };

  return { title: parts.length ? prettify(parts[parts.length - 1]) : "Processes" };
}
