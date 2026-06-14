"use client"

import { useRef, useState } from "react"
import { FileText, Loader2, Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  ExtractResults,
  type ExtractView,
} from "@/components/extract-results"
import { postExtractFile, type Envelope } from "@/lib/citation-api"

export function FileExtractView() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<ExtractView>({ kind: "idle" })

  function applyEnvelope(env: Envelope) {
    switch (env.status) {
      case "done":
        setView({ kind: "done", citations: env.data.citations ?? [] })
        break
      case "unsupported":
        setView({
          kind: "unsupported",
          reason: env.data.reason ?? "This file type is not yet supported.",
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

  function pickFile(f: File | null) {
    if (!f) return
    setFile(f)
    setView({ kind: "idle" })
  }

  async function handleSubmit() {
    if (!file || loading) return
    setLoading(true)
    setView({ kind: "idle" })
    try {
      const env = await postExtractFile(file)
      applyEnvelope(env)
    } catch (err) {
      setView({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Could not upload the file. Please check your connection and try again.",
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            pickFile(e.dataTransfer.files?.[0] ?? null)
          }}
          disabled={loading}
          className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-4 py-10 text-center transition-colors ${
            dragOver
              ? "border-primary bg-accent"
              : "border-border bg-card hover:bg-muted/40"
          } disabled:cursor-not-allowed disabled:opacity-60`}
        >
          <Upload className="size-6 text-primary" aria-hidden="true" />
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-foreground">
              Click to select a file, or drag and drop here
            </span>
            <span className="text-xs text-muted-foreground">
              Supports PDF, Word, TXT, and other document formats
            </span>
          </div>
        </button>
        <input
          ref={inputRef}
          type="file"
          className="sr-only"
          accept=".pdf,.doc,.docx,.txt,.rtf"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />

        {file ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
            <span className="flex min-w-0 items-center gap-2">
              <FileText className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <span className="truncate text-foreground">{file.name}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {(file.size / 1024).toFixed(0)} KB
              </span>
            </span>
            {!loading ? (
              <button
                type="button"
                onClick={() => {
                  setFile(null)
                  if (inputRef.current) inputRef.current.value = ""
                }}
                className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                aria-label="Remove file"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="flex justify-end">
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !file}
            className="gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Extracting…
              </>
            ) : (
              <>
                <Upload className="size-4" aria-hidden="true" />
                Extract Citation
              </>
            )}
          </Button>
        </div>
      </div>

      {loading ? (
        <div
          className="flex items-center gap-3 rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
          Parsing file and extracting citations. Please wait…
        </div>
      ) : (
        <ExtractResults view={view} sourceInput={file?.name ?? ""} />
      )}
    </div>
  )
}
