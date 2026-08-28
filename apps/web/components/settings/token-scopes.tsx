"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { McpScopeInfo } from "@/lib/api-types";

/** Human labels for the scope areas (the prefix before ":"). Unknown areas fall
 * back to the raw prefix so future backend scopes still render. */
const AREA_LABELS: Record<string, string> = {
  processes: "Processes",
  modules: "Modules",
  dashboards: "Dashboards",
  jobs: "Jobs",
  watched: "Watched folders",
  account: "Account",
};

const AREA_ORDER = ["processes", "modules", "dashboards", "jobs", "watched", "account"];

interface ScopeGroup {
  area: string;
  label: string;
  scopes: McpScopeInfo[];
}

function groupScopes(supported: McpScopeInfo[]): ScopeGroup[] {
  const byArea = new Map<string, McpScopeInfo[]>();
  for (const s of supported) {
    const area = s.id.split(":")[0] ?? s.id;
    const list = byArea.get(area);
    if (list) list.push(s);
    else byArea.set(area, [s]);
  }
  const known = AREA_ORDER.filter((a) => byArea.has(a));
  const unknown = [...byArea.keys()].filter((a) => !AREA_ORDER.includes(a));
  return [...known, ...unknown].map((area) => ({
    area,
    label: AREA_LABELS[area] ?? area,
    scopes: byArea.get(area) ?? [],
  }));
}

/** Grouped checkbox picker for PAT scopes, with the two presets.
 *
 * Empty selection is meaningful: the backend treats a token with no scopes as
 * "all read scopes" – that's the "Read-only (default)" preset.
 */
export function TokenScopePicker({
  supported,
  selected,
  disabled,
  onChange,
}: {
  supported: McpScopeInfo[];
  selected: Set<string>;
  disabled?: boolean;
  onChange: (next: Set<string>) => void;
}) {
  if (supported.length === 0) return null;

  const groups = groupScopes(supported);
  const allSelected = supported.length > 0 && supported.every((s) => selected.has(s.id));
  const noneSelected = selected.size === 0;

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Permissions</span>
        <div className="flex gap-1.5">
          <Button
            type="button"
            variant={noneSelected ? "secondary" : "outline"}
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={disabled}
            onClick={() => onChange(new Set())}
          >
            Read-only (default)
          </Button>
          <Button
            type="button"
            variant={allSelected ? "secondary" : "outline"}
            size="sm"
            className="h-7 px-2 text-xs"
            disabled={disabled}
            onClick={() => onChange(new Set(supported.map((s) => s.id)))}
          >
            Full access
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        With nothing selected the token gets every read scope and no write access — the safe
        default. Check scopes to grant exactly those instead (write scopes included only if you
        check them).
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {groups.map((g) => (
          <div key={g.area} className="space-y-1.5 rounded-md border border-border p-2.5">
            <p className="text-xs font-medium">{g.label}</p>
            {g.scopes.map((s) => (
              <label key={s.id} className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-primary"
                  checked={selected.has(s.id)}
                  disabled={disabled}
                  onChange={() => toggle(s.id)}
                />
                <span className="min-w-0">
                  <code>{s.id}</code>
                  {s.description && (
                    <span className="block text-muted-foreground">{s.description}</span>
                  )}
                </span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Compact badge strip for a token row: first `max` scopes + a "+N" overflow
 * badge (full list in its title). An empty grant renders as "all read". */
export function ScopeBadges({ scopes, max = 4 }: { scopes: string[]; max?: number }) {
  if (scopes.length === 0) {
    return (
      <Badge variant="outline" className="px-1.5 text-[10px] font-normal text-muted-foreground">
        all read
      </Badge>
    );
  }
  const shown = scopes.slice(0, max);
  const rest = scopes.length - shown.length;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((s) => (
        <Badge key={s} variant="secondary" className="px-1.5 font-mono text-[10px] font-normal">
          {s}
        </Badge>
      ))}
      {rest > 0 && (
        <Badge
          variant="outline"
          className="px-1.5 text-[10px] font-normal"
          title={scopes.slice(max).join(", ")}
        >
          +{rest}
        </Badge>
      )}
    </div>
  );
}
