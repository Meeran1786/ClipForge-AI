# ClipForge AI

Turns one long video into reviewed, ready-to-publish YouTube Shorts: finds the
best moments, cuts them with context, reframes to 9:16, adds word-synced
captions and optional licensed music, and writes titles and hashtags. Nothing
is published without approval.

**Status:** Phases 1 to 6 of 10 are built — the app foundation, dashboard,
video ingestion, whole-video analysis, AI moment picking, clip cutting with
Smart Duration, and word-synced captions. It finds the moments worth
clipping, cuts each one on whole sentences, and shows a vertical preview with
captions burned in. It does not yet frame the shot around faces or action,
add music, render final 1080×1920 Shorts or publish. Everything so far has
been checked on test videos and an openly licensed film, not on real stream
footage. See the [roadmap](docs/ROADMAP.md) for what each
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
- Every prepared video is analysed automatically: a loudness curve, silences,
  sudden loud moments, scene changes, on-screen motion, and a transcript with
  word timings. The project page shows them on a clickable timeline beside a
  searchable transcript. Transcription runs on this computer; the speech model
  (about 500 MB) downloads on first use. A three-hour stream takes roughly
  20–40 minutes to transcribe, and an interrupted run continues from the last
  finished ten-minute chunk.
- After analysis the AI picks the moments worth clipping. Each gets a
  category, seven marks out of ten, a viral score from 0 to 100, a reason in
  plain words, and a note on what frames from the video showed. The project
  page lists them with filters and a play button, and marks them on the
  timeline. Changing a project's clip settings or the score weights
  (Settings) re-ranks them at once without asking the AI again.
- Picking needs an AI provider: the Antigravity command-line tool, signed in,
  or a Gemini API key in `.env`. Without one the video, timeline and
  transcript are still produced and the project says why moments are missing.
- Each selected moment is cut into a clip by the Smart Duration engine. You
  choose a target length (15, 20, 30, 35, 45, 60, 90 seconds or your own) and
  a mode: Strict (never longer), Smart (a little over or under so a sentence
  can finish) or Flexible (as long as the moment needs). A clip always starts
  at the beginning of a sentence and ends after a finished one; if nothing
  complete fits, it says so instead of cutting a sentence. Long moments can
  be split into connected Part 1 / Part 2 clips at finished sentences.
- Every clip has a plain preview, its target against actual length with the
  reason for any difference, a list of checks it passed, and Approve / Reject.
  Changing the duration settings cuts the clips again at once, with no AI
  call.
- Every clip gets captions timed to the spoken words, with the word being
  said highlighted. Four styles: Viral Highlight (the look of the reference
  Short), Bold Emphasis, Minimal Professional and Gaming. The Clip editor
  changes font, size, colours, outline, shadow, height, words per line and
  animation speed for the whole project, so connected parts match, and
  re-renders the previews. Each clip shows whether its captions passed the
  timing and placement checks, and which words the speech model was unsure
  of; those are shown as heard, never guessed. The vertical preview is a plain
  centre crop until Phase 8.
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
6. Put the caption fonts in `tools\fonts\`: `Montserrat-ExtraBold.ttf` and
   `Montserrat-Black.ttf` (github.com/JulietaUla/Montserrat), `Anton-Regular.ttf`
   and `Poppins-SemiBold.ttf` (github.com/google/fonts, `ofl/`). All are under
   the SIL Open Font License. Settings shows whether all four are found.

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
