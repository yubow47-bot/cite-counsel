"use client"

import { useEffect, useState } from "react"
import { getScaffoldConfig, type ScaffoldConfig } from "@/lib/citation-api"

// 模块级缓存：整个 app 生命周期内只请求一次 /api/scaffold/config
let cachedConfig: ScaffoldConfig | null = null
let inflight: Promise<ScaffoldConfig> | null = null

function loadConfig(): Promise<ScaffoldConfig> {
  if (cachedConfig) return Promise.resolve(cachedConfig)
  if (!inflight) {
    inflight = getScaffoldConfig()
      .then((config) => {
        cachedConfig = config
        return config
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

export function useScaffoldConfig() {
  const [config, setConfig] = useState<ScaffoldConfig | null>(cachedConfig)
  const [loading, setLoading] = useState(!cachedConfig)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (cachedConfig) return
    let active = true
    setLoading(true)
    loadConfig()
      .then((c) => {
        if (active) setConfig(c)
      })
      .catch((err) => {
        if (active)
          setError(
            err instanceof Error ? err.message : "无法加载手动填写配置。",
          )
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return { config, loading, error }
}
