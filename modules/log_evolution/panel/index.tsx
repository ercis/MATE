"use client";

import { useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import ActiveCases from "../widgets/ActiveCases";
import ActivityMix from "../widgets/ActivityMix";
import ArrivalsCompletions from "../widgets/ArrivalsCompletions";
import DottedChart from "../widgets/DottedChart";
import { type Granularity } from "./queries";

const GRANULARITIES: { value: Granularity; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

const TOP_N = [4, 6, 8, 10, 12];
const MAX_POINTS = [2000, 4000, 8000, 12000, 20000];

export default function LogEvolutionPanel({ logId }: { logId: string; moduleId: string }) {
  const [granularity, setGranularity] = useState<Granularity>("auto");
  const [topN, setTopN] = useState(8);
  const [maxPoints, setMaxPoints] = useState(8000);

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-4">
          <Field label="Granularity">
            <Select value={granularity} onValueChange={(v) => setGranularity(v as Granularity)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GRANULARITIES.map((g) => (
                  <SelectItem key={g.value} value={g.value}>
                    {g.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Activities shown">
            <Select value={String(topN)} onValueChange={(v) => setTopN(Number(v))}>
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TOP_N.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Dotted-chart points">
            <Select value={String(maxPoints)} onValueChange={(v) => setMaxPoints(Number(v))}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MAX_POINTS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n.toLocaleString()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Section title="Arrivals & completions">
          <ArrivalsCompletions logId={logId} config={{ granularity }} />
        </Section>
        <Section title="Active cases (work in progress)">
          <ActiveCases logId={logId} config={{ granularity }} />
        </Section>
        <Section title="Activity mix over time">
          <ActivityMix logId={logId} config={{ granularity, top_n: topN }} />
        </Section>
        <Section title="Dotted chart">
          <DottedChart logId={logId} config={{ max_points: maxPoints }} />
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="space-y-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <div className="h-80">{children}</div>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}
