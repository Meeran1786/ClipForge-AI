import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api, type Project, type ProjectOptions } from '../api'
import { AnalysisWorkspace } from '../components/AnalysisWorkspace'
import { describeDuration, MODE_INFO, OptionsForm, optionsError } from '../components/OptionsForm'
import { Thumb } from '../components/ProjectRow'
import { RightsFields, type RightsValue } from '../components/RightsFields'
import { ago, CATEGORY_LABEL, duration, fileSize, PROJECT_STATUS, RIGHTS, SOURCE_LABEL } from '../format'
import { Badge, Empty, ErrorNote, JobPanel, PageHeader, Section, Spinner } from '../ui'

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <dt className="shrink-0 text-soft">{label}</dt>
      <dd className="min-w-0 break-words text-right">{children}</dd>
    </div>
  )
}

function RightsSection({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<RightsValue | null>(null)
  const rights = RIGHTS[project.rights_status]

  const save = useMutation({
    mutationFn: (v: RightsValue) =>
      api.updateRights(project.id, {
        rights_status: v.status,
        creator: v.status === 'own' ? undefined : v.creator.trim() || undefined,
        rights_note: v.status === 'own' ? undefined : v.note.trim() || undefined,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
      setDraft(null)
    },
  })

  const startEdit = () => {
    save.reset()
    setDraft({ status: project.rights_status, creator: project.creator ?? '', note: project.rights_note ?? '' })
  }

  return (
    <Section
      title="Rights and credit"
      aside={
        draft === null && (
          <button className="text-xs text-soft hover:text-ink" onClick={startEdit}>
            Edit
          </button>
        )
      }
    >
      {draft ? (
        <>
          <RightsFields value={draft} onChange={setDraft} idPrefix="edit-rights" />
          {save.isError && (
            <div className="mt-4">
              <ErrorNote>{save.error.message}</ErrorNote>
            </div>
          )}
          <div className="mt-5 flex gap-2">
            <button className="btn btn-primary" onClick={() => save.mutate(draft)} disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button className="btn" onClick={() => setDraft(null)} disabled={save.isPending}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <dl className="divide-y divide-line">
            <Fact label="Status">
              <Badge tone={rights.tone}>{rights.label}</Badge>
            </Fact>
            <Fact label="Credit">
              {project.creator ?? (project.rights_status === 'own' ? 'Not needed' : 'No creator named')}
            </Fact>
            {project.rights_note && <Fact label="Permission">{project.rights_note}</Fact>}
          </dl>
          {project.rights_status === 'unverified' && (
            <p className="mt-3 text-xs leading-relaxed text-soft">
              Clips from this video can be exported with a warning but will not be published by ClipForge.
            </p>
          )}
        </>
      )}
    </Section>
  )
}

function ClipSettings({ project }: { project: Project }) {
  const qc = useQueryClient()
  const system = useQuery({ queryKey: ['system'], queryFn: api.system, staleTime: 60_000 })
  const [draft, setDraft] = useState<ProjectOptions | null>(null)
  const save = useMutation({
    mutationFn: (o: ProjectOptions) => api.updateOptions(project.id, o),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['projects'] })
      setDraft(null)
    },
  })
  const o = project.options
  const problem = draft ? optionsError(draft) : null

  return (
    <Section
      title="Clip settings"
      aside={
        draft === null && (
          <button
            className="text-xs text-soft hover:text-ink"
            onClick={() => {
              save.reset()
              setDraft(o)
            }}
          >
            Edit
          </button>
        )
      }
    >
      {draft && system.data ? (
        <>
          <OptionsForm value={draft} categories={system.data.categories} onChange={setDraft} />
          <p className="hint">
            Saving re-ranks the moments already picked and cuts the clips again. It does not ask the AI again; use
            “Pick moments again” for that. If you turn part splitting on, pick moments again so the AI looks for
            longer ones.
          </p>
          {save.isError && (
            <div className="mt-3">
              <ErrorNote>{save.error.message}</ErrorNote>
            </div>
          )}
          <div className="mt-4 flex items-center gap-2">
            <button
              className="btn btn-primary"
              onClick={() => save.mutate(draft)}
              disabled={problem !== null || save.isPending}
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button className="btn" onClick={() => setDraft(null)} disabled={save.isPending}>
              Cancel
            </button>
            {problem && <span className="text-sm text-soft">{problem}</span>}
          </div>
        </>
      ) : (
        <dl className="divide-y divide-line">
          <Fact label="Looking for">{o.categories.map((c) => CATEGORY_LABEL[c] ?? c).join(', ')}</Fact>
          <Fact label="Maximum clips">{o.max_clips}</Fact>
          <Fact label="Minimum score">{o.min_score}</Fact>
          <Fact label="Clip length">
            {describeDuration(o)} · {MODE_INFO[o.duration_mode].label}
          </Fact>
          <Fact label="Long moments">
            {o.split_parts ? `Split into up to ${o.max_parts} parts` : 'Not split into parts'}
          </Fact>
          <Fact label="Dead air">{o.trim_silence ? 'Removed' : 'Kept'}</Fact>
        </dl>
      )}
    </Section>
  )
}

