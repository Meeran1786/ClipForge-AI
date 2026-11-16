import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import { api, type ProjectOptions } from '../api'
import { MusicLibrary } from '../components/MusicLibrary'
import { OptionsForm, optionsError } from '../components/OptionsForm'
import { YouTubeChannel } from '../components/YouTubeChannel'
import { Badge, Empty, ErrorNote, JobPanel, PageHeader, Section, Spinner } from '../ui'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2 text-sm">
      <dt className="shrink-0 text-soft">{label}</dt>
      <dd className="min-w-0 break-words text-right">{children}</dd>
    </div>
  )
}

function Defaults({ categories }: { categories: string[] }) {
  const qc = useQueryClient()
  const defaults = useQuery({ queryKey: ['defaults'], queryFn: api.defaults })
  const [draft, setDraft] = useState<ProjectOptions | null>(null)

  useEffect(() => {
    if (defaults.data && draft === null) setDraft(defaults.data)
  }, [defaults.data, draft])

  const save = useMutation({
    mutationFn: api.saveDefaults,
    onSuccess: (saved) => {
      qc.setQueryData(['defaults'], saved)
      setDraft(saved)
    },
  })

  if (defaults.isError) return <ErrorNote>{defaults.error.message}</ErrorNote>
  if (!draft || !defaults.data) return <Spinner />
  const problem = optionsError(draft)
  const changed = JSON.stringify(draft) !== JSON.stringify(defaults.data)

  return (
    <>
      <OptionsForm value={draft} categories={categories} onChange={(next) => { save.reset(); setDraft(next) }} />
      <div className="mt-6 flex items-center gap-4">
        <button
          className="btn btn-primary"
          disabled={!changed || problem !== null || save.isPending}
          onClick={() => save.mutate(draft)}
        >
          {save.isPending ? 'Saving…' : 'Save defaults'}
        </button>
        {problem ? (
          <span className="text-sm text-soft">{problem}</span>
        ) : save.isError ? (
          <span className="text-sm text-bad">{save.error.message}</span>
        ) : save.isSuccess && !changed ? (
          <span className="text-sm text-good">Saved</span>
        ) : null}
      </div>
    </>
  )
}

const WEIGHT_LABEL: Record<string, string> = {
  entertainment: 'Entertainment',
  emotion: 'Emotion',
  surprise: 'Surprise',
  retention: 'Retention',
  context: 'Setup and payoff',
  replay: 'Replay',
  standalone: 'Stands alone',
}

function ScoreWeights() {
  const qc = useQueryClient()
  const current = useQuery({ queryKey: ['weights'], queryFn: api.weights })
  const [draft, setDraft] = useState<Record<string, number> | null>(null)

  useEffect(() => {
    if (current.data && draft === null)
      setDraft(Object.fromEntries(Object.entries(current.data.weights).map(([k, v]) => [k, Math.round(v * 100)])))
  }, [current.data, draft])

  const save = useMutation({
    mutationFn: (w: Record<string, number>) => api.saveWeights(w),
    onSuccess: (saved) => {
      qc.setQueryData(['weights'], saved)
      setDraft(Object.fromEntries(Object.entries(saved.weights).map(([k, v]) => [k, Math.round(v * 100)])))
      void qc.invalidateQueries({ queryKey: ['project'] })
      void qc.invalidateQueries({ queryKey: ['moments'] })
    },
  })

  if (current.isError) return <ErrorNote>{current.error.message}</ErrorNote>
  if (!draft || !current.data) return <Spinner />
  const total = Object.values(draft).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  const valid = Object.values(draft).every((v) => Number.isFinite(v) && v >= 0) && total > 0

  return (
    <>
      <p className="mb-4 text-sm text-soft">
        The AI gives each moment seven marks out of ten. The viral score is their weighted sum, worked out here, so
        changing a weight re-scores every moment at once without asking the AI again.
      </p>
      <div className="space-y-2.5">
        {Object.keys(WEIGHT_LABEL).map((key) => (
          <div key={key} className="flex items-center gap-3">
            <label htmlFor={`w-${key}`} className="w-36 shrink-0 text-sm">
              {WEIGHT_LABEL[key]}
            </label>
            <input
              id={`w-${key}`}
              type="number"
              min={0}
              max={100}
              className="input h-9 w-20 tabular-nums"
              value={Number.isNaN(draft[key]) ? '' : draft[key]}
              onChange={(e) => {
                save.reset()
                setDraft({ ...draft, [key]: e.target.valueAsNumber })
              }}
            />
            <span className="min-w-0 text-xs text-faint">{current.data.meaning[key]}</span>
          </div>
        ))}
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save weights'}
        </button>
        <button
          className="btn"
          disabled={save.isPending}
          onClick={() => {
            save.reset()
            setDraft(
              Object.fromEntries(Object.entries(current.data.defaults).map(([k, v]) => [k, Math.round(v * 100)])),
            )
          }}
        >
          Reset to defaults
        </button>
        <span className="text-sm text-soft">
          {!valid
            ? 'Weights must be zero or more, and not all zero.'
            : save.isError
              ? save.error.message
              : save.isSuccess
                ? 'Saved'
                : total === 100
                  ? 'Total 100'
                  : `Total ${total}: scaled to 100 when saved`}
        </span>
      </div>
    </>
  )
}

