import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type Clip, type Project } from '../api'
import { CATEGORY_LABEL, duration as clock } from '../format'
import { Badge, Empty, ErrorNote, Section } from '../ui'
import { describeDuration, MODE_INFO } from './OptionsForm'

function Check({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  if (ok == null) return null
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${ok ? 'text-good' : 'text-bad'}`}>
      <span aria-hidden="true">{ok ? '✓' : '✕'}</span>
      {label}
    </span>
  )
}

function ClipCard({ project, clip }: { project: Project; clip: Clip }) {
  const qc = useQueryClient()
  const r = clip.report
  const review = useMutation({
    mutationFn: (v: { review: Clip['review']; override?: boolean }) =>
      api.reviewClip(project.id, clip.id, v.review, v.override),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['clips', project.id] }),
  })
  const diff = clip.duration - r.requested_seconds
  const multi = clip.parts > 1

  return (
    <li
      className={`rounded-lg border p-4 ${
        !clip.valid ? 'border-bad/30 bg-bad/5' : clip.review === 'approved' ? 'border-good/40 bg-good/5' : 'border-line'
      }`}
    >
      <div className={`grid gap-4 ${clip.has_vertical ? 'md:grid-cols-[180px_1fr]' : 'md:grid-cols-[240px_1fr]'}`}>
        <div>
          {clip.has_vertical ? (
            <video
              className="aspect-[9/16] w-full rounded-md bg-black"
              controls
              preload="none"
              poster={project.has_thumbnail ? `/api/projects/${project.id}/thumbnail` : undefined}
              src={`/api/projects/${project.id}/clips/${clip.id}/vertical?v=${encodeURIComponent(project.updated_at)}`}
            />
          ) : clip.has_preview ? (
            <video
              className="aspect-video w-full rounded-md bg-black"
              controls
              preload="none"
              src={`/api/projects/${project.id}/clips/${clip.id}/preview`}
            />
          ) : (
            <div className="grid aspect-video w-full place-items-center rounded-md bg-raised text-xs text-faint">
              No preview
            </div>
          )}
          <p className="mt-1.5 text-[11px] text-faint">
            {clip.has_vertical
              ? 'With captions. Framing is a centre crop until Phase 8.'
              : 'Plain preview. Captions are added after cutting.'}
          </p>
        </div>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">
              Clip {clip.rank}
              {multi && ` · Part ${clip.part} of ${clip.parts}`}
            </span>
            {clip.category && <Badge tone="info">{CATEGORY_LABEL[clip.category] ?? clip.category}</Badge>}
            {clip.score != null && <Badge>Score {clip.score}</Badge>}
            {!clip.valid && <Badge tone="bad">Did not pass</Badge>}
            {clip.review === 'approved' && <Badge tone="good">{clip.overridden ? 'Approved anyway' : 'Approved'}</Badge>}
            {clip.review === 'rejected' && <Badge>Rejected</Badge>}
          </div>

          <p className="mt-2 text-sm tabular-nums">
            <span className="text-soft">Target</span> {r.requested_seconds} s{' '}
            <span className="text-soft">· Cut</span> {clip.duration.toFixed(1)} s
            {Math.abs(diff) >= 0.5 && (
              <span className={diff > 0 ? 'text-accent' : 'text-soft'}>
                {' '}
                ({diff > 0 ? '+' : '−'}
                {Math.abs(diff).toFixed(1)} s)
              </span>
            )}
            <span className="text-soft">
              {' '}
              · {clock(clip.start)}–{clock(clip.end)} in the video · {MODE_INFO[r.mode]?.label ?? r.mode}
            </span>
          </p>

          {r.no_fit && <p className="mt-2 text-sm leading-relaxed text-bad">{r.no_fit}</p>}
          {r.reasons.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-sm leading-relaxed text-soft">
              {r.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          )}
          {!clip.valid && r.problems.length > 0 && (
            <p className="mt-2 text-xs text-bad">Problems: {r.problems.join('; ')}.</p>
          )}

          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
            <Check ok={r.sentence_complete} label="Last sentence finished" />
            <Check ok={r.question_answered} label="No question left hanging" />
            <Check ok={r.event_complete} label="Nothing cut off mid-event" />
            <Check ok={r.speakers_overlap_at_end == null ? null : !r.speakers_overlap_at_end} label="Nobody talked over" />
            <Check ok={r.last_word_fully_inside} label="Last word fully inside" />
            {multi && <Check ok={r.repeats_dialogue === false && r.skips_dialogue === false} label="Continues cleanly" />}
          </div>

          {clip.captions && clip.captions.word_count > 0 && (
            <p className={`mt-2 text-xs ${clip.captions.passed ? 'text-soft' : 'text-bad'}`}>
              Captions: {clip.captions.line_count} lines
              {clip.captions.passed
                ? ', all timing and placement checks passed'
                : `, ${clip.captions.checks.filter((c) => !c.ok).length} check(s) failed`}
              {clip.captions.flagged_words.length > 0 &&
                ` · ${clip.captions.flagged_words.length} word(s) the speech model was unsure of`}
              .
            </p>
          )}

          {clip.music && (
            <p className={`mt-1 text-xs ${clip.music.measured && !clip.music.measured.ok ? 'text-bad' : 'text-soft'}`}>
              {clip.music.used
                ? `Music: “${clip.music.title}”${clip.music.measured?.gap_db != null ? `, ${clip.music.measured.gap_db.toFixed(0)} dB under the speech` : ''}${clip.music.credit ? ' · needs a credit line' : ''}.`
                : `No music: ${clip.music.reason}`}
            </p>
          )}

          <p className="mt-2 text-xs text-faint">
            {r.first_word != null && (
              <>
                Starts “{r.first_word}” · ends “{r.last_word}”
              </>
            )}
            {r.ending_quality != null && ` · ending quality ${r.ending_quality}/100`}
            {r.dead_air_removed ? ` · ${r.dead_air_removed.toFixed(1)} s of dead air removed` : ''}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {clip.review !== 'approved' &&
              (clip.valid ? (
                <button
                  className="btn h-8 px-3"
                  disabled={review.isPending}
                  onClick={() => review.mutate({ review: 'approved' })}
                >
                  Approve
                </button>
              ) : (
                clip.has_preview && (
                  <button
                    className="btn btn-danger h-8 px-3"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ review: 'approved', override: true })}
                    title="Use this clip even though it did not pass the checks"
                  >
                    Approve anyway
                  </button>
                )
              ))}
            {clip.review !== 'rejected' && (
              <button className="btn h-8 px-3" disabled={review.isPending} onClick={() => review.mutate({ review: 'rejected' })}>
                Reject
              </button>
            )}
            {clip.review !== 'pending' && (
              <button
                className="text-xs text-soft hover:text-ink"
                disabled={review.isPending}
                onClick={() => review.mutate({ review: 'pending' })}
              >
                Undo
              </button>
            )}
            {clip.has_vertical && (
              <Link
                to={`/editor?project=${project.id}&clip=${clip.id}`}
                className="ml-auto text-xs text-soft hover:text-ink"
              >
                Edit captions
              </Link>
            )}
            {review.isError && <span className="text-xs text-bad">{review.error.message}</span>}
          </div>
        </div>
      </div>
    </li>
  )
}

export function ClipsPanel({ project, clips, busy }: { project: Project; clips: Clip[]; busy: boolean }) {
  const qc = useQueryClient()
  const summary = project.analysis.clips
  const hasMoments = Boolean(project.analysis.moments?.found)
  const cut = useMutation({
    mutationFn: () => api.cut(project.id),
    onSuccess: (updated) => {
      qc.setQueryData(['project', project.id], updated)
      void qc.invalidateQueries({ queryKey: ['jobs', project.id] })
    },
  })

  return (
    <Section
      title="Clips"
      aside={
        hasMoments &&
        !busy && (
          <button
            className="text-xs text-soft hover:text-ink disabled:opacity-50"
            onClick={() => cut.mutate()}
            disabled={cut.isPending}
          >
            {summary ? 'Cut again' : 'Cut clips'}
          </button>
        )
      }
    >
      {cut.isError && (
        <div className="mb-4">
          <ErrorNote>{cut.error.message}</ErrorNote>
        </div>
      )}
      {!summary ? (
        <Empty title={busy ? 'Cutting the clips…' : 'No clips cut yet'}>
          {hasMoments
            ? 'Each selected moment is cut on whole sentences using the Smart Duration settings.'
            : 'Clips are cut from the selected moments once they have been picked.'}
        </Empty>
      ) : clips.length === 0 ? (
        <Empty title="No clips">
          No moment is selected under this project’s settings. Lower the minimum score or widen the categories in
          “Clip settings”.
        </Empty>
      ) : (
        <>
          <p className="mb-4 text-sm text-soft">
            {summary.made} cut cleanly
            {summary.not_valid > 0 && ` · ${summary.not_valid} did not pass the checks`}
            {summary.multi_part_moments > 0 && ` · ${summary.multi_part_moments} moment(s) told in parts`}
            {summary.duplicates_dropped > 0 && ` · ${summary.duplicates_dropped} dropped as repeats`} · each{' '}
            {describeDuration(project.options)}. Changing “Clip settings” cuts them again without asking the AI.
          </p>
          <ol className="space-y-3">
            {clips.map((c) => (
              <ClipCard key={c.id} project={project} clip={c} />
            ))}
          </ol>
        </>
      )}
    </Section>
  )
}
