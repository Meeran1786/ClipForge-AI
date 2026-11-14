import type { RightsStatus } from '../api'
import { RIGHTS } from '../format'

const ORDER: RightsStatus[] = ['own', 'creator_permission', 'licensed', 'unverified']

export interface RightsValue {
  status: RightsStatus
  creator: string
  note: string
}

export function RightsFields({
  value,
  onChange,
  idPrefix = 'rights',
}: {
  value: RightsValue
  onChange: (next: RightsValue) => void
  idPrefix?: string
}) {
  const needsEvidence = value.status === 'creator_permission' || value.status === 'licensed'
  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Rights">
        {ORDER.map((r) => (
          <button
            key={r}
            type="button"
            role="radio"
            aria-checked={value.status === r}
            onClick={() => onChange({ ...value, status: r })}
            className={`cursor-pointer rounded-lg border px-3.5 py-3 text-left transition-colors ${
              value.status === r ? 'border-accent bg-accent/10' : 'border-line bg-bg hover:border-faint'
            }`}
          >
            <div className="text-sm font-medium">{RIGHTS[r].label}</div>
            <div className="mt-0.5 text-xs leading-relaxed text-soft">{RIGHTS[r].help}</div>
          </button>
        ))}
      </div>

      {value.status !== 'own' && (
        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor={`${idPrefix}-creator`}>
              Creator to credit
            </label>
            <input
              id={`${idPrefix}-creator`}
              className="input"
              placeholder="Streamer or channel name"
              value={value.creator}
              maxLength={200}
              onChange={(e) => onChange({ ...value, creator: e.target.value })}
            />
            <p className="hint">Shown on screen and in the description of every Short from this video.</p>
          </div>
          <div>
            <label className="label" htmlFor={`${idPrefix}-note`}>
              Where the permission comes from{' '}
              {!needsEvidence && <span className="font-normal text-faint">(optional)</span>}
            </label>
            <input
              id={`${idPrefix}-note`}
              className="input"
              placeholder="Link to their clip policy, the licence, or where they said yes"
              value={value.note}
              maxLength={2000}
              onChange={(e) => onChange({ ...value, note: e.target.value })}
            />
          </div>
        </div>
      )}
      <p className="hint mt-4">
        Crediting a creator is good practice but is not permission. A credited clip can still get a copyright claim.
      </p>
    </div>
  )
}
