# ClipForge AI

Turns one long video into reviewed, ready-to-publish YouTube Shorts: finds the
best moments, cuts them with context, reframes to 9:16, adds word-synced
captions and optional licensed music, and writes titles and hashtags. Nothing
is published without approval.

**Status:** Phases 1 and 2 of 10 are built — the app foundation, dashboard and
video ingestion. It does not find or cut clips yet. See the [roadmap](docs/ROADMAP.md) for what each
phase adds and the [architecture](docs/ARCHITECTURE.md) for how it fits
together.

## Run it

```
D:\ClipForge-AI\clipforge.cmd
```

This starts the app and opens the dashboard at http://127.0.0.1:8765. Close it
with Ctrl+C in that window. Running the command again while the app is open
just opens the dashboard.

Options: `--port 9000`, `--no-browser`, `--data-dir <folder>`.

## What works today

- Create a project from a video file on this computer. The app reads its
  length, resolution and codecs, makes a poster frame, a 360p preview copy and
  a 16 kHz mono audio track for analysis.
- Create a project from a YouTube or Twitch link. Public videos are downloaded
  at up to 1080p, but only once the rights record is filled in: you own it, or
  you name the creator and where the permission comes from. Otherwise the link
  is saved and not downloaded.
- Kick links are accepted, but Kick recordings currently cannot be fetched;
  add the video as a file instead. Any link project can take a file.
- The app never signs in or works around access limits. Private,
  members-only, age-restricted, DRM-protected and live videos are refused with
  an explanation.
- Each project folder gets a `rights.json` with the link, channel, credit,
  rights status and permission evidence.
- Live, stage-by-stage progress for background jobs, with cancel and retry.
  A job interrupted by closing or killing the app continues from the first
  unfinished stage when the app starts again.
- Default clip settings (categories, clip count, score, length).
- Settings → Pipeline self-test: checks FFmpeg, renders a captioned vertical
  test clip, checks storage.

Clip editor and Generated Shorts are placeholders until their phases.

## Set up on a new machine

The repository does not contain the Python environment, the built dashboard or
FFmpeg. After cloning:

1. `python -m venv backend\.venv`
2. `backend\.venv\Scripts\python -m pip install -r backend\requirements.lock.txt`
3. `backend\.venv\Scripts\python -m pip install -e backend`
4. In `frontend`: `npm install`, then `npm run build`
5. Put `ffmpeg.exe` and `ffprobe.exe` in `tools\ffmpeg\`

## Layout

| Path | What |
| --- | --- |
| `clipforge.cmd` | launcher |
| `backend/clipforge` | FastAPI app, job queue, pipelines |
| `backend/tests` | tests (`backend\.venv\Scripts\python -m pytest` from `backend`) |
| `frontend` | React dashboard (`npm run build` after changing it) |
| `tools/ffmpeg` | FFmpeg and ffprobe (not in git) |
| `data` | database, logs, per-project files (not in git) |

## Rights

Only use footage you own or have permission to reuse. The app records the
rights status of every source and will refuse to publish clips from sources
whose rights are unverified. Crediting a creator is not permission. Editing a
clip does not make it fair use, and monetisation is never guaranteed.
