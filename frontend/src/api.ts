export type StageStatus = 'pending' | 'running' | 'done' | 'failed'
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Stage {
  name: string
  label: string
  status: StageStatus
  progress: number
  message: string | null
  attempts: number
}

export interface Job {
  id: number
  kind: string
  status: JobStatus
  project_id: number | null
  progress: number
  error: string | null
  created_at: string
  updated_at: string
  stages: Stage[]
}

export interface Media {
  duration: number | null
  width: number | null
  height: number | null
  fps: number | null
  video_codec: string | null
  audio_codec: string | null
  has_audio: boolean
  format: string | null
  size_bytes: number | null
}

export type RightsStatus = 'own' | 'licensed' | 'creator_permission' | 'unverified'
export type SourceKind = 'local' | 'youtube' | 'twitch' | 'kick'
export type ProjectStatus = 'created' | 'processing' | 'ready' | 'needs_rights' | 'failed'

export interface SourceMeta {
  platform: string
  video_id: string | null
  title: string | null
  creator: string | null
  creator_url: string | null
  duration: number | null
  upload_date: string | null
  licence: string | null
  webpage_url: string | null
}

export interface AnalysisSummary {
  audio: { events: number; silences: number; silence_seconds: number } | null
  video: { cuts: number } | null
  transcript: {
    chunks_done: number
    lines: number
    language: string | null
    model: string | null
    done: boolean
  } | null
  moments: {
    found: number
    selected: number
    provider?: string
    visually_checked?: number
    visual_failed?: number
    note?: string
  } | null
  clips: {
    made: number
    not_valid: number
    duplicates_dropped: number
    multi_part_moments: number
    target_seconds: number
    mode: DurationMode
    split: boolean
  } | null
}

export interface Moment {
  id: number
  rank: number
  start: number
  end: number
  peak: number
  category: string
  score: number
  subscores: Record<string, number>
  reason: string
  excerpt: string
  visual: string | null
  visual_checked: boolean
  source: 'ai_text' | 'signals'
  selected: boolean
}

export interface Weights {
  weights: Record<string, number>
  defaults: Record<string, number>
  meaning: Record<string, string>
}

export interface Signals {
  duration: number
  has_audio: boolean
  has_video: boolean
  loudness: number[]
  loudness_hop: number
  motion: number[]
  motion_hop: number
  silences: [number, number][]
  events: { t: number; kind: 'surge' | 'surge_after_silence'; rise_db: number }[]
  cuts: number[]
}

export interface TranscriptLine {
  id: number
  start: number
  end: number
  text: string
}

export interface Transcript {
  language: string | null
  model: string | null
  done: boolean
  segments: TranscriptLine[]
}

export interface RightsRecord {
  rights_status: RightsStatus
  creator?: string
  rights_note?: string
}

export interface ProjectOptions {
  categories: string[]
  max_clips: number
  min_score: number
  target_duration: number
  duration_mode: DurationMode
  tolerance_pct: number
  split_parts: boolean
  max_parts: number
  context: ContextLevel
  trim_silence: boolean
}

export type DurationMode = 'strict' | 'smart' | 'flexible'
export type ContextLevel = 'tight' | 'balanced' | 'full'

export interface ClipReport {
  requested_seconds: number
  actual_seconds: number
  mode: DurationMode
  first_word?: string | null
  last_word?: string | null
  sentence_complete?: boolean | null
  question_answered?: boolean
  event_complete?: boolean | null
  speakers_overlap_at_end?: boolean
  ending_quality?: number | null
  last_word_fully_inside?: boolean
  dead_air_removed?: number
  continues_from_previous?: boolean
  repeats_dialogue?: boolean
  skips_dialogue?: boolean
  reasons: string[]
  problems: string[]
  no_fit?: string
}

export interface Clip {
  id: number
  moment_id: number | null
  rank: number | null
  part: number
  parts: number
  start: number
  end: number
  duration: number
  category: string | null
  score: number | null
  valid: boolean
  report: ClipReport
  has_preview: boolean
  review: 'pending' | 'approved' | 'rejected'
  overridden: boolean
}

