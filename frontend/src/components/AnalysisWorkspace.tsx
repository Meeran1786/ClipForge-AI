import { useQuery } from '@tanstack/react-query'
import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Moment, type Project, type Signals, type TranscriptLine } from '../api'
import { duration as clock } from '../format'
import { MomentsPanel } from './MomentsPanel'
import { Empty, Section, Spinner } from '../ui'

const VIEW_W = 1000
const VIEW_H = 120

/** Loudness in dB mapped to 0..1 across the range this recording actually uses. */
function scaleLoudness(values: number[]): number[] {
  if (values.length === 0) return []
  const sorted = [...values].sort((a, b) => a - b)
  const lo = sorted[Math.floor(sorted.length * 0.05)]
  const hi = sorted[sorted.length - 1]
  const span = Math.max(hi - lo, 1)
  return values.map((v) => Math.max(0, Math.min(1, (v - lo) / span)))
}

function areaPath(values: number[], hop: number, total: number, top: number, height: number): string {
  if (values.length === 0 || total <= 0) return ''
  const x = (i: number) => (Math.min((i + 0.5) * hop, total) / total) * VIEW_W
  let d = `M0,${top + height}`
  values.forEach((v, i) => {
    d += `L${x(i).toFixed(1)},${(top + height - v * height).toFixed(1)}`
  })
  return `${d}L${VIEW_W},${top + height}Z`
}

