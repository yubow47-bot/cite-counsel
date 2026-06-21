"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { postFeedbackMessage } from "@/lib/citation-api"

export function FeedbackBox() {
  const [note, setNote] = useState("")
  const [status, setStatus] = useState<"idle" | "sent">("idle")

  async function handleSubmit() {
    const trimmed = note.trim()
    if (!trimmed) return
    await postFeedbackMessage(trimmed)
    setNote("")
    setStatus("sent")
    setTimeout(() => setStatus("idle"), 3000)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="flex items-center gap-3">
      <input
        type="text"
        value={status === "sent" ? "" : note}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Found something wrong or missing? Let us know! Every piece of feedback helps improve future results."
        disabled={status === "sent"}
        className="flex-1 h-9 rounded-lg border border-border/60 bg-background px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
      />
      {status === "sent" ? (
        <span className="shrink-0 text-sm text-muted-foreground">Thanks 🙏</span>
      ) : (
        <Button
          type="button"
          variant="default"
          size="sm"
          onClick={handleSubmit}
          disabled={note.trim().length === 0}
          className="shrink-0"
        >
          Send
        </Button>
      )}
    </div>
  )
}
