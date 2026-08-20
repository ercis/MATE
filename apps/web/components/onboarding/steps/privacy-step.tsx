"use client";

import { BarChart3, ShieldOff } from "lucide-react";

import { cn } from "@/lib/cn";
import { useAnalytics } from "@/lib/stores/analytics";
import {
  useAnalyticsConfig,
  useUpdateAnalyticsConfig,
} from "@/lib/analytics-queries";
import { trackCustom } from "@/lib/analytics/client";
import { EV } from "@/lib/analytics/events";

const CHOICES = [
  {
    value: "in" as const,
    title: "Help improve the platform",
    description:
      "Share which pages you visit and which features you use. Nothing leaves your computer.",
    icon: BarChart3,
  },
  {
    value: "out" as const,
    title: "No usage tracking",
    description:
      "Stay completely silent. You can change your mind any time in Settings → Privacy.",
    icon: ShieldOff,
  },
];

export function PrivacyStep() {
  const promptResolved = useAnalytics((s) => s.promptResolved);
  const enabled = useAnalytics((s) => s.enabled);
  const resolvePrompt = useAnalytics((s) => s.resolvePrompt);
  const setStoreEnabled = useAnalytics((s) => s.setEnabled);
  const setAnonId = useAnalytics((s) => s.setAnonUserId);
  const cfgQuery = useAnalyticsConfig();
  const updateMut = useUpdateAnalyticsConfig();

  // `off` pre-selects opt-out; `on`/`force` pre-select opt-in. Once the user
  // has answered, their actual choice (mirrored in `enabled`) wins.
  const mode = cfgQuery.data?.onboarding_mode ?? "on";
  const defaultChoice: "in" | "out" = mode === "off" ? "out" : "in";
  const selected = !promptResolved ? defaultChoice : enabled ? "in" : "out";
  const ready = !!cfgQuery.data;

  const onChoose = (choice: "in" | "out") => {
    const cfg = cfgQuery.data;
    if (!cfg) return;
    resolvePrompt(choice === "in");
    updateMut.mutate(
      {
        ...cfg,
        enabled: choice === "in",
        opted_in_at: choice === "in" ? new Date().toISOString() : null,
      },
      {
        onSuccess: (saved) => {
          setStoreEnabled(saved.enabled);
          setAnonId(saved.anon_user_id_seed);
          if (choice === "in") {
            // Retroactive funnel: now that consent is given, log the choice.
            trackCustom(EV.ANALYTICS_OPT_IN, { source: "onboarding" });
          }
        },
      },
    );
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
      <div className="space-y-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Privacy</h1>
        <p className="text-sm text-muted-foreground">
          May we collect anonymous usage events so we can see which features
          are useful? Nothing ever leaves your machine - everything stays in
          your local database.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {CHOICES.map(({ value, title, description, icon: Icon }) => {
          const isSelected = selected === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onChoose(value)}
              disabled={!ready}
              data-track-name={
                value === "in" ? "onboarding_opt_in" : "onboarding_opt_out"
              }
              className={cn(
                "flex flex-col items-start gap-3 rounded-xl border bg-surface p-4 text-left transition-all",
                ready ? "cursor-pointer" : "cursor-wait opacity-60",
                isSelected
                  ? "border-primary ring-2 ring-primary/30"
                  : "border-border hover:border-primary/40 hover:bg-accent/40",
              )}
            >
              <div
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-full",
                  isSelected
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-semibold">{title}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {description}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <p className="text-center text-[11px] text-muted-foreground">
        We never capture form values, AI chat content, file names, or URL query
        parameters. See <code>/settings/privacy</code> for the full list and to
        export or wipe your data.
      </p>
    </div>
  );
}