function Timeline({
  signals,
  moments,
  time,
  onSeek,
}: {
  signals: Signals
  moments: Moment[]
  time: number
  onSeek: (t: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const total = signals.duration

  const loudPath = useMemo(
    () => areaPath(scaleLoudness(signals.loudness), signals.loudness_hop, total, 22, 62),
    [signals, total],
  )
  const motionPath = useMemo(() => {
    const peak = Math.max(...signals.motion, 0.0001)
    return areaPath(signals.motion.map((m) => Math.min(1, m / peak)), signals.motion_hop, total, 90, 26)
  }, [signals, total])

  const at = (e: MouseEvent) => {
    const box = ref.current!.getBoundingClientRect()
    return Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * total
  }
  const px = (t: number) => (t / total) * VIEW_W

  return (
    <div>
      <div
        ref={ref}
        className="relative cursor-pointer select-none rounded-md bg-bg"
        onClick={(e) => onSeek(at(e))}
        onMouseMove={(e) => setHover(at(e))}
        onMouseLeave={() => setHover(null)}
        role="slider"
        aria-label="Video timeline"
        aria-valuemin={0}
        aria-valuemax={Math.round(total)}
        aria-valuenow={Math.round(time)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') onSeek(Math.min(total, time + 5))
          if (e.key === 'ArrowLeft') onSeek(Math.max(0, time - 5))
        }}
      >
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} preserveAspectRatio="none" className="block h-36 w-full">
          {signals.silences.map(([s, e], i) => (
            <rect key={i} x={px(s)} y={22} width={Math.max(px(e) - px(s), 0.5)} height={62} className="fill-info/15" />
          ))}
          <path d={loudPath} className="fill-accent/70" />
          <path d={motionPath} className="fill-soft/40" />
          {signals.cuts.map((t, i) => (
            <line key={i} x1={px(t)} x2={px(t)} y1={90} y2={116} className="stroke-ink/25" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          {moments.map((mo) => (
            <rect
              key={mo.id}
              x={px(mo.start)}
              y={0}
              width={Math.max(px(mo.end) - px(mo.start), 2)}
              height={5}
              rx={1}
              className={mo.selected ? 'fill-good' : 'fill-good/35'}
            />
          ))}
          {signals.events.map((ev, i) => (
            <rect
              key={i}
              x={px(ev.t) - 1.5}
              y={7}
              width={3}
              height={12}
              rx={1}
              className={ev.kind === 'surge_after_silence' ? 'fill-bad' : 'fill-accent'}
            />
          ))}
          <line x1={px(time)} x2={px(time)} y1={0} y2={VIEW_H} className="stroke-ink" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          {hover !== null && (
            <line x1={px(hover)} x2={px(hover)} y1={0} y2={VIEW_H} className="stroke-ink/30" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {hover !== null && (
          <span
            className="pointer-events-none absolute top-1 -translate-x-1/2 rounded bg-raised px-1.5 py-0.5 text-[11px] tabular-nums text-ink"
            style={{ left: `${Math.max(3, Math.min(97, (hover / total) * 100))}%` }}
          >
            {clock(hover)}
          </span>
        )}
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-faint">
        <span>0:00</span>
        <span>{clock(total / 2)}</span>
        <span>{clock(total)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-soft">
        {moments.length > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-4 rounded-sm bg-good" /> Picked moment ({moments.length})
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-accent/70" /> Loudness
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-info/30" /> Silence ({signals.silences.length})
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-1 rounded-sm bg-accent" /> Sudden loud moment (
          {signals.events.filter((e) => e.kind === 'surge').length})
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-1 rounded-sm bg-bad" /> Loud right after silence (
          {signals.events.filter((e) => e.kind === 'surge_after_silence').length})
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-soft/40" /> On-screen motion
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-px bg-ink/40" /> Scene change ({signals.cuts.length})
        </span>
      </div>
    </div>
  )
}

function TranscriptPanel({
  lines,
  time,
  onSeek,
  footer,
}: {
  lines: TranscriptLine[]
  time: number
  onSeek: (t: number) => void
  footer: string
}) {
  const [query, setQuery] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const needle = query.trim().toLowerCase()
  const shown = useMemo(
    () => (needle ? lines.filter((l) => l.text.toLowerCase().includes(needle)) : lines),
    [lines, needle],
  )
  // the line being spoken: the last one that has started
  const activeId = useMemo(() => {
    let found: number | null = null
    for (const l of lines) {
      if (l.start <= time + 0.05) found = l.id
      else break
    }
    return found
  }, [lines, time])

  useEffect(() => {
    if (needle || activeId === null) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-line="${activeId}"]`)
    const box = listRef.current
    if (!el || !box) return
    const top = el.offsetTop - box.offsetTop
    if (top < box.scrollTop || top + el.offsetHeight > box.scrollTop + box.clientHeight)
      box.scrollTo({ top: top - box.clientHeight / 3, behavior: 'smooth' })
  }, [activeId, needle])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <input
        className="input mb-3 h-9"
        placeholder="Search the transcript"
        aria-label="Search the transcript"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div ref={listRef} className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-1" style={{ maxHeight: 360 }}>
        {shown.length === 0 ? (
          <p className="py-6 text-center text-sm text-faint">
            {needle ? 'No lines contain that.' : 'No speech was found in this video.'}
          </p>
        ) : (
          shown.map((l) => (
            <button
              key={l.id}
              data-line={l.id}
              onClick={() => onSeek(l.start)}
              className={`flex w-full cursor-pointer gap-3 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-raised ${
                l.id === activeId ? 'bg-accent/10 text-ink' : 'text-soft'
              }`}
            >
              <span className={`shrink-0 pt-px text-xs tabular-nums ${l.id === activeId ? 'text-accent' : 'text-faint'}`}>
                {clock(l.start)}
              </span>
              <span className="min-w-0 break-words">{l.text}</span>
            </button>
          ))
        )}
      </div>
      <p className="mt-3 text-xs text-faint">{footer}</p>
    </div>
  )
}

export function AnalysisWorkspace({ project, busy }: { project: Project; busy: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [time, setTime] = useState(0)
  const a = project.analysis
  const hasSignals = Boolean(a.audio || a.video)

  const signals = useQuery({
    queryKey: ['signals', project.id, project.updated_at],
    queryFn: () => api.signals(project.id),
    enabled: hasSignals,
  })
  const transcript = useQuery({
    queryKey: ['transcript', project.id, a.transcript?.chunks_done ?? 0, a.transcript?.done ?? false],
    queryFn: () => api.transcript(project.id),
    enabled: Boolean(a.transcript),
  })

  const moments = useQuery({
    queryKey: ['moments', project.id, project.updated_at],
    queryFn: () => api.moments(project.id),
    enabled: Boolean(a.moments),
  })
  const momentList = moments.data ?? []

  const seek = (t: number) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = t
    setTime(t)
  }
  const play = (t: number) => {
    seek(t)
    videoRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    void videoRef.current?.play().catch(() => undefined)
  }

  const t = a.transcript
  const footer = !t
    ? ''
    : t.done
      ? `${t.lines} lines${t.language ? ` · language: ${t.language}` : ''}${t.model ? ` · ${t.model}` : ''}`
      : `Transcribing… ${t.lines} lines so far`

  return (
    <>
    <div className="mb-6 grid gap-6 lg:grid-cols-5">
      <div className="lg:col-span-3">
        <Section title="Video and signals">
          <video
            ref={videoRef}
            key={project.id}
            className="mb-4 aspect-video w-full rounded-md bg-black"
            controls
            preload="metadata"
            poster={project.has_thumbnail ? `/api/projects/${project.id}/thumbnail` : undefined}
            src={`/api/projects/${project.id}/proxy`}
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          />
          {!hasSignals ? (
            <Empty title={busy ? 'Analysing…' : 'Not analysed yet'}>
              {busy
                ? 'The timeline appears as soon as sound and picture analysis finish.'
                : 'Run the analysis to see loudness, silences and scene changes.'}
            </Empty>
          ) : signals.isPending ? (
            <Spinner />
          ) : signals.isError ? (
            <p className="text-sm text-bad">{signals.error.message}</p>
          ) : (
            <Timeline signals={signals.data} moments={momentList} time={time} onSeek={seek} />
          )}
        </Section>
      </div>

      <div className="lg:col-span-2">
        <Section title="Transcript">
          {!t ? (
            <Empty title={busy ? 'Waiting for transcription' : 'No transcript yet'}>
              {busy ? 'Speech is transcribed after the sound and picture analysis.' : undefined}
            </Empty>
          ) : transcript.isPending ? (
            <Spinner />
          ) : transcript.isError ? (
            <p className="text-sm text-bad">{transcript.error.message}</p>
          ) : (
            <TranscriptPanel lines={transcript.data.segments} time={time} onSeek={seek} footer={footer} />
          )}
        </Section>
      </div>
    </div>
    <div className="mb-6">
      <MomentsPanel project={project} moments={momentList} busy={busy} onPlay={play} />
    </div>
    </>
  )
}
