import { useEffect, useMemo, useState } from 'react'
import {
  DISCLAIMER,
  fetchLatency,
  isAbortError,
  type ClientTiming,
  type LatencyLayer,
  type LatencyMode,
  type LatencyReview,
} from './ask'
import './Latency.css'

const PROBES: { mode: LatencyMode; label: string; hint: string }[] = [
  {
    mode: 'full',
    label: 'Full ask',
    hint: 'Same path as chat: classify → MiniLM → Chroma → Gemini',
  },
  {
    mode: 'extractive',
    label: 'Extractive',
    hint: 'Skip Gemini; copy a sentence from the top chunk',
  },
  {
    mode: 'catalog',
    label: 'Catalog table',
    hint: 'Five scheme searches, no Gemini',
  },
]

function fmt(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) {
    return '0 ms'
  }
  if (ms < 10) {
    return `${ms.toFixed(1)} ms`
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`
  }
  return `${(ms / 1000).toFixed(2)} s`
}

function clientLayers(client: ClientTiming): LatencyLayer[] {
  const rows: Array<[string, string, number, string]> = [
    ['fe_round_trip', 'Browser round trip', client.round_trip_ms, 'start of fetch to JSON parsed'],
    ['fe_network', 'Frontend → backend', client.network_ms, 'round trip minus server_ms'],
    ['fe_dns', 'DNS lookup', client.dns_ms, 'needs Timing-Allow-Origin'],
    ['fe_connect', 'TCP connect', client.connect_ms, 'needs Timing-Allow-Origin'],
    ['fe_tls', 'TLS handshake', client.tls_ms, 'needs Timing-Allow-Origin'],
    ['fe_ttfb', 'Time to first byte', client.ttfb_ms, 'request sent until first byte'],
    ['fe_download', 'Response download', client.download_ms, 'first byte to last byte'],
    ['fe_parse', 'Parse JSON', client.parse_ms, 'browser JSON.parse'],
  ]
  return rows.map(([id, label, ms, detail]) => ({
    id,
    label,
    group: 'browser',
    ms,
    skipped: ms <= 0 && id !== 'fe_round_trip' && id !== 'fe_network' && id !== 'fe_parse',
    detail,
  }))
}

function groupTitle(group: string): string {
  if (group === 'browser') {
    return 'Browser → API'
  }
  if (group === 'retrieve') {
    return 'Query → embed → retrieve'
  }
  if (group === 'writer') {
    return 'Writer'
  }
  return 'API'
}

export default function LatencyPage() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [review, setReview] = useState<LatencyReview | null>(null)
  const [mode, setMode] = useState<LatencyMode>('full')
  const [custom, setCustom] = useState('')

  useEffect(() => {
    document.title = 'Latency review — Groww FAQ'
  }, [])

  const mixed = useMemo(() => {
    if (!review) {
      return []
    }
    return [...clientLayers(review.client), ...review.report.layers]
  }, [review])

  const maxMs = useMemo(() => {
    const values = mixed.filter((layer) => !layer.skipped).map((layer) => layer.ms)
    return Math.max(1, ...values, review?.report.server_ms ?? 0, review?.client.round_trip_ms ?? 0)
  }, [mixed, review])

  async function run(nextMode: LatencyMode) {
    if (busy) {
      return
    }
    setMode(nextMode)
    setBusy(true)
    setError(null)
    try {
      const result = await fetchLatency(nextMode, custom)
      setReview(result)
    } catch (cause) {
      if (isAbortError(cause)) {
        return
      }
      setError(cause instanceof Error ? cause.message : 'Latency probe failed.')
    } finally {
      setBusy(false)
    }
  }

  const report = review?.report
  let cursor = 0
  const groups = ['browser', 'api', 'retrieve', 'writer'] as const

  return (
    <div className="latency-shell">
      <header className="latency-intro">
        <p className="latency-kicker">
          <a href="/">← FAQ</a>
        </p>
        <h1>
          Latency <em>review</em>
        </h1>
        <p>
          One timed ask through the same Railway path the chat uses. The Vercel page only measures
          the browser hop; MiniLM, Chroma, and Gemini run on the API.
        </p>
      </header>

      <div className="latency-controls">
        {PROBES.map((probe) => (
          <button
            key={probe.mode}
            type="button"
            className={mode === probe.mode ? 'is-on' : ''}
            disabled={busy}
            onClick={() => void run(probe.mode)}
          >
            {probe.label}
            <span>{probe.hint}</span>
          </button>
        ))}
      </div>

      <label className="latency-custom">
        Optional custom question
        <input
          value={custom}
          disabled={busy}
          autoComplete="off"
          placeholder="Leave blank to use the canned probe"
          onChange={(event) => setCustom(event.target.value)}
        />
      </label>

      {error ? <p className="latency-error">{error}</p> : null}
      {busy ? <p className="latency-pending">Timing a live request…</p> : null}

      {report ? (
        <>
          <section className="latency-summary" aria-label="Totals">
            <article>
              <h2>Round trip</h2>
              <p>{fmt(review.client.round_trip_ms)}</p>
              <span>Browser start to parsed JSON</span>
            </article>
            <article>
              <h2>Frontend → backend</h2>
              <p>{fmt(review.client.network_ms)}</p>
              <span>Network, TLS, and waiting on the wire</span>
            </article>
            <article>
              <h2>Server</h2>
              <p>{fmt(report.server_ms)}</p>
              <span>Railway work after the request arrives</span>
            </article>
            <article>
              <h2>Gemini</h2>
              <p>
                {fmt(
                  report.layers.find((layer) => layer.id === 'gemini' && !layer.skipped)?.ms ?? 0,
                )}
              </p>
              <span>Writer: {report.writer}</span>
            </article>
          </section>

          <ul className="latency-flags">
            <li>Index {report.index_ready ? 'ready' : 'missing'}</li>
            <li>MiniLM {report.encoder_cached ? 'already in RAM' : 'cold load this process'}</li>
            <li>
              {report.chunks} chunk{report.chunks === 1 ? '' : 's'} · intent {report.intent ?? '—'}
            </li>
            <li>Probe {report.probe}</li>
          </ul>

          <p className="latency-hint">
            Run Full ask twice. The first run on a sleeping Railway instance includes MiniLM load;
            the second should drop that bar to skipped.
          </p>

          {groups.map((group) => {
            const rows = mixed.filter((layer) => layer.group === group)
            if (rows.length === 0) {
              return null
            }
            return (
              <section key={group} className="latency-group">
                <h2>{groupTitle(group)}</h2>
                <ol>
                  {rows.map((layer) => {
                    const start = cursor
                    const left = group === 'browser' ? 0 : Math.min(96, (start / maxMs) * 100)
                    const width = layer.skipped
                      ? 0
                      : Math.max(1.5, Math.min(100 - left, (layer.ms / maxMs) * 100))
                    if (!layer.skipped && (group === 'api' || group === 'retrieve' || group === 'writer')) {
                      cursor += layer.ms
                    }
                    return (
                      <li key={layer.id} className={layer.skipped ? 'is-skip' : ''}>
                        <div className="latency-meta">
                          <strong>{layer.label}</strong>
                          <span>{layer.skipped ? 'skipped' : fmt(layer.ms)}</span>
                        </div>
                        <div className="latency-track" aria-hidden="true">
                          <span
                            className={`latency-bar ${group}`}
                            style={{
                              width: `${width}%`,
                              marginLeft: `${left}%`,
                            }}
                          />
                        </div>
                        {layer.detail ? <small>{layer.detail}</small> : null}
                      </li>
                    )
                  })}
                </ol>
              </section>
            )
          })}
        </>
      ) : (
        <p className="latency-empty">Pick Full ask, Extractive, or Catalog to capture a run.</p>
      )}

      <footer>
        <strong>{DISCLAIMER}</strong>
      </footer>
    </div>
  )
}
