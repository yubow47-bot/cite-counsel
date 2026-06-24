import type { MetadataRoute } from "next"

const ROUTES = [""] as const // add future path segments here, e.g. "about", "pricing"

const BASE_URL = "https://citecounsel.com"

export default function sitemap(): MetadataRoute.Sitemap {
  return ROUTES.map((route) => ({
    url: `${BASE_URL}/${route}`,
    lastModified: new Date(),
    changeFrequency: "weekly" as const,
    priority: 1,
  }))
}
