"use client";

import { GraduationCap, Sparkles, Zap } from "lucide-react";

import { cn } from "@/lib/cn";
import { MateLogo } from "@/components/mate-logo";
import { useOnboarding, type ExperienceLevel } from "@/lib/stores/onboarding";

const LEVELS: {
  value: ExperienceLevel;
  title: string;
  description: string;
  icon: typeof GraduationCap;
}[] = [
  {
    value: "beginner",
    title: "Beginner",
    description: "New to process mining",
    icon: GraduationCap,
  },
  {
    value: "intermediate",
    title: "Intermediate",
    description: "Familiar with event logs and basic analysis",
    icon: Sparkles,
  },
  {
    value: "expert",
    title: "Expert",
    description: "Advanced process mining and conformance checking",
    icon: Zap,
  },
];

export function WelcomeStep() {
  const experienceLevel = useOnboarding((s) => s.experienceLevel);
  const setExperienceLevel = useOnboarding((s) => s.setExperienceLevel);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
      <div className="space-y-2 text-center">
        <MateLogo className="mx-auto h-12 w-12 text-foreground" />
        <h1 className="text-3xl font-semibold tracking-tight">Welcome to PM-MATE</h1>
        <p className="text-sm text-muted-foreground">
          Tell us a bit about your background so we can tailor the experience.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {LEVELS.map(({ value, title, description, icon: Icon }) => {
          const selected = experienceLevel === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setExperienceLevel(value)}
              className={cn(
                "flex cursor-pointer flex-col items-start gap-3 rounded-xl border bg-surface p-4 text-left transition-all",
                selected
                  ? "border-primary ring-2 ring-primary/30"
                  : "border-border hover:border-primary/40 hover:bg-accent/40",
              )}
            >
              <div
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-full",
                  selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-semibold">{title}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">{description}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
