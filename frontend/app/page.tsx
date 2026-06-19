"use client"

import { useState } from "react"
import { FileText, Link2, Search } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CitationTool } from "@/components/citation-tool"
import { FeedbackBox } from "@/components/feedback-box"
import { FileExtractView } from "@/components/file-extract-view"
import { UrlExtractView } from "@/components/url-extract-view"

const BOXES = [
  {
    tab: "query" as const,
    icon: Search,
    heading: "Case, statute, gov doc…",
    blurb: <>or just a <strong>legal concept</strong></>,
  },
  {
    tab: "file" as const,
    icon: FileText,
    heading: "Upload a PDF, Word doc,",
    blurb: "or screenshot",
  },
  {
    tab: "url" as const,
    icon: Link2,
    heading: "Paste a URL, DOI,",
    blurb: "or ISBN",
  },
]

export default function Page() {
  const [activeTab, setActiveTab] = useState("query")
  const [entered, setEntered] = useState(false)
  const [animating, setAnimating] = useState(false)

  function handleBoxClick(tab: string) {
    if (animating) return
    setActiveTab(tab)
    setAnimating(true)
    setTimeout(() => {
      setEntered(true)
      setAnimating(false)
    }, 200)
  }

  return (
    <main className="min-h-svh bg-background">
      <div className="mx-auto w-full max-w-[960px] px-4 py-12 sm:py-16">
        <header className="border-b border-border pb-6">
          <p className="text-xs font-medium uppercase tracking-widest text-primary">
            McGill Citation Guide
          </p>
          <h1 className="mt-2 text-balance font-serif text-3xl font-semibold leading-tight text-foreground sm:text-4xl">
            McGill Legal Citation Tool (10th ed)
          </h1>
          <p className="mt-3 text-pretty text-sm leading-relaxed text-muted-foreground">
            Verified McGill citations (10th ed), grounded in real legal databases — A2AJ, CrossRef, Open Library. Not a fill-in-the-blanks form, not AI guesswork.
          </p>
          <p className="mt-1 text-pretty text-sm leading-relaxed text-muted-foreground">
            Enter a case, statute, gov doc, or just a legal concept to get started.
          </p>
        </header>

        {/* ---------- landing: entry boxes ---------- */}
        {!entered && (
          <section
            aria-label="Choose input method"
            className="mt-8 flex flex-col gap-4 sm:flex-row"
          >
            {BOXES.map((box, i) => {
              const Icon = box.icon
              const isTarget = activeTab === box.tab

              /* Animation classes — directional collapse */
              let animClass: string
              if (!animating) {
                animClass = "opacity-100 scale-100 translate-x-0 translate-y-0"
              } else if (isTarget) {
                animClass =
                  "opacity-100 scale-[1.02] sm:scale-[1.02] translate-x-0 translate-y-0"
              } else if (activeTab === "query") {
                animClass =
                  "opacity-0 scale-95 max-sm:translate-y-4 sm:translate-x-8"
              } else if (activeTab === "url") {
                animClass =
                  "opacity-0 scale-95 max-sm:-translate-y-4 sm:-translate-x-8"
              } else {
                /* file */
                animClass =
                  i === 0
                    ? "opacity-0 scale-95 max-sm:-translate-y-2 sm:-translate-x-8"
                    : "opacity-0 scale-95 max-sm:translate-y-2 sm:translate-x-8"
              }

              return (
                <button
                  key={box.tab}
                  type="button"
                  onClick={() => handleBoxClick(box.tab)}
                  className={`flex flex-1 flex-col items-center gap-3 rounded-xl border-2 border-border bg-card p-6 text-center transition-all duration-200 hover:border-primary/50 hover:bg-accent/30 cursor-pointer ${animClass}`}
                >
                  <div className="rounded-full bg-primary/10 p-3">
                    <Icon className="size-6 text-primary" aria-hidden="true" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {box.heading}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {box.blurb}
                    </p>
                  </div>
                </button>
              )
            })}
          </section>
        )}

        {/* ---------- main tabs (visible after entry) ---------- */}
        {entered && (
          <div
            className="mt-8"
            style={{ animation: "tabFadeIn 250ms ease-out both" }}
          >
            <Tabs
              value={activeTab}
              onValueChange={setActiveTab}
              className="gap-6"
            >
              <TabsList className="w-full">
                <TabsTrigger value="query" className="gap-1.5">
                  <Search className="size-4" aria-hidden="true" />
                  Citation Search
                </TabsTrigger>
                <TabsTrigger value="file" className="gap-1.5">
                  <FileText className="size-4" aria-hidden="true" />
                  File Extraction
                </TabsTrigger>
                <TabsTrigger value="url" className="gap-1.5">
                  <Link2 className="size-4" aria-hidden="true" />
                  URL Extraction
                </TabsTrigger>
              </TabsList>
              <TabsContent value="query">
                <CitationTool autoFocus />
              </TabsContent>
              <TabsContent value="file">
                <FileExtractView />
              </TabsContent>
              <TabsContent value="url">
                <UrlExtractView autoFocus />
              </TabsContent>
            </Tabs>
          </div>
        )}

        {/* ---------- feedback box (always visible) ---------- */}
        <div className="mt-10">
          <FeedbackBox />
        </div>

        {/* ---------- footer ---------- */}
        <footer className="mt-6 border-t border-border pt-6 text-xs leading-relaxed text-muted-foreground">
          <p>
            Citations are provided for reference. Always verify against the
            official McGill Guide before submission.
          </p>
          <div className="mt-3 flex items-center gap-3">
            <a
              href="https://ko-fi.com/wwwyyyy0"
              target="_blank"
              rel="noopener"
              aria-label="Support"
              className="inline-flex items-center gap-1 rounded-full border border-border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
            >
              ☕ Support
            </a>
            <span className="text-xs text-muted-foreground">
              This tool runs on paid APIs, support keeps it running.
            </span>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            This tool is actively being developed and may occasionally make mistakes. Please review citations before use.
          </p>
        </footer>
      </div>

      {/*
        Keyframe for the tabs fade-in on entry.
        Needs to live inside the component so it's only injected once.
      */}
      <style>{`@keyframes tabFadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }`}</style>
    </main>
  )
}
