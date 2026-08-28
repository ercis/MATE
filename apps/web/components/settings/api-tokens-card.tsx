"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ScopeBadges, TokenScopePicker } from "@/components/settings/token-scopes";
import { ApiError } from "@/lib/api";
import {
  useAdminApiTokens,
  useAdminRevokeToken,
  useApiTokens,
  useCreateApiToken,
  useMcpAdminConfig,
  useMcpConsent,
  useMcpInfo,
  useRevokeApiToken,
  useSetConsent,
  useUpdateMcpAdminConfig,
} from "@/lib/api-tokens-queries";
import type { CreateTokenResponse, McpInfo } from "@/lib/api-types";

function fmt(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

async function copy(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${label} copied to clipboard`);
  } catch {
    toast.error("Couldn't copy to clipboard");
  }
}

function errMessage(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === "string") return err.detail;
  return (err as Error)?.message ?? "unknown error";
}

export function ApiTokensCard() {
  const mcp = useMcpInfo();
  return (
    <div className="space-y-6">
      <McpServerCard info={mcp.data} loading={mcp.isLoading} />
      <TokensCard info={mcp.data} />
      <AdminCard />
    </div>
  );
}

// ── MCP server + connection info + consent ──────────────────────────────────

function McpServerCard({ info, loading }: { info: McpInfo | undefined; loading: boolean }) {
  const consent = useMcpConsent();
  const setConsent = useSetConsent();
  const endpoint = info?.url ?? "";
  const snippet = JSON.stringify(
    {
      mcpServers: {
        mate: { url: endpoint || "<your-mate-url>/mcp", headers: { Authorization: "Bearer <token>" } },
      },
    },
    null,
    2,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP server</CardTitle>
        <CardDescription>
          Let external tools (Claude Desktop, claude.ai, your own agents) read your
          process-mining results over the Model Context Protocol.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2">
          <span className="text-sm">Status:</span>
          {loading ? (
            <Badge variant="outline">checking…</Badge>
          ) : info?.enabled ? (
            <Badge>enabled</Badge>
          ) : (
            <Badge variant="outline">disabled</Badge>
          )}
          {info?.enabled && info.read_only && (
            <Badge
              variant="outline"
              title="Write tools are disabled platform-wide; MCP clients can only read."
            >
              read-only
            </Badge>
          )}
        </div>

        {info?.enabled && (
          <>
            {info.toolsets.length > 0 && (
              <div className="space-y-1">
                <Label>Enabled toolsets</Label>
                <div className="flex flex-wrap gap-1">
                  {info.toolsets.map((t) => (
                    <Badge
                      key={t}
                      variant="secondary"
                      className="px-1.5 font-mono text-[10px] font-normal"
                    >
                      {t}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-1">
              <Label>Endpoint</Label>
              <div className="flex items-center gap-2">
                <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">{endpoint}</code>
                <Button variant="outline" size="sm" onClick={() => void copy(endpoint, "Endpoint")}>
                  Copy
                </Button>
              </div>
            </div>

            {info.oauth.client_id && (
              <div className="space-y-1 rounded-md border border-border p-3 text-xs">
                <p className="font-medium">Single sign-on (OAuth)</p>
                <p className="text-muted-foreground">
                  Authorization server: <code>{info.oauth.authorization_server}</code>
                </p>
                <p className="text-muted-foreground">
                  Client ID: <code>{info.oauth.client_id}</code>
                </p>
                <p className="text-muted-foreground">
                  Metadata: <code>{info.oauth.metadata_url}</code>
                </p>
              </div>
            )}

            <div className="space-y-1">
              <Label>Client config (token auth)</Label>
              <pre className="overflow-x-auto rounded bg-muted px-3 py-2 text-xs">{snippet}</pre>
              <Button variant="outline" size="sm" onClick={() => void copy(snippet, "Config")}>
                Copy config
              </Button>
            </div>

            {consent.data?.required && (
              <div className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="space-y-0.5 pr-4">
                  <p className="text-sm font-medium">Allow external data access</p>
                  <p className="text-xs text-muted-foreground">
                    Required before any MCP client can read your process data. You can revoke it
                    anytime.
                  </p>
                </div>
                <Switch
                  checked={consent.data?.consented ?? false}
                  disabled={setConsent.isPending}
                  onCheckedChange={(v) =>
                    setConsent
                      .mutateAsync(v)
                      .then(() => toast.success(v ? "External access enabled." : "External access disabled."))
                      .catch((e) => toast.error(errMessage(e)))
                  }
                />
              </div>
            )}
          </>
        )}

        {info && !info.enabled && (
          <p className="text-xs text-muted-foreground">
            The MCP server is turned off. An administrator can enable it (<code>MCP_ENABLED=1</code>
            {" "}or the toggle below).
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Personal access tokens ──────────────────────────────────────────────────

function TokensCard({ info }: { info: McpInfo | undefined }) {
  const tokens = useApiTokens();
  const create = useCreateApiToken();
  const revoke = useRevokeApiToken();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const [created, setCreated] = useState<CreateTokenResponse | null>(null);
  const supported = info?.scopes_supported ?? [];
  const mintAllowed = info?.mint_allowed ?? true;

  async function onCreate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const result = await create.mutateAsync({ name: trimmed, scopes: [...scopes] });
      setCreated(result);
      setName("");
      setScopes(new Set());
      toast.success("Token created – copy it now, it won't be shown again.");
    } catch (err) {
      toast.error(`Couldn't create token: ${errMessage(err)}`);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Personal access tokens</CardTitle>
        <CardDescription>
          Each token acts as you and reads only your own data. Treat it like a password.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!mintAllowed && (
          <p className="text-xs text-destructive">
            Creating tokens is disabled by your administrator.
          </p>
        )}
        <div className="space-y-2">
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1">
              <Label htmlFor="token-name">Name</Label>
              <Input
                id="token-name"
                placeholder="e.g. Claude Desktop"
                value={name}
                maxLength={255}
                disabled={!mintAllowed}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void onCreate();
                }}
              />
            </div>
            <Button onClick={() => void onCreate()} disabled={!name.trim() || !mintAllowed || create.isPending}>
              {create.isPending ? "Creating…" : "Create token"}
            </Button>
          </div>
          <div className="pt-1">
            <TokenScopePicker
              supported={supported}
              selected={scopes}
              disabled={!mintAllowed}
              onChange={setScopes}
            />
          </div>
        </div>

        {created && (
          <div className="space-y-2 rounded-md border border-primary/40 bg-primary/5 p-3">
            <p className="text-sm font-medium">Copy your new token now — it won't be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">{created.token}</code>
              <Button size="sm" onClick={() => void copy(created.token, "Token")}>
                Copy
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setCreated(null)}>
                Dismiss
              </Button>
            </div>
          </div>
        )}

        <Separator />

        {tokens.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading tokens…</p>
        ) : tokens.data && tokens.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Prefix</TableHead>
                <TableHead>Scopes</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tokens.data.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="font-medium">{t.name}</TableCell>
                  <TableCell>
                    <code className="text-xs">{t.token_prefix}…</code>
                  </TableCell>
                  <TableCell>
                    <ScopeBadges scopes={t.scopes} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmt(t.last_used_at)}</TableCell>
                  <TableCell>
                    {t.revoked ? <Badge variant="outline">revoked</Badge> : <Badge>active</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    {!t.revoked && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={revoke.isPending}
                        onClick={() =>
                          revoke
                            .mutateAsync(t.id)
                            .then(() => toast.success("Token revoked."))
                            .catch((e) => toast.error(errMessage(e)))
                        }
                      >
                        Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">No tokens yet.</p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Admin governance (hidden for non-admins: the config query 403s) ─────────

function AdminCard() {
  const cfg = useMcpAdminConfig();
  const update = useUpdateMcpAdminConfig();
  const tokens = useAdminApiTokens(cfg.isSuccess);
  const revoke = useAdminRevokeToken();

  if (!cfg.isSuccess || !cfg.data) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP administration</CardTitle>
        <CardDescription>Platform-wide controls. Only administrators see this.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between rounded-md border border-border p-3">
          <div className="space-y-0.5">
            <p className="text-sm font-medium">MCP enabled</p>
            <p className="text-xs text-muted-foreground">
              Live toggle (no restart). {cfg.data.boot_enabled ? "" : "Also set MCP_ENABLED=1 to mount it."}
            </p>
          </div>
          <Switch
            checked={cfg.data.enabled}
            disabled={!cfg.data.boot_enabled || update.isPending}
            onCheckedChange={(v) =>
              update
                .mutateAsync({ enabled: v })
                .then(() => toast.success(`MCP ${v ? "enabled" : "disabled"}.`))
                .catch((e) => toast.error(errMessage(e)))
            }
          />
        </div>

        <div className="flex items-center justify-between rounded-md border border-border p-3">
          <div className="space-y-0.5">
            <p className="text-sm font-medium">Read-only mode</p>
            <p className="text-xs text-muted-foreground">
              Blocks all MCP write tools platform-wide. Live toggle (no restart).
              {cfg.data.boot_read_only &&
                " Currently forced on by MCP_READ_ONLY (set by env, needs restart to lift)."}
            </p>
          </div>
          <Switch
            checked={cfg.data.boot_read_only || cfg.data.read_only}
            disabled={cfg.data.boot_read_only || update.isPending}
            onCheckedChange={(v) =>
              update
                .mutateAsync({ read_only: v })
                .then(() => toast.success(`MCP is now ${v ? "read-only" : "read-write"}.`))
                .catch((e) => toast.error(errMessage(e)))
            }
          />
        </div>

        <div className="flex items-center justify-between">
          <Label>Who can create tokens</Label>
          <Select
            value={cfg.data.mint_policy}
            onValueChange={(v) =>
              update
                .mutateAsync({ mint_policy: v })
                .then(() => toast.success("Mint policy updated."))
                .catch((e) => toast.error(errMessage(e)))
            }
          >
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all_users">All users</SelectItem>
              <SelectItem value="admin_only">Admins only</SelectItem>
              <SelectItem value="disabled">Nobody</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-start justify-between gap-4">
          <div className="space-y-0.5">
            <Label>Toolsets</Label>
            <p className="text-xs text-muted-foreground">
              Registered at boot — configure via the MCP_TOOLSETS env variable (needs restart).
            </p>
          </div>
          <div className="flex max-w-[50%] flex-wrap justify-end gap-1">
            {cfg.data.toolsets.length > 0 ? (
              cfg.data.toolsets.map((t) => (
                <Badge
                  key={t}
                  variant="secondary"
                  className="px-1.5 font-mono text-[10px] font-normal"
                >
                  {t}
                </Badge>
              ))
            ) : (
              <span className="text-xs text-muted-foreground">none</span>
            )}
          </div>
        </div>

        <Separator />

        <p className="text-sm font-medium">All tokens</p>
        {tokens.data && tokens.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tokens.data.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="text-xs">{t.user_email ?? t.user_id}</TableCell>
                  <TableCell className="font-medium">{t.name}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmt(t.last_used_at)}</TableCell>
                  <TableCell>
                    {t.revoked ? <Badge variant="outline">revoked</Badge> : <Badge>active</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    {!t.revoked && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={revoke.isPending}
                        onClick={() =>
                          revoke
                            .mutateAsync(t.id)
                            .then(() => toast.success("Token revoked."))
                            .catch((e) => toast.error(errMessage(e)))
                        }
                      >
                        Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">No tokens.</p>
        )}
      </CardContent>
    </Card>
  );
}
