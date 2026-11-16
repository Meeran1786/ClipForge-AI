import type { QcStatus, QualityReport } from '../api'
import { Badge } from '../ui'

const MARK: Record<QcStatus, string> = { ok: '✓', warn: '!', fail: '✕' }
const TONE: Record<QcStatus, string> = { ok: 'text-good', warn: 'text-accent', fail: 'text-bad' }

export function ScoreBadge({ q }: { q: QualityReport | undefined | null }) {
  if (!q) return null
  return (
    <Badge tone={!q.passed ? 'bad' : q.warnings ? 'warn' : 'good'}>
      Quality {q.score}
      {q.fails ? ` · ${q.fails} failed` : q.warnings ? ` · ${q.warnings} to look at` : ''}
    </Badge>
  )
}

/** The quality checks: problems first with their reasons; passed checks only when `all` is set. */
export function QualityList({ q, all = false }: { q: QualityReport; all?: boolean }) {
  const order: QcStatus[] = ['fail', 'warn', 'ok']
  const shown = order.flatMap((s) => q.checks.filter((c) => c.status === s && (all || s !== 'ok')))
  if (shown.length === 0) return <p className="text-xs text-good">Every quality check passed.</p>
  return (
    <ul className="space-y-1 text-xs">
      {shown.map((c) => (
        <li key={c.name} className="flex gap-2">
          <span className={`w-3 shrink-0 text-center font-bold ${TONE[c.status]}`} aria-hidden="true">
            {MARK[c.status]}
          </span>
          <span>
            <span className={c.status === 'ok' ? 'text-soft' : 'text-ink'}>{c.label}</span>
            <span className="block text-faint">{c.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}
