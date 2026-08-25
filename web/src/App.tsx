import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  DISCLAIMER,
  EXAMPLE_QUESTIONS,
  IN_SCOPE_SCHEMES,
  applyAskResult,
  askQuestion,
  isAbortError,
  questionWithScheme,
  type InScopeScheme,
  type Turn,
} from './ask'
import './App.css'

const GROWW_URL = /https:\/\/(?:www\.)?groww\.in\/[^\s)>\]]+/gi

function parseMarkdownTable(text: string): {
  headers: string[]
  rows: string[][]
  rest: string
} | null {
  const lines = text.split('\n')
  const start = lines.findIndex((line) => line.trim().startsWith('|'))
  if (start < 0) {
    return null
  }
  const table: string[] = []
  let end = start
  for (let index = start; index < lines.length; index += 1) {
    if (!lines[index].trim().startsWith('|')) {
      break
    }
    table.push(lines[index])
    end = index
  }
  if (table.length < 3) {
    return null
  }
  const cells = (line: string) =>
    line
      .split('|')
      .slice(1, -1)
      .map((cell) => cell.trim())
  const rest = [...lines.slice(0, start), ...lines.slice(end + 1)].join('\n').trim()
  return { headers: cells(table[0]), rows: table.slice(2).map(cells), rest }
}

function LinkedText({ text }: { text: string }) {
  const parts: Array<{ value: string; href?: string }> = []
  let cursor = 0
  const matches = text.matchAll(GROWW_URL)
  for (const match of matches) {
    const start = match.index ?? 0
    if (start > cursor) {
      parts.push({ value: text.slice(cursor, start) })
    }
    parts.push({ value: match[0], href: match[0] })
    cursor = start + match[0].length
  }
  if (cursor < text.length) {
    parts.push({ value: text.slice(cursor) })
  }
  if (parts.length === 0) {
    return null
  }
  return (
    <p className="bubble-text">
      {parts.map((part, index) =>
        part.href ? (
          <a key={`${part.href}-${index}`} href={part.href} target="_blank" rel="noreferrer">
            {part.value}
          </a>
        ) : (
          <span key={index}>{part.value}</span>
        ),
      )}
    </p>
  )
}

