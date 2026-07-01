import { Analytics } from "@vercel/analytics/next"
import type { Metadata, Viewport } from "next"
import localFont from "next/font/local"
import { GeistSans } from "geist/font/sans"
import { GeistMono } from "geist/font/mono"
import WarmupPing from "@/components/warmup"
import "./globals.css"

/* Lora — self-hosted variable woff2 (committed in-repo) */
const lora = localFont({
  src: [
    {
      path: "./fonts/lora/lora-latin-wght-normal.woff2",
      weight: "400 700",
      style: "normal",
    },
    {
      path: "./fonts/lora/lora-latin-wght-italic.woff2",
      weight: "400 700",
      style: "italic",
    },
  ],
  variable: "--font-lora",
  fallback: ["Georgia", "serif"],
})

export const metadata: Metadata = {
  title: "McGill Guide Citation Tool: Data-Verified, AI-Formatted",
  description: "Cite anything in McGill Guide (10th ed) for free. Verified against real databases, not a fill-in-the-blanks form, not AI guesswork.",
  metadataBase: new URL("https://citecounsel.com"),
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    siteName: "Cite Counsel",
    url: "https://citecounsel.com",
    title: "McGill Guide Citation Tool: Data-Verified, AI-Formatted",
    description:
      "Cite anything in McGill Guide (10th ed) for free. Verified against real databases, not a fill-in-the-blanks form, not AI guesswork.",
  },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-96.png", type: "image/png", sizes: "96x96" },
    ],
    apple: "/apple-icon.png",
  },
}

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "white" },
    { media: "(prefers-color-scheme: dark)", color: "black" },
  ],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      className={`light ${GeistSans.variable} ${GeistMono.variable} ${lora.variable} bg-background`}
    >
      <body className="font-sans antialiased">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@graph": [
                {
                  "@type": "WebSite",
                  name: "Cite Counsel",
                  alternateName: ["CiteCounsel", "citecounsel.com"],
                  url: "https://citecounsel.com/",
                },
                {
                  "@type": "Organization",
                  name: "Cite Counsel",
                  url: "https://citecounsel.com/",
                  logo: "https://citecounsel.com/icon-96.png",
                },
              ],
            }),
          }}
        />
        {children}
        {process.env.NODE_ENV === "production" && <Analytics />}
        {process.env.NODE_ENV === "production" && <WarmupPing />}
      </body>
    </html>
  )
}
