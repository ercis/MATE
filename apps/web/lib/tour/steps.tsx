import type { ReactNode } from "react";

/**
 * One step of the interactive product tour. `route` is the exact pathname the
 * overlay drives to before resolving `selector`; omit `selector` for a centered,
 * target-less step (welcome / done). The overlay polls for the selector after
 * navigating, so a step can point at an element that only mounts on its route.
 */
export interface TourStep {
  id: string;
  /** Pathname to navigate to before resolving the target (omit = stay put). */
  route?: string;
  /** CSS selector to spotlight; omit for a centered, target-less step. */
  selector?: string;
  title: string;
  body: ReactNode;
  placement?: "top" | "bottom" | "left" | "right";
}

/**
 * Build the step list. Always **seven** steps, whether or not the user has a log
 * yet: a data-dependent length made the counter grow from "1 of 4" to "1 of 7"
 * under the user when `useEventLogs()` resolved mid-tour. Without a log, the
 * three log-bound steps simply drop their `route`/`selector` and render as
 * centered cards that describe what will be there.
 *
 * The tour is deliberately HIGH-LEVEL: it walks the platform's shape and the
 * core loop - import → open a process → read it → run modules - and never names
 * or enters an individual module. A module's own panel teaches that.
 *
 * It also does not re-teach the import form: the setup wizard owns the hands-on
 * upload (it embeds the very same `ImportForm`) and the wizard's closing screen
 * owns "what modules are and when they compute". The tour only shows *where*
 * things live, so the two first-run experiences complement instead of repeat.
 *
 * `auto` = launched straight off the wizard's Finish: the opener acknowledges
 * setup instead of greeting the user a second time.
 */
export function buildTourSteps(logId: string | null, opts?: { auto?: boolean }): TourStep[] {
  const detailRoute = logId ? `/processes/${logId}` : undefined;

  return [
    opts?.auto
      ? {
          id: "welcome",
          route: "/processes",
          title: "Setup done — here's the map",
          body: "Ninety seconds in the real UI: where things live and how a log becomes an analysis. Skip anytime; replay later from Settings → About.",
        }
      : {
          id: "welcome",
          route: "/processes",
          title: "The shape of the platform",
          body: "Ninety seconds on the essentials: where your data lives, where analysis happens, and how the two connect.",
        },
    {
      id: "nav",
      route: "/processes",
      selector: '[data-tour="nav-processes"]',
      placement: "right",
      title: "The left rail is the whole app",
      body: "Processes (your event logs), Modules (the analyses), Dashboards, and MATE AI. Processes is home base — every analysis starts from a log.",
    },
    {
      id: "import-log",
      route: "/processes",
      selector: '[data-tour="import-log"]',
      placement: "bottom",
      title: logId ? "More data comes in here" : "New data comes in here",
      body: logId
        ? "Same button for the next one: a file, a URL, or a watched folder that picks up new logs on its own."
        : "A file, a URL, or a watched folder that picks up new logs on its own.",
    },
    {
      id: "open-process",
      route: "/processes",
      selector: logId ? `[data-log-id="${logId}"]` : undefined,
      placement: "bottom",
      title: logId ? "Your processes live in this list" : "Your processes will land here",
      body: logId
        ? "Open one to analyse it. A row stays dimmed while it imports and while its modules precompute — the caption under the name tells you which, and how far along."
        : "Every imported log becomes a row. It stays dimmed until the import and its module precompute finish, then it opens.",
    },
    {
      id: "log-stats",
      route: detailRoute,
      selector: detailRoute ? '[data-tour="log-stats"]' : undefined,
      placement: "bottom",
      title: "Every process opens on its shape",
      body: "Cases, events, variants and the time span the log covers — the sanity check before you read anything into an analysis.",
    },
    {
      id: "module-grid",
      route: detailRoute,
      selector: detailRoute ? '[data-tour="module-grid"]' : undefined,
      placement: "top",
      title: "Modules turn it into analysis",
      body: "Every card below is one analysis of this log, precomputed the moment it was imported. Open whichever matches the question you have.",
    },
    {
      id: "done",
      title: "That's the whole loop",
      body: "Import a log → open it → pick a module. Everything else is a variation on that. Replay this tour anytime from Settings → About.",
    },
  ];
}
