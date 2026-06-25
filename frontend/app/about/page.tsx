import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "About Cite Counsel | McGill Guide (10th ed) Citation Tool",
  description:
    "How Cite Counsel works and what it supports: McGill Guide 10th edition citations for Canadian legislation, bills, case law, journal articles, books, and uploaded documents.",
  alternates: { canonical: "/about" },
}

export default function AboutPage() {
  return (
    <main className="min-h-svh bg-background">
      <div className="mx-auto w-full max-w-[960px] px-4 py-16 sm:py-20">
        <header className="border-b border-border/60 pb-8">
          <Link
            href="/"
            className="mb-6 inline-flex text-xs text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
          >
            &larr; Back to Cite Counsel
          </Link>
          <h1 className="text-balance font-serif text-4xl font-semibold leading-tight text-foreground sm:text-5xl">
            About Cite Counsel
          </h1>
        </header>

        <section className="mt-10 space-y-6">
          <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
            Cite Counsel is a free citation tool built around one standard: the McGill Guide, 10th
            edition.
          </p>
          <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
            Most citation managers focus on APA, MLA, and Chicago. McGill Guide citation, used by
            Canadian law schools and courts, usually isn&apos;t supported well, if at all. Cite
            Counsel is built around the McGill Guide, 10th edition. Every citation is either checked
            against real legal databases or built from your own input alone, never generated from a
            language model&apos;s memory. That grounding is what separates a citation generator from
            a citation guesser.
          </p>
        </section>

        <section className="mt-12 space-y-8">
          <h2 className="font-serif text-2xl font-semibold text-foreground">
            What Cite Counsel supports
          </h2>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">
              Legislation, bills, and case law
            </h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              Search with a vague name, citation number, or rough legal concept for a statute, bill,
              or case, then use the guided options to land on exactly what you want. Cite Counsel
              returns it in McGill format, including federal statutes, federal bills across past and
              current sessions, and Canadian case law with neutral and parallel citations.
            </p>
          </article>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">
              Journal articles, books, and web pages
            </h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              Paste a URL, DOI, or ISBN. Cite Counsel crawls the URL for the page&apos;s text, or
              pulls verified metadata from the DOI or ISBN, and generates the citation in McGill
              format.
            </p>
          </article>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">
              Uploaded documents
            </h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              Upload a PDF, Word file, or screenshot, and Cite Counsel reads the source and formats
              it using only the information you provide. This covers material that doesn&apos;t sit
              in a single database, such as news articles, government publications, municipal
              by-laws, and many other types of work.
            </p>
          </article>
        </section>

        <section className="mt-12 space-y-8">
          <h2 className="font-serif text-2xl font-semibold text-foreground">How it works</h2>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">1. Choose how to start</h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              Pick one of three entry points: search for a source, paste a link or identifier, or
              upload a document.
            </p>
          </article>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">2. Refine if needed</h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              For searches, Cite Counsel shows candidate matches and guided options so you can
              confirm the exact source before it builds anything.
            </p>
          </article>

          <article className="space-y-2">
            <h3 className="font-serif text-lg font-medium text-foreground">3. Copy your citation</h3>
            <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
              Get the finished McGill citation, formatted with correct italics and structure, ready
              to paste into your work.
            </p>
          </article>
        </section>
      </div>
    </main>
  )
}
