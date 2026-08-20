"use client";

import { useCallback, useEffect, useState } from "react";
import { HardDrive, Cloud, Plug, RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { rawFetch } from "@/lib/api";

type Mode = "local" | "s3";

interface StorageConfig {
  is_admin: boolean;
  mode: Mode;
  endpoint_url: string | null;
  bucket: string | null;
  region: string | null;
  access_key: string | null;
  secret_set: boolean;
  path_style: boolean;
  use_ssl: boolean;
  prefix: string;
  quota_bytes: number | null;
}

interface Usage {
  mode: string;
  used_bytes: number;
  object_count: number;
  quota_bytes: number | null;
  error: string | null;
}

const GIB = 1024 ** 3;

function formatBytes(n: number | null): string {
  if (n == null) return "–";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

interface FormState {
  mode: Mode;
  endpoint_url: string;
  bucket: string;
  region: string;
  access_key: string;
  secret_key: string; // blank = keep stored
  path_style: boolean;
  use_ssl: boolean;
  prefix: string;
  quota_gb: string; // displayed in GiB; blank = no quota
}

function formFromConfig(c: StorageConfig): FormState {
  return {
    mode: c.mode,
    endpoint_url: c.endpoint_url ?? "",
    bucket: c.bucket ?? "",
    region: c.region ?? "",
    access_key: c.access_key ?? "",
    secret_key: "",
    path_style: c.path_style,
    use_ssl: c.use_ssl,
    prefix: c.prefix ?? "",
    quota_gb: c.quota_bytes != null ? String(Math.round(c.quota_bytes / GIB)) : "",
  };
}

export default function AdminStoragePage() {
  const [config, setConfig] = useState<StorageConfig | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [loadingUsage, setLoadingUsage] = useState(false);

  const loadUsage = useCallback(async () => {
    setLoadingUsage(true);
    try {
      const res = await rawFetch("/api/v1/admin/storage/usage");
      if (res.ok) setUsage((await res.json()) as Usage);
    } catch {
      // Non-fatal: the overview just stays blank.
    } finally {
      setLoadingUsage(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await rawFetch("/api/v1/admin/storage/config");
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as StorageConfig;
        if (cancelled) return;
        setConfig(data);
        if (data.is_admin) {
          setForm(formFromConfig(data));
          if (data.mode === "s3") void loadUsage();
        }
      } catch {
        if (!cancelled) setConfig({ is_admin: false } as StorageConfig);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadUsage]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  function buildBody(f: FormState) {
    const quota = f.quota_gb.trim() === "" ? null : Math.round(Number(f.quota_gb) * GIB);
    return {
      mode: f.mode,
      endpoint_url: f.endpoint_url.trim() || null,
      bucket: f.bucket.trim() || null,
      region: f.region.trim() || null,
      access_key: f.access_key.trim() || null,
      secret_key: f.secret_key.trim() || null,
      path_style: f.path_style,
      use_ssl: f.use_ssl,
      prefix: f.prefix.trim(),
      quota_bytes: quota != null && Number.isFinite(quota) ? quota : null,
    };
  }

  async function onTest() {
    if (!form) return;
    setTesting(true);
    try {
      const res = await rawFetch("/api/v1/admin/storage/test", {
        method: "POST",
        json: buildBody(form),
      });
      const data = (await res.json()) as { ok: boolean; message: string };
      if (data.ok) toast.success(data.message);
      else toast.error(data.message || "Connection failed.");
    } catch (err) {
      toast.error(`Test failed: ${(err as Error).message}`);
    } finally {
      setTesting(false);
    }
  }

  async function onSave() {
    if (!form) return;
    setSaving(true);
    try {
      const res = await rawFetch("/api/v1/admin/storage/config", {
        method: "PUT",
        json: buildBody(form),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        toast.error(
          (detail as { detail?: string } | null)?.detail ?? `Save failed (${res.status}).`,
        );
        return;
      }
      const data = (await res.json()) as StorageConfig;
      setConfig(data);
      setForm(formFromConfig(data));
      toast.success("Storage settings saved.");
      if (data.mode === "s3") void loadUsage();
      else setUsage(null);
    } catch (err) {
      toast.error(`Save failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  const loading = config === null;
  const isAdmin = config?.is_admin === true;

  return (
    <div className="space-y-4">
      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              className="space-y-3 rounded-xl border border-border bg-card p-5"
            >
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-72" />
              <Skeleton className="h-9 w-full max-w-md rounded-md" />
            </div>
          ))}
        </div>
      ) : !isAdmin ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Storage settings require the <code>admin</code> role. Ask an
            administrator to grant it in Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : form == null ? null : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Storage backend</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <RadioGroup
                value={form.mode}
                onValueChange={(v) => set("mode", v as Mode)}
                className="gap-3"
              >
                <label
                  htmlFor="mode-local"
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3"
                >
                  <RadioGroupItem value="local" id="mode-local" className="mt-0.5" />
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      <HardDrive className="h-4 w-4" /> Local disk
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Everything stays on the VM under <code>data/</code>. Best for
                      development and testing.
                    </p>
                  </div>
                </label>
                <label
                  htmlFor="mode-s3"
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3"
                >
                  <RadioGroupItem value="s3" id="mode-s3" className="mt-0.5" />
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      <Cloud className="h-4 w-4" /> S3 bucket
                    </div>
                    <p className="text-xs text-muted-foreground">
                      The connected bucket is the primary store; local disk acts as a
                      working cache. Logs &amp; outputs are uploaded as they&apos;re
                      written.
                    </p>
                  </div>
                </label>
              </RadioGroup>
            </CardContent>
          </Card>

          {form.mode === "s3" && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">S3 connection</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <Field label="Endpoint URL" htmlFor="endpoint">
                  <Input
                    id="endpoint"
                    placeholder="https://s3.example.org"
                    value={form.endpoint_url}
                    onChange={(e) => set("endpoint_url", e.target.value)}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Bucket" htmlFor="bucket">
                    <Input
                      id="bucket"
                      placeholder="pm-mate"
                      value={form.bucket}
                      onChange={(e) => set("bucket", e.target.value)}
                    />
                  </Field>
                  <Field label="Region (optional)" htmlFor="region">
                    <Input
                      id="region"
                      placeholder="us-east-1"
                      value={form.region}
                      onChange={(e) => set("region", e.target.value)}
                    />
                  </Field>
                </div>
                <Field label="Access key" htmlFor="access">
                  <Input
                    id="access"
                    autoComplete="off"
                    value={form.access_key}
                    onChange={(e) => set("access_key", e.target.value)}
                  />
                </Field>
                <Field label="Secret key" htmlFor="secret">
                  <Input
                    id="secret"
                    type="password"
                    autoComplete="new-password"
                    placeholder={config?.secret_set ? "•••••••• (unchanged)" : ""}
                    value={form.secret_key}
                    onChange={(e) => set("secret_key", e.target.value)}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Key prefix (optional)" htmlFor="prefix">
                    <Input
                      id="prefix"
                      placeholder="mate"
                      value={form.prefix}
                      onChange={(e) => set("prefix", e.target.value)}
                    />
                  </Field>
                  <Field label="Quota (GiB, optional)" htmlFor="quota">
                    <Input
                      id="quota"
                      inputMode="numeric"
                      placeholder="50"
                      value={form.quota_gb}
                      onChange={(e) => set("quota_gb", e.target.value)}
                    />
                  </Field>
                </div>
                <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <Label htmlFor="path-style" className="text-sm">
                    Path-style addressing
                    <span className="ml-1 text-xs text-muted-foreground">(Ceph RGW)</span>
                  </Label>
                  <Switch
                    id="path-style"
                    checked={form.path_style}
                    onCheckedChange={(v) => set("path_style", v)}
                  />
                </div>
                <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <Label htmlFor="use-ssl" className="text-sm">
                    Use TLS (https)
                  </Label>
                  <Switch
                    id="use-ssl"
                    checked={form.use_ssl}
                    onCheckedChange={(v) => set("use_ssl", v)}
                  />
                </div>

                <div className="flex gap-2 pt-1">
                  <Button
                    variant="outline"
                    onClick={onTest}
                    disabled={testing}
                    className="cursor-pointer gap-1.5"
                  >
                    <Plug className="h-4 w-4" />
                    {testing ? "Testing…" : "Test connection"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="flex justify-end">
            <Button onClick={onSave} disabled={saving} className="cursor-pointer">
              {saving ? "Saving…" : "Save settings"}
            </Button>
          </div>

          {config?.mode === "s3" && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  Storage overview
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void loadUsage()}
                    disabled={loadingUsage}
                    aria-label="Refresh usage"
                    className="h-7 w-7 cursor-pointer"
                  >
                    <RefreshCw className={`h-4 w-4 ${loadingUsage ? "animate-spin" : ""}`} />
                  </Button>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {usage?.error ? (
                  <p className="text-xs text-destructive">{usage.error}</p>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <Stat label="Used" value={formatBytes(usage?.used_bytes ?? null)} />
                      <Stat label="Objects" value={usage?.object_count ?? "–"} />
                    </div>
                    {usage && usage.quota_bytes != null && usage.quota_bytes > 0 && (
                      <div className="space-y-1">
                        <Progress
                          value={Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100)}
                        />
                        <p className="text-xs text-muted-foreground">
                          {formatBytes(usage.used_bytes)} of {formatBytes(usage.quota_bytes)} used
                        </p>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor} className="text-xs font-medium">
        {label}
      </Label>
      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}
