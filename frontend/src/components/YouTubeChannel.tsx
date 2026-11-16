import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type YouTubeStatus } from '../api'
import { Badge, ErrorNote, Section, Spinner } from '../ui'

/** Settings → YouTube channel: the owner's own OAuth client, sign-in on Google's page, and upload defaults. */
export function YouTubeChannel() {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['youtube'], queryFn: api.youtube })
  const [path, setPath] = useState('')
  const set = (s: YouTubeStatus) => qc.setQueryData(['youtube'], s)
  const loadClient = useMutation({ mutationFn: () => api.youtubeClient(path), onSuccess: set })
  const connect = useMutation({
    mutationFn: api.youtubeConnect,
    onSuccess: ({ url }) => {
      window.open(url, '_blank', 'noopener')
    },
  })
  const disconnect = useMutation({ mutationFn: api.youtubeDisconnect, onSuccess: set })
  const defaults = useMutation({ mutationFn: api.youtubeDefaults, onSuccess: set })

  if (status.isPending) return <Section title="YouTube channel"><Spinner /></Section>
  if (status.isError) return <Section title="YouTube channel"><ErrorNote>{status.error.message}</ErrorNote></Section>
  const s = status.data
  const err = loadClient.error ?? connect.error ?? disconnect.error ?? defaults.error

  return (
    <Section
      title="YouTube channel"
      aside={
        s.connected ? (
          <Badge tone="good">Connected · {s.channel?.title}</Badge>
        ) : s.client ? (
          <Badge tone="warn">Client added · not connected</Badge>
        ) : (
          <Badge>Not set up</Badge>
        )
      }
    >
      <ol className="mb-4 list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-soft">
        <li>
          In Google Cloud Console, in your own project, enable the <span className="text-ink">YouTube Data API v3</span>.
        </li>
        <li>
          Set up the OAuth consent screen (External, Testing) and add your Google account as a test user.
        </li>
        <li>
          Create an OAuth client ID of type <span className="text-ink">Desktop app</span> and download its JSON file.
        </li>
        <li>Give ClipForge that file below, then connect. You sign in on Google’s own page; ClipForge never sees your password.</li>
      </ol>

      <div className="flex flex-wrap gap-2">
        <input
          aria-label="Client file"
          className="input min-w-0 flex-1"
          placeholder={s.client ? `Client file added (${s.cloud_project ?? 'project'})` : 'Path to the client JSON file'}
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button className="btn" disabled={!path.trim() || loadClient.isPending} onClick={() => loadClient.mutate()}>
          {s.client ? 'Replace file' : 'Add file'}
        </button>
      </div>
      {s.client_error && <p className="mt-2 text-xs text-bad">{s.client_error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {s.connected ? (
          <>
            <a className="btn" href={s.channel?.url} target="_blank" rel="noreferrer">
              Open channel
            </a>
            <button className="btn" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
              Disconnect
            </button>
          </>
        ) : (
          <button className="btn btn-primary" disabled={!s.client || connect.isPending} onClick={() => connect.mutate()}>
            Connect channel
          </button>
        )}
        <button className="btn" onClick={() => void status.refetch()}>
          Check again
        </button>
      </div>
      {connect.isSuccess && !s.connected && (
        <p className="mt-2 text-xs text-soft">
          Google’s sign-in opened in a new tab. When it says “YouTube connected”, press “Check again”.
        </p>
      )}

      <div className="mt-5 grid gap-4 border-t border-line pt-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="yt-category">
            Category for uploads
          </label>
          <select
            id="yt-category"
            className="input"
            value={s.defaults.category_id}
            onChange={(e) => defaults.mutate({ ...s.defaults, category_id: e.target.value })}
          >
            {Object.entries(s.categories).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-start gap-2 pt-6 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 accent-accent"
            checked={s.defaults.made_for_kids}
            onChange={(e) => defaults.mutate({ ...s.defaults, made_for_kids: e.target.checked })}
          />
          <span>
            Made for kids
            <span className="hint block">YouTube requires this declaration for every video. Leave off unless it is.</span>
          </span>
        </label>
      </div>
      <p className="mt-4 text-xs leading-relaxed text-faint">
        Every Short is uploaded as private and made public only when you confirm it. YouTube allows about six uploads a
        day on a new project (10,000 quota units, 1,600 per upload). Videos uploaded through a project YouTube has not
        audited stay private on YouTube’s side; you can still publish them yourself in YouTube Studio.
      </p>
      {err && (
        <div className="mt-3">
          <ErrorNote>{err.message}</ErrorNote>
        </div>
      )}
    </Section>
  )
}
