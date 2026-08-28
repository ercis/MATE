"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Boxes, Check, ChevronDown, Loader2 } from "lucide-react";

import { BorderBeam } from "@/components/glass/border-beam";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ColumnRoleQuality, LogProbeResponse } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import {
  isLowConfidence,
  OPTIONAL_ROLES,
  REQUIRED_ROLES,
  ROLE_LABELS,
  RoleCard,
} from "@/components/processes/import/role-card";

export interface MappingState {
  roles: Record<string, string>;
  quality: Record<string, ColumnRoleQuality>;
  delimiter: string;
  timestampFormat: string;
  eventElement: string;
  eventPath: string;
}

export function initialMapping(probe: LogProbeResponse): MappingState {
  return {
    roles: { ...probe.roles },
    quality: { ...probe.quality },
    delimiter: probe.delimiter ?? ",",
    timestampFormat: "",
    eventElement: probe.event_element ?? "",
    eventPath: probe.event_path ?? "",
  };
}

export function missingRequiredRoles(mapping: MappingState): string[] {
  return REQUIRED_ROLES.filter((role) => !mapping.roles[role]);
}

/** True when at least one mandatory role was guessed rather than matched. */
export function hasLowConfidence(mapping: MappingState): boolean {
  return REQUIRED_ROLES.some((role) => isLowConfidence(mapping.quality[role]));
}

/**
 * The deliberate step: what the importer *would* do, shown with real values, and
 * gated behind an explicit confirmation. Anything the user changes is recorded
 * as `quality: "user"`, which is what later suppresses the "review your column
 * mapping" warning on the log.
 */
export function MappingStep({
  probe,
  mapping,
  onChange,
  confirmed,
  onConfirm,
  aiPending,
  aiSuggested,
}: {
  probe: LogProbeResponse;
  mapping: MappingState;
  onChange: (next: MappingState) => void;
  confirmed: boolean;
  onConfirm: () => void;
  aiPending?: boolean;
  aiSuggested?: Set<string>;
}) {
  const lowConfidence = hasLowConfidence(mapping);
  const missing = missingRequiredRoles(mapping);
  const [showOptional, setShowOptional] = useState(
    () => OPTIONAL_ROLES.some((role) => Boolean(probe.roles[role])),
  );

  const setRole = (role: string) => (column: string | undefined) => {
    const roles = { ...mapping.roles };
    const quality = { ...mapping.quality };
    if (column) {
      // A column can only feed one role - drop it from whichever role held it.
      for (const [other, held] of Object.entries(roles)) {
        if (other !== role && held === column) {
          delete roles[other];
          delete quality[other];
        }
      }
      roles[role] = column;
      quality[role] = "user";
    } else {
      delete roles[role];
      delete quality[role];
    }
    // Any edit re-opens the gate: the user confirms what's on screen now.
    onChange({ ...mapping, roles, quality });
  };

  if (!probe.needs_mapping) {
    return (
      <ObjectCentricSummary probe={probe} confirmed={confirmed} onConfirm={onConfirm} />
    );
  }

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold">Check the column mapping</h2>
          <span className="text-xs text-muted-foreground">
            {missing.length === 0
              ? `${REQUIRED_ROLES.length} of ${REQUIRED_ROLES.length} required roles mapped`
              : `${REQUIRED_ROLES.length - missing.length} of ${REQUIRED_ROLES.length} required roles mapped`}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          Every analysis on this log reads these three columns. We sampled{" "}
          {formatNumber(probe.events_sampled)} events from{" "}
          <span className="font-medium">{probe.filename}</span> - confirm the values below look
          right.
        </p>
      </header>

      {lowConfidence && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            At least one required column had to be guessed. Getting this wrong silently distorts
            every metric downstream, so please check the sample values.
          </span>
        </div>
      )}

      {aiPending && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Asking AI for column suggestions…
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        {REQUIRED_ROLES.map((role, index) => (
          <RoleCard
            key={role}
            role={role}
            required
            index={index}
            value={mapping.roles[role]}
            quality={mapping.quality[role]}
            aiSuggested={aiSuggested?.has(role)}
            columns={probe.columns}
            onChange={setRole(role)}
          />
        ))}
      </div>

      <div className="space-y-3">
        <button
          type="button"
          onClick={() => setShowOptional((v) => !v)}
          className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
          aria-expanded={showOptional}
        >
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform", showOptional && "rotate-180")}
          />
          Optional roles and parser settings
        </button>

        {showOptional && (
          <div className="animate-in fade-in-0 slide-in-from-top-1 space-y-4 duration-200">
            <div className="grid gap-3 sm:grid-cols-3">
              {OPTIONAL_ROLES.map((role, index) => (
                <RoleCard
                  key={role}
                  role={role}
                  index={index}
                  value={mapping.roles[role]}
                  quality={mapping.quality[role]}
                  columns={probe.columns}
                  onChange={setRole(role)}
                />
              ))}
            </div>
            <ParserSettings probe={probe} mapping={mapping} onChange={onChange} />
          </div>
        )}
      </div>

      <ConfirmBar
        confirmed={confirmed}
        onConfirm={onConfirm}
        disabled={missing.length > 0}
        missing={missing}
      />
    </div>
  );
}

