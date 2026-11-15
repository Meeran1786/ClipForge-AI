import type { ProjectOptions } from '../api'
import { CATEGORY_LABEL } from '../format'

export function optionsError(o: ProjectOptions): string | null {
  if (o.categories.length === 0) return 'Pick at least one category.'
  if (!(o.max_clips >= 1 && o.max_clips <= 50)) return 'Maximum clips must be between 1 and 50.'
  if (!(o.min_duration >= 15 && o.min_duration <= 90) || !(o.max_duration >= 15 && o.max_duration <= 90))
    return 'Clip length must be between 15 and 90 seconds.'
  if (o.min_duration > o.max_duration) return 'Shortest clip length cannot be longer than the longest.'
  return null
}

function NumberField({
  id,
  label,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  id: string
  label: string
  value: number
  min: number
  max: number
  suffix?: string
  onChange: (n: number) => void
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type="number"
          className="input tabular-nums"
          min={min}
          max={max}
          value={Number.isNaN(value) ? '' : value}
          onChange={(e) => onChange(e.target.valueAsNumber)}
        />
        {suffix && (
          <span className="pointer-events-none absolute inset-y-0 right-9 flex items-center text-xs text-faint">
            {suffix}
          </span>
        )}
      </div>
    </div>
  )
}

export function OptionsForm({
  value,
  categories,
  onChange,
}: {
  value: ProjectOptions
  categories: string[]
  onChange: (next: ProjectOptions) => void
}) {
  const set = <K extends keyof ProjectOptions>(key: K, v: ProjectOptions[K]) => onChange({ ...value, [key]: v })
  const toggle = (c: string) =>
    set(
      'categories',
      value.categories.includes(c) ? value.categories.filter((x) => x !== c) : [...value.categories, c],
    )
  const all = value.categories.length === categories.length

  return (
    <div className="space-y-6">
      <fieldset>
        <div className="mb-2 flex items-center justify-between">
          <legend className="text-sm font-medium">Moments to look for</legend>
          <button
            type="button"
            className="text-xs text-soft hover:text-ink"
            onClick={() => set('categories', all ? [] : categories)}
          >
            {all ? 'Clear all' : 'Select all'}
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => {
            const on = value.categories.includes(c)
            return (
              <button
                key={c}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(c)}
                className={`cursor-pointer rounded-full border px-3 py-1.5 text-sm transition-colors ${
                  on
                    ? 'border-accent bg-accent/15 text-accent'
                    : 'border-line bg-raised text-soft hover:border-faint hover:text-ink'
                }`}
              >
                {CATEGORY_LABEL[c] ?? c}
              </button>
            )
          })}
        </div>
      </fieldset>

      <div className="grid gap-5 sm:grid-cols-3">
        <NumberField id="max-clips" label="Maximum clips" min={1} max={50} value={value.max_clips} onChange={(n) => set('max_clips', n)} />
        <NumberField id="min-dur" label="Shortest clip" suffix="sec" min={15} max={90} value={value.min_duration} onChange={(n) => set('min_duration', n)} />
        <NumberField id="max-dur" label="Longest clip" suffix="sec" min={15} max={90} value={value.max_duration} onChange={(n) => set('max_duration', n)} />
      </div>

      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <label className="text-sm font-medium" htmlFor="min-score">
            Minimum viral score
          </label>
          <span className="text-sm tabular-nums text-accent">{value.min_score}</span>
        </div>
        <input
          id="min-score"
          type="range"
          min={0}
          max={100}
          step={5}
          value={value.min_score}
          onChange={(e) => set('min_score', e.target.valueAsNumber)}
          className="w-full accent-accent"
        />
        <p className="hint">Moments scoring below this are found and listed, but not selected for clips.</p>
      </div>
    </div>
  )
}