function AttachFile({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [path, setPath] = useState('')
  const pick = useMutation({
    mutationFn: api.pickFile,
    onSuccess: (r) => {
      if (r.path) setPath(r.path)
    },
  })
  const attach = useMutation({
    mutationFn: () => api.attachFile(project.id, path.trim()),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
      void qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  return (
    <Section title="Use a file from this computer">
      <p className="mb-4 text-sm text-soft">
        If you already have this video as a file, add it here and ClipForge will use it instead of downloading. The
        link, credit and rights record stay with the project.
      </p>
      <div className="flex gap-2">
        <input
          className="input"
          aria-label="Video file"
          placeholder="D:\Videos\stream.mp4"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button type="button" className="btn h-10 shrink-0" onClick={() => pick.mutate()} disabled={pick.isPending}>
          {pick.isPending ? 'Choosing…' : 'Browse…'}
        </button>
        <button
          type="button"
          className="btn btn-primary h-10 shrink-0"
          onClick={() => attach.mutate()}
          disabled={path.trim() === '' || attach.isPending}
        >
          {attach.isPending ? 'Adding…' : 'Use this file'}
        </button>
      </div>
      {(attach.isError || pick.isError) && (
        <p className="mt-2 text-sm text-bad">{(attach.error ?? pick.error)?.message}</p>
      )}
    </Section>
  )
}

export default function ProjectDetail() {
  const id = Number(useParams().id)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)

  const project = useQuery({
    queryKey: ['project', id],
    queryFn: () => api.project(id),
    enabled: Number.isInteger(id),
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 1,
  })
  const jobs = useQuery({ queryKey: ['jobs', id], queryFn: () => api.jobs(id), enabled: Number.isInteger(id) })

  const remove = useMutation({
    mutationFn: () => api.deleteProject(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ['project', id] })
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['stats'] })
      navigate('/analysis')
    },
  })

  const reanalyse = useMutation({
    mutationFn: () => api.analyze(id),
    onSuccess: (updated) => {
      qc.setQueryData(['project', id], updated)
      void qc.invalidateQueries({ queryKey: ['jobs', id] })
    },
  })

  if (!Number.isInteger(id) || (project.isError && project.error instanceof ApiError && project.error.status === 404))
    return (
      <Empty title="Project not found">
        <Link to="/analysis" className="text-accent hover:underline">
          Back to all projects
        </Link>
      </Empty>
    )
  if (project.isPending) return <Spinner />
  if (project.isError) return <ErrorNote>{project.error.message}</ErrorNote>

  const p = project.data
  const status = PROJECT_STATUS[p.status]
  const m = p.media
  const meta = p.source_meta
  const latestJob = jobs.data?.[0]
  const jobActive = latestJob?.status === 'running' || latestJob?.status === 'queued'
  const isLink = p.source_kind !== 'local'
  const canAttach = isLink && !jobActive && (p.status === 'needs_rights' || p.status === 'failed')

  return (
    <>
      <PageHeader
        title={p.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone={status.tone}>{status.label}</Badge>
            <span>
              {SOURCE_LABEL[p.source_kind]} · created {ago(p.created_at)}
            </span>
          </span>
        }
        actions={
          confirming ? (
            <>
              <span className="text-sm text-soft">Delete this project?</span>
              <button className="btn btn-danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
                Yes, delete
              </button>
              <button className="btn" onClick={() => setConfirming(false)}>
                Keep
              </button>
            </>
          ) : (
            <button className="btn" onClick={() => setConfirming(true)}>
              Delete project
            </button>
          )
        }
      />

      {remove.isError && (
        <div className="mb-5">
          <ErrorNote>{remove.error.message}</ErrorNote>
        </div>
      )}
      {p.status_detail && (
        <div
          className={`mb-5 rounded-lg border px-3.5 py-2.5 text-sm leading-relaxed ${
            p.status === 'failed' ? 'border-bad/30 bg-bad/10 text-bad' : 'border-accent/30 bg-accent/10 text-accent'
          }`}
        >
          {p.status_detail}
          {p.status === 'needs_rights' && ' Edit “Rights and credit” below to continue.'}
        </div>
      )}

      {p.has_proxy && <AnalysisWorkspace project={p} busy={jobActive} />}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-2">
          <Section title="Source video">
            {!p.has_proxy && <Thumb project={p} className="mb-4 aspect-video w-full" />}
            <dl className="divide-y divide-line">
              {p.source_url && (
                <Fact label="Link">
                  <a href={p.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                    {p.source_url}
                  </a>
                </Fact>
              )}
              {meta?.title && meta.title !== p.title && <Fact label="Original title">{meta.title}</Fact>}
              {meta?.creator && <Fact label="Channel">{meta.creator}</Fact>}
              {meta?.upload_date && <Fact label="Published">{meta.upload_date}</Fact>}
              {meta?.licence && <Fact label="Platform licence">{meta.licence}</Fact>}
              {p.source_path && <Fact label="File">{p.source_path}</Fact>}
              {m ? (
                <>
                  <Fact label="Length">{duration(m.duration)}</Fact>
                  <Fact label="Resolution">
                    {m.width}×{m.height}
                    {m.fps ? ` · ${m.fps} fps` : ''}
                  </Fact>
                  <Fact label="Video / audio">
                    {m.video_codec ?? '—'} / {m.has_audio ? m.audio_codec : 'no audio'}
                  </Fact>
                  <Fact label="Size">{fileSize(m.size_bytes)}</Fact>
                </>
              ) : (
                <Fact label="Video details">Not read yet</Fact>
              )}
              {p.status === 'ready' && (
                <Fact label="Prepared for analysis">
                  {[p.has_proxy && 'preview copy', p.has_audio_file && 'audio track'].filter(Boolean).join(' + ') ||
                    'nothing'}
                </Fact>
              )}
            </dl>
            {m && !m.has_audio && (
              <p className="mt-3 text-xs text-accent">
                This video has no sound, so speech and reaction detection will have nothing to work with.
              </p>
            )}
          </Section>

          <RightsSection project={p} />

          <ClipSettings project={p} />
        </div>

        <div className="space-y-6 lg:col-span-3">
          <Section
            title="Processing"
            aside={
              p.has_proxy &&
              !jobActive && (
                <button
                  className="text-xs text-soft hover:text-ink disabled:opacity-50"
                  onClick={() => reanalyse.mutate()}
                  disabled={reanalyse.isPending}
                >
                  Run analysis again
                </button>
              )
            }
          >
            {reanalyse.isError && (
              <div className="mb-4">
                <ErrorNote>{reanalyse.error.message}</ErrorNote>
              </div>
            )}
            {jobs.isPending ? (
              <Spinner />
            ) : latestJob ? (
              <JobPanel job={latestJob} />
            ) : (
              <Empty title="Nothing has run yet">
                {isLink
                  ? 'This link has not been downloaded. Confirm the rights, or add the video as a file.'
                  : 'No job exists for this project.'}
              </Empty>
            )}
          </Section>

          {canAttach && <AttachFile project={p} />}

          <Section title="Finished Shorts" aside={<Badge tone="warn">Not built yet · Phases 6–9</Badge>}>
            <Empty title="No finished Shorts yet">
              The clips above are plain previews of where each cut falls. Captions, vertical framing, music and the
              final render are built in Phases 6 to 9.
            </Empty>
          </Section>
        </div>
      </div>
    </>
  )
}
