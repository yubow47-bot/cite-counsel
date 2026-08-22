"use client"

import { useState, useRef, useEffect } from "react"
import { Check, Copy, PencilLine, Plus, ThumbsDown, ThumbsUp, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { postFeedback, type Citation } from "@/lib/citation-api"
import { copyCitation } from "@/lib/clipboard"

/** Pinpoint patterns that indicate a citation may already contain a locator. */
const PINPOINT_PATTERNS = [
  /\bat\s+para(s)?\b/i,
  /\bs\s*\d/i,
  /\bss\s*\d/i,
  /\bart\b/i,
  /\bat\s+\d+\b/,
  /\bat\s+ch\b/i,
]

/**
 * Check if the citation already contains a pinpoint pattern.
 * If yes, warn the user instead of blindly concatenating.
 * Detection is advisory-only — never rewrites the base citation.
 */
function hasExistingPinpoint(text: string): boolean {
  return PINPOINT_PATTERNS.some((re) => re.test(text))
}

/** Convert markdown *italic* to <em> tags (the only markup the citation engine emits). */
function renderCitation(text: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc(text).replace(/\*(.+?)\*/g, "<em>$1</em>");
}

/**
 * Pinpoint placeholder dictionary — hardcoded examples per source type.
 * No normalization applied (不做归一化).
 */
function getPinpointPlaceholder(sourceType?: string): string {
  const st = (sourceType ?? "").toLowerCase()

  // case — e.g. para 47 or paras 47–49
  if (
    st === "case" ||
    st === "case_name" ||
    st === "citation_number" ||
    st === "concept" ||
    st.startsWith("juris")
  ) {
    return "e.g. at para 47 or at paras 47–49"
  }

  // bill — e.g. cl 15(1)(a) or cl 5
  if (st === "bill") {
    return "e.g. cl 15(1)(a) or cl 5"
  }

  // statute / regulation — e.g. s 7(2)(c) or ss 1–3
  if (
    st === "legislation" ||
    st === "statute" ||
    st === "regulation" ||
    st.startsWith("leg.")
  ) {
    return "e.g. s 7(2)(c) or ss 1–3"
  }

  // treaty — e.g. art 14 or art 14(2)
  if (st === "treaty" || st === "foreign") {
    return "e.g. art 14 or art 14(2)"
  }

  // book / journal — e.g. 47 or ch 7
  if (
    st === "book" ||
    st === "journal_article" ||
    st === "book_chapter" ||
    st === "thesis" ||
    st.startsWith("secondary_sources.")
  ) {
    return "e.g. at 47 or at ch 7"
  }

  // webpage — e.g. heading or para 6 (not official)
  if (
    st === "website" ||
    st === "webpage" ||
    st === "newspaper" ||
    st === "news_online" ||
    st === "report" ||
    st === "government_document" ||
    st === "government_docs" ||
    st.startsWith("gov.")
  ) {
    return "e.g. at para 6 or heading (not official)"
  }

  // unknown / low confidence
  return "e.g. at para / s / art / at page"
}

export function CitationCard({
  item,
  sourceInput,
}: {
  item: Citation
  sourceInput: string
}) {
  const [copied, setCopied] = useState(false)
  const [vote, setVote] = useState<"up" | "down" | null>(null)
  const [pinpointOpen, setPinpointOpen] = useState(false)
  const [pinpointText, setPinpointText] = useState("")
  const prefillAppliedForQueryRef = useRef<string | null>(null)

  // State-lock prefill: populate the pinpoint input from the API response on first receipt
  // for each unique query only.  Within the same query (same sourceInput), once applied or
  // the user clears the field, subsequent re-renders or identical props re-passes will NOT
  // re-populate the value.  When a new query produces a new API response (different
  // sourceInput), the lock resets so the new pinpoint prefills once.
  useEffect(() => {
    if (item.pinpoint && prefillAppliedForQueryRef.current !== sourceInput) {
      setPinpointText(item.pinpoint)
      setPinpointOpen(true)
      prefillAppliedForQueryRef.current = sourceInput
    }
  }, [item.pinpoint, sourceInput])

  const placeholder = getPinpointPlaceholder(item.source_type)

  /**
   * Advisory check: does the base citation look like it already has a pinpoint?
   * If yes, warn — but still let the user choose to add one.
   */
  const citationHasPinpoint = item.pinpoint ? false : hasExistingPinpoint(item.citation)

  /**
   * Build the full citation text with pinpoint concatenated (if any).
   *
   * Pinpoint lives ONLY in client-side local state:
   *   - NEVER written to extracted_fields
   *   - NEVER passed to format_citation or LLM prompt
   *   - Synthesised here at render/copy time
   *
   * Concatenation rule (two lines of real logic):
   *   Strip trailing period → add space + pinpoint → re-add period.
   *   Example: R v Jordan, 2016 SCC 27. + at para 47
   *          → R v Jordan, 2016 SCC 27 at para 47.
   */
  const fullCitation = pinpointText.trim()
    ? item.citation.replace(/\.\s*$/, "") + " " + pinpointText.trim() + "."
    : item.citation

  const hasPinpoint = pinpointText.trim().length > 0

  async function handleCopy() {
    await copyCitation(fullCitation)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function handleVote(next: "up" | "down") {
    setVote(next)
    void postFeedback({ verdict: next, input: sourceInput, output: item.citation })
  }

  function clearPinpoint() {
    setPinpointText("")
    setPinpointOpen(false)
  }

  return (
    <div className="rounded-xl border border-border/60 bg-card p-5 shadow-[0_1px_4px_0_rgb(0_0_0_/_0.04)]">
      <div className="flex items-start justify-between gap-3">
        <p
          className="flex-1 font-mono text-sm leading-relaxed text-card-foreground break-words [&_em]:italic"
          dangerouslySetInnerHTML={{ __html: renderCitation(fullCitation) }}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleCopy}
          className="shrink-0 gap-1.5"
          aria-label="Copy citation"
        >
          {copied ? (
            <>
              <Check className="size-3.5" aria-hidden="true" />
              Copied
            </>
          ) : (
            <>
              <Copy className="size-3.5" aria-hidden="true" />
              Copy
            </>
          )}
        </Button>
      </div>

      {item.source_case ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Source case: {item.source_case}
        </p>
      ) : null}

      {item.verified === false ? (
        <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <PencilLine className="size-3.5 shrink-0" aria-hidden="true" />
          Manually entered — not database-verified
        </p>
      ) : null}

      {/* ── Pinpoint section (client-side only, never grounded) ── */}
      {citationHasPinpoint && !pinpointOpen ? (
        <p className="mt-3 text-xs text-amber-600 dark:text-amber-400">
          This citation may already contain a pinpoint — verify before adding another.
        </p>
      ) : null}
      <div className="mt-3">
        {!pinpointOpen ? (
          <button
            type="button"
            onClick={() => setPinpointOpen(true)}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
          >
            <Plus className="size-3" aria-hidden="true" />
            Add pinpoint
            <span className="text-muted-foreground/60">(optional)</span>
          </button>
        ) : (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <Input
                value={pinpointText}
                onChange={(e) => setPinpointText(e.target.value)}
                placeholder={placeholder}
                className="h-8 bg-background font-mono text-xs border-border/60"
                aria-label="Pinpoint reference"
              />
              <button
                type="button"
                onClick={clearPinpoint}
                className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                aria-label="Remove pinpoint"
              >
                <X className="size-3.5" />
              </button>
            </div>
            {citationHasPinpoint ? (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">
                This citation may already contain a pinpoint
              </p>
            ) : null}
            {hasPinpoint ? (
              <p className="text-[11px] text-muted-foreground/60">
                Pinpoint is user-provided and not database-verified
              </p>
            ) : null}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center gap-2 border-t border-border/60 pt-4">
        <span className="text-xs text-muted-foreground">Is this citation accurate?</span>
        <Button
          type="button"
          variant={vote === "up" ? "default" : "ghost"}
          size="sm"
          onClick={() => handleVote("up")}
          className="size-8 p-0"
          aria-pressed={vote === "up"}
          aria-label="Mark as accurate"
        >
          <ThumbsUp className="size-4" aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant={vote === "down" ? "destructive" : "ghost"}
          size="sm"
          onClick={() => handleVote("down")}
          className="size-8 p-0"
          aria-pressed={vote === "down"}
          aria-label="Mark as incorrect"
        >
          <ThumbsDown className="size-4" aria-hidden="true" />
        </Button>
        {vote ? (
          <span className="text-xs text-muted-foreground">Thanks for the feedback</span>
        ) : null}
      </div>
    </div>
  )
}
