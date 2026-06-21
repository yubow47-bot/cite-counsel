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
      <div className="mx-auto w-full max-w-[960px] px-4 py-16 sm:py-20">
        <header className="border-b border-border/60 pb-8">
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
            McGill Citation Guide
          </p>
          <h1 className="mt-3 text-balance font-serif text-4xl font-semibold leading-tight text-foreground sm:text-5xl">
            McGill Legal Citation Tool
          </h1>
          <p className="mt-1 font-serif text-xl text-muted-foreground sm:text-2xl">
            10th Edition
          </p>
          <p className="mt-4 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            Verified McGill citations grounded in real legal databases — A2AJ, CrossRef, Open Library. Not a fill-in-the-blanks form, not AI guesswork.
          </p>
        </header>

        {/* ---------- landing: entry boxes ---------- */}
        {!entered && (
          <section
            aria-label="Choose input method"
            className="mt-10 flex flex-col gap-4 sm:flex-row"
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
                  className={`flex flex-1 flex-col items-center gap-4 rounded-xl border border-border/60 bg-card p-7 text-center shadow-[0_1px_4px_0_rgb(0_0_0_/_0.04)] transition-all duration-200 hover:border-border hover:shadow-[0_2px_8px_0_rgb(0_0_0_/_0.06)] cursor-pointer ${animClass}`}
                >
                  <Icon className="size-6 text-primary" aria-hidden="true" />
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
            className="mt-10"
            style={{ animation: "tabFadeIn 200ms ease-out both" }}
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
        <footer className="mt-10 border-t border-border/60 pt-8 text-xs leading-relaxed text-muted-foreground">
          <p>
            Citations are provided for reference. Always verify against the
            official McGill Guide before submission.
          </p>
          <div className="mt-4 flex items-center gap-3">
            <a
              href="https://ko-fi.com/wwwyyyy0"
              target="_blank"
              rel="noopener"
              aria-label="Support this project"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-border hover:text-foreground"
            >
              ☕ Support
            </a>
            <span className="text-xs text-muted-foreground">
              This tool runs on paid APIs — support keeps it running.
            </span>
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
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
