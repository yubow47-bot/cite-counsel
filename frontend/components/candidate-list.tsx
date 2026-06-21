"use client"

import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { Candidate } from "@/lib/citation-api"

export function CandidateList({
  candidates,
  onSelect,
  selectingIndex,
}: {
  candidates: Candidate[]
  onSelect: (index: number) => void
  selectingIndex: number | null
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-5 shadow-[0_1px_4px_0_rgb(0_0_0_/_0.04)]">
      <h2 className="font-serif text-lg font-semibold text-card-foreground">
        Multiple matches found
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Select the source that best matches your citation:
      </p>
      <ul className="mt-4 flex flex-col gap-2">
        {candidates.map((candidate, index) => {
          const busy = selectingIndex === index
          const disabled = selectingIndex !== null
          return (
            <li key={index}>
              <button
                type="button"
                onClick={() => onSelect(index)}
                disabled={disabled}
                className="flex w-full items-center justify-between gap-3 rounded-md border border-border bg-background px-4 py-3 text-left text-sm text-foreground transition-colors hover:border-primary hover:bg-accent disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="leading-relaxed">{candidate.display}</span>
                {busy ? (
                  <Loader2
                    className="size-4 shrink-0 animate-spin text-primary"
                    aria-hidden="true"
                  />
                ) : null}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
