import type { Metadata } from "next"
import Link from "next/link"
import { FeedbackBox } from "@/components/feedback-box"

export const metadata: Metadata = {
  title: "FAQ | Cite Counsel",
  description:
    "Frequently asked questions about Cite Counsel, the free McGill Guide 10th edition citation tool. Send feedback or report an issue.",
  alternates: { canonical: "/faq" },
}

export default function FaqPage() {
  return (
    <main className="min-h-svh bg-background">
      <div className="mx-auto w-full max-w-[960px] px-4 py-16 sm:py-20">
        <Link
          href="/"
          className="mb-6 inline-flex text-xs text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
        >
          &larr; Back to Cite Counsel
        </Link>

        <h1 className="text-balance font-serif text-4xl font-semibold leading-tight text-foreground sm:text-5xl">
          Frequently asked questions
        </h1>

        <section className="mt-10 space-y-6">
          <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
            We&apos;re putting together answers to the questions users ask most. In the meantime,
            the{" "}
            <Link
              href="/about"
              className="underline-offset-4 transition-colors hover:text-foreground hover:underline"
            >
              About page
            </Link>{" "}
            explains what Cite Counsel supports and how each input works.
          </p>
          <p className="text-pretty text-sm leading-relaxed text-muted-foreground">
            Have a question or something that didn&apos;t work? Let us know below.
          </p>
        </section>

        <div className="mt-10">
          <FeedbackBox />
        </div>
      </div>
    </main>
  )
}