/** Format-specific knobs that a role mapping can't express. */
function ParserSettings({
  probe,
  mapping,
  onChange,
}: {
  probe: LogProbeResponse;
  mapping: MappingState;
  onChange: (next: MappingState) => void;
}) {
  const isCsv = probe.source_format === "csv";
  const isXml = probe.source_format === "xml" && Boolean(probe.event_element);
  const isJson = probe.source_format === "json";

  if (!isCsv && !isXml && !isJson) return null;

  return (
    <div className="grid gap-3 rounded-lg border border-border bg-surface p-3 sm:grid-cols-2">
      {isCsv && (
        <div className="grid gap-1.5">
          <Label className="text-xs">Delimiter</Label>
          <Select
            value={mapping.delimiter}
            onValueChange={(v) => onChange({ ...mapping, delimiter: v })}
          >
            <SelectTrigger className="cursor-pointer">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="," className="cursor-pointer">
                , (comma)
              </SelectItem>
              <SelectItem value=";" className="cursor-pointer">
                ; (semicolon)
              </SelectItem>
              <SelectItem value={"\t"} className="cursor-pointer">
                Tab
              </SelectItem>
              <SelectItem value="|" className="cursor-pointer">
                | (pipe)
              </SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            Detected from the file. Changing it needs a fresh upload to re-read the columns.
          </p>
        </div>
      )}
      {isXml && (
        <div className="grid gap-1.5">
          <Label className="text-xs">Event element</Label>
          <Input
            value={mapping.eventElement}
            onChange={(e) => onChange({ ...mapping, eventElement: e.target.value })}
            placeholder={probe.event_element ?? "event"}
          />
        </div>
      )}
      {isJson && (
        <div className="grid gap-1.5">
          <Label className="text-xs">Array key (optional)</Label>
          <Input
            value={mapping.eventPath}
            onChange={(e) => onChange({ ...mapping, eventPath: e.target.value })}
            placeholder={probe.event_path ?? "(top-level array)"}
          />
        </div>
      )}
      <div className="grid gap-1.5">
        <Label className="text-xs">Timestamp format (optional)</Label>
        <Input
          value={mapping.timestampFormat}
          onChange={(e) => onChange({ ...mapping, timestampFormat: e.target.value })}
          placeholder="e.g. %Y-%m-%d %H:%M:%S"
        />
      </div>
    </div>
  );
}

function ConfirmBar({
  confirmed,
  onConfirm,
  disabled,
  missing,
}: {
  confirmed: boolean;
  onConfirm: () => void;
  disabled: boolean;
  missing: string[];
}) {
  return (
    <div
      className={cn(
        "relative flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors",
        confirmed ? "border-chart-2/40 bg-chart-2/5" : "border-border bg-surface",
      )}
    >
      {/* Rides the border until the user has actually looked and confirmed. */}
      {!confirmed && !disabled && <BorderBeam duration={5} />}
      <div className="flex items-center gap-2 text-sm">
        {confirmed ? (
          <>
            <Check className="h-4 w-4 shrink-0 animate-in zoom-in-50 text-chart-2 duration-200" />
            <span className="text-muted-foreground">Mapping confirmed - ready to import.</span>
          </>
        ) : disabled ? (
          <span className="text-muted-foreground">
            Still missing: {missing.map((r) => ROLE_LABELS[r] ?? r).join(", ")}
          </span>
        ) : (
          <span className="text-muted-foreground">
            Confirm the mapping to unlock the import.
          </span>
        )}
      </div>
      {!confirmed && (
        <Button onClick={onConfirm} disabled={disabled} className="cursor-pointer gap-2">
          <Check className="h-4 w-4" />
          Confirm mapping
        </Button>
      )}
    </div>
  );
}

/** OCEL logs have no roles to map – the user acknowledges what was detected. */
function ObjectCentricSummary({
  probe,
  confirmed,
  onConfirm,
}: {
  probe: LogProbeResponse;
  confirmed: boolean;
  onConfirm: () => void;
}) {
  const size = useMemo(() => (probe.size_bytes / 1024 / 1024).toFixed(1), [probe.size_bytes]);

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h2 className="text-base font-semibold">Object-centric log detected</h2>
        <p className="text-sm text-muted-foreground">
          OCEL logs carry their own schema - object types, events, and relations are read straight
          from the file, so there are no columns to map.
        </p>
      </header>

      <div className="flex items-start gap-3 rounded-lg border border-border bg-surface p-3">
        <Boxes className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 text-sm">
          <div className="truncate font-medium">{probe.filename}</div>
          <div className="text-xs text-muted-foreground">
            {probe.source_format.toUpperCase()} · {size} MB
          </div>
        </div>
      </div>

      <ConfirmBar confirmed={confirmed} onConfirm={onConfirm} disabled={false} missing={[]} />
    </div>
  );
}
