import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type CaptionStyle, type Clip, type Project } from '../api'
import { CaptionStyleForm } from '../components/CaptionStyleForm'
import { CATEGORY_LABEL, duration as clock } from '../format'
import { Badge, Empty, ErrorNote, JobPanel, PageHeader, Section, Spinner } from '../ui'

const CHECK_LABEL: Record<string, string> = {
  first_caption_on_time: 'First caption appears with the first word',
  last_word_shown_in_full: 'Last word stays on screen until it is said',
  no_caption_ends_before_its_words: 'No line disappears before it is spoken',
  no_caption_lingers: 'No line hangs around after it is spoken',
  every_word_once: 'Every spoken word shown once, in order',
  clear_of_part_label: 'Clear of the Part label area',
  clear_of_platform_buttons: 'Clear of YouTube’s buttons and title',
  fits_the_width: 'Each line fits the width',
  speech: 'Speech',
  captions: 'Captions',
}

function clipLabel(c: Clip) {
  return `Clip ${c.rank}${c.parts > 1 ? ` · Part ${c.part}/${c.parts}` : ''} · ${c.duration.toFixed(0)} s`
}

function MusicPanel({ project, clip, busy }: { project: Project; clip: Clip; busy: boolean }) {
  const qc = useQueryClient()
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks })
  const music = clip.music
  const choose = useMutation({
    mutationFn: (v: { mode: 'auto' | 'none' | 'track'; trackId?: number }) =>
      api.setClipMusic(project.id, clip.id, v.mode, v.trackId),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], (old: Project | undefined) => (old ? { ...old, status: 'processing' as const } : old))
      void qc.invalidateQueries({ queryKey: ['project', project.id] })
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
      void qc.invalidateQueries({ queryKey: ['clips', project.id] })
      return updated
    },
  })
  const value = music?.used ? `track:${music.track_id}` : music?.mode === 'manual' ? 'none' : 'auto'
  const m = music?.measured

  return (
    <Section title="Music for this clip" aside={music?.used ? <Badge tone="info">{music.mood ?? 'chosen by hand'}</Badge> : <Badge>No music</Badge>}>
      <label className="label" htmlFor="clip-music">
        Track
      </label>
      <select
        id="clip-music"
        className="input"
        value={value}
        disabled={busy || choose.isPending}
        onChange={(e) => {
          const v = e.target.value
          if (v === 'auto') choose.mutate({ mode: 'auto' })
          else if (v === 'none') choose.mutate({ mode: 'none' })
          else choose.mutate({ mode: 'track', trackId: Number(v.slice(6)) })
        }}
      >
        <option value="auto">Automatic: let ClipForge choose</option>
        <option value="none">No music for this clip</option>
        {(tracks.data ?? []).map((t) => (
          <option key={t.id} value={`track:${t.id}`} disabled={!t.usable}>
            {t.title}
            {t.moods.length ? ` · ${t.moods.join(', ')}` : ''}
            {t.usable ? '' : ' (licence details missing)'}
          </option>
        ))}
      </select>
      {clip.parts > 1 && (
        <p className="hint">This applies to every part of this moment, so connected parts keep the same music.</p>
      )}
      {choose.isError && (
        <div className="mt-3">
          <ErrorNote>{choose.error.message}</ErrorNote>
        </div>
      )}

      <p className="mt-4 text-sm leading-relaxed text-soft">{music?.reason ?? 'Music is chosen when the clip is rendered.'}</p>
      {music?.used && (
        <dl className="mt-3 space-y-1 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-soft">Plays from</dt>
            <dd className="tabular-nums">{clock(music.offset ?? 0)} into the track{music.loop ? ', repeating' : ''}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-soft">Beat</dt>
            <dd className="max-w-[60%] text-right">
              {music.beat
                ? `A ${music.beat.kind === 'downbeat' ? 'bar line' : 'beat'} falls on the key moment at ${clock(music.beat.anchor)}`
                : (music.beat_note ?? '—')}
            </dd>
          </div>
          {m && m.gap_db != null && (
            <div className="flex justify-between gap-3">
              <dt className="text-soft">Under the speech</dt>
              <dd className={`tabular-nums ${m.ok ? 'text-good' : 'text-bad'}`}>
                {m.gap_db.toFixed(0)} dB {m.ok ? '✓' : `✕ (needs ${m.required_gap_db} dB or more)`}
              </dd>
            </div>
          )}
          {m && m.gap_db == null && (
            <div className="flex justify-between gap-3">
              <dt className="text-soft">Under the speech</dt>
              <dd className="text-soft">Nobody speaks here</dd>
            </div>
          )}
          <div className="flex justify-between gap-3">
            <dt className="text-soft">Licence</dt>
            <dd className="max-w-[60%] text-right">{music.licence_name} · {music.source}</dd>
          </div>
        </dl>
      )}
      {music?.used && music.credit && (
        <p className="mt-3 rounded-md border border-accent/30 bg-accent/10 px-3 py-2 text-xs leading-relaxed text-accent">
          This licence needs credit. Add to the description: “{music.credit}”
        </p>
      )}
    </Section>
  )
}

