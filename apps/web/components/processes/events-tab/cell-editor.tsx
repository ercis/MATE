"use client";

import { useEffect, useRef, useState } from "react";
import { toastError } from "@/lib/toast";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ColumnSpec } from "@/lib/api-types";
import { ApiError } from "@/lib/api";
import { usePatchEventRow } from "@/lib/queries";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";

export interface CellEditorProps {
  logId: string;
  rowIndex: number;
  column: ColumnSpec;
  value: unknown;
  editMode: boolean;
  /** Optional rename overlay applied to the displayed cell value. Editing
   * still operates on the raw underlying value – the override is purely
   * cosmetic (used by the activity rename overlay).
   */
  displayOverride?: string;
}

export function CellEditor({
  logId,
  rowIndex,
  column,
  value,
  editMode,
  displayOverride,
}: CellEditorProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(toInputValue(value, column));
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const patch = usePatchEventRow(logId);

  // When the underlying value changes (re-fetch / page change), reset the draft
  // so we never display stale text.
  useEffect(() => {
    setDraft(toInputValue(value, column));
    setErrorMsg(null);
  }, [value, column]);

  const baseDisplay = formatCell(value, column);
  const renamed = displayOverride && displayOverride.trim().length > 0;
  const display = renamed ? displayOverride! : baseDisplay;
  const empty = value === null || value === undefined || value === "";

  if (!editing) {
    return (
      <button
        type="button"
        disabled={!editMode}
        onClick={() => setEditing(true)}
        className={cn(
          "block w-full text-left text-sm tabular-nums",
          editMode && "cursor-pointer rounded px-1 py-0.5 hover:bg-muted",
          !editMode && "cursor-default",
          empty && "italic text-muted-foreground",
        )}
        title={
          renamed
            ? `Renamed from "${baseDisplay}"`
            : editMode
              ? "Click to edit"
              : undefined
        }
      >
        {empty ? "–" : display}
      </button>
    );
  }

  const save = async (next: string) => {
    setErrorMsg(null);
    try {
      const parsed = parseDraft(next, column);
      await patch.mutateAsync({
        rowIndex,
        patch: { field: column.name, value: parsed },
      });
      setEditing(false);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? typeof err.detail === "object" && err.detail !== null && "detail" in err.detail
            ? String((err.detail as { detail: unknown }).detail)
            : err.message
          : (err as Error).message;
      setErrorMsg(message);
      toastError(message);
    }
  };

  const cancel = () => {
    setDraft(toInputValue(value, column));
    setEditing(false);
    setErrorMsg(null);
  };

  if (column.type === "enum" && column.enum_values && column.enum_values.length > 0) {
    return (
      <Select
        defaultOpen
        value={draft}
        onValueChange={(v) => {
          setDraft(v);
          void save(v);
        }}
      >
        <SelectTrigger className="h-7 text-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {column.enum_values.map((v) => (
            <SelectItem key={v} value={v}>
              {v}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  return (
    <CellInput
      column={column}
      draft={draft}
      onChange={setDraft}
      onCommit={() => void save(draft)}
      onCancel={cancel}
      pending={patch.isPending}
      error={errorMsg}
    />
  );
}

function CellInput({
  column,
  draft,
  onChange,
  onCommit,
  onCancel,
  pending,
  error,
}: {
  column: ColumnSpec;
  draft: string;
  onChange: (next: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const inputType =
    column.type === "datetime"
      ? "datetime-local"
      : column.type === "number" || column.type === "duration"
        ? "number"
        : "text";

  return (
    <div className="flex items-center gap-1">
      <Input
        ref={ref}
        type={inputType}
        step={column.type === "duration" ? "1" : column.type === "number" ? "any" : undefined}
        value={draft}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onCommit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        className={cn(
          "h-7 text-sm",
          error && "border-destructive ring-1 ring-destructive",
          pending && "opacity-60",
        )}
        list={
          column.enum_values && column.enum_values.length > 0
            ? `enum-${column.name}`
            : undefined
        }
      />
      {column.enum_values && column.enum_values.length > 0 && (
        <datalist id={`enum-${column.name}`}>
          {column.enum_values.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      )}
    </div>
  );
}

function toInputValue(value: unknown, column: ColumnSpec): string {
  if (value === null || value === undefined) return "";
  if (column.type === "datetime") {
    const d = new Date(String(value));
    if (Number.isNaN(d.getTime())) return String(value);
    // datetime-local expects YYYY-MM-DDTHH:MM:SS – strip tz.
    const pad = (n: number) => String(n).padStart(2, "0");
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    );
  }
  return String(value);
}

function parseDraft(draft: string, column: ColumnSpec): unknown {
  if (draft === "" || draft === undefined) {
    if (column.required) {
      throw new Error(`${column.label} cannot be empty.`);
    }
    return null;
  }
  if (column.type === "number" || column.type === "duration") {
    const n = Number(draft);
    if (!Number.isFinite(n)) throw new Error(`${column.label} expects a number.`);
    return n;
  }
  if (column.type === "boolean") {
    return draft === "true" || draft === "1";
  }
  if (column.type === "datetime") {
    // datetime-local already gives us a parseable string.
    return draft;
  }
  return draft;
}

function formatCell(value: unknown, column: ColumnSpec): string {
  if (value === null || value === undefined) return "–";
  if (column.type === "datetime") {
    const d = new Date(String(value));
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString();
  }
  if (column.type === "duration" && typeof value === "number") {
    return formatDuration(value);
  }
  return String(value);
}
