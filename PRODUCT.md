# Product

## Register

product

## Users

Process analysts, researchers, and students (university context, WWU-brokered login) working with event logs. They are technical, task-focused users running analysis workflows: import an event log, discover and inspect process models, compose dashboards from module widgets, monitor long-running jobs. Sessions are long and repeated; the UI is a daily tool, not a showcase.

## Product Purpose

Mate is a locally-hosted, modular process mining platform (FastAPI + Next.js, SQLite/DuckDB/Parquet, no cloud). Modules are the extension mechanism: discovery, performance, drift detection etc. all ship as sandboxed modules exposing jobs, events, and dashboard widgets. Success = analysts get from raw event log to trustworthy insight quickly, with every user's workspace fully isolated.

## Brand Personality

Precise, calm, engineering-grade. The tool disappears into the task. Interactions feel immediate and physical (direct manipulation on canvases, live job progress), never decorated. Reference feel: n8n / Linear-class snappiness — fast pointer-driven canvases, restrained neutral surfaces, motion only as state feedback.

## Anti-references

- Consumer SaaS marketing gloss: gradient heroes, glassmorphism-as-decoration, orchestrated page-load choreography.
- Dashboard-vendor maximalism (crowded chrome, badge soup, decorative icons on every label).
- Anything that trades responsiveness for polish: animations longer than ~200ms in task flows, blocking spinners where skeletons fit.

## Design Principles

1. **Direct manipulation first.** Drag, resize, and place with 1:1 pointer tracking; snapping and reflow are instant and predictable. No easing between the cursor and the thing it holds.
2. **Motion conveys state.** 150–250ms ease-out transitions for enter/exit/reflow; nothing decorative; `prefers-reduced-motion` always honored (CSS guard + framer-motion).
3. **Restrained neutral palette.** Near-monochrome OKLCH tokens; `--primary` reserved for actions, selection, and live state. Light/dark parity through shared token names.
4. **Density where users work.** Compact toolbars, 12px paddings, small type (xs/sm) in chrome; data areas get the space.
5. **Trust through feedback.** Every long operation streams progress; every mutation is optimistic with rollback; empty states teach the next action.

## Accessibility & Inclusion

WCAG AA target: 4.5:1 body-text contrast, visible focus rings (`--ring` tokens), full keyboard reachability for toolbar actions, reduced-motion alternatives for all animation. Canvas gestures (drag/resize) always have a non-pointer equivalent for the same outcome where feasible (settings dialogs, click-to-add).
