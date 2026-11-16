import type { ProjectStatus, RightsStatus, SourceKind } from './api'

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

export function fileSize(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

export function ago(iso: string): string {
  const diff = (new Date(iso).getTime() - Date.now()) / 1000
  const abs = Math.abs(diff)
  if (abs < 45) return 'just now'
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour')
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day')
  return new Date(iso).toLocaleDateString()
}

export const SOURCE_LABEL: Record<SourceKind, string> = {
  local: 'Local file',
  youtube: 'YouTube',
  twitch: 'Twitch',
  kick: 'Kick',
}

export const PROJECT_STATUS: Record<ProjectStatus, { label: string; tone: Tone }> = {
  created: { label: 'Created', tone: 'neutral' },
  processing: { label: 'Processing', tone: 'info' },
  ready: { label: 'Ready', tone: 'good' },
  needs_rights: { label: 'Needs rights', tone: 'warn' },
  failed: { label: 'Failed', tone: 'bad' },
}

export const RIGHTS: Record<RightsStatus, { label: string; help: string; tone: Tone }> = {
  own: { label: 'My own footage', help: 'You recorded or own this video.', tone: 'good' },
  licensed: { label: 'Licensed', help: 'You hold a licence that allows reuse.', tone: 'good' },
  creator_permission: {
    label: 'Creator permission',
    help: 'The creator allows clipping: a published clip policy, a clipping programme, or a message from them.',
    tone: 'good',
  },
  unverified: {
    label: 'Not confirmed',
    help: 'You can analyse and export with a warning, but ClipForge will not publish these clips.',
    tone: 'warn',
  },
}

export type Tone = 'neutral' | 'good' | 'bad' | 'warn' | 'info'

/** Mirrors the server rule: what is still missing before a link may be downloaded. */
export function downloadBlocker(status: RightsStatus, creator: string, note: string): string | null {
  if (status === 'own') return null
  if (status === 'unverified') return 'Confirm that you own this video or have permission to use it.'
  if (creator.trim() === '') return 'Name the creator to credit.'
  if (note.trim() === '') return 'Say where the permission comes from.'
  return null
}

export const CATEGORY_LABEL: Record<string, string> = {
  funny: 'Funny',
  shocking: 'Shocking',
  unexpected: 'Unexpected',
  horror: 'Horror',
  rage: 'Rage',
  exciting: 'Exciting',
  emotional: 'Emotional',
  failure: 'Failures',
  insane: 'Insane moments',
  suspense: 'Suspense',
  celebration: 'Celebrations',
  punchline: 'Punchlines',
  gameplay_outcome: 'Gameplay outcomes',
}

/** Every score a moment can have, in display order. */
export const SCORE_LABEL: Record<string, string> = {
  hook: 'Hook',
  entertainment: 'Entertainment',
  emotion: 'Emotional impact',
  surprise: 'Surprise',
  retention: 'Retention',
  context: 'Setup and payoff',
  replay: 'Replay',
  standalone: 'Stands alone',
  audio_quality: 'Sound quality',
  visual_quality: 'Picture quality',
}

export const SCORE_SOURCE_LABEL: Record<string, string> = {
  ai: 'AI estimate',
  measured: 'measured',
  'ai+measured': 'AI + transcript',
}
