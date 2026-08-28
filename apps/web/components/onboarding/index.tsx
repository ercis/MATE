"use client";

import { useState } from "react";
import { useProgressRouter } from "@/lib/use-progress-router";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useOnboarding } from "@/lib/stores/onboarding";
import { useOnboardingState, useUpdateOnboarding } from "@/lib/onboarding-queries";
import { useTour } from "@/lib/stores/tour";
import { useAnalytics } from "@/lib/stores/analytics";
import { useUpdateAnalyticsConfig, useAnalyticsConfig } from "@/lib/analytics-queries";

import { WelcomeStep } from "./steps/welcome-step";
import { PrivacyStep } from "./steps/privacy-step";
import { UploadStep } from "./steps/upload-step";
import { AnalysisStep } from "./steps/analysis-step";

type StepKey = "welcome" | "privacy" | "upload" | "analysis";

export function OnboardingOverlay() {
  const router = useProgressRouter();
  const onboardingQuery = useOnboardingState();
  const updateOnboarding = useUpdateOnboarding();
  const experienceLevel = useOnboarding((s) => s.experienceLevel);
  const promptResolved = useAnalytics((s) => s.promptResolved);
  const resolvePrompt = useAnalytics((s) => s.resolvePrompt);
  const cfgQuery = useAnalyticsConfig();
  const updateMut = useUpdateAnalyticsConfig();
  const startTour = useTour((s) => s.start);

  const [step, setStep] = useState(0);
  const [uploadedLogId, setUploadedLogId] = useState<string | null>(null);

  // Wait for the server's per-user answer before deciding – never flash the
  // overlay for a user who already finished it.
  if (onboardingQuery.isLoading || !onboardingQuery.data) return null;
  if (onboardingQuery.data.completed) return null;
  // Hold until the tracking config is settled so the step list (which depends
  // on the mode) is stable before the user can navigate. On error, fall back
  // to showing the privacy step rather than silently hiding the choice.
  if (cfgQuery.data === undefined && !cfgQuery.isError) return null;

  const mode = cfgQuery.data?.onboarding_mode ?? "on";
  // `force` removes the privacy step entirely – tracking is mandated and the
  // choice is never presented.
  const steps: StepKey[] =
    mode === "force"
      ? ["welcome", "upload", "analysis"]
      : ["welcome", "privacy", "upload", "analysis"];

  const current = steps[Math.min(step, steps.length - 1)];
  const isLast = step === steps.length - 1;
  const canGoBack = step > 0;

  const ensurePrivacyDefault = () => {
    // The privacy step pre-selects a choice based on the mode (opt-in for
    // `on`/`force`, opt-out for `off`). Finishing or skipping without an
    // explicit click persists that default to the server.
    if (promptResolved) return;
    const optIn = mode !== "off";
    resolvePrompt(optIn);
    const cfg = cfgQuery.data;
    if (cfg && cfg.enabled !== optIn) {
      updateMut.mutate({
        ...cfg,
        enabled: optIn,
        opted_in_at: optIn ? new Date().toISOString() : null,
      });
    }
  };

  const finish = () => {
    ensurePrivacyDefault();
    // Persist completion per-user so it never re-shows for this account.
    updateOnboarding.mutate({ completed: true, experience_level: experienceLevel });
    // Chain straight into the interactive product tour the first time the wizard
    // is finished (skippable, and replayable later from Settings → About). Gated
    // on the per-user flag so it can never re-trigger on a later session.
    //
    // The tour owns the navigation from here: its first step routes to
    // /processes, and `logId` lets it spotlight the log this wizard just queued
    // even though it is still importing (a brand-new user has no `ready` log to
    // fall back on). Only when the tour won't run do we route by hand.
    if (!onboardingQuery.data?.tour_completed) {
      startTour({ auto: true, logId: uploadedLogId });
    } else if (uploadedLogId) {
      router.push(`/processes/${uploadedLogId}`);
    }
  };

  const onNext = () => {
    if (isLast) {
      finish();
    } else {
      setStep((s) => s + 1);
    }
  };

  const onBack = () => {
    if (canGoBack) setStep((s) => s - 1);
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex justify-center pt-10">
        <StepIndicator current={step} total={steps.length} />
      </div>

      <div className="flex flex-1 items-center justify-center overflow-y-auto px-6 py-8">
        {current === "welcome" && <WelcomeStep />}
        {current === "privacy" && <PrivacyStep />}
        {current === "upload" && (
          <UploadStep uploadedLogId={uploadedLogId} onUploaded={setUploadedLogId} />
        )}
        {current === "analysis" && <AnalysisStep />}
      </div>

      <div className="border-t border-border bg-background">
        <div className="mx-auto flex w-full max-w-2xl items-center justify-between gap-3 px-6 py-4">
          <div>
            {canGoBack && (
              <Button
                variant="ghost"
                onClick={onBack}
                className="cursor-pointer gap-1.5"
              >
                <ArrowLeft className="h-4 w-4" />
                Back
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={onNext}
              className="cursor-pointer gap-1.5"
            >
              {isLast ? (
                <>
                  Finish
                  <Check className="h-4 w-4" />
                </>
              ) : (
                <>
                  Next
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function StepIndicator({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-2">
      {Array.from({ length: total }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "h-2 rounded-full transition-all",
            i === current ? "w-8 bg-primary" : "w-2 bg-muted",
            i < current && "bg-primary/50",
          )}
          aria-label={`Step ${i + 1}${i === current ? " (current)" : ""}`}
        />
      ))}
    </div>
  );
}
