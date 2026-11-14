import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ProjectRow } from '../components/ProjectRow'
import { Empty, ErrorNote, PageHeader, Spinner } from '../ui'

export default function Analysis() {
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })

  return (
    <>
      <PageHeader
        title="AI analysis"
        subtitle="Pick a project to see its video, processing stages and, from Phase 4, the moments ClipForge found."
        actions={
          <Link to="/new" className="btn btn-primary">
            New project
          </Link>
        }
      />
      {projects.isPending ? (
        <Spinner />
      ) : projects.isError ? (
        <ErrorNote>{projects.error.message}</ErrorNote>
      ) : projects.data.length === 0 ? (
        <Empty title="No projects yet">
          <Link to="/new" className="text-accent hover:underline">
            Create your first project
          </Link>
        </Empty>
      ) : (
        <div className="card p-2">
          {projects.data.map((p) => (
            <ProjectRow key={p.id} project={p} />
          ))}
        </div>
      )}
    </>
  )
}
