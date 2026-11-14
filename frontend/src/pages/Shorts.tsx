import { NotBuilt, PageHeader } from '../ui'

export default function Shorts() {
  return (
    <>
      <PageHeader title="Generated Shorts" subtitle="Review, approve and export finished Shorts." />
      <NotBuilt phase="Phases 5–9">
        <p>
          Finished Shorts will be listed here with their viral score, quality checks, title, description and hashtags.
          You will be able to preview each one, approve or reject it, regenerate captions, change music, and export one
          or all.
        </p>
        <p>Nothing can appear here until clip extraction (Phase 5) and rendering (Phase 8) are built.</p>
      </NotBuilt>
    </>
  )
}
