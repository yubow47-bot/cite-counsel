"use client"

import { useRef, useState } from "react"
import { AlertCircle, Info, Loader2, PencilLine, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { CitationCard } from "@/components/citation-card"
import { CandidateList } from "@/components/candidate-list"
import { ScaffoldForm } from "@/components/scaffold-form"
import {
  postCitation,
  postCitationSelect,
  type Candidate,
  type Citation,
  type Envelope,
} from "@/lib/citation-api"
import { SCAFFOLD_ENABLED } from "@/lib/scaffold"

type View =
  | { kind: "idle" }
  | { kind: "done"; citations: Citation[] }
  | { kind: "needs_selection"; candidates: Candidate[] }
  | {
      kind: "needs_input"
      initialType?: string
      prefill?: Record<string, string>
    }
  | { kind: "unsupported"; reason: string; suggestedType?: string }
  | { kind: "error"; reason: string }

export function CitationTool({ autoFocus }: { autoFocus?: boolean }) {
  const [input, setInput] = useState("")
  const [submittedInput, setSubmittedInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [selectingIndex, setSelectingIndex] = useState<number | null>(null)
  const [view, setView] = useState<View>({ kind: "idle" })
  // Manual override: scaffold form opened by user from a result state
  const [manualOpen, setManualOpen] = useState(false)
  const isComposingRef = useRef(false)

  function applyEnvelope(env: Envelope) {
    setManualOpen(false)
    switch (env.status) {
      case "done":
        setView({ kind: "done", citations: env.data.citations ?? [] })
        break
      case "needs_selection":
        setView({
          kind: "needs_selection",
          candidates: env.data.candidates ?? [],
        })
        break
      case "needs_input":
        setView({
          kind: "needs_input",
          initialType: env.data.type,
          prefill: env.data.prefill,
        })
        break
      case "unsupported":
        setView({
          kind: "unsupported",
          reason:
            env.error?.reason ?? "This type of citation isn't supported yet.",
          suggestedType: env.data.type,
        })
        break
      case "error":
      default:
        setView({
          kind: "error",
          reason:
            env.error?.reason ??
            "Something went wrong. Please try again shortly.",
        })
        break
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    await submit()
  }

  async function submit() {
    const trimmed = input.trim()
    if (!trimmed || loading) return

    setLoading(true)
    setSubmittedInput(trimmed)
    setManualOpen(false)
    setView({ kind: "idle" })
    try {
      const env = await postCitation(trimmed)
      applyEnvelope(env)
    } catch (err) {
      setView({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Couldn't reach the citation service. Check your connection and try again.",
      })
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return
    // IME composition guard: Enter used to confirm a CJK candidate must never submit
    if (
      isComposingRef.current ||
      (e.nativeEvent as KeyboardEvent).isComposing ||
      e.keyCode === 229
    )
      return
    e.preventDefault()
    submit()
  }

  async function handleSelect(index: number) {
    if (view.kind !== "needs_selection" || selectingIndex !== null) return
    setSelectingIndex(index)
    try {
      const env = await postCitationSelect(view.candidates, index)
      applyEnvelope(env)
    } catch (err) {
      setView({
        kind: "error",
        reason:
          err instanceof Error
            ? err.message
            : "Something went wrong submitting your selection. Please try again.",
      })
    } finally {
      setSelectingIndex(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label htmlFor="citation-input" className="sr-only">
          Enter the source you want to cite
        </label>
        <Textarea
          id="citation-input"
          autoFocus={autoFocus}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => { isComposingRef.current = true }}
          onCompositionEnd={() => { isComposingRef.current = false }}
          placeholder="Enter a case, statute, bill, legal principle, or any legal topic.

Examples: r v ..., bill ..., ccc, gladue principle, charter s.7"
          rows={4}
          disabled={loading}
          className="resize-y bg-card text-sm leading-relaxed border-border/60 focus-visible:border-border focus-visible:ring-2 focus-visible:ring-border/40 shadow-[0_1px_3px_0_rgb(0_0_0_/_0.03)]"
        />
        <p className="text-xs text-muted-foreground">
          AI searches legal databases, verifies sources, and generates citations. Just type naturally, no special format required.
        </p>
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Formatted to the Canadian Guide to Uniform Legal Citation (McGill)
          </p>
          <Button
            type="submit"
            disabled={loading || input.trim().length === 0}
            className="gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Generating…
              </>
            ) : (
              <>
                <Search className="size-4" aria-hidden="true" />
                Generate Citation
              </>
            )}
          </Button>
        </div>
      </form>

      {loading ? (
        <div
          className="flex items-center gap-3 rounded-xl border border-border/60 bg-muted/50 px-5 py-6 text-sm text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
          Parsing your input and generating citations. This usually takes a few seconds…
        </div>
      ) : null}

      {!loading && view.kind === "done" ? (
        <section aria-label="Generated results" className="flex flex-col gap-4">
          {view.citations.length === 0 ? (
            <div className="rounded-xl border border-border/60 bg-muted/50 px-5 py-6 text-sm text-muted-foreground">
              No citations were generated. Try providing more complete information.
            </div>
          ) : (
            view.citations.map((item, i) => (
              <CitationCard key={i} item={item} sourceInput={submittedInput} />
            ))
          )}
          {SCAFFOLD_ENABLED && !manualOpen ? (
            <button
              type="button"
              onClick={() => setManualOpen(true)}
              className="self-start text-xs text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
            >
              Not what you&apos;re looking for? Build a citation manually &rarr;
            </button>
          ) : null}
        </section>
      ) : null}

      {!loading && view.kind === "needs_selection" ? (
        <div className="flex flex-col gap-3">
          <CandidateList
            candidates={view.candidates}
            onSelect={handleSelect}
            selectingIndex={selectingIndex}
          />
          {SCAFFOLD_ENABLED && !manualOpen ? (
            <button
              type="button"
              onClick={() => setManualOpen(true)}
              className="self-start text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
            >
              Not what you&apos;re looking for? Build a citation manually &rarr;
            </button>
          ) : null}
        </div>
      ) : null}

      {SCAFFOLD_ENABLED && !loading && view.kind === "needs_input" ? (
        <ScaffoldForm initialType={view.initialType} prefill={view.prefill} />
      ) : null}

      {!loading && view.kind === "unsupported" ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/50 px-5 py-4 text-sm text-foreground">
            <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
            <div>
              <p className="font-medium">This type of citation isn&apos;t supported</p>
              <p className="mt-1 text-muted-foreground">{view.reason}</p>
            </div>
          </div>
          {SCAFFOLD_ENABLED && !manualOpen ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setManualOpen(true)}
              className="self-start gap-2"
            >
              <PencilLine className="size-4" aria-hidden="true" />
              Build a citation manually
            </Button>
          ) : null}
        </div>
      ) : null}

      {SCAFFOLD_ENABLED && !loading &&
      manualOpen &&
      (view.kind === "done" ||
        view.kind === "needs_selection" ||
        view.kind === "unsupported") ? (
        <ScaffoldForm
          onClose={() => setManualOpen(false)}
          initialType={view.kind === "unsupported" ? view.suggestedType : undefined}
        />
      ) : null}

      {!loading && view.kind === "error" ? (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/[0.04] px-5 py-4 text-sm">
          <AlertCircle
            className="mt-0.5 size-4 shrink-0 text-destructive"
            aria-hidden="true"
          />
          <div>
            <p className="font-medium text-destructive">Generation failed</p>
            <p className="mt-1 text-muted-foreground">{view.reason}</p>
          </div>
        </div>
      ) : null}
    </div>
  )
}
