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
        <p>
          Until then, finished 1080×1920 Shorts are rendered and downloaded from each project’s clips and the Clip
          editor.
        </p>
      </NotBuilt>
    </>
  )
}