function Editor({ project, clips, clipId }: { project: Project; clips: Clip[]; clipId: number }) {
  const qc = useQueryClient()
  const [, setParams] = useSearchParams()
  const videoRef = useRef<HTMLVideoElement>(null)
  const [time, setTime] = useState(0)
  const [draft, setDraft] = useState<CaptionStyle>(project.options.caption_style)
  const jobs = useQuery({ queryKey: ['jobs', project.id], queryFn: () => api.jobs(project.id) })
  const job = jobs.data?.[0]
  const busy = job?.status === 'running' || job?.status === 'queued'

  useEffect(() => setDraft(project.options.caption_style), [project.options.caption_style])

  const save = useMutation({
    mutationFn: () => api.updateCaptions(project.id, draft),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
    },
  })

  const clip = clips.find((c) => c.id === clipId) ?? clips[0]
  const caps = clip.captions
  const dirty = JSON.stringify(draft) !== JSON.stringify(project.options.caption_style)
  const activeLine = caps?.lines.findIndex((l) => l.start <= time && time < l.end) ?? -1

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,380px)_1fr]">
      <div className="space-y-4">
        <Section
          title="Preview"
          aside={
            <select
              aria-label="Clip"
              className="input h-8 w-auto py-0 text-xs"
              value={clip.id}
              onChange={(e) => setParams({ project: String(project.id), clip: e.target.value })}
            >
              {clips.map((c) => (
                <option key={c.id} value={c.id}>
                  {clipLabel(c)}
                  {c.valid ? '' : ' (did not pass)'}
                </option>
              ))}
            </select>
          }
        >
          {clip.has_vertical ? (
            <video
              ref={videoRef}
              key={`${clip.id}-${project.updated_at}`}
              className="mx-auto aspect-[9/16] max-h-[640px] w-full rounded-md bg-black"
              controls
              preload="metadata"
              src={`/api/projects/${project.id}/clips/${clip.id}/vertical?v=${encodeURIComponent(project.updated_at)}`}
              onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            />
          ) : (
            <Empty title="No captioned preview yet">
              {busy ? 'Rendering…' : 'Captions are added after the clips are cut.'}
            </Empty>
          )}
          <p className="mt-2 text-xs leading-relaxed text-faint">
            {caps?.framing ?? 'Centre crop.'} Clip {clip.rank}
            {clip.category ? ` · ${CATEGORY_LABEL[clip.category] ?? clip.category}` : ''}.
          </p>
        </Section>

        <MusicPanel project={project} clip={clip} busy={busy} />

        {caps && (
          <Section title="Caption checks" aside={caps.passed ? <Badge tone="good">All passed</Badge> : <Badge tone="bad">Look at these</Badge>}>
            <ul className="space-y-1.5 text-sm">
              {caps.checks.map((c) => (
                <li key={c.name} className="flex gap-2">
                  <span className={c.ok ? 'text-good' : 'text-bad'} aria-hidden="true">
                    {c.ok ? '✓' : '✕'}
                  </span>
                  <span>
                    {CHECK_LABEL[c.name] ?? c.name}
                    <span className="block text-xs text-faint">{c.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
            {caps.flagged_words.length > 0 && (
              <div className="mt-4 rounded-md border border-accent/30 bg-accent/10 px-3 py-2 text-xs leading-relaxed text-accent">
                The speech model was unsure of {caps.flagged_words.length} word(s):{' '}
                {caps.flagged_words.map((f) => `“${f.word}” at ${clock(f.t)}`).join(', ')}. They are shown as heard;
                listen and reject the clip if they are wrong.
              </div>
            )}
          </Section>
        )}
      </div>

      <div className="space-y-4">
        <Section title="Caption style for this project">
          <p className="mb-5 text-sm text-soft">
            One style for every clip and every part in this project, so connected parts match. Saving re-renders the
            captioned previews; the cuts stay as they are and the AI is not asked again.
          </p>
          <CaptionStyleForm value={draft} onChange={setDraft} />
          {save.isError && (
            <div className="mt-4">
              <ErrorNote>{save.error.message}</ErrorNote>
            </div>
          )}
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" disabled={!dirty || busy || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save and re-caption all clips'}
            </button>
            {dirty && (
              <button className="btn" onClick={() => setDraft(project.options.caption_style)} disabled={save.isPending}>
                Discard changes
              </button>
            )}
            {busy && <span className="text-sm text-soft">Processing — the preview updates when it finishes.</span>}
          </div>
          {job && busy && (
            <div className="mt-5">
              <JobPanel job={job} />
            </div>
          )}
        </Section>

        {caps && caps.lines.length > 0 && (
          <Section title={`Captions (${caps.line_count} lines, ${caps.word_count} words)`}>
            <ol className="max-h-72 space-y-0.5 overflow-y-auto pr-1">
              {caps.lines.map((l, i) => (
                <li key={i}>
                  <button
                    className={`flex w-full cursor-pointer gap-3 rounded-md px-2 py-1 text-left text-sm hover:bg-raised ${
                      i === activeLine ? 'bg-accent/10 text-ink' : 'text-soft'
                    }`}
                    onClick={() => {
                      if (videoRef.current) {
                        videoRef.current.currentTime = l.start
                        setTime(l.start)
                      }
                    }}
                  >
                    <span className="w-10 shrink-0 text-xs tabular-nums text-faint">{clock(l.start)}</span>
                    <span>
                      {l.words.map((w, k) => (
                        <span key={k} className={w.p < 0.5 ? 'rounded bg-accent/20 px-0.5 text-accent' : ''}>
                          {w.w}
                          {k < l.words.length - 1 ? ' ' : ''}
                        </span>
                      ))}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </Section>
        )}
      </div>
    </div>
  )
}

export default function ClipEditor() {
  const [params, setParams] = useSearchParams()
  const projectId = Number(params.get('project'))
  const clipId = Number(params.get('clip'))
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const project = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.project(projectId),
    enabled: projectId > 0,
  })
  const clips = useQuery({
    queryKey: ['clips', projectId, project.data?.updated_at],
    queryFn: () => api.clips(projectId),
    enabled: projectId > 0,
  })

  const withClips = (projects.data ?? []).filter((p) => p.clip_count > 0)

  return (
    <>
      <PageHeader
        title="Clip editor"
        subtitle="Captions and their style, and each clip's music. Vertical framing arrives in Phase 8."
        actions={
          withClips.length > 0 && (
            <select
              aria-label="Project"
              className="input h-9 w-auto"
              value={projectId || ''}
              onChange={(e) => setParams({ project: e.target.value })}
            >
              <option value="" disabled>
                Choose a project
              </option>
              {withClips.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          )
        }
      />
      {!projectId ? (
        projects.isPending ? (
          <Spinner />
        ) : withClips.length === 0 ? (
          <Empty title="No clips to edit yet">
            Clips appear here once a project has been analysed and cut.{' '}
            <Link to="/new" className="text-accent hover:underline">
              Start a project
            </Link>
          </Empty>
        ) : (
          <Empty title="Choose a project">Pick a project above to edit its clips’ captions.</Empty>
        )
      ) : project.isError ? (
        <ErrorNote>{project.error.message}</ErrorNote>
      ) : !project.data || !clips.data ? (
        <Spinner />
      ) : clips.data.length === 0 ? (
        <Empty title="This project has no clips yet">
          <Link to={`/analysis/${projectId}`} className="text-accent hover:underline">
            Open the project
          </Link>
        </Empty>
      ) : (
        <Editor project={project.data} clips={clips.data} clipId={clipId} />
      )}
    </>
  )
}
