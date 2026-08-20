"use client";

import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";

import { Button } from "@/components/ui/button";

import { type CdeChatState, useSendChat } from "./queries";

export function ChatPanel({
  logId,
  driftKey,
  initialHistory,
}: {
  logId: string;
  driftKey: string | null;
  initialHistory: CdeChatState["chat_history"];
}) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<CdeChatState["chat_history"]>(initialHistory);
  const send = useSendChat(logId);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setHistory(initialHistory);
  }, [driftKey, initialHistory]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [history.length]);

  if (!driftKey) {
    return (
      <p className="rounded border border-dashed py-6 text-center text-xs text-muted-foreground">
        Run an analysis first – the chatbot answers questions about the active
        drift&apos;s evidence.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div
        ref={scrollRef}
        className="max-h-72 min-h-32 space-y-2 overflow-y-auto rounded-md border bg-muted/20 p-3"
      >
        {history.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            Ask a follow-up about the explanation – e.g. <em>Which policy
            change explains the new pre-approval step?</em>
          </p>
        ) : (
          history.map(([q, a], i) => (
            <div key={i} className="space-y-1.5">
              <div className="rounded-md bg-muted px-3 py-1.5 text-xs">
                <span className="font-semibold">You:</span> {q}
              </div>
              <div className="rounded-md bg-card px-3 py-2 text-xs leading-relaxed">
                <span className="font-semibold">Assistant:</span> {a}
              </div>
            </div>
          ))
        )}
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!question.trim() || send.isPending) return;
          const next = await send.mutateAsync({
            drift_key: driftKey,
            user_question: question.trim(),
          });
          setHistory(next.chat_history);
          setQuestion("");
        }}
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about this drift's evidence…"
          disabled={send.isPending}
          className="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <Button type="submit" size="icon" disabled={send.isPending}>
          <Send className="h-3.5 w-3.5" />
        </Button>
      </form>
    </div>
  );
}
