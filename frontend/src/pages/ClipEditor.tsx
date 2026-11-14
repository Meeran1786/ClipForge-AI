import { NotBuilt, PageHeader } from '../ui'

export default function ClipEditor() {
  return (
    <>
      <PageHeader title="Clip editor" subtitle="Adjust one Short: captions, framing, music and sound." />
      <NotBuilt phase="Phases 6–8">
        <p>
          This page will show a clip preview with caption styling (font, size, colours, stroke, position, animation
          speed), the four layouts for gaming footage, crop controls, music choice and volume.
        </p>
        <p>It needs captions (Phase 6), music (Phase 7) and rendering (Phase 8) to exist first.</p>
      </NotBuilt>
    </>
  )
}
