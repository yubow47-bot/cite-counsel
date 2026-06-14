"use client"

import { useState } from "react"
import { Check, Copy, PencilLine, ThumbsDown, ThumbsUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { postFeedback, type Citation } from "@/lib/citation-api"
import { copyCitation } from "@/lib/clipboard"

/** Convert markdown *italic* to <em> tags (the only markup the citation engine emits). */
function renderCitation(text: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc(text).replace(/\*(.+?)\*/g, "<em>$1</em>");
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

  async function handleCopy() {
    await copyCitation(item.citation)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function handleVote(next: "up" | "down") {
    setVote(next)
    void postFeedback({ citation: item.citation, vote: next, input: sourceInput })
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <p
          className="flex-1 font-mono text-sm leading-relaxed text-card-foreground break-words [&_em]:italic"
          dangerouslySetInnerHTML={{ __html: renderCitation(item.citation) }}
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

      <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
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
