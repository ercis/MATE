"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitBranch, Package, Upload, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  PageContainer,
  PageTitle,
  PageDescription,
} from "@/components/page";
import { api, ApiError, rawFetch } from "@/lib/api";
import { cn } from "@/lib/cn";
import { toastError } from "@/lib/toast";

const ACCEPT_SUFFIXES = [".zip", ".tar", ".tar.gz", ".tgz"];

function hasAcceptedSuffix(name: string) {
  const lowered = name.toLowerCase();
  return ACCEPT_SUFFIXES.some((s) => lowered.endsWith(s));
}

export default function ImportModulePage() {
  const router = useRouter();
  const qc = useQueryClient();

  const onInstalled = useCallback(
    (jobId: string) => {
      toast.success("Module install queued", {
        description: "Track progress in the jobs dock (bottom-left).",
      });
      // Refresh the listings when the user gets back to /modules.
      qc.invalidateQueries({ queryKey: ["modules"] });
      router.push(`/modules?install=${jobId}`);
    },
    [qc, router],
  );

  return (
    <PageContainer className="space-y-6">
      <header className="space-y-1">
        <PageTitle>Install a module</PageTitle>
        <PageDescription>
          Pick a source. The platform unpacks the module, resolves its
          dependencies, and registers it without a restart.
        </PageDescription>
      </header>

      <Tabs defaultValue="upload" className="space-y-4">
        <TabsList>
          <TabsTrigger value="upload" className="cursor-pointer">
            <Upload className="mr-1.5 h-3.5 w-3.5" />
            Upload
          </TabsTrigger>
          <TabsTrigger value="git" className="cursor-pointer">
            <GitBranch className="mr-1.5 h-3.5 w-3.5" />
            From git URL
          </TabsTrigger>
          <TabsTrigger value="registry" className="cursor-pointer">
            <Package className="mr-1.5 h-3.5 w-3.5" />
            From PyPI / npm
          </TabsTrigger>
        </TabsList>

        <TabsContent value="upload">
          <UploadTab onInstalled={onInstalled} />
        </TabsContent>
        <TabsContent value="git">
          <GitTab onInstalled={onInstalled} />
        </TabsContent>
        <TabsContent value="registry">
          <RegistryTab onInstalled={onInstalled} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

function UploadTab({ onInstalled }: { onInstalled: (jobId: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const mutation = useMutation({
    mutationFn: async (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      const res = await rawFetch("/api/v1/modules/install", { method: "POST", body: fd });
      if (!res.ok) {
        const text = await res.text();
        let detail: unknown = text;
        try {
          detail = JSON.parse(text);
        } catch {
          /* keep as text */
        }
        throw new ApiError(res.status, detail);
      }
      return (await res.json()) as { job_id: string };
    },
    onSuccess: (r) => onInstalled(r.job_id),
    onError: (err: Error) => toastError(`Upload failed: ${err.message}`),
  });

  const onPick = (f: File | null) => {
    if (!f) return;
    if (!hasAcceptedSuffix(f.name)) {
      toastError(`Unsupported file: ${f.name}. Use ${ACCEPT_SUFFIXES.join(", ")}.`);
      return;
    }
    setFile(f);
  };

  return (
    <Card>
      <CardContent className="space-y-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            onPick(e.dataTransfer.files?.[0] ?? null);
          }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-border bg-card/40 px-6 py-12 text-center transition-colors",
            drag && "border-primary bg-primary/5",
          )}
        >
          <Upload className="h-6 w-6 text-muted-foreground" />
          <div className="text-sm font-medium">
            {file ? file.name : "Drop a module archive here or click to browse"}
          </div>
          <div className="text-xs text-muted-foreground">
            {ACCEPT_SUFFIXES.join(" · ")}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT_SUFFIXES.join(",")}
            className="hidden"
            onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          />
        </div>

        {file && (
          <div className="flex items-center justify-between gap-3">
            <Button
              variant="ghost"
              size="sm"
              className="cursor-pointer text-muted-foreground"
              onClick={() => setFile(null)}
              disabled={mutation.isPending}
            >
              <X className="mr-1 h-3.5 w-3.5" /> Clear
            </Button>
            <Button
              className="cursor-pointer"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(file)}
            >
              {mutation.isPending ? "Uploading…" : "Install"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GitTab({ onInstalled }: { onInstalled: (jobId: string) => void }) {
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      return api<{ job_id: string }>("/api/v1/modules/install/git", {
        method: "POST",
        json: { url, ref: ref || undefined },
      });
    },
    onSuccess: (r) => onInstalled(r.job_id),
    onError: (err: Error) => toastError(`Clone failed: ${err.message}`),
  });

  const ready = url.trim().length > 0;

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="git-url">Repository URL</Label>
          <Input
            id="git-url"
            placeholder="https://github.com/org/repo.git"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={mutation.isPending}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="git-ref">Branch, tag, or commit (optional)</Label>
          <Input
            id="git-ref"
            placeholder="main"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            disabled={mutation.isPending}
          />
        </div>
        <div className="flex justify-end">
          <Button
            className="cursor-pointer"
            disabled={!ready || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Queuing…" : "Clone & install"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function RegistryTab({ onInstalled }: { onInstalled: (jobId: string) => void }) {
  const [source, setSource] = useState<"pypi" | "npm">("pypi");
  const [id, setId] = useState("");
  const [version, setVersion] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      return api<{ job_id: string }>("/api/v1/modules/install/registry", {
        method: "POST",
        json: { source, id, version: version || undefined },
      });
    },
    onSuccess: (r) => onInstalled(r.job_id),
    onError: (err: Error) => toastError(`Install failed: ${err.message}`),
  });

  const ready = id.trim().length > 0;

  return (
    <Card>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Resolves through the <code className="rounded bg-muted px-1">mate.modules</code> entry point.
          Currently pending - install will fail with a clear message until entry-point discovery lands.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="reg-source">Source</Label>
            <Select value={source} onValueChange={(v: string) => setSource(v as "pypi" | "npm")}>
              <SelectTrigger id="reg-source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pypi">PyPI</SelectItem>
                <SelectItem value="npm">npm</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="reg-id">Package id</Label>
            <Input
              id="reg-id"
              placeholder="mate-organizational"
              value={id}
              onChange={(e) => setId(e.target.value)}
              disabled={mutation.isPending}
            />
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="reg-version">Version (optional)</Label>
          <Input
            id="reg-version"
            placeholder="1.0.0"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            disabled={mutation.isPending}
          />
        </div>
        <div className="flex justify-end">
          <Button
            className="cursor-pointer"
            disabled={!ready || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Queuing…" : "Install"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
