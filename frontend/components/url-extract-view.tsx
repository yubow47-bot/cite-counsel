"use client"

import { useState } from "react"
import { AlertCircle, Link2, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScaffoldForm } from "@/components/scaffold-form"
import {
  ExtractResults,
  type ExtractView,
} from "@/components/extract-results"
import { postExtractUrl, type Envelope } from "@/lib/citation-api"

export function UrlExtractView() {
  const [url, setUrl] = useState("")
  const [submittedUrl, setSubmittedUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<ExtractView>({ kind: "idle" })
  const [scaffold, setScaffold] = useState<{
    initialType?: string
    prefill?: Record<string, string>
    message?: string
  } | null>(null)

  function applyEnvelope(env: Envelope) {
    setScaffold(null)
    switch (env.status) {
      case "done":
        setView({ kind: "done", citations: env.data.citations ?? [] })
        break
      case "needs_input":
        setView({ kind: "idle" })
        setScaffold({
          initialType: env.data.type,
          prefill: env.data.prefill,
          message: env.data.message ?? env.data.reason,
        })
        break
      case "unsupported":
        setView({
          kind: "unsupported",
          reason: env.data.reason ?? "This URL source is not yet supported.",
        })
        break
      case "error":
      default:
        setView({
          kind: "error",
          reason: env.error?.reason ?? "An unknown error occurred during extraction. Please try again later.",
        })
        break
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed || loading) return

    setLoading(true)
    setSubmittedUrl(trimmed)
    setView({ kind: "idle" })
    try {
      const env = await postExtractUrl(trimmed)
      applyEnvelope(env)
    } catch (err) {
      setView({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Could not access this URL. Please check the link and try again.",
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label htmlFor="url-input" className="sr-only">
          Enter the URL of the page to extract citations from
        </label>
        <Input
          id="url-input"
          type="url"
          inputMode="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.canlii.org/en/ca/scc/doc/1986/..."
          disabled={loading}
          className="bg-card font-mono text-sm"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Supports case law databases, legislation pages, and other public web pages
          </p>
          <Button type="submit" disabled={loading || url.trim().length === 0} className="gap-2">
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Extracting…
              </>
            ) : (
              <>
                <Link2 className="size-4" aria-hidden="true" />
                Extract Citation
              </>
            )}
          </Button>
        </div>
      </form>

      {loading ? (
        <div
          className="flex items-center gap-3 rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
          Fetching page and extracting citations. Please wait…
        </div>
      ) : scaffold ? (
        <div className="flex flex-col gap-4">
          {scaffold.message ? (
            <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/40 px-4 py-4 text-sm">
              <AlertCircle className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              <p className="text-muted-foreground">{scaffold.message}</p>
            </div>
          ) : null}
          <ScaffoldForm
            initialType={scaffold.initialType}
            prefill={scaffold.prefill}
          />
        </div>
      ) : (
        <ExtractResults view={view} sourceInput={submittedUrl} />
      )}
    </div>
  )
}
