export type AskResponse = {
  text: string
  intent: string | null
  scheme_id: string | null
  topic: string | null
  source_url: string | null
  as_of_date: string | null
  pii_blocked: boolean
}

export type Turn = {
  role: 'user' | 'assistant' | 'scheme'
  text: string
  caption?: string
  source_url?: string | null
  as_of_date?: string | null
}

export const EXAMPLE_QUESTIONS = [
  {
    label: 'Expense ratio',
    topic: 'expense ratio',
    question: 'What is the expense ratio of HDFC Large Cap Fund Direct Growth?',
  },
  {
    label: 'Exit load',
    topic: 'exit load',
    question: 'What is the exit load of HDFC ELSS Tax Saver Direct Plan?',
  },
  {
    label: 'Min SIP',
    topic: 'minimum SIP amount',
    question: 'What is the minimum SIP amount for HDFC Mid Cap Fund Direct Growth?',
  },
  {
    label: 'NAV',
    topic: 'NAV',
    question: 'What is the NAV of HDFC Large Cap Fund Direct Growth?',
  },
] as const

export const IN_SCOPE_SCHEMES = [
  {
    code: 'HDFC LG DG',
    short: 'Large Cap',
    title: 'HDFC Large Cap Fund Direct Growth',
  },
  {
    code: 'HDFC MD DG',
    short: 'Mid Cap',
    title: 'HDFC Mid Cap Fund Direct Growth',
  },
  {
    code: 'HDFC SM DG',
    short: 'Small Cap',
    title: 'HDFC Small Cap Fund Direct Growth',
  },
  {
    code: 'HDFC GL DG',
    short: 'Gold ETF FoF',
    title: 'HDFC Gold ETF Fund of Fund Direct Plan Growth',
  },
  {
    code: 'HDFC ELSS DG',
    short: 'ELSS Tax Saver',
    title: 'HDFC ELSS Tax Saver Fund Direct Plan Growth',
  },
] as const

export type InScopeScheme = (typeof IN_SCOPE_SCHEMES)[number]

export const DISCLAIMER = 'Facts-only. No investment advice.'

export function questionWithScheme(query: string, scheme: InScopeScheme | null): string {
  const trimmed = query.trim()
  if (!scheme || !trimmed) {
    return trimmed
  }
  const folded = trimmed.toLowerCase()
  if (
    folded === scheme.code.toLowerCase() ||
    folded === scheme.short.toLowerCase() ||
    folded === scheme.title.toLowerCase()
  ) {
    return scheme.title
  }
  if (
    folded.includes(scheme.title.toLowerCase()) ||
    folded.includes(scheme.short.toLowerCase()) ||
    folded.includes(scheme.code.toLowerCase())
  ) {
    return trimmed
  }
  const topic = trimmed.replace(/^what is (the )?/i, '').replace(/\?+$/g, '').trim()
  return `What is the ${topic} of ${scheme.title}?`
}

export const MISSING_API_URL =
  'The FAQ API address is not configured. Set VITE_API_BASE_URL and reload.'

export function apiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL
  if (typeof raw !== 'string') {
    return ''
  }
  return raw.trim().replace(/\/$/, '')
}

export function peelCitation(
  text: string,
  extras?: { source_url?: string | null; as_of_date?: string | null },
): { body: string; source_url: string | null; as_of_date: string | null } {
  const sourceMatch = text.match(/^\s*Source:\s+(\S+)\s*$/im)
  const footerMatch = text.match(/^\s*Last updated from sources:\s+(.+?)\s*$/im)
  const sourceUrl = extras?.source_url?.trim() || sourceMatch?.[1] || null
  const asOfDate = extras?.as_of_date?.trim() || footerMatch?.[1]?.trim() || null
  const body = text
    .replace(/^\s*Source:\s+\S+\s*$/gim, '')
    .replace(/^\s*Last updated from sources:\s+.+\s*$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return { body, source_url: sourceUrl, as_of_date: asOfDate }
}

export function applyAskResult(
  history: Turn[],
  query: string,
  response: Pick<AskResponse, 'text' | 'pii_blocked' | 'source_url' | 'as_of_date'>,
): Turn[] {
  const assistant: Turn = {
    role: 'assistant',
    text: response.text,
    source_url: response.source_url,
    as_of_date: response.as_of_date,
  }
  if (response.pii_blocked) {
    return [...history, assistant]
  }
  return [...history, { role: 'user', text: query }, assistant]
}

export function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === 'AbortError'
}

export type LatencyMode = 'full' | 'extractive' | 'catalog'

export type LatencyLayer = {
  id: string
  label: string
  group: string
  ms: number
  skipped: boolean
  detail?: string
}

