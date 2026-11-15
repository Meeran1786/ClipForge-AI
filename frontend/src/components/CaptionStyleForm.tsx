import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { api, type CaptionPreset, type CaptionStyle, type PresetValues } from '../api'

export const EMPTY_OVERRIDES = {
  font: null,
  size: null,
  text_color: null,
  highlight_color: null,
  outline: null,
  shadow: null,
  position: null,
  uppercase: null,
  words_per_line: null,
  speed: null,
} satisfies Partial<CaptionStyle>

/** The values a style resolves to: the preset, with any changed values on top. */
export function resolveStyle(style: CaptionStyle, presets: PresetValues[]): PresetValues {
  const base = presets.find((p) => p.name === style.preset) ?? presets[0]
  const out = { ...base }
  for (const key of Object.keys(EMPTY_OVERRIDES) as (keyof typeof EMPTY_OVERRIDES)[]) {
    const v = style[key]
    if (v !== null && v !== undefined) (out as Record<string, unknown>)[key] = v
  }
  return out
}

/** @font-face rules so a style sample can be drawn in the real caption typeface. */
export function FontFaces({ fonts }: { fonts: { name: string; file: string }[] }) {
  const css = fonts
    .map((f) => `@font-face{font-family:"CF ${f.name}";src:url("/api/fonts/${f.file}") format("truetype");}`)
    .join('')
  return <style>{css}</style>
}

/** A rough, in-browser sample of the style. The rendered preview is the real thing. */
export function StyleSample({ values, enabled }: { values: PresetValues; enabled: boolean }) {
  const scale = 0.25 // the sample frame is a quarter of 1080x1920
  const words = ['WHY', 'IS', 'THAT']
  const text = (w: string) => (values.uppercase ? w : w.charAt(0) + w.slice(1).toLowerCase())
  const outline = values.outline * scale
  const shadowPx = values.shadow * scale
  const strokes =
    outline > 0
      ? [-1, 0, 1]
          .flatMap((x) => [-1, 0, 1].map((y) => `${x * outline}px ${y * outline}px 0 #000`))
          .concat(shadowPx > 0 ? [`${shadowPx}px ${shadowPx}px 0 rgba(0,0,0,.6)`] : [])
          .join(',')
      : shadowPx > 0
        ? `${shadowPx}px ${shadowPx}px ${shadowPx}px rgba(0,0,0,.6)`
        : 'none'
  return (
    <div
      className="relative mx-auto overflow-hidden rounded-md bg-gradient-to-b from-[#3a4250] to-[#1b1f27]"
      style={{ width: 1080 * scale, height: 1920 * scale }}
      aria-label="Caption style sample"
    >
      <div className="absolute inset-x-0 top-0 border-b border-dashed border-white/20" style={{ height: '18%' }}>
        <span className="absolute left-2 top-1 text-[10px] text-white/40">Part label area</span>
      </div>
      <div className="absolute inset-x-0 bottom-0 border-t border-dashed border-white/20" style={{ height: '17%' }}>
        <span className="absolute bottom-1 left-2 text-[10px] text-white/40">YouTube’s buttons and title</span>
      </div>
      {enabled ? (
        <div
          className="absolute inset-x-0 -translate-y-1/2 whitespace-nowrap text-center leading-none"
          style={{
            top: `${values.position}%`,
            fontFamily: `"CF ${values.font}", sans-serif`,
            fontSize: values.size * scale,
            textShadow: strokes,
          }}
        >
          {words.slice(0, Math.max(1, Math.min(values.words_per_line, 3))).map((w, i) => (
            <span key={w} style={{ color: i === 1 ? values.highlight_color : values.text_color }}>
              {text(w)}
              {i < 2 ? ' ' : ''}
            </span>
          ))}
        </div>
      ) : (
        <p className="absolute inset-x-0 top-1/2 text-center text-xs text-white/50">Captions off</p>
      )}
    </div>
  )
}

function Row({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] items-center gap-3">
      <label className="text-sm text-soft" htmlFor={htmlFor}>
        {label}
      </label>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function Slider({
  id,
  value,
  min,
  max,
  step = 1,
  suffix = '',
  onChange,
}: {
  id: string
  value: number
  min: number
  max: number
  step?: number
  suffix?: string
  onChange: (n: number) => void
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.valueAsNumber)}
        className="w-full accent-accent"
      />
      <span className="w-12 shrink-0 text-right text-sm tabular-nums">
        {value}
        {suffix}
      </span>
    </div>
  )
}