export interface Project {
  id: number
  title: string
  source_kind: SourceKind
  source_url: string | null
  source_path: string | null
  creator: string | null
  rights_status: RightsStatus
  rights_note: string | null
  options: ProjectOptions
  media: Media | null
  source_meta: SourceMeta | null
  analysis: AnalysisSummary
  has_thumbnail: boolean
  has_proxy: boolean
  has_audio_file: boolean
  download_blocker: string | null
  status: ProjectStatus
  status_detail: string | null
  clip_count: number
  created_at: string
  updated_at: string
}

export interface Stats {
  projects_total: number
  videos_processed: number
  clips_total: number
  clips_exported: number
  jobs_by_status: Partial<Record<JobStatus, number>>
  active_jobs: Job[]
  recent_projects: Project[]
}

export interface SystemInfo {
  version: string
  python: string
  os: string
  ffmpeg: string | null
  ffprobe: string | null
  data_dir: string
  disk_free_gb: number
  disk_total_gb: number
  gemini_route: string
  ai_models: string
  speech_model: string
  categories: string[]
  video_extensions: string[]
}

export interface NewProject {
  source: 'local' | 'url'
  path?: string
  url?: string
  title?: string
  creator?: string
  rights_status: RightsStatus
  rights_note?: string
  options: ProjectOptions
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError('Cannot reach ClipForge. Is the app still running?', 0)
  }
  if (res.status === 204) return undefined as T
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const message =
      body && typeof body === 'object' && 'error' in body && typeof body.error === 'string'
        ? body.error
        : `Request failed (${res.status})`
    throw new ApiError(message, res.status)
  }
  return body as T
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  stats: () => request<Stats>('/api/stats'),
  system: () => request<SystemInfo>('/api/system/info'),
  pickFile: () => post<{ path: string | null }>('/api/system/pick-file'),
  defaults: () => request<ProjectOptions>('/api/settings/defaults'),
  saveDefaults: (o: ProjectOptions) =>
    request<ProjectOptions>('/api/settings/defaults', { method: 'PUT', body: JSON.stringify(o) }),
  projects: () => request<Project[]>('/api/projects'),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  createProject: (p: NewProject) => post<Project>('/api/projects', p),
  deleteProject: (id: number) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  updateRights: (id: number, r: RightsRecord) =>
    request<Project>(`/api/projects/${id}/rights`, { method: 'PATCH', body: JSON.stringify(r) }),
  attachFile: (id: number, path: string) => post<Project>(`/api/projects/${id}/attach`, { path }),
  signals: (id: number) => request<Signals>(`/api/projects/${id}/signals`),
  transcript: (id: number) => request<Transcript>(`/api/projects/${id}/transcript`),
  analyze: (id: number) => post<Project>(`/api/projects/${id}/analyze`),
  moments: (id: number) => request<Moment[]>(`/api/projects/${id}/moments`),
  detect: (id: number) => post<Project>(`/api/projects/${id}/detect`),
  updateOptions: (id: number, o: ProjectOptions) =>
    request<Project>(`/api/projects/${id}/options`, { method: 'PATCH', body: JSON.stringify(o) }),
  clips: (id: number) => request<Clip[]>(`/api/projects/${id}/clips`),
  cut: (id: number) => post<Project>(`/api/projects/${id}/cut`),
  reviewClip: (projectId: number, clipId: number, review: Clip['review'], override = false) =>
    request<Clip>(`/api/projects/${projectId}/clips/${clipId}`, {
      method: 'PATCH',
      body: JSON.stringify({ review, override }),
    }),
  weights: () => request<Weights>('/api/settings/weights'),
  saveWeights: (w: Record<string, number>) =>
    request<Weights>('/api/settings/weights', { method: 'PUT', body: JSON.stringify(w) }),
  jobs: (projectId?: number) =>
    request<Job[]>(projectId === undefined ? '/api/jobs' : `/api/jobs?project_id=${projectId}`),
  selfTest: (stageSeconds: number) => post<Job>('/api/jobs/selftest', { stage_seconds: stageSeconds }),
  cancelJob: (id: number) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: number) => post<Job>(`/api/jobs/${id}/retry`),
}
