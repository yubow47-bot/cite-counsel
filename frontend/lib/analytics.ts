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