export function CaptionStyleForm({
  value,
  onChange,
}: {
  value: CaptionStyle
  onChange: (next: CaptionStyle) => void
}) {
  const presets = useQuery({ queryKey: ['caption-presets'], queryFn: api.captionPresets, staleTime: Infinity })
  if (!presets.data) return <p className="text-sm text-soft">Loading styles…</p>
  const list = presets.data.presets
  const resolved = resolveStyle(value, list)
  const set = <K extends keyof CaptionStyle>(key: K, v: CaptionStyle[K]) => onChange({ ...value, [key]: v })
  const changed = (Object.keys(EMPTY_OVERRIDES) as (keyof typeof EMPTY_OVERRIDES)[]).some((k) => value[k] !== null)

  return (
    <div className="grid gap-6 md:grid-cols-[1fr_auto]">
      <FontFaces fonts={presets.data.fonts} />
      <div className="space-y-5">
        <div>
          <div className="mb-2 text-sm font-medium">Style</div>
          <div className="grid grid-cols-2 gap-2">
            {list.map((p) => (
              <button
                key={p.name}
                type="button"
                aria-pressed={value.preset === p.name}
                onClick={() => onChange({ ...value, ...EMPTY_OVERRIDES, preset: p.name as CaptionPreset })}
                className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                  value.preset === p.name ? 'border-accent bg-accent/10' : 'border-line bg-bg hover:border-faint'
                }`}
              >
                <span className="font-medium">{p.label}</span>
                {p.name === 'viral' && <span className="ml-1.5 text-xs text-faint">reference look</span>}
              </button>
            ))}
          </div>
          {changed && (
            <button
              type="button"
              className="mt-2 text-xs text-soft hover:text-ink"
              onClick={() => onChange({ ...value, ...EMPTY_OVERRIDES })}
            >
              Undo my changes to this style
            </button>
          )}
        </div>

        <label className="flex cursor-pointer items-center gap-2.5 text-sm">
          <input
            type="checkbox"
            className="accent-accent"
            checked={value.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
          />
          Show captions
        </label>

        <fieldset disabled={!value.enabled} className="space-y-3.5 disabled:opacity-50">
          <Row label="Font" htmlFor="cap-font">
            <select
              id="cap-font"
              className="input h-9"
              value={resolved.font}
              onChange={(e) => set('font', e.target.value)}
            >
              {presets.data.fonts.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Size" htmlFor="cap-size">
            <Slider id="cap-size" value={resolved.size} min={36} max={160} onChange={(n) => set('size', n)} />
          </Row>
          <Row label="Text colour" htmlFor="cap-text">
            <input
              id="cap-text"
              type="color"
              className="h-9 w-16 cursor-pointer rounded border border-line bg-bg"
              value={resolved.text_color}
              onChange={(e) => set('text_color', e.target.value.toUpperCase())}
            />
          </Row>
          <Row label="Spoken word" htmlFor="cap-hot">
            <input
              id="cap-hot"
              type="color"
              className="h-9 w-16 cursor-pointer rounded border border-line bg-bg"
              value={resolved.highlight_color}
              onChange={(e) => set('highlight_color', e.target.value.toUpperCase())}
            />
          </Row>
          <Row label="Outline" htmlFor="cap-outline">
            <Slider id="cap-outline" value={resolved.outline} min={0} max={14} onChange={(n) => set('outline', n)} />
          </Row>
          <Row label="Shadow" htmlFor="cap-shadow">
            <Slider id="cap-shadow" value={resolved.shadow} min={0} max={10} onChange={(n) => set('shadow', n)} />
          </Row>
          <Row label="Height" htmlFor="cap-pos">
            <Slider id="cap-pos" value={resolved.position} min={45} max={83} suffix="%" onChange={(n) => set('position', n)} />
          </Row>
          <Row label="Words per line" htmlFor="cap-words">
            <Slider id="cap-words" value={resolved.words_per_line} min={1} max={8} onChange={(n) => set('words_per_line', n)} />
          </Row>
          <Row label="Animation speed" htmlFor="cap-speed">
            <Slider id="cap-speed" value={resolved.speed} min={0.5} max={2.5} step={0.1} suffix="×" onChange={(n) => set('speed', n)} />
          </Row>
          <Row label="Capitals">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="accent-accent"
                checked={resolved.uppercase}
                onChange={(e) => set('uppercase', e.target.checked)}
              />
              ALL CAPITALS
            </label>
          </Row>
        </fieldset>
      </div>
      <div>
        <StyleSample values={resolved} enabled={value.enabled} />
        <p className="mt-2 max-w-[270px] text-center text-[11px] leading-relaxed text-faint">
          Quick sample in the browser. The rendered preview is exact.
        </p>
      </div>
    </div>
  )
}
