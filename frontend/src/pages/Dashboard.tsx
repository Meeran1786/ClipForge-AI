import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ProjectRow } from '../components/ProjectRow'
import { Empty, ErrorNote, JobPanel, PageHeader, Section, Spinner } from '../ui'

function Stat({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div className="card px-5 py-4">
      <div className="text-xs font-medium text-soft">{label}</div>
      <div className="mt-1.5 text-3xl font-semibold tabular-nums tracking-tight">{value}</div>
      {note && <div className="mt-1 text-xs text-faint">{note}</div>}
    </div>
  )
}

export default function Dashboard() {
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 15_000 })
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })

  if (stats.isPending) return <Spinner />
  if (stats.isError) return <ErrorNote>{stats.error.message}</ErrorNote>
  const s = stats.data
  const titleOf = (id: number | null) => projects.data?.find((p) => p.id === id)?.title

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Long videos in, reviewed Shorts out."
        actions={
          <Link to="/new" className="btn btn-primary">
            New project
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Videos processed" value={s.videos_processed} note={`${s.projects_total} project${s.projects_total === 1 ? '' : 's'} in total`} />
        <Stat label="Clips generated" value={s.clips_total} />
        <Stat label="Shorts exported" value={s.clips_exported} />
        <Stat
          label="Jobs in progress"
          value={s.active_jobs.length}
          note={s.jobs_by_status.failed ? `${s.jobs_by_status.failed} failed` : undefined}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Section title="Processing">
            {s.active_jobs.length === 0 ? (
              <Empty title="Nothing is processing">Start a project and its progress shows here, stage by stage.</Empty>
            ) : (
              <div className="space-y-6 divide-y divide-line [&>*:not(:first-child)]:pt-6">
                {s.active_jobs.map((job) => (
                  <JobPanel key={job.id} job={job} title={titleOf(job.project_id)} />
                ))}
              </div>
            )}
          </Section>
        </div>

        <div className="lg:col-span-2">
          <Section
            title="Recent projects"
            aside={
              s.projects_total > 0 && (
                <Link to="/analysis" className="text-xs text-soft hover:text-ink">
                  View all
                </Link>
              )
            }
          >
            {s.recent_projects.length === 0 ? (
              <Empty title="No projects yet">
                <Link to="/new" className="text-accent hover:underline">
                  Create your first project
                </Link>
              </Empty>
            ) : (
              <div className="-mx-3 -my-2">
                {s.recent_projects.map((p) => (
                  <ProjectRow key={p.id} project={p} />
                ))}
              </div>
            )}
          </Section>
        </div>
      </div>
    </>
  )
}
