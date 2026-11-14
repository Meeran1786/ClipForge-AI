import { useMutation } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { api, type Job, type Stage } from './api'
import type { Tone } from './format'

const TONE: Record<Tone, string> = {
  neutral: 'border-line bg-raised text-soft',
  good: 'border-good/30 bg-good/10 text-good',
  bad: 'border-bad/30 bg-bad/10 text-bad',
  warn: 'border-accent/30 bg-accent/10 text-accent',
  info: 'border-info/30 bg-info/10 text-info',
}

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  )
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="mb-7 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-soft">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Section({
  title,
  aside,
  children,
}: {
  title: string
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {aside}
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <div className="mx-auto mt-1.5 max-w-md text-sm text-soft">{children}</div>}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3.5 py-2.5 text-sm text-bad">
      {children}
    </div>
  )
}

export function ProgressBar({
  value,
  active = false,
  tone = 'accent',
}: {
  value: number
  active?: boolean
  tone?: 'accent' | 'good' | 'bad'
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100)
  const color = tone === 'good' ? 'bg-good' : tone === 'bad' ? 'bg-bad' : 'bg-accent'
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-raised"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <div
        className={`h-full rounded-full transition-[width] duration-300 ease-out ${color} ${active ? 'bar-active' : ''}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function StageIcon({ status }: { status: Stage['status'] }) {
  if (status === 'done')
    return (
      <span className="grid size-5 place-items-center rounded-full bg-good/15 text-good">
        <svg viewBox="0 0 16 16" className="size-3" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path d="M3.5 8.5l3 3 6-6.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    )
  if (status === 'failed')
    return (
      <span className="grid size-5 place-items-center rounded-full bg-bad/15 text-bad">
        <svg viewBox="0 0 16 16" className="size-3" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path d="M4.5 4.5l7 7m0-7l-7 7" strokeLinecap="round" />
        </svg>
      </span>
    )
  if (status === 'running')
    return (
      <span className="grid size-5 place-items-center">
        <span className="size-3.5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      </span>
    )
  return (
    <span className="grid size-5 place-items-center">
      <span className="size-2 rounded-full bg-line" />
    </span>
  )
}

export function StageList({ job }: { job: Job }) {
  return (
    <ol className="space-y-3">
      {job.stages.map((stage) => (
        <li key={stage.name} className="flex gap-3">
          <div className="pt-0.5">
            <StageIcon status={stage.status} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className={`text-sm ${stage.status === 'pending' ? 'text-faint' : 'text-ink'}`}>
                {stage.label}
              </span>
              {stage.status === 'running' && (
                <span className="text-xs tabular-nums text-soft">{Math.round(stage.progress * 100)}%</span>
              )}
            </div>
            {stage.message && (
              <p className={`mt-0.5 break-words text-xs ${stage.status === 'failed' ? 'text-bad' : 'text-faint'}`}>
                {stage.message}
              </p>
            )}
            {stage.status === 'running' && (
              <div className="mt-1.5">
                <ProgressBar value={stage.progress} active />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

const JOB_TONE: Record<Job['status'], Tone> = {
  queued: 'neutral',
  running: 'info',
  done: 'good',
  failed: 'bad',
  cancelled: 'neutral',
}
const JOB_LABEL: Record<Job['status'], string> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
}
const JOB_KIND: Record<string, string> = {
  ingest_local: 'Preparing video',
  ingest_url: 'Downloading and preparing video',
  selftest: 'Pipeline self-test',
}

export function JobPanel({ job, title }: { job: Job; title?: string }) {
  const cancel = useMutation({ mutationFn: () => api.cancelJob(job.id) })
  const retry = useMutation({ mutationFn: () => api.retryJob(job.id) })
  const active = job.status === 'running' || job.status === 'queued'
  const done = job.stages.filter((s) => s.status === 'done').length

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-sm font-medium">{title ?? JOB_KIND[job.kind] ?? job.kind}</span>
          <Badge tone={JOB_TONE[job.status]}>{JOB_LABEL[job.status]}</Badge>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs tabular-nums text-soft">
            {done} of {job.stages.length} stages · {Math.round(job.progress * 100)}%
          </span>
          {active && (
            <button className="btn h-8 px-3" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              Cancel
            </button>
          )}
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <button className="btn h-8 px-3" onClick={() => retry.mutate()} disabled={retry.isPending}>
              {job.status === 'failed' ? 'Retry' : 'Resume'}
            </button>
          )}
        </div>
      </div>
      <ProgressBar
        value={job.progress}
        active={job.status === 'running'}
        tone={job.status === 'done' ? 'good' : job.status === 'failed' ? 'bad' : 'accent'}
      />
      {job.error && <p className="mt-3 break-words text-sm text-bad">{job.error}</p>}
      <div className="mt-4">
        <StageList job={job} />
      </div>
    </div>
  )
}

export function Spinner() {
  return (
    <div className="grid place-items-center py-16" aria-label="Loading">
      <span className="size-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
  )
}

export function NotBuilt({ phase, children }: { phase: string; children: ReactNode }) {
  return (
    <div className="card mx-auto max-w-2xl px-8 py-10">
      <Badge tone="warn">Not built yet · {phase}</Badge>
      <div className="mt-4 space-y-3 text-sm leading-relaxed text-soft">{children}</div>
    </div>
  )
}
