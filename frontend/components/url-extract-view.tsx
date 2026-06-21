"use client"

import { useState } from "react"
import { AlertCircle, FileText, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ExtractResults,
  type ExtractView,
} from "@/components/extract-results"
import { postExtractUrl, type Envelope } from "@/lib/citation-api"

const FAIL_MESSAGES: Record<string, string> = {
  url:  "URL extraction failed. Please upload a file or screenshot instead.",
  doi:  "DOI extraction failed. Please upload a file or screenshot instead.",
  isbn: "ISBN extraction failed. Please upload a file or screenshot instead.",
}

type InputKind = "url" | "doi" | "isbn"

export function UrlExtractView({ autoFocus }: { autoFocus?: boolean }) {
  const [url, setUrl] = useState("")
  const [doi, setDoi] = useState("")
  const [isbn, setIsbn] = useState("")
  const [submittedUrl, setSubmittedUrl] = useState("")
  const [submittedKind, setSubmittedKind] = useState<InputKind>("url")
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<ExtractView>({ kind: "idle" })

  function applyEnvelope(env: Envelope, kind: InputKind) {
    setSubmittedKind(kind)
    switch (env.status) {
      case "done":
        setView({ kind: "done", citations: env.data.citations ?? [] })
        break
      case "unsupported":
        setView({
          kind: "unsupported",
          reason: FAIL_MESSAGES[kind] ?? FAIL_MESSAGES.url,
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

  async function submitKind(kind: InputKind, value: string) {
    if (!value.trim() || loading) return

    setLoading(true)
    setSubmittedUrl(value.trim())
    setView({ kind: "idle" })
    try {
      const env = await postExtractUrl({
        url:   kind === "url"  ? value.trim() : undefined,
        doi:   kind === "doi"  ? value.trim() : undefined,
        isbn:  kind === "isbn" ? value.trim() : undefined,
      })
      applyEnvelope(env, kind)
    } catch (err) {
      setView({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Could not access this source. Please check your input and try again.",
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        {/* URL */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="url-input" className="text-sm font-medium text-foreground">
            URL
          </label>
          <Input
            id="url-input"
            autoFocus={autoFocus}
            type="url"
            inputMode="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.canlii.org/en/ca/scc/doc/..."
            disabled={loading}
            className="bg-card font-mono text-sm border-border/60 shadow-[0_1px_3px_0_rgb(0_0_0_/_0.03)]"
          />
          <p className="text-xs text-muted-foreground">
            Some sites block scraping — if a link fails, upload a full-page
            screenshot instead.
          </p>
          <div className="flex justify-end">
            <Button
              type="button"
              onClick={() => submitKind("url", url)}
              disabled={loading || url.trim().length === 0}
              className="gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Extracting…
                </>
              ) : (
                <>
                  <FileText className="size-4" aria-hidden="true" />
                  Generate citation
                </>
              )}
            </Button>
          </div>
        </div>

        {/* DOI */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="doi-input" className="text-sm font-medium text-foreground">
            DOI
          </label>
          <Input
            id="doi-input"
            type="text"
            value={doi}
            onChange={(e) => setDoi(e.target.value)}
            placeholder="10.1006/bbrc.2001.4705"
            disabled={loading}
            className="bg-card font-mono text-sm border-border/60 shadow-[0_1px_3px_0_rgb(0_0_0_/_0.03)]"
          />
          <div className="flex justify-end">
            <Button
              type="button"
              onClick={() => submitKind("doi", doi)}
              disabled={loading || doi.trim().length === 0}
              className="gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Extracting…
                </>
              ) : (
                <>
                  <FileText className="size-4" aria-hidden="true" />
                  Generate citation
                </>
              )}
            </Button>
          </div>
        </div>

        {/* ISBN */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="isbn-input" className="text-sm font-medium text-foreground">
            ISBN
          </label>
          <Input
            id="isbn-input"
            type="text"
            value={isbn}
            onChange={(e) => setIsbn(e.target.value)}
            placeholder="978-0-19-957685-7"
            disabled={loading}
            className="bg-card font-mono text-sm border-border/60 shadow-[0_1px_3px_0_rgb(0_0_0_/_0.03)]"
          />
          <div className="flex justify-end">
            <Button
              type="button"
              onClick={() => submitKind("isbn", isbn)}
              disabled={loading || isbn.trim().length === 0}
              className="gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Extracting…
                </>
              ) : (
                <>
                  <FileText className="size-4" aria-hidden="true" />
                  Generate citation
                </>
              )}
            </Button>
          </div>
        </div>
      </div>

      {loading ? (
        <div
          className="flex items-center gap-3 rounded-xl border border-border/60 bg-muted/50 px-5 py-6 text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
          Fetching and extracting citation. Please wait…
        </div>
      ) : view.kind === "unsupported" ? (
        <div className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/50 px-5 py-4 text-sm text-foreground">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
          <p className="text-muted-foreground">{view.reason}</p>
        </div>
      ) : (
        <ExtractResults view={view} sourceInput={submittedUrl} />
      )}
    </div>
  )
}
