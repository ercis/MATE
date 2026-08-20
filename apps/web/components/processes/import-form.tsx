"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useProgressRouter } from "@/lib/use-progress-router";
import {
  CheckCircle2,
  FileText,
  FileUp,
  FolderOpen,
  Link2,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useCreateFolder,
  useImportEventLog,
  useImportEventLogFromUrl,
  useProbeJson,
  useProbeXml,
  type JsonProbeResponse,
  type XmlProbeResponse,
} from "@/lib/queries";
import { useCreateWatchedFolder } from "@/lib/watched-queries";
import type { WatchMode } from "@/lib/api-types";
import { useAiConfig } from "@/lib/ai-queries";
import { useImportColumnMapping } from "@/lib/ai-guidance";
import { useUi } from "@/lib/stores/ui";
import { useTrack } from "@/lib/analytics/hooks";
import { EV } from "@/lib/analytics/events";
import { cn } from "@/lib/cn";

type DetectedFormat = "xes" | "xes.gz" | "csv" | "xml" | "json" | "ocel" | "unsupported";

function detect(file: File): DetectedFormat {
  const n = file.name.toLowerCase();
  if (n.endsWith(".xes.gz")) return "xes.gz";
  if (n.endsWith(".xes")) return "xes";
  if (n.endsWith(".csv")) return "csv";
  if (n.endsWith(".jsonocel") || n.endsWith(".xmlocel") || n.endsWith(".sqlite")) return "ocel";
  // Plain .xml / .json are ambiguous (case-centric vs OCEL); the server sniffs
  // the content and auto-routes. We probe them client-side only to decide
  // whether to show the mapping wizard.
  if (n.endsWith(".xml")) return "xml";
  if (n.endsWith(".json")) return "json";
  return "unsupported";
}

async function readFirstLine(file: File): Promise<string> {
  // Read up to 4KB – far more than we need for headers, less than what would
  // hurt to slurp synchronously into memory.
  const blob = file.slice(0, 4096);
  const text = await blob.text();
  return text.split(/\r?\n/, 1)[0] ?? "";
}

async function readSampleLines(file: File, lineCount: number): Promise<string[]> {
  // Read a 32 KB prefix so we get enough rows even on wide schemas.
  const blob = file.slice(0, 32 * 1024);
  const text = await blob.text();
  return text.split(/\r?\n/).filter((l) => l.length > 0).slice(0, lineCount);
}

function parseCsvHeader(line: string, delimiter: string): string[] {
  // Minimal split – quoted commas in headers are vanishingly rare; the
  // backend revalidates on import and the wizard is otherwise advisory.
  return line.split(delimiter).map((c) => c.replace(/^"(.*)"$/, "$1").trim());
}

interface CsvMapping {
  case_id: string;
  activity: string;
  timestamp: string;
  end_timestamp?: string;
  resource?: string;
  cost?: string;
  delimiter: string;
  timestamp_format?: string;
}

interface XmlMapping {
  event_element: string;
  case_id: string;
  activity: string;
  timestamp: string;
  end_timestamp?: string;
  resource?: string;
  cost?: string;
  timestamp_format?: string;
}

type XmlMappingFieldKey =
  | "case_id"
  | "activity"
  | "timestamp"
  | "end_timestamp"
  | "resource"
  | "cost";

interface JsonMapping {
  event_path?: string;
  case_id: string;
  activity: string;
  timestamp: string;
  end_timestamp?: string;
  resource?: string;
  cost?: string;
  timestamp_format?: string;
}

type JsonMappingFieldKey = XmlMappingFieldKey;

// Each canonical field has an ordered list of candidate names. The first
// candidate is also the canonical key itself, so a header literally named
// "case_id", "Case ID", "case-id", "CASE_ID", or "caseId" all auto-map.
const COMMON_GUESSES: Record<keyof CsvMapping, string[]> = {
  case_id: ["case_id", "case", "case concept name", "trace_id", "id"],
  activity: ["activity", "task", "concept name", "event"],
  timestamp: ["timestamp", "time", "datetime", "date", "time timestamp", "start_timestamp", "start"],
  end_timestamp: ["end_timestamp", "complete_timestamp", "time complete", "completion", "end"],
  resource: ["resource", "user", "agent", "org resource", "performer"],
  cost: ["cost", "amount", "cost total", "price"],
  delimiter: [],
  timestamp_format: [],
};

const CANONICAL_FIELDS = [
  "case_id",
  "activity",
  "timestamp",
  "end_timestamp",
  "resource",
  "cost",
] as const;

/** Normalise an identifier for fuzzy comparison: lowercase + strip every
 * character that isn't a letter or digit. So "Case ID", "case-id",
 * "Case:Concept:Name", and "caseConceptName" all collapse to a comparable form.
 */
