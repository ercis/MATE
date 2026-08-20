# UI system

How the web app stays visually consistent: one set of page primitives, one
typography scale, one spacing convention. Read this before adding a page or a
new top-level view – the goal is that every screen has identical gutters,
heading sizes and rhythm, so nothing looks "slightly off" from page to page.

Stack: Next.js App Router + Tailwind v4 (tokens in [`app/globals.css`](app/globals.css))
+ shadcn "new-york" primitives in [`components/ui/`](components/ui/). The class
merge helper is [`@/lib/cn`](lib/cn.ts). shadcn config: [`components.json`](components.json).

## Page layout

Every page under `app/(platform)/` renders its content inside **`PageContainer`**
([`components/page.tsx`](components/page.tsx)). Never hand-roll an outer
`mx-auto max-w-… px-… py-…` wrapper – use the primitive so width and padding are
identical everywhere.

`PageContainer` is fluid full-width with low, responsive margins, capped only so
text/tables don't stretch on ultra-wide monitors. **One width everywhere** – data
pages, lists, grids, settings, profile and import wizards all share it, so no
screen looks narrower than another:

| Width            | Applies to                                                  |
| ---------------- | ----------------------------------------------------------- |
| `max-w-[1760px]` | every page – data, lists, grids, dashboards, settings, forms |

Padding is `px-4 sm:px-6 lg:px-8` + `py-6` (16px on mobile → 24px tablet → 32px
desktop; 24px top/bottom). The topbar uses the same horizontal gutter so the
breadcrumb lines up with the page heading. There is intentionally **no width
variant** – every page gets the same width and margin.

### Nested layouts

When a section has shared chrome that wraps several pages (a sub-tab nav), put a
single `PageContainer` in the section's `layout.tsx` and let the child pages
render **plain content** – do not nest a second `PageContainer` (double padding).
Examples: [`settings/layout.tsx`](app/(platform)/settings/layout.tsx) owns the
container for all `settings/*` pages. Conversely, `modules/layout.tsx` is a
pass-through because its children render their own container with distinct
loading/empty states (listing grid vs. detail vs. import), so each modules page
owns its own `PageContainer`.

### Full-bleed views

A few views are deliberately full-bleed app canvases, not padded documents (e.g.
the dashboard editor in [`dashboard-view.tsx`](components/dashboards/dashboard-view.tsx),
which is `flex h-full flex-col` with its own toolbar). These don't use
`PageContainer`, but their toolbar/skeleton still use the standard horizontal
gutter (`px-4 sm:px-6 lg:px-8`) so the left edge matches everything else.

## Header pattern

Use the page header primitives for the title block. Standard copy-paste:

```tsx
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
  PageActions,
} from "@/components/page";

export default function Example() {
  return (
    <PageContainer>
      <PageHeader>
        <div className="space-y-1">
          <PageTitle>Processes</PageTitle>
          <PageDescription>Short one-line subtitle.</PageDescription>
        </div>
        <PageActions>
          <Button>Primary action</Button>
        </PageActions>
      </PageHeader>

      {/* page content */}
    </PageContainer>
  );
}
```

- `PageHeader` is `flex flex-wrap items-start justify-between gap-4 pb-6` – actions
  sit on the right and wrap below the title on narrow screens.
- Omit `PageActions` if there are no actions.
- `PageTitle` renders an `<h1>` – exactly one per page.

## Typography scale

Canonical sizes – don't invent new heading sizes inline. (`PageTitle` /
`PageDescription` already encode the first two.)

| Role               | Classes                                       |
| ------------------ | --------------------------------------------- |
| Page title (`h1`)  | `text-2xl font-semibold tracking-tight`       |
| Section heading    | `text-lg font-semibold tracking-tight`        |
| Card title         | `text-base font-bold` (see [`card.tsx`](components/ui/card.tsx)) |
| Body / description | `text-sm text-muted-foreground`               |
| Labels / meta      | `text-xs`                                      |

## Spacing

- Page padding: handled by `PageContainer` (`px-4 sm:px-6 lg:px-8 py-6`).
- Header bottom gap: `pb-6` (built into `PageHeader`).
- Vertical rhythm between sections: `space-y-4` (dense) or `space-y-6` (looser).
- Inline gaps: `gap-2` (buttons/badges) or `gap-3` (looser clusters).
- Cards space their sections via the card's own `gap`/`px-6` – don't add extra
  padding inside `CardContent` (see the note at the top of `card.tsx`).

## Color & radius tokens

Use the semantic tokens defined in [`app/globals.css`](app/globals.css) – never
raw hex/Tailwind palette colors. Common ones: `bg-background`, `text-foreground`,
`text-muted-foreground`, `bg-card`, `border-border`, `bg-primary` /
`text-primary-foreground`, `bg-destructive`. Dark mode is automatic via the
`.dark` class (every token has a dark value), so token-based styling needs no
`dark:` overrides. Corner radius derives from `--radius` (`rounded-md`,
`rounded-lg`, etc.).

## Responsiveness

- Mobile-first: base classes target small screens, add `sm:` / `lg:` upward.
- Horizontal scaling is the container's job – pages rarely need their own
  breakpoint padding.
- Headers use `flex-wrap` so action buttons wrap instead of clipping.
- Grids: start at one column and step up, e.g. `grid gap-4 sm:grid-cols-2 lg:grid-cols-3`.

## Components

Prefer existing primitives in [`components/ui/`](components/ui/) (Button, Card,
Dialog, Table, Tabs, Badge, Input, …) over bespoke markup. Icons come from
`lucide-react`. Add new shadcn primitives via the shadcn CLI so they inherit the
`new-york` style and token wiring.
