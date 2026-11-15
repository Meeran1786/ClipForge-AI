import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, type Moment, type Project } from '../api'
import { CATEGORY_LABEL, duration as clock } from '../format'
import { Badge, Empty, ErrorNote, Section } from '../ui'

const SUBSCORE_LABEL: Record<string, string> = {
  entertainment: 'Entertainment',
  emotion: 'Emotion',
  surprise: 'Surprise',
  retention: 'Retention',
  context: 'Setup and payoff',
  replay: 'Replay',
  standalone: 'Stands alone',
}

function scoreTone(score: number): string {
  if (score >= 75) return 'text-good'
  if (score >= 55) return 'text-accent'
  return 'text-soft'
}

function MomentRow({ moment, onPlay }: { moment: Moment; onPlay: (t: number) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <li className={`rounded-lg border px-4 py-3 ${moment.selected ? 'border-accent/40 bg-accent/5' : 'border-line'}`}>
      <div className="flex gap-4">
        <div className="w-12 shrink-0 text-center">
          <div className={`text-2xl font-semibold tabular-nums leading-none ${scoreTone(moment.score)}`}>
            {moment.score}
          </div>
          <div className="mt-1 text-[11px] text-faint">#{moment.rank}</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="info">{CATEGORY_LABEL[moment.category] ?? moment.category}</Badge>
            <button
              className="cursor-pointer rounded-md border border-line bg-raised px-2 py-0.5 text-xs tabular-nums text-ink hover:border-faint"
              onClick={() => onPlay(moment.start)}
              title="Play this moment"
            >
              ▶ {clock(moment.start)}–{clock(moment.end)} · {Math.round(moment.end - moment.start)} s
            </button>
            {moment.selected && <Badge tone="warn">Selected</Badge>}
            {!moment.visual_checked && <Badge>Words only</Badge>}
            {moment.source === 'signals' && <Badge>Found by sound</Badge>}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink">{moment.reason || 'No reason given.'}</p>
          <button className="mt-1.5 cursor-pointer text-xs text-soft hover:text-ink" onClick={() => setOpen(!open)}>
            {open ? 'Hide details' : 'Show details'}
          </button>
          {open && (
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <dl className="space-y-1.5">
                {Object.entries(SUBSCORE_LABEL).map(([key, label]) => {
                  const v = moment.subscores[key] ?? 0
                  return (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      <dt className="w-28 shrink-0 text-soft">{label}</dt>
                      <dd className="flex flex-1 items-center gap-2">
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                          <span className="block h-full rounded-full bg-accent" style={{ width: `${v * 10}%` }} />
                        </span>
                        <span className="w-4 text-right tabular-nums text-ink">{v}</span>
                      </dd>
                    </div>
                  )
                })}
              </dl>
              <div className="space-y-3 text-xs leading-relaxed">
                <div>
                  <div className="font-medium text-soft">What is said</div>
                  <p className="mt-0.5 text-ink">{moment.excerpt || 'No speech in this stretch.'}</p>
                </div>
                <div>
                  <div className="font-medium text-soft">What the frames showed</div>
                  <p className="mt-0.5 text-ink">
                    {moment.visual ?? 'Not checked against the picture; the scores come from the words alone.'}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

export function MomentsPanel({
  project,
  moments,
  busy,
  onPlay,
}: {
  project: Project
  moments: Moment[]
  busy: boolean
  onPlay: (t: number) => void
}) {
  const qc = useQueryClient()
  const [category, setCategory] = useState<string | null>(null)
  const [minScore, setMinScore] = useState(0)
  const [selectedOnly, setSelectedOnly] = useState(false)
  const summary = project.analysis.moments
  const analysed = Boolean(project.analysis.transcript?.done)

  const detect = useMutation({
    mutationFn: () => api.detect(project.id),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
    },
  })

  const present = useMemo(() => [...new Set(moments.map((m) => m.category))], [moments])
  const shown = moments.filter(
    (m) => (!category || m.category === category) && m.score >= minScore && (!selectedOnly || m.selected),
  )

  const pickButton = analysed && !busy && (
    <button
      className="text-xs text-soft hover:text-ink disabled:opacity-50"
      onClick={() => detect.mutate()}
      disabled={detect.isPending}
    >
      {summary ? 'Pick moments again' : 'Pick moments'}
    </button>
  )

  return (
    <Section title="Detected moments" aside={pickButton}>
      {detect.isError && (
        <div className="mb-4">
          <ErrorNote>{detect.error.message}</ErrorNote>
        </div>
      )}
      {!summary ? (
        <Empty title={busy ? 'Looking for the best moments…' : 'No moments picked yet'}>
          {busy
            ? 'The AI reads the transcript, then checks its picks against frames from the video.'
            : analysed
              ? 'Use “Pick moments” to have the AI choose and score them.'
              : 'Moments are picked once the video has been analysed.'}
        </Empty>
      ) : moments.length === 0 ? (
        <Empty title="Nothing worth clipping was found">
          {summary.note === 'no speech and no loud moments'
            ? 'This video has no speech and no sudden loud moments to pick from.'
            : 'The AI read the transcript and did not find a moment that would stand on its own.'}
        </Empty>
      ) : (
        <>
          <p className="mb-4 text-sm text-soft">
            {summary.found} found · {summary.selected} selected for this project’s settings
            {summary.provider ? ` · picked by ${summary.provider}` : ''}
            {summary.visually_checked != null ? ` · ${summary.visually_checked} checked against the picture` : ''}
            {summary.visual_failed ? ` · ${summary.visual_failed} picture check(s) failed` : ''}
          </p>
          <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex flex-wrap gap-1.5">
              <button
                aria-pressed={category === null}
                onClick={() => setCategory(null)}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs ${
                  category === null ? 'border-accent bg-accent/15 text-accent' : 'border-line text-soft hover:text-ink'
                }`}
              >
                All
              </button>
              {present.map((c) => (
                <button
                  key={c}
                  aria-pressed={category === c}
                  onClick={() => setCategory(category === c ? null : c)}
                  className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs ${
                    category === c ? 'border-accent bg-accent/15 text-accent' : 'border-line text-soft hover:text-ink'
                  }`}
                >
                  {CATEGORY_LABEL[c] ?? c}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs text-soft">
              Score at least
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={minScore}
                onChange={(e) => setMinScore(e.target.valueAsNumber)}
                className="w-28 accent-accent"
              />
              <span className="w-6 tabular-nums text-ink">{minScore}</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-soft">
              <input
                type="checkbox"
                checked={selectedOnly}
                onChange={(e) => setSelectedOnly(e.target.checked)}
                className="accent-accent"
              />
              Selected only
            </label>
          </div>
          {shown.length === 0 ? (
            <p className="py-6 text-center text-sm text-faint">No moments match these filters.</p>
          ) : (
            <ol className="space-y-2.5">
              {shown.map((m) => (
                <MomentRow key={m.id} moment={m} onPlay={onPlay} />
              ))}
            </ol>
          )}
        </>
      )}
    </Section>
  )
}