function SelfTest() {
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs() })
  const start = useMutation({ mutationFn: () => api.selfTest(6) })
  const latest = jobs.data?.find((j) => j.kind === 'selftest')
  const busy = latest?.status === 'running' || latest?.status === 'queued'

  return (
    <Section
      title="Pipeline self-test"
      aside={
        <button className="btn h-8 px-3" onClick={() => start.mutate()} disabled={busy || start.isPending}>
          {latest ? 'Run again' : 'Run self-test'}
        </button>
      }
    >
      <p className="mb-5 text-sm text-soft">
        Checks that FFmpeg works, renders a one-second captioned vertical test Short, and checks storage. The last two
        stages only wait a few seconds each, so you can watch progress, cancel, or close the app mid-run and see it
        continue when you start it again.
      </p>
      {start.isError && (
        <div className="mb-4">
          <ErrorNote>{start.error.message}</ErrorNote>
        </div>
      )}
      {latest ? <JobPanel job={latest} /> : <Empty title="The self-test has not been run" />}
    </Section>
  )
}

export default function Settings() {
  const system = useQuery({ queryKey: ['system'], queryFn: api.system, staleTime: 60_000 })

  if (system.isPending) return <Spinner />
  if (system.isError) return <ErrorNote>{system.error.message}</ErrorNote>
  const s = system.data
  const lowDisk = s.disk_free_gb < 10

  return (
    <>
      <PageHeader title="Settings" subtitle="This computer, your default clip settings, and a pipeline check." />
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Section title="This computer">
            <dl className="divide-y divide-line">
              <Row label="ClipForge AI">Version {s.version}</Row>
              <Row label="FFmpeg">
                {s.ffmpeg && s.ffprobe ? <Badge tone="good">Installed · {s.ffmpeg}</Badge> : <Badge tone="bad">Missing</Badge>}
              </Row>
              <Row label="Python">{s.python}</Row>
              <Row label="Data folder">{s.data_dir}</Row>
              <Row label="Free space">
                <span className={lowDisk ? 'text-accent' : ''}>
                  {s.disk_free_gb} GB of {s.disk_total_gb} GB
                </span>
              </Row>
              <Row label="AI for picking moments">
                {s.gemini_route === 'api' ? 'Gemini API key' : 'Antigravity sign-in'}
                {s.ai_models && <span className="text-faint"> · {s.ai_models}</span>}
              </Row>
              <Row label="Speech-to-text">
                On this computer <span className="text-faint">· {s.speech_model} model</span>
              </Row>
              <Row label="Caption fonts">
                {s.fonts_installed === s.fonts_total ? (
                  <Badge tone="good">{s.fonts_installed} installed</Badge>
                ) : (
                  <Badge tone="bad">
                    {s.fonts_installed} of {s.fonts_total} installed
                  </Badge>
                )}
              </Row>
            </dl>
            {lowDisk && (
              <p className="mt-3 text-xs text-accent">
                Free space is low. A three-hour video plus its working files can need 10 GB or more.
              </p>
            )}
          </Section>

          <YouTubeChannel />

          <Section title="Not built yet">
            <ul className="space-y-2 text-sm text-soft">
              <li>Choosing the AI provider and models here (set in the .env file for now)</li>
              <li>The export folder is set with CLIPFORGE_EXPORT_DIR in the .env file</li>
            </ul>
          </Section>
        </div>

        <div className="space-y-6">
          <Section title="Default clip settings">
            <p className="mb-5 text-sm text-soft">New projects start with these. Each project can change them.</p>
            <Defaults categories={s.categories} />
          </Section>
          <Section title="Viral score weights">
            <ScoreWeights />
          </Section>
          <SelfTest />
        </div>
      </div>
      <div className="mt-6">
        <MusicLibrary />
      </div>
    </>
  )
}