function AnswerText({ text }: { text: string }) {
  const table = parseMarkdownTable(text)
  if (!table) {
    return <LinkedText text={text} />
  }
  return (
    <div className="catalog">
      <div className="catalog-scroll">
        <table>
          <thead>
            <tr>
              {table.headers.map((header) => (
                <th key={header}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, rowIndex) => (
              <tr key={`${row[0] ?? 'row'}-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`${rowIndex}-${cellIndex}`}>
                    <LinkedText text={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.rest ? <LinkedText text={table.rest} /> : null}
    </div>
  )
}

function exchangeStamp(turns: Turn[], index: number): string {
  const role = turns[index]?.role
  const count = turns.slice(0, index + 1).filter((turn) => turn.role === role).length
  return String(count).padStart(2, '0')
}

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8.1" r="1.2" fill="currentColor" />
      <path d="M11.15 11.05h1.7V17.4h-1.7z" fill="currentColor" />
    </svg>
  )
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 2.8 13.7 9.2 20 11 13.7 12.8 12 19.2 10.3 12.8 4 11 10.3 9.2z"
        fill="currentColor"
      />
    </svg>
  )
}

function ClearIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M7.2 7.2 16.8 16.8M16.8 7.2 7.2 16.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.85"
        strokeLinecap="round"
      />
    </svg>
  )
}

function EnterIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M19.25 6.5v6.25H8.06l2.72-2.72-1.06-1.06-4.53 4.53 4.53 4.53 1.06-1.06-2.72-2.72h12.44V6.5z"
        fill="currentColor"
      />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="1.2" fill="currentColor" />
    </svg>
  )
}

function App() {
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [picked, setPicked] = useState<InScopeScheme | null>(null)
  const [pickedTopic, setPickedTopic] = useState<(typeof EXAMPLE_QUESTIONS)[number] | null>(null)
  const [showFacts, setShowFacts] = useState(false)
  const [howtoOpen, setHowtoOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const log = logRef.current
    if (!log) {
      return
    }
    log.scrollTop = log.scrollHeight
  }, [turns, busy])

  function focusDraft(value: string) {
    setDraft(value)
    requestAnimationFrame(() => {
      const box = inputRef.current
      if (!box) {
        return
      }
      box.focus()
      box.setSelectionRange(box.value.length, box.value.length)
    })
  }

  function pickScheme(scheme: InScopeScheme) {
    if (busy) {
      return
    }
    if (picked?.title === scheme.title) {
      return
    }
    setPicked(scheme)
    setPickedTopic(null)
    focusDraft(scheme.title)
  }

  function pickHowToQuestion(example: (typeof EXAMPLE_QUESTIONS)[number]) {
    if (busy) {
      return
    }
    const match = IN_SCOPE_SCHEMES.find((scheme) => example.question.includes(scheme.title))
    setHowtoOpen(false)
    setShowFacts(false)
    if (match) {
      setPicked(match)
    }
    setPickedTopic(example)
    focusDraft(example.question)
  }

  function pickTopic(example: (typeof EXAMPLE_QUESTIONS)[number]) {
    if (busy || !picked) {
      return
    }
    setPickedTopic(example)
    focusDraft(`What is the ${example.topic} of ${picked.title}?`)
  }

  function clearPrompt() {
    if (busy) {
      return
    }
    setDraft('')
    setPicked(null)
    setPickedTopic(null)
    setError(null)
    inputRef.current?.focus()
  }

  function clearHistory() {
    if (busy) {
      return
    }
    setTurns([])
    setError(null)
  }

  function stopAsk() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    inputRef.current?.focus()
  }

  async function submit(query: string) {
    const trimmed = query.trim()
    if (!trimmed || busy) {
      return
    }
    const asked = questionWithScheme(trimmed, picked)
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setError(null)
    try {
      const response = await askQuestion(asked, controller.signal)
      setTurns((current) => applyAskResult(current, asked, response))
      setDraft('')
      setPicked(null)
      setPickedTopic(null)
    } catch (cause) {
      if (isAbortError(cause)) {
        return
      }
      const message = cause instanceof Error ? cause.message : 'The FAQ API could not answer that question.'
      setError(message)
      setTurns((current) => [...current, { role: 'assistant', text: message }])
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
      }
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void submit(draft)
  }

  return (
    <div className="shell">
      <header className="intro">
        <div className="intro-bar">
          <h1>
            Groww’s HDFC <em>Limited FAQ</em>
          </h1>
          <div className="intro-actions">
            <div
              className={`howto-wrap${howtoOpen ? ' is-open' : ''}`}
              onMouseEnter={() => {
                setShowFacts(false)
                setHowtoOpen(true)
              }}
              onMouseLeave={() => setHowtoOpen(false)}
            >
              <button
                type="button"
                className="howto"
                aria-expanded={howtoOpen}
                aria-haspopup="true"
                disabled={busy}
                onClick={() => setHowtoOpen((open) => !open)}
              >
                <SparkIcon />
                Sample FAQs
              </button>
              <div className="howto-box" role="menu" aria-label="Sample FAQs">
                {EXAMPLE_QUESTIONS.map((example) => (
                  <button
                    key={example.question}
                    type="button"
                    role="menuitem"
                    className="howto-item"
                    disabled={busy}
                    onClick={() => pickHowToQuestion(example)}
                  >
                    <span>{example.label}</span>
                    {example.question}
                  </button>
                ))}
              </div>
            </div>
            <div className={`info-wrap${showFacts ? ' is-open' : ''}`}>
              <button
                type="button"
                className="info-btn"
                aria-label="About"
                aria-expanded={showFacts}
                onClick={() => {
                  setHowtoOpen(false)
                  setShowFacts((open) => !open)
                }}
              >
                <InfoIcon />
              </button>
              {showFacts ? (
                <p className="info-box">
                  Official facts from five Groww scheme pages plus Groww help on statements, TER,
                  exit load, and the riskometer. Ask expense ratio, SIP, lock-in, or benchmark. This
                  assistant will not recommend a fund, compare schemes, or calculate returns.
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </header>

      <main className="transcript" aria-live="polite">
        {turns.length > 0 ? (
          <div className="ledger-bar">
            <button type="button" className="clear-history" disabled={busy} onClick={clearHistory}>
              Clear history
            </button>
          </div>
        ) : null}
        <div className={`transcript-log${turns.length === 0 && !busy ? ' is-empty' : ''}`} ref={logRef}>
          {turns.length === 0 && !busy ? (
            <div className="ledger-empty">
              <span aria-hidden="true">?</span>
              <p>No facts pulled yet</p>
              <p>Pick a fund below, or hover Sample FAQs. This strip stays in this tab only.</p>
            </div>
          ) : (
            turns.map((turn, index) => (
              <article
                key={`${turn.role}-${index}`}
                className={`ledger-row ${turn.role}${turn.text.includes('| Scheme |') ? ' wide' : ''}`}
              >
                <div className="ledger-pane ans">
                  {turn.role !== 'user' ? (
                    <div className="bubble assistant">
                      <h2>
                        Ans: <span>{exchangeStamp(turns, index)}</span>
                      </h2>
                      <AnswerText text={turn.text} />
                    </div>
                  ) : null}
                </div>
                <span className="ledger-node" aria-hidden="true" />
                <div className="ledger-pane q">
                  {turn.role === 'user' ? (
                    <div className="bubble user">
                      <h2>
                        Q: <span>{exchangeStamp(turns, index)}</span>
                      </h2>
                      <AnswerText text={turn.text} />
                    </div>
                  ) : null}
                </div>
              </article>
            ))
          )}
          {busy ? (
            <div className="ledger-row pending-row">
              <p className="pending">Looking up the Groww pages…</p>
              <span className="ledger-node is-pulse" aria-hidden="true" />
              <span />
            </div>
          ) : null}
        </div>
      </main>

      <form className="composer" onSubmit={onSubmit}>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="composer-box">
          <label className="sr-only" htmlFor="question">
            Factual question
          </label>
          <textarea
            id="question"
            ref={inputRef}
            name="question"
            rows={2}
            autoComplete="off"
            spellCheck
            disabled={busy}
            placeholder={
              picked
                ? `Now pick Expense ratio, Exit load, Min SIP, or NAV for ${picked.code}`
                : 'Ask a factual question, or tap a fund below'
            }
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void submit(draft)
              }
            }}
          />
          <div className="composer-actions">
            <button
              type="button"
              className="prompt-clear"
              aria-label="Clear prompt"
              disabled={busy || (!draft && !picked && !error)}
              onClick={clearPrompt}
            >
              <ClearIcon />
            </button>
            {busy ? (
              <button type="button" className="stop" aria-label="Stop" onClick={stopAsk}>
                <StopIcon />
              </button>
            ) : (
              <button type="submit" className="send" aria-label="Ask" disabled={!draft.trim()}>
                <EnterIcon />
              </button>
            )}
          </div>
        </div>
        <nav className="scheme-rail" aria-label="Available schemes">
          {IN_SCOPE_SCHEMES.map((scheme) => (
            <button
              key={scheme.title}
              type="button"
              className={`scheme-chip${picked?.title === scheme.title ? ' is-on' : ''}`}
              aria-label={scheme.title}
              disabled={busy}
              onClick={() => pickScheme(scheme)}
            >
              {scheme.code}
              <span className="scheme-tip" role="tooltip">
                {scheme.title}
              </span>
            </button>
          ))}
        </nav>
        {picked ? (
          <nav className="topic-rail" aria-label="Factual questions">
            {EXAMPLE_QUESTIONS.map((example) => (
              <button
                key={example.label}
                type="button"
                className={`topic-chip${pickedTopic?.label === example.label ? ' is-on' : ''}`}
                disabled={busy}
                onClick={() => pickTopic(example)}
              >
                {example.label}
              </button>
            ))}
          </nav>
        ) : null}
      </form>

      <footer className="disclaimer" role="note">
        <strong>{DISCLAIMER}</strong>
      </footer>
    </div>
  )
}

export default App
