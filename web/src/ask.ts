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

export function applyAskResult(
  history: Turn[],
  query: string,
  response: Pick<AskResponse, 'text' | 'pii_blocked'>,
): Turn[] {
  const assistant: Turn = { role: 'assistant', text: response.text }
  if (response.pii_blocked) {
    return [...history, assistant]
  }
  return [...history, { role: 'user', text: query }, assistant]
}

export function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === 'AbortError'
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
