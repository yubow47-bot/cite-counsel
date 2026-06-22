// API 客户端与类型定义
// base URL 先用 localhost:8000，后续可用 NEXT_PUBLIC_API_BASE_URL 覆盖为生产地址
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

export type CitationStatus =
  | "done"
  | "needs_selection"
  | "needs_input"
  | "unsupported"
  | "error"

export interface Citation {
  citation: string
  source_case?: string
  /** false 表示由用户手动填写、未经数据库核验 */
  verified?: boolean
  /** 后端透传的来源类型，用于前端 placeholder 选择 */
  source_type?: string
}

export interface Candidate {
  display: string
  [key: string]: unknown
}

export interface CitationData {
  citations?: Citation[]
  candidates?: Candidate[]
  reason?: string
  /** needs_input 时后端建议的脚手架来源类型 */
  type?: string
  /** needs_input 时用于预填脚手架表单 */
  prefill?: Record<string, string>
}

/** 脚手架表单的单个字段配置 */
export interface ScaffoldField {
  name: string
  label: string
  placeholder?: string
  required?: boolean
}

/** GET /api/scaffold/config 的返回结构 */
export interface ScaffoldConfig {
  type_options: { value: string; label: string }[]
  field_configs: Record<string, { fields: ScaffoldField[] }>
}

export interface Envelope {
  ok: boolean
  route: string
  status: CitationStatus
  data: CitationData
  error: null | { reason: string }
}

/** POST /api/citation —— 提交原始引用文本 */
export async function postCitation(input: string): Promise<Envelope> {
  return request("/api/citation", { input })
}

/** POST /api/citation/select —— 在 needs_selection 后提交用户选择 */
export async function postCitationSelect(
  candidates: Candidate[],
  selectedIndex: number,
): Promise<Envelope> {
  return request("/api/citation/select", {
    candidates,
    selected_index: selectedIndex,
  })
}

/** POST /api/extract/file —— 上传文件（multipart，字段名 file）提取引用 */
export async function postExtractFile(file: File): Promise<Envelope> {
  const form = new FormData()
  form.append("file", file)

  const res = await fetch(`${API_BASE_URL}/api/extract/file`, {
    method: "POST",
    body: form,
  })

  let json: unknown
  try {
    json = await res.json()
  } catch {
    throw new Error(`服务器返回了无法解析的响应（HTTP ${res.status}）`)
  }
  return json as Envelope
}

/** POST /api/extract/url —— 提交 URL / DOI / ISBN 提取引用 */
export async function postExtractUrl(args: {
  url?: string
  doi?: string
  isbn?: string
}): Promise<Envelope> {
  return request("/api/extract/url", args)
}

/** GET /api/scaffold/config —— 获取手动填写表单的类型与字段配置 */
export async function getScaffoldConfig(): Promise<ScaffoldConfig> {
  const res = await fetch(`${API_BASE_URL}/api/scaffold/config`)
  if (!res.ok) {
    throw new Error(`无法获取脚手架配置（HTTP ${res.status}）`)
  }
  return (await res.json()) as ScaffoldConfig
}

/** POST /api/citation/assemble —— 用手动填写的字段组装引用 */
export async function postCitationAssemble(
  type: string,
  fields: Record<string, string>,
): Promise<Envelope> {
  return request("/api/citation/assemble", { type, fields })
}

/** POST /api/feedback —— 对单条引用结果反馈 */
export async function postFeedback(payload: {
  citation: string
  vote: "up" | "down"
  input?: string
}): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  } catch {
    // 反馈失败不阻塞主流程，静默处理
  }
}

/** POST /api/feedback —— 提交自由反馈消息 (kind=message) */
export async function postFeedbackMessage(note: string): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "message", note }),
    })
  } catch {
    // 反馈失败不阻塞主流程，静默处理
  }
}

async function request(path: string, body: unknown): Promise<Envelope> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })

  // 即使是 4xx/5xx，后端也可能返回信封结构；优先解析 JSON
  let json: unknown
  try {
    json = await res.json()
  } catch {
    throw new Error(`服务器返回了无法解析的响应（HTTP ${res.status}）`)
  }
  return json as Envelope
}
