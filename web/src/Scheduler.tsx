import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  DISCLAIMER,
  createSchedule,
  deleteSchedule,
  fetchScheduleRuns,
  fetchSchedules,
  patchSchedule,
  runScheduleNow,
  type Schedule,
  type ScheduleRun,
} from './ask'
import './Scheduler.css'

const PRESETS = [
  { label: '10:03 AM IST', time: '10:03' },
  { label: '11:00 PM IST', time: '23:00' },
]

function formatIst(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) {
    return '—'
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`
  }
  return `${(ms / 1000).toFixed(1)} s`
}

function statusClass(status: string): string {
  if (status === 'success') {
    return 'ok'
  }
  if (status === 'failure' || status === 'skipped') {
    return 'bad'
  }
  return ''
}

export default function SchedulerPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [runs, setRuns] = useState<ScheduleRun[]>([])
  const [name, setName] = useState('Morning + night corpus refresh')
  const [times, setTimes] = useState<string[]>(['10:03', '23:00'])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    const [nextSchedules, nextRuns] = await Promise.all([fetchSchedules(), fetchScheduleRuns(40)])
    setSchedules(nextSchedules)
    setRuns(nextRuns)
  }, [])

  useEffect(() => {
    document.title = 'Scheduler — Groww FAQ'
    let cancelled = false
    async function load() {
      try {
        await refresh()
        if (!cancelled) {
          setError(null)
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Could not load schedules.')
        }
      }
    }
    void load()
    const timer = window.setInterval(() => {
      void load()
    }, 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [refresh])

  function addTime(value = '10:03') {
    setTimes((current) => (current.includes(value) ? current : [...current, value].sort()))
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await createSchedule(name, times)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create the schedule.')
    } finally {
      setBusy(false)
    }
  }

  async function onPause(row: Schedule, enabled: boolean) {
    setBusy(true)
    setError(null)
    try {
      await patchSchedule(row.id, { enabled })
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update the schedule.')
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(row: Schedule) {
    setBusy(true)
    setError(null)
    try {
      await deleteSchedule(row.id)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete the schedule.')
    } finally {
      setBusy(false)
    }
  }

  async function onRun(row: Schedule) {
    setBusy(true)
    setError(null)
    try {
      await runScheduleNow(row.id)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start a run.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="scheduler-shell">
      <header className="scheduler-intro">
        <p className="scheduler-kicker">
          <a href="/">Chat</a>
          {' · '}
          GitHub ingest on main
        </p>
        <h1>
          Corpus <em>scheduler</em>
        </h1>
        <p>
          Times are IST. A saved time starts the Refresh Groww corpus Action on GitHub{" "}
          <code>main</code> — scrape and jsonl come from that remote branch, not this laptop.
          Pause stops future fires; history stays. Delete removes the schedule only.
        </p>
      </header>

      <section className="scheduler-card">
        <h2>New schedule</h2>
        <form onSubmit={onCreate}>
          <label>
            Name
            <input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} />
          </label>
          <div className="scheduler-times">
            {times.map((time, index) => (
              <div className="scheduler-time-row" key={`${time}-${index}`}>
                <label>
                  Time {index + 1} (IST)
                  <input
                    type="time"
                    value={time}
                    onChange={(event) => {
                      const next = [...times]
                      next[index] = event.target.value
                      setTimes(next)
                    }}
                    required
                  />
                </label>
                {times.length > 1 ? (
                  <button type="button" onClick={() => setTimes(times.filter((_, i) => i !== index))}>
                    Remove
                  </button>
                ) : null}
              </div>
            ))}
          </div>
          <div className="scheduler-presets">
            {PRESETS.map((preset) => (
              <button key={preset.time} type="button" onClick={() => addTime(preset.time)}>
                {preset.label}
              </button>
            ))}
            <button type="button" onClick={() => addTime('12:00')}>
              Add another time
            </button>
          </div>
          <button type="submit" disabled={busy}>
            Save schedule
          </button>
        </form>
      </section>

      {error ? <p className="scheduler-error">{error}</p> : null}

      <section className="scheduler-card">
        <h2>Saved schedules</h2>
        {schedules.length === 0 ? (
          <p className="scheduler-empty">No schedules yet. Save one above, or use Run now after saving.</p>
        ) : (
          <ul className="scheduler-list">
            {schedules.map((row) => (
              <li key={row.id}>
                <header>
                  <h3>{row.name}</h3>
                  <span className={row.enabled ? 'scheduler-flag is-on' : 'scheduler-flag'}>
                    {row.enabled ? 'active' : 'paused'}
                  </span>
                </header>
                <p>
                  Daily at {row.times.join(', ')} IST
                  {row.next_run_at ? ` · next ${formatIst(row.next_run_at)}` : ''}
                  {row.last_status ? ` · last ${row.last_status}` : ''}
                </p>
                <div className="scheduler-actions">
                  <button type="button" disabled={busy} onClick={() => onRun(row)}>
                    Run now
                  </button>
                  <button type="button" disabled={busy} onClick={() => onPause(row, !row.enabled)}>
                    {row.enabled ? 'Pause' : 'Resume'}
                  </button>
                  <button type="button" disabled={busy} onClick={() => onDelete(row)}>
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="scheduler-card">
        <h2>Run history and results</h2>
        {runs.length === 0 ? (
          <p className="scheduler-empty">No runs yet. They appear here when a schedule fires or you click Run now.</p>
        ) : (
          <ul className="scheduler-history">
            {runs.map((run) => (
              <li key={run.id}>
                <header>
                  <strong>{run.schedule_name}</strong>
                  <span className={statusClass(run.status)}>{run.status}</span>
                </header>
                <p>
                  {run.trigger} · started {formatIst(run.started_at)}
                  {run.finished_at ? ` · finished ${formatIst(run.finished_at)}` : ''}
                  {` · ${formatDuration(run.duration_ms)}`}
                </p>
                {run.result ? <pre>{run.result}</pre> : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <footer>
        <strong>{DISCLAIMER}</strong>
        {' '}
        Scheduler data stays on this API under data/scheduler/.
      </footer>
    </div>
  )
}