export type LatencyReport = {
  ok: boolean
  index_ready: boolean
  encoder_cached: boolean
  mode: LatencyMode
  probe: string
  intent: string | null
  scheme_id: string | null
  topic: string | null
  writer: string
  chunks: number
  pii_blocked: boolean
  layers: LatencyLayer[]
  server_ms: number
}

export type ClientTiming = {
  round_trip_ms: number
  network_ms: number
  parse_ms: number
  dns_ms: number
  connect_ms: number
  tls_ms: number
  ttfb_ms: number
  download_ms: number
}

export type LatencyReview = {
  report: LatencyReport
  client: ClientTiming
  url: string
}

function asLayer(raw: Partial<LatencyLayer> | undefined, fallback: LatencyLayer): LatencyLayer {
  if (!raw || typeof raw.id !== 'string') {
    return fallback
  }
  return {
    id: raw.id,
    label: typeof raw.label === 'string' ? raw.label : fallback.label,
    group: typeof raw.group === 'string' ? raw.group : fallback.group,
    ms: typeof raw.ms === 'number' ? raw.ms : 0,
    skipped: Boolean(raw.skipped),
    detail: typeof raw.detail === 'string' ? raw.detail : undefined,
  }
}

function resourceTiming(url: string): PerformanceResourceTiming | undefined {
  const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[]
  const matches = entries.filter((entry) => {
    if (entry.name === url) {
      return true
    }
    return entry.name.split('?')[0] === url.split('?')[0] && entry.name.includes('/latency')
  })
  return matches.at(-1)
}

function clientTiming(url: string, roundTripMs: number, parseMs: number, serverMs: number): ClientTiming {
  const entry = resourceTiming(url)
  const dns =
    entry && entry.domainLookupEnd >= entry.domainLookupStart
      ? Math.max(0, entry.domainLookupEnd - entry.domainLookupStart)
      : 0
  const connect =
    entry && entry.connectEnd >= entry.connectStart
      ? Math.max(0, entry.connectEnd - entry.connectStart)
      : 0
  const tls =
    entry && entry.secureConnectionStart > 0
      ? Math.max(0, entry.connectEnd - entry.secureConnectionStart)
      : 0
  const ttfb =
    entry && entry.responseStart >= entry.requestStart
      ? Math.max(0, entry.responseStart - entry.requestStart)
      : 0
  const download =
    entry && entry.responseEnd >= entry.responseStart
      ? Math.max(0, entry.responseEnd - entry.responseStart)
      : 0
  return {
    round_trip_ms: roundTripMs,
    network_ms: Math.max(0, roundTripMs - serverMs),
    parse_ms: parseMs,
    dns_ms: dns,
    connect_ms: connect,
    tls_ms: tls,
    ttfb_ms: ttfb,
    download_ms: download,
  }
}

export async function fetchLatency(
  mode: LatencyMode,
  query?: string,
  signal?: AbortSignal,
): Promise<LatencyReview> {
  const base = apiBaseUrl()
  if (!base) {
    throw new Error(MISSING_API_URL)
  }

  const trimmed = query?.trim() ?? ''
  const url = trimmed ? `${base}/latency` : `${base}/latency?mode=${encodeURIComponent(mode)}`
  const started = performance.now()
  let response: Response
  try {
    response = trimmed
      ? await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: trimmed, mode }),
          signal,
        })
      : await fetch(url, { method: 'GET', signal })
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause
    }
    throw new Error('Could not reach the FAQ API. Confirm it is running and VITE_API_BASE_URL is correct.')
  }

  const parseStarted = performance.now()
  let body: Partial<LatencyReport> & { text?: string; layers?: Partial<LatencyLayer>[] } = {}
  try {
    body = (await response.json()) as typeof body
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause
    }
    throw new Error('The FAQ API returned an unreadable latency report.')
  }
  const parseMs = performance.now() - parseStarted
  const roundTripMs = performance.now() - started

  if (!response.ok && response.status !== 503 && response.status !== 400) {
    throw new Error(typeof body.text === 'string' ? body.text : 'Latency probe failed.')
  }
  if (response.status === 400 && !Array.isArray(body.layers)) {
    throw new Error(typeof body.text === 'string' ? body.text : 'Latency probe failed.')
  }

  const layers = Array.isArray(body.layers)
    ? body.layers.map((layer, index) =>
        asLayer(layer, {
          id: `layer-${index}`,
          label: 'Unknown',
          group: 'api',
          ms: 0,
          skipped: true,
        }),
      )
    : []
  const report: LatencyReport = {
    ok: Boolean(body.ok),
    index_ready: Boolean(body.index_ready),
    encoder_cached: Boolean(body.encoder_cached),
    mode: body.mode === 'extractive' || body.mode === 'catalog' ? body.mode : 'full',
    probe: typeof body.probe === 'string' ? body.probe : 'unknown',
    intent: body.intent ?? null,
    scheme_id: body.scheme_id ?? null,
    topic: body.topic ?? null,
    writer: typeof body.writer === 'string' ? body.writer : 'unknown',
    chunks: typeof body.chunks === 'number' ? body.chunks : 0,
    pii_blocked: Boolean(body.pii_blocked),
    layers,
    server_ms: typeof body.server_ms === 'number' ? body.server_ms : 0,
  }
  return {
    report,
    client: clientTiming(url, roundTripMs, parseMs, report.server_ms),
    url,
  }
}

