import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

export function middleware(req: NextRequest) {
  const host = req.headers.get("host") ?? ""

  if (process.env.VERCEL_ENV === "production" && host.endsWith(".vercel.app")) {
    const url = new URL(req.url)
    url.host = "citecounsel.com"
    url.protocol = "https:"
    url.port = ""
    return NextResponse.redirect(url, 308)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
}
