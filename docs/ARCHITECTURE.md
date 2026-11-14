# ClipForge AI — Architecture

Status: design, nothing implemented yet. Written 2026-11-14.

ClipForge AI takes one long video (URL or local file) and produces reviewed,
ready-to-publish 1080×1920 Shorts: it finds the best moments, cuts them with
context, reframes to vertical, burns in word-synced captions, optionally adds
licensed music, and writes titles/descriptions/hashtags. Nothing is published
without the user's approval.

(The original brief calls the product "ViralClip AI". The repo and folder are
`ClipForge-AI`, so that is the name used here.)

## 1. Constraints that shape the design

Measured on the development machine:

| Fact | Consequence |
| --- | --- |
| Intel i7-8565U, 4 cores / 8 threads, 16 GB RAM | Local work must be light: signal processing, face detection at low frame rates, FFmpeg rendering |
| Intel UHD 620, no discrete GPU | No local vision models or large Whisper models. Heavy AI runs in the cloud. Intel Quick Sync (`h264_qsv`) is available for encoding |
| C: has 3.8 GB free, D: has 52 GB free | Everything lives on D: — virtualenv, `node_modules`, pip/npm caches, temp files, model caches, media |
| Python 3.11.9, Node 24, Git present | Usable as-is |
| FFmpeg 9.0.2 + ffprobe in `tools\ffmpeg\` (installed 2026-11-14) | Has libass, libx264 and Quick Sync; a 1080×1920 captioned test render passed. Not on the system PATH — the app calls it by path |
| yt-dlp installed in the backend virtualenv | Updated with `pip install -U yt-dlp` |
| Antigravity CLI present and signed in (`D:\Helios\tools\agy\bin`) | Possible route to the Gemini subscription; see section 6 |

Product decisions already made by the owner:

- Default AI provider is Gemini. Providers stay swappable.
- Publishing always needs manual approval.
- One short command starts the app.

## 2. Shape of the system

A local web application: one Python process serves both the API and the built
frontend, and opens the browser.

```
clipforge  (one command)
   │
   ▼
FastAPI process ──────────────────────────────────────────────┐
 ├─ REST API + WebSocket progress                             │
 ├─ serves built React app (static files)                     │
 ├─ job worker (background thread pool, SQLite-backed queue)  │
 └─ pipeline stages ──► FFmpeg / OpenCV / AI providers        │
                                                              │
SQLite (data\clipforge.db)      media on disk (data\projects\…)
```

Why this shape:

- **No Redis/Celery.** They are awkward on Windows and unnecessary for one
  user. Jobs are rows in SQLite; a worker thread claims them. Jobs survive a
  restart and resume from the last finished stage.
- **Vite + React, built to static files.** The dev server is only needed while
  developing the UI. Normal use loads the built bundle from FastAPI, which
  keeps memory low (a Next.js dev server has run out of memory on this machine
  before).
- **SQLite.** Single user, local files. The data layer uses SQLAlchemy so a
  move to PostgreSQL later is a connection-string change plus migrations.

### Repository layout

```
ClipForge-AI\
  clipforge.cmd            one-command launcher
  backend\
    clipforge\
      api\                 FastAPI routers (projects, clips, jobs, settings, music)
      core\                config, db session, logging, job queue
      models\              SQLAlchemy models
      ingest\              base.py + youtube.py, twitch.py, kick.py, local.py
      analysis\            audio_features, scenes, transcript, candidates
      scoring\             moment detection + viral score
      clipping\            boundary refinement, de-duplication
      captions\            word alignment, ASS generation, presets
      framing\             face/facecam detection, crop paths, layouts
      music\               library, mood matching, ducking, beat sync
      render\              FFmpeg command builders
      qc\                  export checks + quality score
      metadata\            titles, descriptions, hashtags
      providers\           gemini_api, gemini_cli, claude, whisper_local
      rights\              rights records + publish gate
    tests\
  frontend\                React + TypeScript + Tailwind (Vite)
  tools\                   ffmpeg, yt-dlp (downloaded, git-ignored)
  data\                    db, projects, music library, caches (git-ignored)
  docs\
