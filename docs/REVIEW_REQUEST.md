# Review Request: GA4 / Google Ads Conversion Tracking

**Date:** 2026-07-06
**Author:** Claude Code
**Reviewer:** Yubo (sole committer)

---

## Summary

Add GA4/Google Ads conversion tracking via gtag.js to the citecounsel.com frontend.
Two events are tracked: `citation_generated` (when a citation is successfully produced)
and `kofi_click` (when the Ko-fi support link is clicked). Both events are gated to
production only, require a `NEXT_PUBLIC_GA_MEASUREMENT_ID` env var to be set, and are
silent no-ops otherwise.

---

## Changes

### 1. New file: `frontend/lib/analytics.ts`

Exports `trackEvent(name, params?)` — calls `window.gtag("event", name, params)` when
`window.gtag` exists, silent no-op otherwise. Handles SSR, dev, preview, and missing
env var gracefully.

### 2. `frontend/app/layout.tsx` — gtag.js loader

Added two `<Script>` tags (via `next/script` with `strategy="afterInteractive"`):
- The external loader from `googletagmanager.com/gtag/js?id=...`
- An inline config snippet that sets up `gtag()` and calls `gtag('config', ...)`

Both are gated on `process.env.NODE_ENV === "production"` and
`process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID` being non-empty. When unset (dev, preview,
or production without the env var), nothing renders and no scripts load.

### 3. `frontend/components/citation-tool.tsx` — `citation_generated` event

In `applyEnvelope()`, inside the existing `case "done":` branch, calls
`trackEvent("citation_generated")` only when `env.data.citations?.length > 0`.
The empty-citations path (which shows "No citations were generated") does NOT fire.

### 4. `frontend/app/page.tsx` — `kofi_click` event

Added `onClick={() => trackEvent("kofi_click")}` to the Ko-fi `<a>` link.
Does NOT call `preventDefault()` — the link's default navigation is unaffected.
The link already has `target="_blank"`, so tracking fires without blocking navigation.

---

## Diff

```diff
diff --git a/frontend/app/layout.tsx b/frontend/app/layout.tsx
index ad2b8bd..4dd3d18 100644
--- a/frontend/app/layout.tsx
+++ b/frontend/app/layout.tsx
@@ -1,5 +1,6 @@
 import { Analytics } from "@vercel/analytics/next"
 import type { Metadata, Viewport } from "next"
+import Script from "next/script"
 import localFont from "next/font/local"
 import { GeistSans } from "geist/font/sans"
 import { GeistMono } from "geist/font/mono"
@@ -89,6 +90,22 @@ export default function RootLayout({
             }),
           }}
         />
+        {process.env.NODE_ENV === "production" && process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID ? (
+          <>
+            <Script
+              src={`https://www.googletagmanager.com/gtag/js?id=${process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID}`}
+              strategy="afterInteractive"
+            />
+            <Script id="google-analytics" strategy="afterInteractive">
+              {`
+                window.dataLayer = window.dataLayer || [];
+                function gtag(){dataLayer.push(arguments);}
+                gtag('js', new Date());
+                gtag('config', '${process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID}');
+              `}
+            </Script>
+          </>
+        ) : null}
         {children}
         {process.env.NODE_ENV === "production" && <Analytics />}
         {process.env.NODE_ENV === "production" && <WarmupPing />}
diff --git a/frontend/app/page.tsx b/frontend/app/page.tsx
index c320b58..18ccc79 100644
--- a/frontend/app/page.tsx
+++ b/frontend/app/page.tsx
@@ -8,6 +8,7 @@ import { CitationTool } from "@/components/citation-tool"
 import { FeedbackBox } from "@/components/feedback-box"
 import { FileExtractView } from "@/components/file-extract-view"
 import { UrlExtractView } from "@/components/url-extract-view"
