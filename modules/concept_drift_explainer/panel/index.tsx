"use client";

import { useMemo, useState } from "react";
import { Brain, Play, Sparkles } from "lucide-react";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";

import { ChatPanel } from "./chat-panel";
import { DocumentManager } from "./document-manager";
import { DriftTable, DriftTypeLegend } from "./drift-table";
import { ExplanationView } from "./explanation-view";
import {
  useCdeDocuments,
  useCdeDrifts,
  useCdeExplanations,
  useRunExplain,
} from "./queries";

export function ConceptDriftExplainerPanel({
  logId,
}: {
  logId: string;
  moduleId: string;
}) {
  const driftsQ = useCdeDrifts(logId);
  const docsQ = useCdeDocuments(logId);
  const explanationsQ = useCdeExplanations(logId);
  const runExplain = useRunExplain(logId);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const drifts = driftsQ.data?.drifts ?? [];
  const documents = docsQ.data?.documents ?? [];
  const hasIndexable = documents.some((d) => d.indexable);
  const explanations = explanationsQ.data?.explanations ?? {};
  const selectedStored = selectedKey ? explanations[selectedKey] : undefined;

  const effectiveKey = useMemo(() => {
    if (selectedKey && drifts.some((d) => d.drift_key === selectedKey)) {
      return selectedKey;
    }
    return drifts[0]?.drift_key ?? null;
  }, [selectedKey, drifts]);

  const chatHistory = effectiveKey
    ? // The chat history is stored separately under `chat_{drift_key}`. We
      // don't have a dedicated query for it yet because /chat returns the
      // updated history directly, but on first load we re-derive from cache
      // if the user has chatted before; falling back to [] is fine for MVP.
      []
    : [];

  if (driftsQ.isLoading) {
    return <Skeleton className="h-96 w-full" />;
  }

  if (!driftsQ.data?.ran && drifts.length === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="No drifts to explain yet"
        description="The CDE explains drifts detected by the CV4CDD module. Run CV4CDD first – its auto-detection job kicks off on log import."
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Detected drifts</CardTitle>
            <DriftTypeLegend drifts={drifts} />
          </CardHeader>
          <CardContent className="space-y-3">
            <DriftTable
              drifts={drifts}
              selectedKey={effectiveKey}
              onSelect={setSelectedKey}
            />
            <Button
              size="sm"
              onClick={() =>
                effectiveKey &&
                runExplain.mutate({ drift_key: effectiveKey })
              }
              disabled={
                !effectiveKey || !hasIndexable || runExplain.isPending
              }
            >
              <Play className="mr-1.5 h-3.5 w-3.5" />
              {runExplain.isPending ? "Running…" : "Run analysis"}
            </Button>
            {!hasIndexable && (
              <p className="text-[11px] text-muted-foreground">
                Upload and re-index at least one dated document before running.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Context documents</CardTitle>
          </CardHeader>
          <CardContent>
            <DocumentManager logId={logId} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <Brain className="h-4 w-4 text-muted-foreground" />
          <CardTitle className="text-base">Explanation</CardTitle>
        </CardHeader>
        <CardContent>
          <ExplanationView stored={selectedStored} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Follow-up chat</CardTitle>
        </CardHeader>
        <CardContent>
          <ChatPanel
            logId={logId}
            driftKey={effectiveKey}
            initialHistory={chatHistory}
          />
        </CardContent>
      </Card>
    </div>
  );
}

export default ConceptDriftExplainerPanel;
