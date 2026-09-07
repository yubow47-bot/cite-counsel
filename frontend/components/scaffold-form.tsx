"use client"

import { useEffect, useMemo, useState } from "react"
import { AlertCircle, Info, Loader2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { CitationCard } from "@/components/citation-card"
import { SCAFFOLD_ENABLED } from "@/lib/scaffold"
import { useScaffoldConfig } from "@/lib/use-scaffold-config"
import {
  postCitationAssemble,
  type Citation,
  type Envelope,
} from "@/lib/citation-api"

type Result =
  | { kind: "idle" }
  | { kind: "done"; citations: Citation[] }
  | { kind: "error"; reason: string }

export function ScaffoldForm({
  initialType,
  prefill,
  onClose,
}: {
  /** Initial source type suggested by the backend (needs_input). */
  initialType?: string
  /** Prefill values for form fields (needs_input). */
  prefill?: Record<string, string>
  /** Hide/close the form (manual override scenario). */
  onClose?: () => void
}) {
  // Hooks must run unconditionally (Rules of Hooks) — the SCAFFOLD_ENABLED
  // gate below sits after them, right before the first render.
  const { config, loading: configLoading, error: configError } =
    useScaffoldConfig()

  const [type, setType] = useState<string>("")
  const [fields, setFields] = useState<Record<string, string>>({})
  const [assembling, setAssembling] = useState(false)
  const [result, setResult] = useState<Result>({ kind: "idle" })

  if (!SCAFFOLD_ENABLED) return null

  // Use backend suggestion first, fall back to first option
  useEffect(() => {
    if (!config || type) return
    const fallback = config.type_options[0]?.value ?? ""
    const next =
      initialType && config.field_configs[initialType]
        ? initialType
        : fallback
    setType(next)
  }, [config, initialType, type])

  const activeFields = useMemo(
    () => (type && config ? config.field_configs[type]?.fields ?? [] : []),
    [config, type],
  )

  // Rebuild field values when type changes, applying prefill
  useEffect(() => {
    if (!type || !config) return
    const defs = config.field_configs[type]?.fields ?? []
    setFields((prev) => {
      const next: Record<string, string> = {}
      for (const f of defs) {
        next[f.name] = prefill?.[f.name] ?? prev[f.name] ?? ""
      }
      return next
    })
  }, [type, config, prefill])

  const missingRequired = activeFields.some(
    (f) => f.required && !fields[f.name]?.trim(),
  )

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault()
    if (assembling || missingRequired || !type) return
    setAssembling(true)
    setResult({ kind: "idle" })
    try {
      const env: Envelope = await postCitationAssemble(type, fields)
      if (env.status === "done") {
        setResult({ kind: "done", citations: env.data.citations ?? [] })
      } else {
        setResult({
          kind: "error",
          reason:
            env.error?.reason ??
            env.data.reason ??
            "Could not assemble a citation from those fields.",
        })
      }
    } catch (err) {
      setResult({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Could not reach the citation service. Please try again.",
      })
    } finally {
      setAssembling(false)
    }
  }

  return (
    <div className="rounded-xl border border-border/60 bg-card p-5 sm:p-6 shadow-[0_1px_4px_0_rgb(0_0_0_/_0.04)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold text-card-foreground">
            Build a citation manually
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Fill in the fields below and we&apos;ll format them to McGill style.
          </p>
        </div>
        {onClose ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="size-8 shrink-0 p-0"
            aria-label="Close manual form"
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      {configLoading ? (
        <div
          className="mt-4 flex items-center gap-3 rounded-lg border border-border/60 bg-muted/50 px-5 py-6 text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
          Loading citation builder…
        </div>
      ) : null}

      {configError ? (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.04] px-5 py-4 text-sm">
          <AlertCircle
            className="mt-0.5 size-4 shrink-0 text-destructive"
            aria-hidden="true"
          />
          <p className="text-muted-foreground">{configError}</p>
        </div>
      ) : null}

      {config && !configLoading ? (
        <form onSubmit={handleGenerate} className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="scaffold-type">Source type</Label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger id="scaffold-type" className="bg-background border-border/60">
                <SelectValue placeholder="Select a source type" />
              </SelectTrigger>
              <SelectContent>
                {config.type_options.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {activeFields.map((field) => (
            <div key={field.name} className="flex flex-col gap-1.5">
              <Label htmlFor={`scaffold-${field.name}`}>
                {field.label}
                {field.required ? (
                  <span className="ml-1 text-destructive" aria-hidden="true">
                    *
                  </span>
                ) : null}
              </Label>
              <Input
                id={`scaffold-${field.name}`}
                value={fields[field.name] ?? ""}
                placeholder={field.placeholder}
                required={field.required}
                onChange={(e) =>
                  setFields((prev) => ({
                    ...prev,
                    [field.name]: e.target.value,
                  }))
                }
                className="bg-background font-mono text-sm border-border/60"
              />
            </div>
          ))}

          <div className="flex items-center justify-end">
            <Button
              type="submit"
              disabled={assembling || missingRequired}
              className="gap-2"
            >
              {assembling ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Generating…
                </>
              ) : (
                "Generate Citation"
              )}
            </Button>
          </div>
        </form>
      ) : null}

      {result.kind === "done" ? (
        <section
          aria-label="Manually built citation"
          className="mt-5 flex flex-col gap-3 border-t border-border pt-5"
        >
          {result.citations.length === 0 ? (
            <div className="flex items-start gap-3 rounded-lg border border-border/60 bg-muted/50 px-5 py-4 text-sm text-muted-foreground">
              <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              No citation was produced. Please check the fields and try again.
            </div>
          ) : (
            result.citations.map((item, i) => (
              <CitationCard key={i} item={item} sourceInput="" />
            ))
          )}
        </section>
      ) : null}

      {result.kind === "error" ? (
        <div className="mt-5 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.04] px-5 py-4 text-sm">
          <AlertCircle
            className="mt-0.5 size-4 shrink-0 text-destructive"
            aria-hidden="true"
          />
          <p className="text-muted-foreground">{result.reason}</p>
        </div>
      ) : null}
    </div>
  )
}
