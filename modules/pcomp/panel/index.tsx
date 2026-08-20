"use client";

import { useEffect, useRef, useState } from "react";
import { FlaskConical, GitCompareArrows, Layers } from "lucide-react";

import { api } from "@/lib/api";
import { subscribeJob } from "@/lib/ws";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/empty-state";
import { useQuery } from "@tanstack/react-query";

// ── Types ─────────────────────────────────────────────────────────────────────

interface LogSummary {
  id: string;
  name: string;
  log_model: string;
  status: string;
  cases_count?: number | null;
}

interface JobStarted {
  job_id: string;
}

interface PermutationResult {
  kind: "pcomp_permutation_test";
  pvalue: number;
  baseline_log_id: string;
  other_log_id: string;
  distribution_size: number;
  seed: number;
  weighted_time_cost: boolean;
}

interface BootstrapResult {
  kind: "pcomp_bootstrap_test";
  pvalue: number;
  baseline_log_id: string;
  other_log_id: string;
  bootstrapping_dist_size: number;
  resample_size: number;
  seed: number;
}

type TestResult = PermutationResult | BootstrapResult;

type JobStatus = "idle" | "running" | "completed" | "failed";

// ── Helpers ───────────────────────────────────────────────────────────────────

const MOD = "/api/v1/modules/pcomp";

function pvalueColor(pvalue: number): string {
  if (pvalue < 0.01) return "text-red-600 dark:text-red-400";
  if (pvalue < 0.05) return "text-amber-600 dark:text-amber-400";
  return "text-emerald-600 dark:text-emerald-400";
}

function pvalueLabel(pvalue: number): string {
  if (pvalue < 0.01) return "Highly significant difference (p < 0.01)";
  if (pvalue < 0.05) return "Significant difference (p < 0.05)";
  if (pvalue < 0.1) return "Marginal difference (p < 0.10)";
  return "No significant difference (p ≥ 0.10)";
}

// ── Log picker ────────────────────────────────────────────────────────────────

function useComparisonLogs(logId: string) {
  return useQuery<LogSummary[]>({
    queryKey: ["pcomp", "logs"],
    queryFn: () => api<LogSummary[]>("/api/v1/event-logs?status=ready"),
    select: (logs) =>
      logs.filter((l) => l.log_model === "case_centric" && l.status === "ready" && l.id !== logId),
    staleTime: 30_000,
  });
}

// ── Job tracker hook ──────────────────────────────────────────────────────────

function useJobTracker() {
  const [jobStatus, setJobStatus] = useState<JobStatus>("idle");
  const [progress, setProgress] = useState<{ fraction: number; stage: string }>({
    fraction: 0,
    stage: "",
  });
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const subRef = useRef<{ close: () => void } | null>(null);

  const track = (jobId: string, logId: string, otherId: string, test: "permutation" | "bootstrap") => {
    subRef.current?.close();
    setJobStatus("running");
    setProgress({ fraction: 0, stage: "Starting…" });
    setResult(null);
    setError(null);

    subRef.current = subscribeJob(jobId, (env) => {
      const p = env.payload as Record<string, unknown>;
      if (env.topic === "job.progress") {
        const fraction =
          typeof p.fraction === "number" ? p.fraction : typeof p.current === "number" ? 0 : 0;
        setProgress({ fraction, stage: String(p.stage ?? p.message ?? "") });
      } else if (env.topic === "job.completed") {
        setJobStatus("completed");
        subRef.current?.close();
        api<TestResult>(
          `${MOD}/results?log_id=${logId}&other_log_id=${otherId}&test=${test}`,
        )
          .then(setResult)
          .catch(() => setError("Job finished but could not fetch the result."));
      } else if (env.topic === "job.failed") {
        setJobStatus("failed");
        setError(String(p.error ?? "Unknown error"));
        subRef.current?.close();
      }
    });
  };

  useEffect(() => () => subRef.current?.close(), []);

  return { jobStatus, progress, result, error, track, reset: () => setJobStatus("idle") };
}

// ── Result card ───────────────────────────────────────────────────────────────

