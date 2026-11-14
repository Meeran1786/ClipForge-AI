import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import { api, type ProjectOptions } from '../api'
import { OptionsForm, optionsError } from '../components/OptionsForm'
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
              <Row label="AI provider">
                Gemini via {s.gemini_route === 'agy' ? 'Antigravity sign-in' : 'API key'}{' '}
                <span className="text-faint">· first used in Phase 3</span>
              </Row>
            </dl>
            {lowDisk && (
              <p className="mt-3 text-xs text-accent">
                Free space is low. A three-hour video plus its working files can need 10 GB or more.
              </p>
            )}
          </Section>

          <Section title="Not built yet">
            <ul className="space-y-2 text-sm text-soft">
              <li>AI provider and model choice — Phase 3</li>
              <li>Caption presets — Phase 6</li>
              <li>Music library and licences — Phase 7</li>
              <li>Export settings — Phase 9</li>
            </ul>
          </Section>
        </div>

        <div className="space-y-6">
          <Section title="Default clip settings">
            <p className="mb-5 text-sm text-soft">New projects start with these. Each project can change them.</p>
            <Defaults categories={s.categories} />
          </Section>
          <SelfTest />
        </div>
      </div>
    </>
  )
}
