"use client";

import { useTheme } from "next-themes";

import { useMounted } from "@/lib/use-mounted";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import {
  RadioGroup,
  RadioGroupItem,
} from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useUi } from "@/lib/stores/ui";
import { StorageGauge } from "@/components/settings/storage-gauge";
import { useOnboardingState, useUpdateOnboarding } from "@/lib/onboarding-queries";
import type { ExperienceLevel } from "@/lib/stores/onboarding";

export default function GeneralSettingsPage() {
  const { theme = "system", setTheme } = useTheme();
  // The user's stored theme is client-only; until mounted, render the "system"
  // default so the RadioGroup's checked state matches SSR (hydration #418).
  const mounted = useMounted();
  const muted = useUi((s) => s.notificationsMuted);
  const setMuted = useUi((s) => s.setNotificationsMuted);
  const confidentialOnly = useUi((s) => s.confidentialOnly);
  const setConfidentialOnly = useUi((s) => s.setConfidentialOnly);
  const timezone = useUi((s) => s.timezone);
  const setTimezone = useUi((s) => s.setTimezone);
  const dateFormat = useUi((s) => s.dateFormat);
  const setDateFormat = useUi((s) => s.setDateFormat);
  const csvDelimiter = useUi((s) => s.csvDelimiter);
  const setCsvDelimiter = useUi((s) => s.setCsvDelimiter);
  const csvTimestampFormat = useUi((s) => s.csvTimestampFormat);
  const setCsvTimestampFormat = useUi((s) => s.setCsvTimestampFormat);
  const backButtonMode = useUi((s) => s.backButtonMode);
  const setBackButtonMode = useUi((s) => s.setBackButtonMode);

  const onboardingQuery = useOnboardingState();
  const updateOnboarding = useUpdateOnboarding();
  const experienceLevel = onboardingQuery.data?.experience_level ?? null;
  const setExperienceLevel = (level: ExperienceLevel) => {
    if (!onboardingQuery.data) return;
    updateOnboarding.mutate({
      // Preserve the completed flag – this only re-tunes the proficiency the
      // welcome flow captured, it doesn't re-open onboarding.
      completed: onboardingQuery.data.completed,
      experience_level: level,
    });
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Appearance</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label>Theme</Label>
            <RadioGroup
              value={mounted ? theme : "system"}
              onValueChange={setTheme}
              className="flex gap-3"
            >
              {(["light", "dark", "system"] as const).map((t) => (
                <Label
                  key={t}
                  className="flex cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm capitalize has-[input:checked]:border-primary"
                >
                  <RadioGroupItem value={t} />
                  {t}
                </Label>
              ))}
            </RadioGroup>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Navigation</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label className="flex items-center justify-between gap-3">
            <span className="space-y-0.5">
              <span className="block text-sm">Back button goes to parent page</span>
              <span className="block text-xs text-muted-foreground">
                When on, the topbar back arrow goes up one level in the page
                hierarchy. When off, it returns to the last page you visited.
              </span>
            </span>
            <Switch
              checked={backButtonMode === "parent"}
              onCheckedChange={(v) => setBackButtonMode(v ? "parent" : "history")}
              className="cursor-pointer"
            />
          </Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Notifications</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label className="flex items-center justify-between gap-3">
            <span className="space-y-0.5">
              <span className="block text-sm">Hide success notifications</span>
              <span className="block text-xs text-muted-foreground">
                When on, success and info popups are hidden. Error messages are
                always shown.
              </span>
            </span>
            <Switch checked={muted} onCheckedChange={setMuted} className="cursor-pointer" />
          </Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Process proficiency</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="proficiency">Experience level</Label>
            <p className="text-xs text-muted-foreground">
              Your process-mining experience, captured during onboarding. Used
              to tailor the experience.
            </p>
            <Select
              value={experienceLevel ?? ""}
              onValueChange={(v) => setExperienceLevel(v as ExperienceLevel)}
              disabled={!onboardingQuery.data || updateOnboarding.isPending}
            >
              <SelectTrigger id="proficiency" className="w-56">
                <SelectValue placeholder="Not set" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="beginner">Beginner</SelectItem>
                <SelectItem value="intermediate">Intermediate</SelectItem>
                <SelectItem value="expert">Expert</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Modules</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label className="flex items-center justify-between gap-3">
            <span className="space-y-0.5">
              <span className="block text-sm">Show only confidential modules</span>
              <span className="block text-xs text-muted-foreground">
                When on, only modules that declare <code className="rounded bg-muted px-1">isConfidentialSafe: true</code> in
                their manifest are available. Modules that may ship data to an
                external service are hidden.
              </span>
            </span>
            <Switch
              checked={confidentialOnly}
              onCheckedChange={setConfidentialOnly}
              className="cursor-pointer"
            />
          </Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Location &amp; imports</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="timezone">Timezone</Label>
              <Input
                id="timezone"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="UTC"
              />
              <p className="text-[11px] text-muted-foreground">IANA name, e.g. <code className="rounded bg-muted px-1">Europe/Zurich</code>.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="date-format">Date format</Label>
              <Select value={dateFormat} onValueChange={(v) => setDateFormat(v as "iso" | "us" | "eu")}>
                <SelectTrigger id="date-format">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="iso">ISO - 2026-05-20</SelectItem>
                  <SelectItem value="us">US - 05/20/2026</SelectItem>
                  <SelectItem value="eu">EU - 20/05/2026</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="csv-delim">CSV delimiter (import default)</Label>
              <Select value={csvDelimiter} onValueChange={(v) => setCsvDelimiter(v as "," | ";" | "\t" | "|")}>
                <SelectTrigger id="csv-delim">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value=",">Comma (,)</SelectItem>
                  <SelectItem value=";">Semicolon (;)</SelectItem>
                  <SelectItem value={"\t"}>Tab</SelectItem>
                  <SelectItem value="|">Pipe (|)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="csv-ts">CSV timestamp format (import default)</Label>
              <Input
                id="csv-ts"
                value={csvTimestampFormat}
                onChange={(e) => setCsvTimestampFormat(e.target.value)}
                placeholder="(blank - auto-detect)"
              />
              <p className="text-[11px] text-muted-foreground">strftime pattern; leave blank to let pandas guess.</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Data &amp; storage</CardTitle>
        </CardHeader>
        <CardContent>
          <StorageGauge />
        </CardContent>
      </Card>
    </div>
  );
}
