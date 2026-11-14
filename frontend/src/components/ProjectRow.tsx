import { Link } from 'react-router-dom'
import type { Project } from '../api'
import { ago, duration, PROJECT_STATUS, SOURCE_LABEL } from '../format'
import { Badge } from '../ui'

export function Thumb({ project, className = '' }: { project: Project; className?: string }) {
  return (
    <div className={`grid shrink-0 place-items-center overflow-hidden rounded-md bg-raised ${className}`}>
      {project.has_thumbnail ? (
        <img
          src={`/api/projects/${project.id}/thumbnail?v=${encodeURIComponent(project.updated_at)}`}
          alt=""
          className="size-full object-cover"
        />
      ) : (
        <svg viewBox="0 0 20 20" className="size-5 text-faint" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="4.5" width="14" height="11" rx="1.5" />
          <path d="M8.5 8v4l3.5-2z" fill="currentColor" stroke="none" />
        </svg>
      )}
    </div>
  )
}

export function ProjectRow({ project }: { project: Project }) {
  const status = PROJECT_STATUS[project.status]
  return (
    <Link
      to={`/analysis/${project.id}`}
      className="flex items-center gap-4 rounded-lg px-3 py-2.5 transition-colors hover:bg-raised"
    >
      <Thumb project={project} className="h-12 w-20" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{project.title}</div>
        <div className="mt-0.5 truncate text-xs text-faint">
          {SOURCE_LABEL[project.source_kind]}
          {project.media?.duration != null && ` · ${duration(project.media.duration)}`}
          {project.creator && ` · ${project.creator}`}
          {` · ${ago(project.created_at)}`}
        </div>
      </div>
      <Badge tone={status.tone}>{status.label}</Badge>
    </Link>
  )
}
