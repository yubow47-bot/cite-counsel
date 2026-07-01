"use client"

import { useEffect } from "react"

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

/**
 * Fire-and-forget GET to /api/warmup on mount to warm connection pools
 * (cold-start mitigation for serverless deployments).
 *
 * Errors are swallowed silently — this is an optimisation, not a feature.
 */
export default function WarmupPing() {
  useEffect(() => {
    fetch(`${API_BASE}/api/warmup`, { method: "GET", mode: "cors" }).catch(
      () => {
        /* swallow */
      },
    )
  }, [])
  return null
}