function normaliseIdent(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function autoMap(headers: string[]): Partial<CsvMapping> {
  const normalisedHeaders = headers.map((h) => ({ raw: h, norm: normaliseIdent(h) }));
  const claimed = new Set<string>();
  const out: Partial<CsvMapping> = {};

  const findFor = (
    key: (typeof CANONICAL_FIELDS)[number],
    predicate: (headerNorm: string, candNorm: string) => boolean,
  ): string | null => {
    for (const cand of COMMON_GUESSES[key]) {
      const candNorm = normaliseIdent(cand);
      if (!candNorm) continue;
      for (const h of normalisedHeaders) {
        if (claimed.has(h.raw)) continue;
        if (predicate(h.norm, candNorm)) return h.raw;
      }
    }
    return null;
  };

  // Pass 1 – exact normalised match. Strongest signal: the user wrote
  // "Case ID" or "case-id" intending the canonical case_id column.
  for (const key of CANONICAL_FIELDS) {
    const found = findFor(key, (h, c) => h === c);
    if (found) {
      out[key] = found;
      claimed.add(found);
    }
  }

  // Pass 2 – substring containment for whatever's still unclaimed. So
  // "registered_case_id" still resolves to case_id, but only if no exact
  // match was found for any other field first.
  for (const key of CANONICAL_FIELDS) {
    if (out[key]) continue;
    const found = findFor(key, (h, c) => h.includes(c) || c.includes(h));
    if (found) {
      out[key] = found;
      claimed.add(found);
    }
  }

  return out;
}

interface ImportFormProps {
  onSuccess?: (logId: string) => void;
}

export function ImportForm({ onSuccess }: ImportFormProps = {}) {
  return (
    <Tabs defaultValue="file" className="space-y-6">
      <TabsList>
        <TabsTrigger value="file" className="cursor-pointer">
          <FileUp className="mr-1.5 h-3.5 w-3.5" />
          Upload file
        </TabsTrigger>
        <TabsTrigger value="url" className="cursor-pointer">
          <Link2 className="mr-1.5 h-3.5 w-3.5" />
          From URL
        </TabsTrigger>
        <TabsTrigger value="folder" className="cursor-pointer">
          <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
          Upload folder
        </TabsTrigger>
        <TabsTrigger value="watch" className="cursor-pointer">
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Watched folder
        </TabsTrigger>
      </TabsList>

      <TabsContent value="file">
        <FileImportForm onSuccess={onSuccess} />
      </TabsContent>
      <TabsContent value="url">
        <UrlImportForm onSuccess={onSuccess} />
      </TabsContent>
      <TabsContent value="folder">
        <FolderImportForm onSuccess={onSuccess} />
      </TabsContent>
      <TabsContent value="watch">
        <WatchImportForm />
      </TabsContent>
    </Tabs>
  );
}

// ── File upload form ──────────────────────────────────────────────────────────

function FileImportForm({ onSuccess }: ImportFormProps) {
  const router = useProgressRouter();
  const importer = useImportEventLog();
  const probeXml = useProbeXml();
  const probeJson = useProbeJson();
  const { data: aiConfig } = useAiConfig();
  const aiMapping = useImportColumnMapping();
  const track = useTrack();

  const defaultDelimiter = useUi((s) => s.csvDelimiter);
  const defaultTsFormat = useUi((s) => s.csvTimestampFormat);

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [headers, setHeaders] = useState<string[]>([]);
  const [delimiter, setDelimiter] = useState<string>(defaultDelimiter);
  const [mapping, setMapping] = useState<Partial<CsvMapping>>({});
  const [aiSuggested, setAiSuggested] = useState<Set<keyof CsvMapping>>(new Set());
  const [tsFormat, setTsFormat] = useState<string>(defaultTsFormat);

  // XML wizard state. `xmlProbe` is null until the file has been inspected by
  // the backend; xmlMapping is what we'll actually send on submit.
  const [xmlProbe, setXmlProbe] = useState<XmlProbeResponse | null>(null);
  const [xmlMapping, setXmlMapping] = useState<Partial<XmlMapping>>({});
  const [xmlError, setXmlError] = useState<string | null>(null);

  // JSON wizard state – same shape as XML. `jsonProbe.format_hint === "ocel"`
  // means the server will auto-route it object-centric, so no mapping is shown.
  const [jsonProbe, setJsonProbe] = useState<JsonProbeResponse | null>(null);
  const [jsonMapping, setJsonMapping] = useState<Partial<JsonMapping>>({});
  const [jsonError, setJsonError] = useState<string | null>(null);

  const fmt = file ? detect(file) : null;
  const aiConfigured = Boolean(aiConfig?.selected_provider && aiConfig?.selected_model);

  const onDrop = useCallback(
    async (f: File) => {
      const detected = detect(f);
      if (detected === "unsupported") {
        toastError(
          `Unsupported file: ${f.name}. Use .xes, .xes.gz, .csv, .xml, .json, or OCEL ` +
            `(.jsonocel/.xmlocel/.sqlite).`,
        );
        return;
      }
      setFile(f);
      setName((current) =>
        current || f.name.replace(/\.(xes\.gz|xes|csv|xml|json|jsonocel|xmlocel|sqlite)$/i, ""),
      );
      setAiSuggested(new Set());
      setXmlProbe(null);
      setXmlMapping({});
      setXmlError(null);
      setJsonProbe(null);
      setJsonMapping({});
      setJsonError(null);
      if (detected === "json") {
        try {
          const probe = await probeJson.mutateAsync(f);
          setJsonProbe(probe);
          const auto = probe.auto_mapping;
          if (auto) {
            setJsonMapping({
              event_path: auto.event_path ?? undefined,
              case_id: auto.case_id,
              activity: auto.activity,
              timestamp: auto.timestamp,
              end_timestamp: auto.end_timestamp ?? undefined,
              resource: auto.resource ?? undefined,
              cost: auto.cost ?? undefined,
              timestamp_format: auto.timestamp_format ?? undefined,
            });
          } else if (probe.event_path) {
            setJsonMapping({ event_path: probe.event_path });
          }
        } catch (err: unknown) {
          setJsonError((err as Error).message || "Failed to inspect JSON");
        }
        setHeaders([]);
        setMapping({});
        return;
      }
      if (detected === "xml") {
        try {
          const probe = await probeXml.mutateAsync(f);
          setXmlProbe(probe);
          const auto = probe.auto_mapping;
          if (auto) {
            setXmlMapping({
              event_element: auto.event_element,
              case_id: auto.case_id,
              activity: auto.activity,
              timestamp: auto.timestamp,
              end_timestamp: auto.end_timestamp ?? undefined,
              resource: auto.resource ?? undefined,
              cost: auto.cost ?? undefined,
              timestamp_format: auto.timestamp_format ?? undefined,
            });
          } else if (probe.event_element) {
            setXmlMapping({ event_element: probe.event_element });
          }
        } catch (err: unknown) {
          setXmlError((err as Error).message || "Failed to inspect XML");
        }
        setHeaders([]);
        setMapping({});
        return;
      }
      if (detected === "csv") {
        const sample = await readSampleLines(f, 11);
        const headerLine = sample[0] ?? "";
        const cols = parseCsvHeader(headerLine, delimiter);
        setHeaders(cols);
        const base = autoMap(cols);
        setMapping(base);

        // Best-effort AI fill for fields autoMap left blank. Silently no-ops
        // if the user hasn't configured AI; surfaces nothing if the call
        // fails – autoMap's coverage is fine on its own.
        if (aiConfigured && cols.length > 0) {
          const sampleRows = sample
            .slice(1, 11)
            .map((line) => parseCsvHeader(line, delimiter));
          try {
            const res = await aiMapping.mutateAsync({
              headers: cols,
              sample_rows: sampleRows,
            });
            const filled: Partial<CsvMapping> = { ...base };
            const newlySuggested = new Set<keyof CsvMapping>();
            for (const [key, header] of Object.entries(res.suggestions) as [
              keyof CsvMapping,
              string,
            ][]) {
              if (!filled[key] && header && cols.includes(header)) {
                filled[key] = header;
                newlySuggested.add(key);
              }
            }
            if (newlySuggested.size > 0) {
              setMapping(filled);
              setAiSuggested(newlySuggested);
            }
          } catch {
            // Drop silently – autoMap is the source of truth and the user
            // can map manually below.
          }
        }
      } else {
        setHeaders([]);
        setMapping({});
      }
    },
    [delimiter, aiConfigured, aiMapping, probeXml, probeJson],
  );

  const ready = useMemo(() => {
    if (!file) return false;
    if (fmt === "csv") {
      return Boolean(mapping.case_id && mapping.activity && mapping.timestamp);
    }
    if (fmt === "xml") {
      // XES-shaped or OCEL .xml is handled server-side – no mapping needed.
      // For generic XML we require the four canonical fields.
      if (xmlProbe?.format_hint === "xes" || xmlProbe?.format_hint === "ocel") return true;
      return Boolean(
        xmlMapping.event_element &&
          xmlMapping.case_id &&
          xmlMapping.activity &&
          xmlMapping.timestamp,
      );
    }
    if (fmt === "json") {
      // OCEL .json auto-routes server-side. Generic JSON needs the three
      // mandatory roles (event_path is optional – top-level arrays have none).
      if (jsonProbe?.format_hint === "ocel") return true;
      return Boolean(jsonMapping.case_id && jsonMapping.activity && jsonMapping.timestamp);
    }
    return true;
  }, [file, fmt, mapping, xmlMapping, xmlProbe, jsonMapping, jsonProbe]);

  const submit = async () => {
    if (!file) return;
    track(EV.PROCESS_IMPORT_STARTED, { source: "file", format: fmt });
    try {
      const csvMappingPayload =
        fmt === "csv" ? { ...mapping, delimiter, timestamp_format: tsFormat || undefined } : undefined;
      const xmlMappingPayload =
        fmt === "xml" &&
        xmlProbe?.format_hint !== "xes" &&
        xmlProbe?.format_hint !== "ocel" &&
        xmlMapping.event_element
          ? {
              event_element: xmlMapping.event_element,
              case_id: xmlMapping.case_id,
              activity: xmlMapping.activity,
              timestamp: xmlMapping.timestamp,
              end_timestamp: xmlMapping.end_timestamp || undefined,
              resource: xmlMapping.resource || undefined,
              cost: xmlMapping.cost || undefined,
              timestamp_format: xmlMapping.timestamp_format || undefined,
            }
          : undefined;
      const jsonMappingPayload =
        fmt === "json" &&
        jsonProbe?.format_hint !== "ocel" &&
        jsonMapping.case_id
          ? {
              event_path: jsonMapping.event_path || undefined,
              case_id: jsonMapping.case_id,
              activity: jsonMapping.activity,
              timestamp: jsonMapping.timestamp,
              end_timestamp: jsonMapping.end_timestamp || undefined,
              resource: jsonMapping.resource || undefined,
              cost: jsonMapping.cost || undefined,
              timestamp_format: jsonMapping.timestamp_format || undefined,
            }
          : undefined;
      const resp = await importer.mutateAsync({
        file,
        name: name || file.name,
        csvMapping: csvMappingPayload,
        xmlMapping: xmlMappingPayload,
        jsonMapping: jsonMappingPayload,
      });
      track(EV.PROCESS_IMPORT_FINISHED, { source: "file", format: fmt, ok: true });
      toast.success("Import queued");
      if (onSuccess) {
        onSuccess(resp.log_id);
      } else {
        router.push(`/processes?focus=${resp.log_id}`);
      }
    } catch (err: unknown) {
      track(EV.PROCESS_IMPORT_FINISHED, { source: "file", format: fmt, ok: false });
      toastError(`Import failed: ${(err as Error).message}`);
    }
  };

  return (
    <div className="space-y-6">
      <DropZone file={file} onDrop={onDrop} onClear={() => setFile(null)} />

      {file && (
        <Card>
          <CardContent className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="display-name">Display name</Label>
              <Input
                id="display-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={file.name}
              />
            </div>

            {fmt && <DetectedTypeBanner fmt={fmt} />}

            {fmt === "csv" && (
              <>
                {aiConfig !== undefined && !aiConfigured && (
                  <div className="flex items-start gap-2 rounded-md border border-dashed border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                    <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>
                      Tip: configure an AI provider in{" "}
                      <a
                        href="/settings/ai"
                        className="font-medium underline underline-offset-2"
                      >
                        Settings → AI
                      </a>{" "}
                      to auto-fill the column mapping for unfamiliar headers.
                    </span>
                  </div>
                )}
                {aiMapping.isPending && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Asking AI for column suggestions…
                  </div>
                )}
                <CsvMappingFields
                  headers={headers}
                  mapping={mapping}
                  setMapping={(m) => {
                    setMapping(m);
                    setAiSuggested(new Set());
                  }}
                  aiSuggested={aiSuggested}
                  delimiter={delimiter}
                  setDelimiter={async (d) => {
                    setDelimiter(d);
                    if (file) {
                      const header = await readFirstLine(file);
                      const cols = parseCsvHeader(header, d);
                      setHeaders(cols);
                      setMapping(autoMap(cols));
                      setAiSuggested(new Set());
                    }
                  }}
                  tsFormat={tsFormat}
                  setTsFormat={setTsFormat}
                />
              </>
            )}

            {fmt === "xml" && (
              <XmlMappingSection
                probe={xmlProbe}
                mapping={xmlMapping}
                setMapping={setXmlMapping}
                loading={probeXml.isPending}
                error={xmlError}
                autoMappingApplied={Boolean(xmlProbe?.auto_mapping)}
              />
            )}

            {fmt === "json" && (
              <JsonMappingSection
                probe={jsonProbe}
                mapping={jsonMapping}
                setMapping={setJsonMapping}
                loading={probeJson.isPending}
                error={jsonError}
                autoMappingApplied={Boolean(jsonProbe?.auto_mapping)}
              />
            )}

            <div className="flex justify-end gap-2 border-t border-border pt-4">
              <Button
                variant="outline"
                onClick={() => router.back()}
                className="cursor-pointer"
              >
                Cancel
              </Button>
              <Button
                onClick={submit}
                disabled={!ready || importer.isPending}
                className="cursor-pointer gap-2"
              >
                {importer.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Import
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── URL import form ───────────────────────────────────────────────────────────

function isValidUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function UrlImportForm({ onSuccess }: ImportFormProps) {
  const router = useProgressRouter();
  const importer = useImportEventLogFromUrl();

  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [urlTouched, setUrlTouched] = useState(false);

  const urlValid = isValidUrl(url);
  const urlError = urlTouched && url.length > 0 && !urlValid;

  const submit = async () => {
    if (!urlValid) return;
    try {
      const resp = await importer.mutateAsync({
        url,
        name: name.trim() || undefined,
      });
      toast.success("Import queued");
      if (onSuccess) {
        onSuccess(resp.log_id);
      } else {
        router.push(`/processes?focus=${resp.log_id}`);
      }
    } catch (err: unknown) {
      toastError(`Import failed: ${(err as Error).message}`);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <Label htmlFor="url-input">
            URL <span className="text-destructive">*</span>
          </Label>
          <Input
            id="url-input"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onBlur={() => setUrlTouched(true)}
            placeholder="https://example.com/event-log.xes"
            className={cn(urlError && "border-destructive focus-visible:ring-destructive")}
          />
          {urlError ? (
            <p className="text-xs text-destructive">Enter a valid https:// or http:// URL.</p>
          ) : (
            <p className="text-xs text-muted-foreground">
              The file must be publicly accessible and end with .xes, .xes.gz, .csv, .xml, .json,
              or an OCEL extension.
            </p>
          )}
        </div>

        <div className="grid gap-2">
          <Label htmlFor="url-name">Display name (optional)</Label>
          <Input
            id="url-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Leave blank to use the filename from the URL"
          />
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button
            variant="outline"
            onClick={() => router.back()}
            className="cursor-pointer"
          >
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!urlValid || importer.isPending}
            className="cursor-pointer gap-2"
          >
            {importer.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Import
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Watched-folder form ───────────────────────────────────────────────────────

const WATCH_MODES: { value: WatchMode; label: string; hint: string }[] = [
  { value: "manual", label: "Manual", hint: "Only scan when you click “Scan now”." },
  {
    value: "interval",
    label: "Every N minutes",
    hint: "Poll on a timer and import any new files found.",
  },
  {
    value: "continuous",
    label: "Automatic",
    hint: "Import new files as soon as they appear (checked about once a minute).",
  },
];

function WatchImportForm() {
  const router = useProgressRouter();
  const createWatch = useCreateWatchedFolder();

  const [name, setName] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [mode, setMode] = useState<WatchMode>("manual");
  const [minutes, setMinutes] = useState(10);

  const intervalValid = mode !== "interval" || minutes >= 1;
  const ready = name.trim().length > 0 && intervalValid;

  const submit = async () => {
    if (!ready) return;
    try {
      const watch = await createWatch.mutateAsync({
        name: name.trim(),
        source_path: sourcePath.trim() || undefined,
        mode,
        interval_seconds: mode === "interval" ? Math.max(60, Math.round(minutes) * 60) : null,
        create_dest_folder: true,
      });
      toast.success(`Watching “${watch.name}”`);
      router.push("/processes/watched");
    } catch (err: unknown) {
      toastError(`Couldn't create watched folder: ${(err as Error).message}`);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="rounded-md border border-dashed border-border bg-surface px-3 py-2 text-xs text-muted-foreground">
          A watched folder scans a location in your storage backend and imports new event-log
          files automatically. Drop files there from an upstream pipeline (an S3 prefix when S3
          storage is connected, otherwise a server directory) – nothing is uploaded from your
          browser here.
        </div>

        <div className="grid gap-2">
          <Label htmlFor="watch-name">
            Name <span className="text-destructive">*</span>
          </Label>
          <Input
            id="watch-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Nightly SAP export"
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="watch-source">Source location (optional)</Label>
          <Input
            id="watch-source"
            value={sourcePath}
            onChange={(e) => setSourcePath(e.target.value)}
            placeholder="Leave blank for a managed folder, or enter an S3 prefix / server path"
          />
          <p className="text-xs text-muted-foreground">
            Leave blank to let Mate create and manage the location. Otherwise point it at an
            existing prefix/path an upstream process already writes to.
          </p>
        </div>

        <div className="grid gap-2">
          <Label>Refresh</Label>
          <div className="flex flex-wrap gap-2">
            {WATCH_MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setMode(m.value)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors cursor-pointer",
                  mode === m.value
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
                aria-pressed={mode === m.value}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {WATCH_MODES.find((m) => m.value === mode)?.hint}
          </p>
          {mode === "interval" && (
            <div className="flex items-center gap-2 pt-1">
              <Label htmlFor="watch-minutes" className="text-xs">
                Every
              </Label>
              <Input
                id="watch-minutes"
                type="number"
                min={1}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                className="w-24"
              />
              <span className="text-xs text-muted-foreground">minute(s)</span>
            </div>
          )}
        </div>

        <div className="flex items-start gap-2 rounded-md border border-dashed border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Columns are auto-detected for each file. If a CSV/XML/JSON file maps incorrectly,
            fix it later in that log’s settings → Column roles.
          </span>
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button variant="outline" onClick={() => router.back()} className="cursor-pointer">
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!ready || createWatch.isPending}
            className="cursor-pointer gap-2"
          >
            {createWatch.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Create watched folder
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function DropZone({
  file,
  onDrop,
  onClear,
}: {
  file: File | null;
  onDrop: (file: File) => void;
  onClear: () => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
        <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="text-xs text-muted-foreground">
            {(file.size / 1024 / 1024).toFixed(2)} MB
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClear}
          className="cursor-pointer"
          aria-label="Remove file"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onDrop(f);
      }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-surface p-12 text-center transition-colors",
        dragOver
          ? "border-primary/60 bg-accent"
          : "border-border hover:border-primary/40 hover:bg-accent/40",
      )}
    >
      <FileUp className="h-8 w-8 text-muted-foreground" />
      <div className="text-sm font-medium">
        Drop a XES, XES.gz, CSV, XML, JSON, or OCEL file here
      </div>
      <div className="text-xs text-muted-foreground">Or click to choose a file</div>
      <input
        type="file"
        className="sr-only"
        accept=".xes,.xes.gz,.csv,.xml,.json,.jsonocel,.xmlocel,.sqlite,application/xml,text/xml,text/csv,application/json"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onDrop(f);
        }}
      />
    </label>
  );
}

function CsvMappingFields({
  headers,
  mapping,
  setMapping,
  aiSuggested,
  delimiter,
  setDelimiter,
  tsFormat,
  setTsFormat,
}: {
  headers: string[];
  mapping: Partial<CsvMapping>;
  setMapping: (m: Partial<CsvMapping>) => void;
  aiSuggested: Set<keyof CsvMapping>;
  delimiter: string;
  setDelimiter: (d: string) => void;
  tsFormat: string;
  setTsFormat: (s: string) => void;
}) {
  const set = (k: keyof CsvMapping) => (v: string) =>
    setMapping({ ...mapping, [k]: v === "__none__" ? undefined : v });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FieldSelect
          label="Delimiter"
          value={delimiter}
          onChange={setDelimiter}
          options={[
            { value: ",", label: ", (comma)" },
            { value: ";", label: "; (semicolon)" },
            { value: "\t", label: "Tab" },
            { value: "|", label: "| (pipe)" },
          ]}
          required
        />
        <FieldText
          label="Timestamp format (optional)"
          placeholder="e.g. %Y-%m-%d %H:%M:%S"
          value={tsFormat}
          onChange={setTsFormat}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <FieldSelect
          label="case_id"
          value={mapping.case_id ?? ""}
          onChange={set("case_id")}
          options={headers.map((h) => ({ value: h, label: h }))}
          required
          aiSuggested={aiSuggested.has("case_id")}
        />
        <FieldSelect
          label="activity"
          value={mapping.activity ?? ""}
          onChange={set("activity")}
          options={headers.map((h) => ({ value: h, label: h }))}
          required
          aiSuggested={aiSuggested.has("activity")}
        />
        <FieldSelect
          label="timestamp"
          value={mapping.timestamp ?? ""}
          onChange={set("timestamp")}
          options={headers.map((h) => ({ value: h, label: h }))}
          required
          aiSuggested={aiSuggested.has("timestamp")}
        />
        <FieldSelect
          label="end_timestamp"
          value={mapping.end_timestamp ?? "__none__"}
          onChange={set("end_timestamp")}
          options={[{ value: "__none__", label: "–" }, ...headers.map((h) => ({ value: h, label: h }))]}
          aiSuggested={aiSuggested.has("end_timestamp")}
        />
        <FieldSelect
          label="resource"
          value={mapping.resource ?? "__none__"}
          onChange={set("resource")}
          options={[{ value: "__none__", label: "–" }, ...headers.map((h) => ({ value: h, label: h }))]}
          aiSuggested={aiSuggested.has("resource")}
        />
        <FieldSelect
          label="cost"
          value={mapping.cost ?? "__none__"}
          onChange={set("cost")}
          options={[{ value: "__none__", label: "–" }, ...headers.map((h) => ({ value: h, label: h }))]}
          aiSuggested={aiSuggested.has("cost")}
        />
      </div>
    </div>
  );
}

function FieldSelect({
  label,
  value,
  onChange,
  options,
  required,
  aiSuggested,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  required?: boolean;
  aiSuggested?: boolean;
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="text-xs flex items-center gap-1.5">
        <span>{label}</span>
        {required && <span className="text-destructive">*</span>}
        {aiSuggested && (
          <span className="rounded-sm bg-primary/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
            AI
          </span>
        )}
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="cursor-pointer">
          <SelectValue placeholder="Pick a column" />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value} className="cursor-pointer">
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function FieldText({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="text-xs">{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}

// ── Detected-format banner ────────────────────────────────────────────────────

// Shows "<Type> detected" for the formats whose mapping section doesn't already
// self-describe (XES, CSV, explicit OCEL). XML / JSON are auto-detected by the
// server after probing, so their banner lives inside their mapping section.
function DetectedTypeBanner({ fmt }: { fmt: DetectedFormat }) {
  const label: Partial<Record<DetectedFormat, string>> = {
    xes: "XES detected",
    "xes.gz": "XES detected",
    csv: "CSV detected",
    ocel: "Object-centric (OCEL) log detected",
  };
  const text = label[fmt];
  if (!text) return null;
  return (
    <div className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{text}</span>
    </div>
  );
}

// ── XML mapping wizard ────────────────────────────────────────────────────────

function XmlMappingSection({
  probe,
  mapping,
  setMapping,
  loading,
  error,
  autoMappingApplied,
}: {
  probe: XmlProbeResponse | null;
  mapping: Partial<XmlMapping>;
  setMapping: (m: Partial<XmlMapping>) => void;
  loading: boolean;
  error: string | null;
  autoMappingApplied: boolean;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Inspecting XML structure…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        {error}
      </div>
    );
  }

  if (probe?.format_hint === "xes") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>XES detected</span>
      </div>
    );
  }

  if (probe?.format_hint === "ocel") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Object-centric (OCEL) log detected</span>
      </div>
    );
  }

  if (!probe || !probe.event_element) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        No event records found in this XML - it can&apos;t be imported.
      </div>
    );
  }

  const fieldNames = probe.fields.map((f) => f.name);
  const set = (k: XmlMappingFieldKey) => (v: string) =>
    setMapping({ ...mapping, [k]: v === "__none__" ? undefined : v });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          Detected event element:{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
            &lt;{probe.event_element}&gt;
          </code>
          {probe.events_sampled > 0 && (
            <span className="ml-1">({probe.events_sampled} sampled)</span>
          )}
        </span>
        {autoMappingApplied && (
          <span className="rounded-sm bg-primary/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
            Auto-mapped
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FieldText
          label="Event element"
          value={mapping.event_element ?? probe.event_element ?? ""}
          onChange={(v) => setMapping({ ...mapping, event_element: v })}
          placeholder={probe.event_element ?? "event"}
        />
        <FieldText
          label="Timestamp format (optional)"
          placeholder="e.g. %Y-%m-%d %H:%M:%S"
          value={mapping.timestamp_format ?? ""}
          onChange={(v) => setMapping({ ...mapping, timestamp_format: v })}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <FieldSelect
          label="case_id"
          value={mapping.case_id ?? ""}
          onChange={set("case_id")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="activity"
          value={mapping.activity ?? ""}
          onChange={set("activity")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="timestamp"
          value={mapping.timestamp ?? ""}
          onChange={set("timestamp")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="end_timestamp"
          value={mapping.end_timestamp ?? "__none__"}
          onChange={set("end_timestamp")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
        <FieldSelect
          label="resource"
          value={mapping.resource ?? "__none__"}
          onChange={set("resource")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
        <FieldSelect
          label="cost"
          value={mapping.cost ?? "__none__"}
          onChange={set("cost")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
      </div>
    </div>
  );
}

// ── JSON mapping wizard ───────────────────────────────────────────────────────

function JsonMappingSection({
  probe,
  mapping,
  setMapping,
  loading,
  error,
  autoMappingApplied,
}: {
  probe: JsonProbeResponse | null;
  mapping: Partial<JsonMapping>;
  setMapping: (m: Partial<JsonMapping>) => void;
  loading: boolean;
  error: string | null;
  autoMappingApplied: boolean;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Inspecting JSON structure…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        {error}
      </div>
    );
  }

  if (probe?.format_hint === "ocel") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Object-centric (OCEL) log detected</span>
      </div>
    );
  }

  if (!probe || probe.fields.length === 0) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        No event records found in this JSON - it can&apos;t be imported.
      </div>
    );
  }

  const fieldNames = probe.fields.map((f) => f.name);
  const set = (k: JsonMappingFieldKey) => (v: string) =>
    setMapping({ ...mapping, [k]: v === "__none__" ? undefined : v });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          Detected event array
          {probe.event_path && (
            <>
              {" "}
              at{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                {probe.event_path}
              </code>
            </>
          )}
          {probe.events_sampled > 0 && (
            <span className="ml-1">({probe.events_sampled} sampled)</span>
          )}
        </span>
        {autoMappingApplied && (
          <span className="rounded-sm bg-primary/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
            Auto-mapped
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FieldText
          label="Array key (optional)"
          value={mapping.event_path ?? ""}
          onChange={(v) => setMapping({ ...mapping, event_path: v })}
          placeholder={probe.event_path ?? "(top-level array)"}
        />
        <FieldText
          label="Timestamp format (optional)"
          placeholder="e.g. %Y-%m-%d %H:%M:%S"
          value={mapping.timestamp_format ?? ""}
          onChange={(v) => setMapping({ ...mapping, timestamp_format: v })}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <FieldSelect
          label="case_id"
          value={mapping.case_id ?? ""}
          onChange={set("case_id")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="activity"
          value={mapping.activity ?? ""}
          onChange={set("activity")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="timestamp"
          value={mapping.timestamp ?? ""}
          onChange={set("timestamp")}
          options={fieldNames.map((h) => ({ value: h, label: h }))}
          required
        />
        <FieldSelect
          label="end_timestamp"
          value={mapping.end_timestamp ?? "__none__"}
          onChange={set("end_timestamp")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
        <FieldSelect
          label="resource"
          value={mapping.resource ?? "__none__"}
          onChange={set("resource")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
        <FieldSelect
          label="cost"
          value={mapping.cost ?? "__none__"}
          onChange={set("cost")}
          options={[
            { value: "__none__", label: "–" },
            ...fieldNames.map((h) => ({ value: h, label: h })),
          ]}
        />
      </div>
    </div>
  );
}

// ── Folder import form ────────────────────────────────────────────────────────

type ItemStatus = "pending" | "uploading" | "done" | "failed" | "skipped";

interface FolderItem {
  file: File;
  relativePath: string;
  format: DetectedFormat;
  status: ItemStatus;
  error?: string;
  groupId?: string;
}

// Schema-grouped folder import: scan each file's headers/probe once, cluster
// by header signature, and ask the user to map each unique schema once.

type SchemaKind =
  | "xes"
  | "csv"
  | "xml-generic"
  | "xml-xes"
  | "json-generic"
  | "json-ocel"
  | "ocel"
  | "error";

interface SchemaGroup {
  id: string; // signature – also the React key
  kind: SchemaKind;
  itemIndices: number[];
  // CSV-only:
  headers?: string[];
  delimiter?: string;
  csvMapping?: Partial<CsvMapping>;
  csvTsFormat?: string;
  // XML-only:
  probe?: XmlProbeResponse;
  xmlMapping?: Partial<XmlMapping>;
  // JSON-only:
  jsonProbe?: JsonProbeResponse;
  jsonMapping?: Partial<JsonMapping>;
  // Set on any scan error (malformed XML/JSON, etc.). Files in this group are
  // skipped at import time.
  error?: string;
}

const CSV_DELIM_CANDIDATES = [",", ";", "\t", "|"] as const;

function detectCsvDelimiter(firstLine: string): string {
  let best = ",";
  let bestN = -1;
  for (const d of CSV_DELIM_CANDIDATES) {
    let n = 0;
    for (let i = 0; i < firstLine.length; i++) {
      if (firstLine[i] === d) n++;
    }
    if (n > bestN) {
      best = d;
      bestN = n;
    }
  }
  return best;
}

function csvSignature(headers: string[], delimiter: string): string {
  const norm = headers.map(normaliseIdent).filter(Boolean).sort().join("|");
  return `csv:${delimiter === "\t" ? "tab" : delimiter}:${norm}`;
}

function xmlSignature(probe: XmlProbeResponse): string {
  if (probe.format_hint === "xes") return "xml-xes";
  if (probe.format_hint === "ocel") return "ocel";
  const fields = probe.fields
    .map((f) => normaliseIdent(f.name))
    .filter(Boolean)
    .sort()
    .join("|");
  return `xml:${probe.event_element ?? "?"}:${fields}`;
}

function jsonSignature(probe: JsonProbeResponse): string {
  if (probe.format_hint === "ocel") return "ocel";
  const fields = probe.fields
    .map((f) => normaliseIdent(f.name))
    .filter(Boolean)
    .sort()
    .join("|");
  return `json:${probe.event_path ?? "?"}:${fields}`;
}

// Bounded-concurrency map (lightweight – only used during folder scan).
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  async function run() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      out[i] = await worker(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
  return out;
}

interface SelectedFolder {
  rootName: string;
  items: FolderItem[];
}

/** Files surfaced by `webkitdirectory` carry their relative path via
 * `webkitRelativePath`. We split that into segments to (a) derive the
 * top-level folder name and (b) build a clean display name per file. */
function collectFolderItems(files: FileList): SelectedFolder | null {
  const list: File[] = Array.from(files);
  if (list.length === 0) return null;

  // Every file shares the same first path segment when the user picks a folder.
  const firstRel = (list[0] as File & { webkitRelativePath?: string })
    .webkitRelativePath;
  const rootName = (firstRel?.split("/")[0] ?? "Imported").trim() || "Imported";

  const items: FolderItem[] = [];
  for (const f of list) {
    const rel =
      (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    const fmt = detect(f);
    if (fmt === "unsupported") continue; // silently skip non-log files (READMEs, .DS_Store…)
    items.push({ file: f, relativePath: rel, format: fmt, status: "pending" });
  }
  // Stable order – by relative path so subdirs cluster.
  items.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
  return { rootName, items };
}

function FolderImportForm({ onSuccess }: ImportFormProps) {
  const router = useProgressRouter();
  const importer = useImportEventLog();
  const createFolder = useCreateFolder();
  const probeXml = useProbeXml();
  const probeJson = useProbeJson();

  const [picked, setPicked] = useState<SelectedFolder | null>(null);
  const [folderName, setFolderName] = useState("");
  const [running, setRunning] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [groups, setGroups] = useState<SchemaGroup[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const onPick = (files: FileList | null) => {
    if (!files) return;
    const collected = collectFolderItems(files);
    if (!collected || collected.items.length === 0) {
      toastError("No supported files found (.xes, .xes.gz, .csv, .xml, .json, or OCEL).");
      return;
    }
    setPicked(collected);
    setFolderName(collected.rootName);
    setGroups([]);
    void scanAndGroup(collected);
  };

  const reset = () => {
    setPicked(null);
    setFolderName("");
    setGroups([]);
    setScanning(false);
    if (inputRef.current) inputRef.current.value = "";
  };

  // Run once per pick: read headers / probe XML for every file, then cluster
  // files by schema signature so the user maps each unique schema once.
  const scanAndGroup = async (collected: SelectedFolder) => {
    setScanning(true);
    try {
      type Scan = {
        index: number;
        signature: string;
        kind: SchemaKind;
        headers?: string[];
        delimiter?: string;
        probe?: XmlProbeResponse;
        jsonProbe?: JsonProbeResponse;
        error?: string;
      };

      const scans = await mapWithConcurrency<FolderItem, Scan>(
        collected.items,
        4,
        async (item, index) => {
          try {
            if (item.format === "xes" || item.format === "xes.gz") {
              return { index, signature: "xes", kind: "xes" };
            }
            if (item.format === "csv") {
              const headerLine = await readFirstLine(item.file);
              const delim = detectCsvDelimiter(headerLine);
              const headers = parseCsvHeader(headerLine, delim);
              return {
                index,
                signature: csvSignature(headers, delim),
                kind: "csv",
                headers,
                delimiter: delim,
              };
            }
            if (item.format === "xml") {
              const probe = await probeXml.mutateAsync(item.file);
              const sig = xmlSignature(probe);
              const kind: SchemaKind =
                probe.format_hint === "xes"
                  ? "xml-xes"
                  : probe.format_hint === "ocel"
                    ? "ocel"
                    : "xml-generic";
              return { index, signature: sig, kind, probe };
            }
            if (item.format === "json") {
              const jsonProbe = await probeJson.mutateAsync(item.file);
              const sig = jsonSignature(jsonProbe);
              return {
                index,
                signature: sig,
                kind: jsonProbe.format_hint === "ocel" ? "ocel" : "json-generic",
                jsonProbe,
              };
            }
            if (item.format === "ocel") {
              // Explicit OCEL extension (.jsonocel / .xmlocel / .sqlite) – no
              // mapping, server reads it directly.
              return { index, signature: "ocel", kind: "ocel" };
            }
            return { index, signature: "unknown", kind: "error", error: "Unsupported file" };
          } catch (err) {
            return {
              index,
              signature: `error:${index}`, // unique – each error is its own group
              kind: "error",
              error: (err as Error).message || "Scan failed",
            };
          }
        },
      );

      const bySig = new Map<string, SchemaGroup>();
      for (const s of scans) {
        let g = bySig.get(s.signature);
        if (!g) {
          g = {
            id: s.signature,
            kind: s.kind,
            itemIndices: [],
            headers: s.headers,
            delimiter: s.delimiter,
            probe: s.probe,
            jsonProbe: s.jsonProbe,
            error: s.error,
          };
          if (s.kind === "csv" && s.headers) {
            g.csvMapping = autoMap(s.headers);
            g.csvTsFormat = "";
          }
          if (s.kind === "xml-generic" && s.probe?.auto_mapping) {
            const a = s.probe.auto_mapping;
            g.xmlMapping = {
              event_element: a.event_element,
              case_id: a.case_id,
              activity: a.activity,
              timestamp: a.timestamp,
              end_timestamp: a.end_timestamp ?? undefined,
              resource: a.resource ?? undefined,
              cost: a.cost ?? undefined,
              timestamp_format: a.timestamp_format ?? undefined,
            };
          } else if (s.kind === "xml-generic" && s.probe?.event_element) {
            g.xmlMapping = { event_element: s.probe.event_element };
          }
          if (s.kind === "json-generic" && s.jsonProbe?.auto_mapping) {
            const a = s.jsonProbe.auto_mapping;
            g.jsonMapping = {
              event_path: a.event_path ?? undefined,
              case_id: a.case_id,
              activity: a.activity,
              timestamp: a.timestamp,
              end_timestamp: a.end_timestamp ?? undefined,
              resource: a.resource ?? undefined,
              cost: a.cost ?? undefined,
              timestamp_format: a.timestamp_format ?? undefined,
            };
          } else if (s.kind === "json-generic" && s.jsonProbe?.event_path) {
            g.jsonMapping = { event_path: s.jsonProbe.event_path };
          }
          bySig.set(s.signature, g);
        }
        g.itemIndices.push(s.index);
      }

      const orderedGroups = Array.from(bySig.values()).sort(
        (a, b) => b.itemIndices.length - a.itemIndices.length,
      );
      setGroups(orderedGroups);

      // Mirror groupId onto each item so progress UI can group by source.
      setPicked((cur) =>
        cur
          ? {
              ...cur,
              items: cur.items.map((it, idx) => {
                const g = orderedGroups.find((gr) => gr.itemIndices.includes(idx));
                return g ? { ...it, groupId: g.id } : it;
              }),
            }
          : cur,
      );
    } finally {
      setScanning(false);
    }
  };

  const updateGroup = (id: string, patch: Partial<SchemaGroup>) =>
    setGroups((cur) => cur.map((g) => (g.id === id ? { ...g, ...patch } : g)));

  const groupReady = (g: SchemaGroup): boolean => {
    if (g.kind === "xes" || g.kind === "xml-xes" || g.kind === "json-ocel" || g.kind === "ocel")
      return true;
    if (g.kind === "error") return false;
    if (g.kind === "csv") {
      const m = g.csvMapping ?? {};
      return Boolean(m.case_id && m.activity && m.timestamp);
    }
    if (g.kind === "xml-generic") {
      const m = g.xmlMapping ?? {};
      return Boolean(m.event_element && m.case_id && m.activity && m.timestamp);
    }
    if (g.kind === "json-generic") {
      const m = g.jsonMapping ?? {};
      return Boolean(m.case_id && m.activity && m.timestamp);
    }
    return false;
  };

  const allGroupsReady = groups.length > 0 && groups.every(groupReady);
  const needsMappingCount = groups.filter(
    (g) => g.kind === "csv" || g.kind === "xml-generic" || g.kind === "json-generic",
  ).length;
  const errorCount = groups.filter((g) => g.kind === "error").length;
  const skippedCount = groups
    .filter((g) => g.kind === "error")
    .reduce((acc, g) => acc + g.itemIndices.length, 0);

  const totalDone = useMemo(
    () =>
      picked
        ? picked.items.filter(
            (i) => i.status === "done" || i.status === "failed" || i.status === "skipped",
          ).length
        : 0,
    [picked],
  );

  const buildMappingForGroup = (g: SchemaGroup): {
    csvMapping?: Record<string, unknown>;
    xmlMapping?: Record<string, unknown>;
    jsonMapping?: Record<string, unknown>;
  } => {
    if (g.kind === "csv" && g.csvMapping) {
      return {
        csvMapping: {
          ...g.csvMapping,
          delimiter: g.delimiter ?? ",",
          timestamp_format: g.csvTsFormat || undefined,
        },
      };
    }
    if (g.kind === "xml-generic" && g.xmlMapping?.event_element) {
      return {
        xmlMapping: {
          event_element: g.xmlMapping.event_element,
          case_id: g.xmlMapping.case_id,
          activity: g.xmlMapping.activity,
          timestamp: g.xmlMapping.timestamp,
          end_timestamp: g.xmlMapping.end_timestamp || undefined,
          resource: g.xmlMapping.resource || undefined,
          cost: g.xmlMapping.cost || undefined,
          timestamp_format: g.xmlMapping.timestamp_format || undefined,
        },
      };
    }
    if (g.kind === "json-generic" && g.jsonMapping?.case_id) {
      return {
        jsonMapping: {
          event_path: g.jsonMapping.event_path || undefined,
          case_id: g.jsonMapping.case_id,
          activity: g.jsonMapping.activity,
          timestamp: g.jsonMapping.timestamp,
          end_timestamp: g.jsonMapping.end_timestamp || undefined,
          resource: g.jsonMapping.resource || undefined,
          cost: g.jsonMapping.cost || undefined,
          timestamp_format: g.jsonMapping.timestamp_format || undefined,
        },
      };
    }
    return {};
  };

  const submit = async () => {
    if (!picked) return;
    const cleanName = folderName.trim() || picked.rootName;
    const groupById = new Map(groups.map((g) => [g.id, g] as const));

    setRunning(true);
    try {
      // 1. Create the destination folder so every file has a home.
      const folder = await createFolder.mutateAsync({ name: cleanName, parent_id: null });

      // 2. Upload files sequentially so the per-file progress is meaningful
      //    and the server isn't slammed with N parallel multiparts.
      let failed = 0;
      let ok = 0;
      let skipped = 0;
      let firstLogId: string | null = null;

      for (let i = 0; i < picked.items.length; i++) {
        const item = picked.items[i];
        const g = item.groupId ? groupById.get(item.groupId) : undefined;

        // Files whose schema scan failed get marked skipped – we don't have a
        // valid mapping to send so there's nothing useful to attempt.
        if (g?.kind === "error") {
          skipped++;
          setPicked((cur) =>
            cur
              ? {
                  ...cur,
                  items: cur.items.map((it, idx) =>
                    idx === i
                      ? { ...it, status: "skipped", error: g.error ?? "Unscannable" }
                      : it,
                  ),
                }
              : cur,
          );
          continue;
        }

        // Mark uploading.
        setPicked((cur) =>
          cur
            ? {
                ...cur,
                items: cur.items.map((it, idx) =>
                  idx === i ? { ...it, status: "uploading" } : it,
                ),
              }
            : cur,
        );
        try {
          const cleanFileName = item.file.name.replace(
            /\.(xes\.gz|xes|csv|xml|json|jsonocel|xmlocel|sqlite)$/i,
            "",
          );
          const mapping = g ? buildMappingForGroup(g) : {};
          const resp = await importer.mutateAsync({
            file: item.file,
            name: cleanFileName,
            folderId: folder.id,
            csvMapping: mapping.csvMapping,
            xmlMapping: mapping.xmlMapping,
            jsonMapping: mapping.jsonMapping,
          });
          if (firstLogId === null) firstLogId = resp.log_id;
          ok++;
          setPicked((cur) =>
            cur
              ? {
                  ...cur,
                  items: cur.items.map((it, idx) =>
                    idx === i ? { ...it, status: "done" } : it,
                  ),
                }
              : cur,
          );
        } catch (err) {
          failed++;
          setPicked((cur) =>
            cur
              ? {
                  ...cur,
                  items: cur.items.map((it, idx) =>
                    idx === i
                      ? { ...it, status: "failed", error: (err as Error).message }
                      : it,
                  ),
                }
              : cur,
          );
        }
      }

      if (ok > 0 && failed === 0 && skipped === 0) {
        toast.success(`Imported ${ok} log${ok === 1 ? "" : "s"} into "${cleanName}"`);
      } else if (ok > 0) {
        const parts = [`Imported ${ok}`];
        if (failed) parts.push(`${failed} failed`);
        if (skipped) parts.push(`${skipped} skipped`);
        toast.warning(`${parts.join(", ")} - see status below`);
      } else {
        toastError(`All uploads failed or skipped`);
      }

      if (ok > 0) {
        if (onSuccess && firstLogId) {
          onSuccess(firstLogId);
        } else {
          router.push("/processes");
        }
      }
    } catch (err) {
      toastError(`Folder import failed: ${(err as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  if (!picked) {
    return (
      <label
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-surface p-12 text-center transition-colors",
          "border-border hover:border-primary/40 hover:bg-accent/40",
        )}
      >
        <FolderOpen className="h-8 w-8 text-muted-foreground" />
        <div className="text-sm font-medium">Select a folder of event logs</div>
        <div className="text-xs text-muted-foreground">
          All .xes, .xes.gz, .csv, .xml, .json, and OCEL files inside will be imported into a
          new folder named after the selected directory.
        </div>
        <input
          ref={inputRef}
          type="file"
          className="sr-only"
          // The two non-standard attributes that surface every file inside the
          // chosen folder. React's typings don't include them.
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          multiple
          onChange={(e) => onPick(e.target.files)}
        />
      </label>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <Label htmlFor="folder-name">Folder name</Label>
          <Input
            id="folder-name"
            value={folderName}
            onChange={(e) => setFolderName(e.target.value)}
            placeholder={picked.rootName}
            disabled={running}
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {picked.items.length} file{picked.items.length === 1 ? "" : "s"} ready
              {groups.length > 0 && (
                <>
                  {" · "}
                  {groups.length} schema{groups.length === 1 ? "" : "s"} detected
                </>
              )}
              {errorCount > 0 && (
                <>
                  {" · "}
                  <span className="text-amber-600 dark:text-amber-400">
                    {skippedCount} unscannable
                  </span>
                </>
              )}
            </span>
            {running && (
              <span>
                {totalDone} / {picked.items.length}
              </span>
            )}
          </div>
          {running && (
            <Progress
              value={(totalDone / Math.max(1, picked.items.length)) * 100}
              className="h-1"
            />
          )}
        </div>

        {scanning && (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Inspecting file schemas…
          </div>
        )}

        {!scanning && groups.length > 0 && !running && (
          <div className="space-y-3">
            {needsMappingCount > 0 && (
              <p className="text-xs text-muted-foreground">
                Confirm the column mapping for each unique schema. Each mapping
                is applied to every file in its group.
              </p>
            )}
            {groups.map((g) => (
              <SchemaGroupCard
                key={g.id}
                group={g}
                items={picked.items}
                ready={groupReady(g)}
                onChange={(patch) => updateGroup(g.id, patch)}
              />
            ))}
          </div>
        )}

        {running && (
          <div className="max-h-72 overflow-y-auto rounded-md border border-border">
            <ul className="divide-y divide-border text-xs">
              {picked.items.map((it, idx) => (
                <li key={idx} className="flex items-center gap-2 px-3 py-2">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {it.relativePath}
                  </span>
                  <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    {it.format}
                  </span>
                  <StatusIcon status={it.status} error={it.error} />
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button
            variant="outline"
            onClick={running ? () => router.back() : reset}
            disabled={running}
            className="cursor-pointer"
          >
            {running ? "Cancel" : "Pick another folder"}
          </Button>
          <Button
            onClick={submit}
            disabled={
              running ||
              scanning ||
              picked.items.length === 0 ||
              !allGroupsReady
            }
            className="cursor-pointer gap-2"
            title={!allGroupsReady ? "Map every schema before importing" : undefined}
          >
            {running && <Loader2 className="h-4 w-4 animate-spin" />}
            {running
              ? "Importing…"
              : `Import ${picked.items.length - skippedCount} file${picked.items.length - skippedCount === 1 ? "" : "s"}`}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SchemaGroupCard({
  group,
  items,
  ready,
  onChange,
}: {
  group: SchemaGroup;
  items: FolderItem[];
  ready: boolean;
  onChange: (patch: Partial<SchemaGroup>) => void;
}) {
  const groupItems = group.itemIndices.map((i) => items[i]).filter(Boolean);
  const sample = groupItems[0]?.relativePath ?? "";
  const more = groupItems.length - 1;

  const headerLabel: Record<SchemaKind, string> = {
    xes: "XES",
    "xml-xes": "XES inside XML",
    csv: "CSV",
    "xml-generic": "Generic XML",
    "json-generic": "Generic JSON",
    "json-ocel": "OCEL (JSON)",
    ocel: "OCEL",
    error: "Unscannable",
  };

  const noMappingNeeded =
    group.kind === "xes" ||
    group.kind === "xml-xes" ||
    group.kind === "json-ocel" ||
    group.kind === "ocel";
  const needsMapping = group.kind === "csv" || group.kind === "xml-generic" || group.kind === "json-generic";

  return (
    <div
      className={cn(
        "rounded-md border border-border bg-surface p-3 space-y-3",
        group.kind === "error" && "border-amber-500/30 bg-amber-500/5",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium">
              {headerLabel[group.kind]} · {groupItems.length} file
              {groupItems.length === 1 ? "" : "s"}
            </span>
            {noMappingNeeded && (
              <span className="rounded-sm bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
                No mapping needed
              </span>
            )}
            {group.kind === "error" && (
              <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400">
                Will be skipped
              </span>
            )}
            {needsMapping && (
              <span
                className={cn(
                  "rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                  ready
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "bg-destructive/10 text-destructive",
                )}
              >
                {ready ? "Ready" : "Needs mapping"}
              </span>
            )}
          </div>
          <div className="mt-1 truncate text-[11px] text-muted-foreground font-mono">
            {sample}
            {more > 0 && (
              <span className="text-muted-foreground/70"> +{more} more</span>
            )}
          </div>
          {group.error && (
            <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
              {group.error}
            </div>
          )}
        </div>
      </div>

      {group.kind === "csv" && group.headers && (
        <CsvMappingFields
          headers={group.headers}
          mapping={group.csvMapping ?? {}}
          setMapping={(m) => onChange({ csvMapping: m })}
          aiSuggested={new Set()}
          delimiter={group.delimiter ?? ","}
          setDelimiter={(d) => {
            // Delimiter change only affects this group's mapping; the file
            // group itself stays as-is (signatures were frozen at scan time).
            // We can't easily re-read the file's first line here without
            // re-clustering, so we just update the value used at submit.
            onChange({ delimiter: d });
          }}
          tsFormat={group.csvTsFormat ?? ""}
          setTsFormat={(s) => onChange({ csvTsFormat: s })}
        />
      )}

      {group.kind === "xml-generic" && group.probe && (
        <XmlMappingSection
          probe={group.probe}
          mapping={group.xmlMapping ?? {}}
          setMapping={(m) => onChange({ xmlMapping: m })}
          loading={false}
          error={null}
          autoMappingApplied={Boolean(group.probe.auto_mapping)}
        />
      )}

      {group.kind === "json-generic" && group.jsonProbe && (
        <JsonMappingSection
          probe={group.jsonProbe}
          mapping={group.jsonMapping ?? {}}
          setMapping={(m) => onChange({ jsonMapping: m })}
          loading={false}
          error={null}
          autoMappingApplied={Boolean(group.jsonProbe.auto_mapping)}
        />
      )}
    </div>
  );
}

function StatusIcon({ status, error }: { status: ItemStatus; error?: string }) {
  if (status === "uploading")
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />;
  if (status === "done") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />;
  if (status === "failed")
    return (
      <span title={error} className="inline-flex">
        <XCircle className="h-3.5 w-3.5 text-destructive" />
      </span>
    );
  if (status === "skipped")
    return (
      <span title={error} className="inline-flex">
        <XCircle className="h-3.5 w-3.5 text-amber-500" />
      </span>
    );
  return <span className="h-3.5 w-3.5" />;
}
