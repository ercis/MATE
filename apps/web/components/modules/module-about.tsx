"use client";

import { BookOpen, ExternalLink, Info, Package } from "lucide-react";

import type { ManifestArtifact, ManifestSource } from "@/lib/api-types";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/cn";

/** Hard ceiling mirroring the manifest schema (`MAX_SOURCES`/`MAX_ARTIFACTS`). */
const MAX_CREDITS = 20;

export interface ModuleAboutProps {
  name: string;
  /** Short one-liner from the manifest `description`. */
  description?: string | null;
  /** Longer "with this module you can …" text from the manifest `about`. */
  about?: string | null;
  /** Cited works (manifest `source[]`); `fullCitation` carries the authors. */
  sources?: ManifestSource[] | null;
  /** Named links (manifest `artifacts[]`) — code repo, dataset, demo, model. */
  artifacts?: ManifestArtifact[] | null;
  license?: string | null;
  version?: string | null;
  className?: string;
}

/**
 * Resolve the credit lists to render, capped at 20 each to mirror the manifest
 * schema. The manifest has no author fields and no singular shorthands, so this
 * only normalises null/undefined and enforces the ceiling.
 */
export function resolveCredits({
  sources,
  artifacts,
}: Pick<ModuleAboutProps, "sources" | "artifacts">): {
  sourceList: ManifestSource[];
  artifactList: ManifestArtifact[];
} {
  return {
    sourceList: (sources ?? []).slice(0, MAX_CREDITS),
    artifactList: (artifacts ?? []).slice(0, MAX_CREDITS),
  };
}

/**
 * "About this module" info popover for module detail headers.
 *
 * Platform-side (not module-authored): every module gets it for free from its
 * manifest fields - `description`, `about` (what-you-can-do text), `source`
 * (cited works) and `artifacts` (named links).
 */
export function ModuleAboutInfo({ name, className, ...content }: ModuleAboutProps) {
  const { description, about } = content;
  const { sourceList, artifactList } = resolveCredits(content);
  const hasBody = Boolean(about || description);
  if (!hasBody && sourceList.length === 0 && artifactList.length === 0) return null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`About ${name}`}
          className={cn("cursor-pointer text-muted-foreground hover:text-foreground", className)}
        >
          <Info className="h-3.5 w-3.5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)] space-y-3">
        <ModuleAboutContent {...content} />
      </PopoverContent>
    </Popover>
  );
}

/**
 * Body of the module "About" popover — header, what-you-can-do, cited sources,
 * artifact links, and the version · license footer. Split out so it can render
 * inside any popover shell (the in-page {@link ModuleAboutInfo} button and the
 * topbar ⓘ).
 */
export function ModuleAboutContent({
  description,
  about,
  license,
  version,
  ...credits
}: Omit<ModuleAboutProps, "name" | "className">) {
  const meta = [version ? `v${version}` : null, license].filter(Boolean).join(" · ");
  const { sourceList, artifactList } = resolveCredits(credits);

  return (
    <>
      <PopoverHeader>
        <PopoverTitle>About this module</PopoverTitle>
        {description && (
          <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
        )}
      </PopoverHeader>

      {about && (
        <div className="space-y-1">
          <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            What you can do
          </div>
          <p className="text-xs leading-relaxed text-foreground/90">{about}</p>
        </div>
      )}

      {(sourceList.length > 0 || artifactList.length > 0) && (
        <div className="space-y-2 border-t border-white/10 pt-2.5 text-xs">
          {sourceList.map((s, i) => (
            <div key={`${s.title}-${i}`} className="flex gap-1.5 text-muted-foreground">
              <BookOpen className="mt-0.5 h-3 w-3 shrink-0" />
              <div className="min-w-0 space-y-0.5">
                {s.url ? (
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-foreground/90 hover:text-foreground hover:underline"
                  >
                    {s.title}
                    <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                  </a>
                ) : (
                  <div className="font-medium text-foreground/90">{s.title}</div>
                )}
                {/* Full reference string, quotable as-is. */}
                <p className="text-[10px] leading-relaxed text-muted-foreground/80">
                  {s.fullCitation}
                </p>
              </div>
            </div>
          ))}
          {artifactList.map((a, i) => (
            <div
              key={`${a.url}-${i}`}
              className="flex items-center gap-1.5 text-muted-foreground"
            >
              <Package className="h-3 w-3 shrink-0" />
              <a
                href={a.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
              >
                {a.name}
                <ExternalLink className="h-2.5 w-2.5" />
              </a>
            </div>
          ))}
        </div>
      )}

      {meta && <div className="text-[10px] text-muted-foreground/70">{meta}</div>}
    </>
  );
}