+import { trackEvent } from "@/lib/analytics"
 
 const BOXES = [
   {
@@ -174,6 +175,7 @@ export default function Page() {
               target="_blank"
               rel="noopener"
               aria-label="Support this project"
+              onClick={() => trackEvent("kofi_click")}
               className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors"
               style={{ background: "#FAECE7", color: "#993C1D", border: "0.5px solid #F0997B" }}
             >
diff --git a/frontend/components/citation-tool.tsx b/frontend/components/citation-tool.tsx
index 1b96696..bdf5b8a 100644
--- a/frontend/components/citation-tool.tsx
+++ b/frontend/components/citation-tool.tsx
@@ -15,6 +15,7 @@ import {
   type Envelope,
 } from "@/lib/citation-api"
 import { SCAFFOLD_ENABLED } from "@/lib/scaffold"
+import { trackEvent } from "@/lib/analytics"
 
 type View =
   | { kind: "idle" }
@@ -43,6 +44,9 @@ export function CitationTool({ autoFocus }: { autoFocus?: boolean }) {
     switch (env.status) {
       case "done":
         setView({ kind: "done", citations: env.data.citations ?? [] })
+        if (env.data.citations?.length > 0) {
+          trackEvent("citation_generated")
+        }
         break
       case "needs_selection":
         setView({
```

### New file: `frontend/lib/analytics.ts`

```typescript
/**
 * Analytics helpers for GA4 / Google Ads conversion tracking.
 *
 * Uses gtag.js loaded via next/script in the root layout.
 * All functions are silent no-ops when gtag is not loaded (dev, preview, SSR).
 */

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void
  }
}

/**
 * Fire a GA4 / Google Ads event via gtag.js.
 * Safe to call in any environment — no-ops when gtag is unavailable.
 */
export function trackEvent(name: string, params?: Record<string, unknown>): void {
  if (typeof window !== "undefined" && window.gtag) {
    window.gtag("event", name, params)
  }
}
```

### New file: `frontend/lib/analytics.test.ts` (4 tests)

```typescript
// — trackEvent: calls window.gtag with correct event name and params
// — trackEvent: does not throw when window.gtag is undefined
// — trackEvent: does not throw when window is undefined (SSR)
// — trackEvent: calls gtag with just event name when no params
```

### New file: `frontend/components/citation-tool.test.tsx` (2 tests)

```typescript
// — CitationTool: calls trackEvent("citation_generated") on done with citations
// — CitationTool: does NOT call trackEvent on done with empty citations
```

---

## Test Output

```
$ npx vitest run --reporter=verbose

 ✓ lib/analytics.test.ts > trackEvent > calls window.gtag with correct event name and params when gtag is defined
 ✓ lib/analytics.test.ts > trackEvent > calls window.gtag with just event name when no params provided
 ✓ lib/analytics.test.ts > trackEvent > does not throw when window.gtag is undefined
 ✓ lib/analytics.test.ts > trackEvent > does not throw when window is undefined (SSR)
 ✓ components/citation-tool.test.tsx > CitationTool — analytics tracking > calls trackEvent("citation_generated") when done envelope has non-empty citations
 ✓ components/citation-tool.test.tsx > CitationTool — analytics tracking > does NOT call trackEvent when done envelope has empty citations
 ✓ components/citation-card.test.tsx > (33 existing tests) all pass
 ✓ components/url-extract-view.test.tsx > (8 existing tests) all pass

 Test Files  4 passed (4)
      Tests  47 passed (47)
```

## Build Output

```
$ npm run build

▲ Next.js 16.2.6 (Turbopack)
✓ Compiled successfully in 2.3s
  Skipping validation of types
  Generating static pages ✓ (9/9)

Route (app)
┌ ○ /
├ ○ /_not-found
├ ○ /about
├ ○ /faq
├ ○ /icon.svg
├ ○ /robots.txt
└ ○ /sitemap.xml
```

Build succeeds cleanly without `NEXT_PUBLIC_GA_MEASUREMENT_ID` set — the gtag
code path simply renders nothing, confirming no breakage for dev/preview.

---

## Regression Count

| Metric | Before | After |
|---|---|---|
| Test files | 2 | 4 |
| Tests passed | 41 | 47 |
| Tests failed | 0 | 0 |
| Build | succeeds | succeeds |

All 41 existing tests in `citation-card.test.tsx` and `url-extract-view.test.tsx`
pass unchanged. The 6 new tests are the analytics-specific tests above.

---

## Files Changed / Created

```
 frontend/app/layout.tsx               | 17 +++++++++++++++++
 frontend/app/page.tsx                 |  2 ++
 frontend/components/citation-tool.tsx |  4 ++++
 frontend/lib/analytics.ts             | 20 ++++++++++++++++++++
 frontend/lib/analytics.test.ts        | 40 ++++++++++++++++++++++++++++++++++++++++
 frontend/components/citation-tool.test.tsx | 45 ++++++++++++++++++++++++++++++++++++++++++++
 6 files changed, 128 insertions(+)
```

## Verification Commands

```bash
npx vitest run --reporter=verbose          # frontend tests
npm run build                               # production build
```