```

## 3. The pipeline

Each project runs these stages in order. Every stage writes its result to disk
and to the database, so a failed or cancelled run resumes instead of starting
over, and each stage can be tested on its own.

| # | Stage | Runs | Output |
| --- | --- | --- | --- |
| 1 | Ingest + rights record | local | source file, metadata, rights status |
| 2 | Proxy + audio extract | FFmpeg | 360p proxy video, 16 kHz mono WAV |
| 3 | Signal analysis | local | loudness curve, onsets, silence map, scene cuts |
| 4 | Transcript | cloud or existing captions | timestamped transcript with speakers where possible |
| 5 | Candidate proposal | local + LLM | 30–60 candidate windows |
| 6 | Multimodal scoring | cloud (video+audio) | category, viral score 0–100, reasons |
| 7 | Boundary refinement + de-dup | local + LLM | final start/end per clip |
| 8 | Word alignment | local Whisper, clip audio only | word-level timestamps |
| 9 | Framing | local OpenCV | crop path and layout per clip |
| 10 | Music selection | local + LLM | track choice or "no music" |
| 11 | Render | FFmpeg | 1080×1920 H.264/AAC MP4 |
| 12 | Metadata | LLM | title, description, hashtags, pinned comment |
| 13 | QC | local | pass/fail checks, quality score |
| 14 | Review | user | approve / reject / regenerate / export |

### Why detection is staged, not one big model call

Sending a 3-hour VOD to a multimodal model frame by frame is slow and
expensive, and a single pass tends to pick loud moments rather than good ones.
Instead the funnel narrows:

1. **Cheap local signals on the whole video** — loudness changes, sudden
   onsets, silence followed by a spike, overlapping speech, scene cuts. These
   are hints, never the decision.
2. **Transcript reasoning on the whole video** — the LLM reads the full
   transcript in overlapping chunks with the signal hints attached and proposes
   candidate windows. This is where conversation context, setups and
   punchlines are understood.
3. **Multimodal check on candidates only** — each candidate's low-resolution
   proxy clip (with audio) goes to the vision model for what the transcript
   cannot show: facial reactions, gameplay events, jump scares.

Stage 6 returns structured JSON per candidate: category (funny, shocking,
unexpected, horror, rage, exciting, emotional, failure, insane), seven
sub-scores (entertainment, emotional intensity, surprise, retention, context,
replay, standalone clarity), and a plain-language reason. The viral score is a
weighted sum of the sub-scores computed in code, so the weights are tunable and
the score is explainable.

### Boundaries and duplicates

- Start is moved back to the beginning of the sentence that sets the moment up;
  end is moved forward to the end of the reaction or punchline, then snapped to
  the nearest silence gap so no word is cut.
- Internal silences longer than a threshold are trimmed with short audio
  crossfades.
- Two clips overlapping more than 50% in time are merged or the lower-scoring
  one is dropped; near-identical clips are also caught by transcript similarity.

## 4. Captions

Reference studied: `youtube.com/shorts/t_92QNLlUiE`. Observed style:

- All capitals, heavy rounded sans-serif (Montserrat ExtraBold class).
- White fill, thick black outline, soft drop shadow.
- One line of two to four words at a time, centred.
- The word being spoken is yellow; the rest stay white (karaoke highlight).
- Placed in the lower part of the picture, roughly 75–80% down, clear of faces.

Implementation:

- Word timestamps come from local Whisper (`faster-whisper`, small model, int8)
  run only on each finished clip's audio — a few minutes of audio in total, so
  CPU-only is acceptable. Words below a confidence threshold are flagged for
  review instead of being guessed.
- Captions are generated as ASS subtitles with per-word colour overrides and
  burned in by FFmpeg/libass. This is fast, frame-accurate, and every style
  parameter (font, size, colours, stroke, shadow, position, animation speed) is
  a plain value in a preset.
- Presets: Viral Highlight (the reference style, default), Bold Emphasis,
  Minimal Professional, Gaming.
- Placement avoids detected faces and the facecam region; if the default band
  collides, the caption moves to the next free band.
- Fonts bundled are open-licensed only (Montserrat, Poppins, Anton — SIL OFL).

## 5. Framing and layouts

- Face detection with OpenCV's YuNet model on the proxy at 2–5 fps; positions
  are smoothed into a crop path so the frame glides rather than jitters.
- For gaming sources, a facecam detector looks for a face that stays inside a
  small fixed rectangle across the video.
- Layouts: full-screen gameplay, gameplay with facecam inset, facecam-focused
  reaction, split-screen (gameplay top, streamer bottom). The scorer suggests a
  layout per clip; the user can change it in the editor.
- Reaction zoom: a slow push-in keyed to the scored peak of the clip.

## 6. AI providers

Each responsibility has its own interface so a provider can be replaced without
touching the pipeline:

| Interface | Default | Alternative |
| --- | --- | --- |
| `Reasoner` (transcript reasoning, metadata) | Gemini | Claude |
| `VisionScorer` (clip video+audio scoring) | Gemini | — |
| `Transcriber` (full-video transcript) | platform captions, else Gemini audio | local Whisper (slow on this CPU) |
| `WordAligner` (word timestamps) | local faster-whisper | — |

**Open point — how Gemini is reached.** A Gemini Pro consumer subscription
does not include API access by itself. Two routes exist:

1. The Antigravity CLI (`agy`, `D:\Helios\tools\agy\bin`), already signed in
   with the owner's subscription. Checked 2026-11-14: version 1.4.6, lists
   Gemini Flash models, supports non-interactive runs with a JSON schema. No
   new credentials needed. Not yet verified: whether it can take a video or
   audio file as input. (The older Gemini CLI no longer accepts personal
   account sign-in.)
2. A Gemini API key from Google AI Studio (has a free tier with daily limits).
   This is the dependable route for uploading video and getting JSON back. The
   key goes in the local `.env` file, typed in by the owner.

Both sit behind the same interface; Phase 3 starts by testing route 1 with a
real video file and falls back to route 2 if it cannot see video.

The owner's Google password is never needed by the app and must not be stored
or shared.

All model names live in settings, not in code.

## 7. Ingestion and what is legally possible

| Source | Metadata | Media |
| --- | --- | --- |
| Local file | ffprobe | Always works. This is the guaranteed path |
| YouTube | YouTube Data API v3 | The API offers no download. `yt-dlp` can fetch public, non-DRM video |
| Twitch | Helix API (`videos`) | No official download endpoint. `yt-dlp` can fetch public VODs |
| Kick | Kick public API has no recording endpoints (checked 2026-11-14) | `yt-dlp`'s lookup failed for every public recording tried; use a supplied file |

As built in Phase 2: all three platforms go through one `yt-dlp`-based source
class with no cookies, sign-in, geo-bypass or browser impersonation. Platform
errors are mapped to final refusals with a plain explanation; network trouble
is retried. Downloads resume from partial files. Official-API metadata lookups
were not built, because the downloader's own lookup returns what is needed.

Rules built into the code:

- No DRM circumvention, no logging in on the user's behalf, no bypassing
  members-only, age or geo restrictions. If a fetch fails for those reasons,
  the app says why and asks for an authorised file.
- Downloading from these platforms is only within their terms when the user
  owns the content or has the creator's permission. A URL fetch therefore
  requires the rights record (section 8) to be filled in first.
- Each platform is one module implementing `IngestSource` (`can_handle`,
  `fetch_metadata`, `fetch_media`), so new platforms are additive.

## 8. Rights and publishing gate

Every source carries a rights record: original URL, creator, status (`own`,
`licensed`, `creator_permission`, `unverified`), and optional evidence (note,
link, file).

- A link is downloaded only when the status is `own`, or `licensed` /
  `creator_permission` with both a creator and permission evidence filled in
  (`rights.py: download_blocker`). The check runs when the project is created
  and again inside the lookup and download stages.
- `unverified` sources can be analysed and previewed when the user supplies
  the file. Exports carry a visible warning in the UI and in a sidecar
  `rights.json`.
- Publishing (Phase 10) is refused in code for `unverified` sources. This is a
  check in the publish service, not a UI hint.
- The app shows YouTube's reused-content risk plainly and never states that
  editing makes footage fair use or that monetisation is guaranteed.

### Creator credit

Every clip from someone else's footage credits the creator automatically:

- an on-screen tag (for example `@StreamerName` with the platform icon) in a
  corner that does not collide with captions or the facecam;
- a credit line with the creator's name and the original video link at the top
  of the generated description;
- the same details in the `rights.json` sidecar.

Credit is good practice and is usually a condition when a streamer allows
clipping, but it is not permission. A credited clip can still receive a
copyright claim or strike, and credit alone does not satisfy YouTube's
reused-content rule for monetisation. So credit never changes a source's
rights status; only permission does.

Accepted permission evidence, stored on the rights record: the streamer's
published clip policy (many state publicly that clipping with credit is
allowed), a clipping-programme membership, or a direct message or email from
the streamer.

This matters for the GTA 6 plan: streamer footage from Twitch, Kick or YouTube
is not free to reuse. The practical path is streamers who publicly allow
clipping, written permission from specific streamers, or the owner's own
gameplay.

## 9. Music

- A local library folder plus a `tracks` table: file, mood tags, BPM, and
  required licence fields (source, licence name, licence URL or file,
  attribution text). A track without licence fields cannot be selected.
- Mood comes from the clip's category and score; ordinary conversation gets no
  music. A per-project "no music" switch overrides everything.
- Mixing: speech stays at its original level, music is ducked under speech with
  FFmpeg `sidechaincompress`, fades in and out, and is cut on a beat
  (`librosa` beat tracking) so the drop lands on the scored peak when possible.
- The library starts empty. Tracks are added by the user from sources they
  hold licences for.

## 10. Quality control

Run on every rendered clip before it appears as ready:

| Check | Method |
| --- | --- |
| 1080×1920, H.264, AAC, duration in range | ffprobe |
| No black borders or frozen/black frames | `cropdetect`, `blackdetect`, `freezedetect` |
| Audio present, not clipping | `volumedetect`, `ebur128` |
| Music below speech | loudness of music stem vs speech stem |
| No long silence | `silencedetect` |
| Captions inside safe area and off faces | geometry check against detections |
| Captions match speech | alignment confidence |
| No cut words at start/end | boundary vs word timestamps |
| Not a duplicate | time overlap + transcript similarity |

Each check contributes to a quality score; failures are listed on the clip
card with the reason.

## 11. Data model (main tables)

`projects`, `sources` (with rights fields), `jobs`, `job_stages`,
`transcript_segments`, `moments` (candidates with sub-scores and reasons),
`clips` (boundaries, layout, caption preset, music, status), `renders`,
`qc_results`, `tracks`, `settings`. API keys are stored in a local `.env`
file outside git, never in the database or the repo.

## 12. Frontend

React + TypeScript + Tailwind, six pages: Dashboard, New Project, AI Analysis
(timeline with scored moments), Clip Editor, Generated Shorts (approve, reject,
regenerate, export), Settings. Progress arrives over a WebSocket so long jobs
show live stage-by-stage status.

## 13. Expected performance on this machine

Estimates, to be replaced with measurements as phases land:

- A 3-hour VOD is several GB; proxy and audio extraction is minutes.
- Cloud analysis time depends on upload speed and provider limits; expect a
  3-hour video to take well over an hour end to end.
- Rendering one 60-second Short with captions is on the order of one to a few
  minutes on CPU, faster with Quick Sync.

A discrete GPU or a cloud render worker would shorten this later; the stage
design allows either without restructuring.