function ResultCard({ result }: { result: TestResult }) {
  return (
    <Card className="p-5 space-y-3">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">
          {result.kind === "pcomp_permutation_test" ? "Permutation Test" : "Bootstrap Test"} Result
        </span>
      </div>
      <div className="flex items-baseline gap-3">
        <span className={`text-4xl font-bold tabular-nums ${pvalueColor(result.pvalue)}`}>
          p = {result.pvalue.toFixed(4)}
        </span>
        <Badge
          variant="outline"
          className={pvalueColor(result.pvalue)}
        >
          {pvalueLabel(result.pvalue)}
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-muted-foreground pt-1">
        {result.kind === "pcomp_permutation_test" ? (
          <>
            <span>Distribution size: {result.distribution_size.toLocaleString()}</span>
            <span>Seed: {result.seed}</span>
            <span>Weighted time cost: {result.weighted_time_cost ? "yes" : "no"}</span>
          </>
        ) : (
          <>
            <span>Bootstrap dist size: {result.bootstrapping_dist_size.toLocaleString()}</span>
            <span>Resample size: {result.resample_size}</span>
            <span>Seed: {result.seed}</span>
          </>
        )}
      </div>
    </Card>
  );
}

// ── Progress bar ──────────────────────────────────────────────────────────────

function ProgressBar({ fraction, stage }: { fraction: number; stage: string }) {
  return (
    <div className="space-y-1.5">
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-primary transition-all duration-300 rounded-full"
          style={{ width: `${Math.round(fraction * 100)}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">{stage || "Running…"}</p>
    </div>
  );
}

// ── Permutation tab ───────────────────────────────────────────────────────────

function PermutationTab({
  logId,
  otherId,
  tracker,
}: {
  logId: string;
  otherId: string;
  tracker: ReturnType<typeof useJobTracker>;
}) {
  const [distSize, setDistSize] = useState(1000);
  const [seed, setSeed] = useState(42);
  const [weighted, setWeighted] = useState(true);
  const running = tracker.jobStatus === "running";

  const handleRun = async () => {
    try {
      const { job_id } = await api<JobStarted>(
        `${MOD}/permutation-test?log_id=${logId}`,
        { method: "POST", json: { other_log_id: otherId, distribution_size: distSize, seed, weighted_time_cost: weighted } },
      );
      tracker.track(job_id, logId, otherId, "permutation");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to start job";
      tracker.reset();
      alert(msg);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Compares two logs using the Earth Mover&apos;s Distance and a permutation test
        (Timed Levenshtein). A lower p-value means the two processes are significantly different.
        Larger distribution sizes give more precise p-values but take longer to run.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="text-xs">Distribution size</Label>
          <input
            type="number"
            min={100}
            max={20000}
            step={100}
            value={distSize}
            onChange={(e) => setDistSize(Number(e.target.value))}
            disabled={running}
            className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">Seed</Label>
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value))}
            disabled={running}
            className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Switch
          id="weighted"
          checked={weighted}
          onCheckedChange={setWeighted}
          disabled={running}
        />
        <Label htmlFor="weighted" className="text-xs cursor-pointer">
          Weighted time cost
        </Label>
      </div>
      <Button onClick={handleRun} disabled={running || !otherId} size="sm">
        {running ? "Running…" : "Run permutation test"}
      </Button>
      {running && <ProgressBar fraction={tracker.progress.fraction} stage={tracker.progress.stage} />}
      {tracker.jobStatus === "failed" && tracker.error && (
        <p className="text-xs text-destructive">{tracker.error}</p>
      )}
      {tracker.jobStatus === "completed" && tracker.result?.kind === "pcomp_permutation_test" && (
        <ResultCard result={tracker.result} />
      )}
    </div>
  );
}

// ── Bootstrap tab ─────────────────────────────────────────────────────────────

function BootstrapTab({
  logId,
  otherId,
  tracker,
}: {
  logId: string;
  otherId: string;
  tracker: ReturnType<typeof useJobTracker>;
}) {
  const [distSize, setDistSize] = useState(1000);
  const [resampleSize, setResampleSize] = useState(1.0);
  const [seed, setSeed] = useState(42);
  const running = tracker.jobStatus === "running";

  const handleRun = async () => {
    try {
      const { job_id } = await api<JobStarted>(
        `${MOD}/bootstrap-test?log_id=${logId}`,
        { method: "POST", json: { other_log_id: otherId, bootstrapping_dist_size: distSize, resample_size: resampleSize, seed } },
      );
      tracker.track(job_id, logId, otherId, "bootstrap");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to start job";
      tracker.reset();
      alert(msg);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        P-P-UP (Process-Process-Unknown Process) bootstrap test for control-flow similarity.
        Tests whether two logs were generated by the same unknown process.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="text-xs">Bootstrap dist size</Label>
          <input
            type="number"
            min={100}
            max={20000}
            step={100}
            value={distSize}
            onChange={(e) => setDistSize(Number(e.target.value))}
            disabled={running}
            className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">Resample size (0.1 – 1.0)</Label>
          <input
            type="number"
            min={0.1}
            max={1.0}
            step={0.1}
            value={resampleSize}
            onChange={(e) => setResampleSize(Number(e.target.value))}
            disabled={running}
            className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">Seed</Label>
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value))}
            disabled={running}
            className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
          />
        </div>
      </div>
      <Button onClick={handleRun} disabled={running || !otherId} size="sm">
        {running ? "Running…" : "Run bootstrap test"}
      </Button>
      {running && <ProgressBar fraction={tracker.progress.fraction} stage={tracker.progress.stage} />}
      {tracker.jobStatus === "failed" && tracker.error && (
        <p className="text-xs text-destructive">{tracker.error}</p>
      )}
      {tracker.jobStatus === "completed" && tracker.result?.kind === "pcomp_bootstrap_test" && (
        <ResultCard result={tracker.result} />
      )}
    </div>
  );
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export default function PcompPanel({ logId }: { logId: string; moduleId: string }) {
  const logsQuery = useComparisonLogs(logId);
  const logs = logsQuery.data ?? [];
  const [otherId, setOtherId] = useState<string>("");
  const permTracker = useJobTracker();
  const bootTracker = useJobTracker();

  const otherName = logs.find((l) => l.id === otherId)?.name ?? otherId.slice(0, 8);

  return (
    <div className="flex flex-col gap-4">
      {/* Log picker */}
      <div className="rounded-lg border bg-muted/40 px-4 py-3 space-y-2">
        <div className="flex items-center gap-2 text-sm">
          <GitCompareArrows className="h-4 w-4 text-muted-foreground" />
          <span className="text-muted-foreground">Compare baseline against</span>
          {otherId && (
            <Badge variant="secondary" className="font-medium flex items-center gap-1">
              <Layers className="h-3 w-3" />
              {otherName}
            </Badge>
          )}
        </div>
        {logsQuery.isLoading ? (
          <Skeleton className="h-8 w-64" />
        ) : logs.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No other ready case-centric logs available. Import a second log first.
          </p>
        ) : (
          <Select value={otherId} onValueChange={setOtherId}>
            <SelectTrigger className="h-8 w-72 text-xs">
              <SelectValue placeholder="Select a log to test against…" />
            </SelectTrigger>
            <SelectContent>
              {logs.map((l) => (
                <SelectItem key={l.id} value={l.id}>
                  {l.name}
                  {typeof l.cases_count === "number" && (
                    <span className="ml-2 opacity-60 text-[10px]">
                      {l.cases_count.toLocaleString()} cases
                    </span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {!otherId ? (
        <EmptyState
          icon={FlaskConical}
          title="Select a log to test against"
          description="Pick a second event log above to run a hypothesis test comparing it to the baseline."
        />
      ) : (
        <Tabs defaultValue="permutation" className="w-full">
          <TabsList>
            <TabsTrigger value="permutation">Permutation Test</TabsTrigger>
            <TabsTrigger value="bootstrap">Bootstrap Test</TabsTrigger>
          </TabsList>
          <TabsContent value="permutation" className="mt-4">
            <PermutationTab logId={logId} otherId={otherId} tracker={permTracker} />
          </TabsContent>
          <TabsContent value="bootstrap" className="mt-4">
            <BootstrapTab logId={logId} otherId={otherId} tracker={bootTracker} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
