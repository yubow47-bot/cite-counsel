"use client"

import { AlertCircle, Info } from "lucide-react"
import { CitationCard } from "@/components/citation-card"
import type { Citation } from "@/lib/citation-api"

type ExtractView =
  | { kind: "idle" }
  | { kind: "done"; citations: Citation[] }
  | { kind: "unsupported"; reason: string }
  | { kind: "error"; reason: string }

export function ExtractResults({
  view,
  sourceInput,
}: {
  view: ExtractView
  sourceInput: string
}) {
  if (view.kind === "done") {
    return (
      <section aria-label="Extracted results" className="flex flex-col gap-3">
        {view.citations.length === 0 ? (
          <div className="rounded-xl border border-border/60 bg-muted/50 px-5 py-6 text-sm text-muted-foreground">
            No citations were extracted from this source. Make sure it contains a recognizable legal source.
          </div>
        ) : (
          view.citations.map((item, i) => (
            <CitationCard key={i} item={item} sourceInput={sourceInput} />
          ))
        )}
      </section>
    )
  }

  if (view.kind === "unsupported") {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/50 px-5 py-4 text-sm text-foreground">
        <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <p className="font-medium">This source type is not yet supported</p>
          <p className="mt-1 text-muted-foreground">{view.reason}</p>
        </div>
      </div>
    )
  }

  if (view.kind === "error") {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/[0.04] px-5 py-4 text-sm">
        <AlertCircle
          className="mt-0.5 size-4 shrink-0 text-destructive"
          aria-hidden="true"
        />
        <div>
          <p className="font-medium text-destructive">Extraction failed</p>
          <p className="mt-1 text-muted-foreground">{view.reason}</p>
        </div>
      </div>
    )
  }

  return null
}

export type { ExtractView }
