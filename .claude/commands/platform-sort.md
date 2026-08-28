---
description: Audit the Notion product-backlog Kanban against the actual codebase, merge overlapping items, verify what's already fixed/built, and publish a structured "Platform sorted" plan page.
argument-hint: [notion-page-name-or-url]
---

# Platform backlog sort

Audit a Notion Kanban backlog of ideas/bugs/feature requests against this repo's real state, then publish a cleaned-up, deduplicated, prioritized plan as a new Notion page called **"Platform sorted"**.

Source page/board: `$ARGUMENTS` (a Notion page name or URL). If empty, search the workspace for the backlog board (try "Kanban" / "Platform" / "Backlog") and confirm the match before proceeding — if zero or multiple plausible candidates come back, ask the user which one instead of guessing.

## Constraints

- Read-only against the source board: never edit, move, archive, or delete the original cards.
- Output is additive: one new Notion page. Don't touch other existing pages.
- If a page titled "Platform sorted" already exists in the target location, stop and ask the user whether to replace it, version it (append today's date), or append to it — don't silently duplicate.
- Traceability: every merged item in the output must list which original card(s) it came from.

## Ask, don't guess

Default to `AskUserQuestion` whenever a decision would otherwise be a silent guess. This is not optional politeness — a wrong guess here means either a missing merge (duplicate work stays scheduled twice) or a wrong verdict published as fact. Concretely, stop and ask when:

- The source board can't be resolved (zero or multiple plausible matches) — Phase 0.
- A card's description is missing, empty, or too thin to tell what it's actually asking for — don't invent intent for it.
- Two clusters *might* overlap but the wording is genuinely ambiguous either way — don't force a merge or a split on a coin flip.
- Codebase verification is inconclusive (conflicting evidence, feature partially matches multiple cards, can't tell if a fix landed) — this is the `unclear` bucket from Phase 3; surface the specific ambiguity in the question, don't just default to `not-started`.
- A card's premise looks wrong but you're not fully certain (vs. clearly contradicted by code) — ask before publishing an `invalid-premise` verdict you're not sure of.
- The destination location for "Platform sorted" is ambiguous (no clear parent, or the source page itself lives in more than one place).
- Anything else structurally required to proceed is missing (board has no status/column property, cards have no stable IDs to link back to, etc.).

Batch questions where possible (the tool supports up to 4 per call) instead of interrupting once per card. Don't ask about things you can resolve yourself by reading the code, git log, or `CLAUDE.md` — only escalate what genuinely requires the user's judgment.

## Phase 0 — load tools

Notion MCP tools are deferred. Before anything else, load them in one call:

`ToolSearch({query: "select:mcp__claude_ai_Notion__notion-search,mcp__claude_ai_Notion__notion-fetch,mcp__claude_ai_Notion__notion-query-database-view,mcp__claude_ai_Notion__notion-query-data-sources,mcp__claude_ai_Notion__notion-create-pages,mcp__claude_ai_Notion__notion-update-page,mcp__claude_ai_Notion__notion-get-comments"})`

## Phase 1 — extract the board

Locate the source page/database, then pull every card: title, column/status, tags/labels, and full description body (card properties alone are usually not enough — fetch the card's page content for the real description).

If the board is large (say 15+ cards), delegate this extraction to a `general-purpose` Agent so raw card text doesn't bloat the main conversation — have it return a compact list of `{id, title, column, tags, one-paragraph summary, url}` per card, not the raw Notion blocks.

## Phase 2 — cluster and dedupe

Working from the compact card list, group cards into clusters by actual overlap, not string matching — two cards can describe the same underlying problem in totally different words. This step needs the full list in view at once (a dedupe pass can't be parallelized across cards), so do it directly, not via sub-agents.

Default categories: reuse this repo's own subsystem breakdown from `CLAUDE.md` (module system, event bus, job runtime, auth/tenant isolation, ingest, web app, dashboards, MCP server, storage backend, sharing, admin) — add a new category only when a cluster genuinely doesn't fit any of these. Within a cluster, keep the list of original card titles/URLs that were merged.

## Phase 3 — verify against the codebase

For each cluster, determine its real status by checking code, tests, git log, and this project's memory files — not by trusting the card's own claim (cards can be stale, already-shipped, or simply wrong about how the system works).

Target verdict per cluster:
- `status`: one of `done` / `partial` / `not-started` / `invalid-premise` / `unclear`
- `evidence`: concrete `file:line` or commit refs backing the verdict
- `recommendation`: what to actually do next (skip this for `done`)
- `priority`: high / medium / low
- `effort`: S / M / L

Scale the verification mechanism to the backlog size:
- **≤8 clusters** — verify inline: fire one `Agent` (subagent_type `Explore` for pure lookups, `general-purpose` if it needs to reason about correctness) per cluster, all in a single message so they run in parallel. Tell each agent explicitly this is read-only investigation, not a fix.
- **>8 clusters** — use the `Workflow` tool: a `pipeline` over clusters with a verify stage (schema-validated output matching the fields above), since free-text agent replies get unreliable to assemble at that scale.

Mark anything low-confidence or contradictory as `unclear` and collect it into a "needs human review" bucket rather than guessing.

## Phase 4 — synthesize the plan

Build the page content:
1. Summary paragraph: total source cards → number of clusters, breakdown by status.
2. One `H2` section per category. Within it, clusters ordered `not-started`/`partial` (high priority first) → `unclear` → `invalid-premise` → `done` last.
3. Each cluster block: title, status badge, merged source cards (with links), evidence, recommendation, priority/effort.
4. Closing "Needs human review" section for every `unclear` or `invalid-premise` item, since those are exactly the ones a script shouldn't resolve on its own.

## Phase 5 — publish

Create the new page ("Platform sorted") as a sibling of the source page (same parent); if the source has no accessible parent, create at workspace root. Use `notion-create-pages`.

## Phase 6 — report

Reply with the page link, the summary counts, and call out anything flagged `invalid-premise` or high-priority `not-started` — those are the surprising/actionable parts, not the routine ones.