export async function askQuestion(query: string, signal?: AbortSignal): Promise<AskResponse> {
  const base = apiBaseUrl()
  if (!base) {
    throw new Error(MISSING_API_URL)
  }

  let response: Response
  try {
    response = await fetch(`${base}/v1/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
      signal,
    })
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause
    }
    throw new Error('Could not reach the FAQ API. Confirm it is running and VITE_API_BASE_URL is correct.')
  }

  let body: Partial<AskResponse> = {}
  try {
    body = (await response.json()) as Partial<AskResponse>
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause
    }
    throw new Error('The FAQ API returned an unreadable response.')
  }

  const text =
    typeof body.text === 'string' && body.text.trim()
      ? body.text
      : 'The FAQ API could not answer that question.'

  if (!response.ok && response.status !== 400 && response.status !== 503) {
    throw new Error(text)
  }

  return {
    text,
    intent: body.intent ?? null,
    scheme_id: body.scheme_id ?? null,
    topic: body.topic ?? null,
    source_url: body.source_url ?? null,
    as_of_date: body.as_of_date ?? null,
    pii_blocked: Boolean(body.pii_blocked),
  }
}

export type Schedule = {
  id: string
  name: string
  times: string[]
  timezone: string
  enabled: boolean
  paused: boolean
  action: string
  created_at: string | null
  updated_at: string | null
  last_run_at: string | null
  last_status: string | null
  next_run_at: string | null
}

export type ScheduleRun = {
  id: string
  schedule_id: string
  schedule_name: string
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  result: string | null
}

async function schedulerRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const base = apiBaseUrl()
  if (!base) {
    throw new Error(MISSING_API_URL)
  }
  let response: Response
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers ?? {}),
      },
    })
  } catch {
    throw new Error('Could not reach the FAQ API. Is it running on VITE_API_BASE_URL?')
  }
  let body: ({ ok?: boolean; text?: string } & T) | undefined
  try {
    body = (await response.json()) as { ok?: boolean; text?: string } & T
  } catch {
    throw new Error('The FAQ API returned an unreadable scheduler response.')
  }
  if (!body) {
    throw new Error('The FAQ API returned an empty scheduler response.')
  }
  if (!response.ok) {
    throw new Error(typeof body.text === 'string' ? body.text : 'Scheduler request failed.')
  }
  return body
}

export async function fetchSchedules(): Promise<Schedule[]> {
  const body = await schedulerRequest<{ schedules?: Schedule[] }>('/v1/schedules')
  return Array.isArray(body.schedules) ? body.schedules : []
}

export async function createSchedule(name: string, times: string[]): Promise<Schedule> {
  const body = await schedulerRequest<{ schedule?: Schedule }>('/v1/schedules', {
    method: 'POST',
    body: JSON.stringify({ name, times }),
  })
  if (!body.schedule) {
    throw new Error('The API did not return the new schedule.')
  }
  return body.schedule
}

export async function patchSchedule(
  id: string,
  changes: { name?: string; times?: string[]; enabled?: boolean },
): Promise<Schedule> {
  const body = await schedulerRequest<{ schedule?: Schedule }>(`/v1/schedules/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })
  if (!body.schedule) {
    throw new Error('The API did not return the updated schedule.')
  }
  return body.schedule
}

export async function deleteSchedule(id: string): Promise<void> {
  await schedulerRequest(`/v1/schedules/${id}`, { method: 'DELETE' })
}

export async function runScheduleNow(id: string): Promise<ScheduleRun> {
  const body = await schedulerRequest<{ run?: ScheduleRun }>(`/v1/schedules/${id}/run`, {
    method: 'POST',
  })
  if (!body.run) {
    throw new Error('The API did not start a run.')
  }
  return body.run
}

export async function fetchScheduleRuns(limit = 40): Promise<ScheduleRun[]> {
  const body = await schedulerRequest<{ runs?: ScheduleRun[] }>(
    `/v1/scheduler/runs?limit=${encodeURIComponent(String(limit))}`,
  )
  return Array.isArray(body.runs) ? body.runs : []
}
