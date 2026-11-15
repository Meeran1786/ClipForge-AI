# ClipForge AI — Roadmap

Each phase ends with a gate: the feature is run for real and its output
checked. A phase is not done because its screen exists.

Design reference: [ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 1 — Foundation and dashboard — done 2026-11-14

Gate results:

- Created a project from a 95-second local video through the dashboard; the
  app read 1920×1080, 30 fps, h264/aac and made a poster frame.
- Started the self-test, hard-killed the server process while stage 4 of 5 was
  running, started it again: the job resumed, the three finished stages kept
  their original timestamps (not re-run), and the job completed.
- The project was still listed after the restart.
- `clipforge.cmd` starts the server, serves the dashboard and deep links, and
  detects an already-running copy.
- 26 automated tests pass (job engine: order, resume, retry, cancel, graceful
  stop; API: projects, validation, defaults, live events).

Not covered: the Browse button's file dialog was only checked to be available
(it opens a real window, so it was not clicked in testing), and the dashboard
was checked at desktop width only.

- Repo layout, Python virtualenv and all caches on D:.
- FastAPI app, SQLite models, settings, structured logging.
- SQLite-backed job queue with resumable stages and WebSocket progress.
- React + TypeScript + Tailwind shell with the six pages and real navigation.
- Dashboard and New Project wired to the API (create project, list projects).
- `clipforge.cmd`: one command starts the server and opens the browser.
- FFmpeg/ffprobe downloaded into `tools\`.

Gate: run `clipforge`, create a project from a local file, see it listed after
a restart; a dummy multi-stage job shows live progress and resumes after the
server is killed mid-run.

## Phase 2 — Ingestion and rights record — done 2026-11-14

Gate results:

- Local file (60 s, 1080p60): preview copy 640×360 H.264 and 16 kHz mono WAV
  verified on disk with ffprobe.
- Real link: Big Buck Bunny (CC BY 3.0) from the Blender Foundation's YouTube
  channel. Without a rights record it was saved and not downloaded; after the
  record was filled in it downloaded at 1080p, was cancelled mid-download,
  resumed from the partial file and finished. Preview copy seeks in the
  dashboard player. `rights.json` written beside the files.
- Twitch: looking up a public recording works (title, channel, length, 1080p,
  size estimate). No Twitch download was run: there was no recording we had
  permission to fetch.
- Kick: the official API has no recording endpoints (docs index checked).
  The unofficial lookup failed for 9 of 9 public recordings on 3 channels, so
  Kick links currently end with an explanation and the "Use a file from this
  computer" path. Kick works through a supplied file only.
- 67 automated tests pass, all against fake platforms (no network).

Bugs found by the live run and fixed: quality selection preferred H.264 over
resolution (720p instead of 1080p); Retry left the project marked Failed;
finished stages kept a mid-run message.

Not built: metadata through the official YouTube/Twitch APIs. The downloader's
own lookup already returns title, channel, date and length without keys.

Measured here: 100 MB downloaded in about 30 s; preview copy of a 10-minute
1080p video took about 70 s, so expect 20+ minutes for a three-hour video. A
three-hour 1080p60 Twitch recording is roughly 8–10 GB.

- `IngestSource` interface; local file, YouTube, Twitch, Kick modules.
- Metadata via official APIs where keys are configured; media via `yt-dlp`
  for public, non-DRM video only.
- Rights record required before a URL fetch; clear error and "upload an
  authorised file" path when a fetch is refused.
- Proxy video and audio extraction.

Gate: ingest one local file and one URL the owner has rights to; verify proxy,
audio and metadata on disk. Confirm what Kick's API actually exposes.

## Phase 3 — Audio and video analysis — done 2026-11-15

What was built: sound analysis (loudness curve, silences, sudden loud moments),
picture analysis (scene changes, on-screen motion), a resumable transcript with
word timings from a local speech model, and a timeline + transcript view on the
project page. Analysis runs automatically after a video is prepared and can be
re-run from the project page.

Gate results:

- Ground-truth video (eight spoken sentences at known times, four hard cuts,
  one loud burst): all nine transcript lines start within 0.28 s of the true
  time; the four scene changes are exact; exactly one loud moment is flagged,
  at the burst; the eleven pauses are found.
- Real speech: Tears of Steel (CC BY 3.0, Blender Foundation, 12 minutes of
  dialogue under music and effects), compared word by word with the video's
  own uploaded English captions. All 31 lines that could be matched start
  within 1 s of their caption (median 0.24 s, worst 0.80 s). The transcript
  has 306 words against the captions' 331. Download to finished analysis took
  about three minutes.
- Dashboard: timeline and transcript render; clicking a line or the timeline
  seeks the player; transcript search filters.
- 84 automated tests pass. Tests use a stand-in speech engine, so the real
  model is exercised only by the gate runs above.

Bugs found by the gate and fixed: steady sound was labelled one long silence;
ordinary sentences after a pause were flagged as loud moments (ten false ones
on the test video); a line after a long pause was timed nine seconds early; and
the speech model's default silence filter discarded speech under music (69% of
caption words found, now 77% with the default "sensitive" setting, 85% with
the filter off at about six times the run time).

Not done, and why:

- **The gate asked for a 30–60 minute real video; the longest real one tested
  was 12 minutes.** Chunked transcription across several chunks is covered
  only by automated tests with the stand-in engine, not by a real long video.
- **Gemini access is still unverified.** Through the Antigravity sign-in, the
  tool opened the test video file, but the service answered "no capacity" on
  two Gemini tiers before it could say whether it understood the video. Each
  attempt cost about 25,000 tokens of overhead. Nothing in this phase needs
  Gemini; Phase 4 does, so this is the first thing to settle there, with an
  API key as the fallback.
- Platform captions are not used as a transcript source; the local speech
  model handles every source the same way.
- No speaker identification. It needs a model too heavy for this PC.

Measured here: the small speech model runs about 10x faster than real time on
clean speech, so a three-hour stream should transcribe in roughly 20–40
minutes. A film with long music-only stretches is slower per minute.

- First task: test Gemini access (API key vs. Gemini CLI) with a real video
  file and settle the default.
- Loudness, onset, silence and scene-cut analysis.
- Full transcript: platform captions when present, otherwise cloud audio
  transcription.
- Analysis page: video preview with timeline, transcript, signal overlays.

Gate: on a real 30–60 minute video, transcript timing is within about a second
of speech when spot-checked at ten points; signals line up with visible events.

## Phase 4 — Moment detection and viral score — built 2026-11-15, owner's gate still open

What was built: an AI provider layer; a three-step detector (the AI reads the
whole transcript with pauses and loud moments marked in; sudden loud moments
with no dialogue are added as extra candidates; the best candidates are
checked against six frames from the video); a viral score computed in code
from seven sub-scores and adjustable weights; per-project selection by
category, minimum score and clip count; a moments panel with filters, play
buttons and timeline markers; editable clip settings and score weights that
re-rank at once without asking the AI again.

Results of the runs done here:

- **Planted-highlight test.** A three-minute spoken video of mostly dull talk
  with two clear highlights. The AI picked exactly those two (scores 86 and
  79), with the setup and the punchline inside each range, and none of the
  dull lines.
- **Real film.** Tears of Steel (CC BY 3.0, Blender Foundation): 7 moments
  found, 5 selected, scores 56–74. Each reason quotes real lines and each
  frame description names things visible in those frames. Two moments came
  from sudden loud sounds and were kept only because the frames showed
  something; they ranked last. Link to finished picks took about 8 minutes;
  the AI part was about 3.5 minutes over 3 calls.
- **Dashboard.** Moments list, timeline bars, play button, category and score
  filters, weight changes and clip-setting changes all checked in the browser.
- 132 automated tests pass. They use a stand-in AI; the real AI is exercised
  only by the two runs above.

Bug found by the first real run and fixed: the picture check could discard
any moment, so a video whose picture is uninformative (a test pattern) ended
with zero moments. Now a picture that adds nothing leaves a words-based moment
and its scores untouched; only a moment found by sound alone needs the picture
to vouch for it.

**The gate is not passed.** It requires the owner to watch the top ten picks
on three of their own videos and judge at least seven of ten worth clipping.
That has not happened, and no real gaming or stream footage has been through
the detector. Until it has, treat the pick quality as promising, not proven.

Other things to know:

- **Gemini is not the model doing the picking.** Through the Antigravity
  sign-in, the Gemini tiers returned an empty reply or timed out, while
  `claude-sonnet-4-6` on the same subscription answered every time. The
  default order is therefore that model first, Gemini second
  (`CLIPFORGE_AGY_MODELS` in `.env` changes it).
- The Gemini API-key route is written but has never been run: no key was
  available.
- The picture check looks at six still frames per moment. It does not watch
  the moving video or hear the sound, and it has misread fine detail in a
  test (a 7 as a 1).
- Each AI call carries about 28,000 tokens of fixed overhead. A long video
  needs one call per 20 minutes of transcript plus one per four candidates.
- Only a single 20-minute transcript stretch has been run with the real AI.
  Videos long enough to need several stretches are covered by tests only.
- Moment ranges are rough. Clean cuts at sentence and pause boundaries are
  Phase 5.

- Candidate proposal from transcript + signals.
- Multimodal scoring of candidates: category, seven sub-scores, reason.
- Score weights in settings; filters by category and minimum score.

Gate: on three different videos the owner watches the top ten picks and rates
them. Target: at least seven of ten judged worth clipping. Tune prompts and
weights until met. **This is the checkpoint before investing in editing.**

## Planned feature — Smart Duration and context-aware part splitting

Requested 2026-11-15, to start only after Phase 4 is finished and tested. Full
requirement list and test cases: [TASKS.md](TASKS.md).

In short: the user picks a target length (15/20/30/35/45/60/90 s or custom)
and a mode (Strict, Smart, Flexible). The target guides the cut but never
breaks a sentence, punchline, reaction or event. Long moments can be split
into connected Part 1 / Part 2 / Part 3 Shorts at natural boundaries. Every
clip gets a pre-export validation report.

It is not a separate phase. Its core (duration engine, ending detection, part
splitting, the New Project panel) becomes the main body of Phase 5; caption
checks land in Phase 6, part labels and consistent layout in Phase 8, the
validation report and per-part titles in Phase 9. Dependencies: Phase 3 word
timings, silences and scene changes; Phase 4 moments.

## Phase 5 — Clip extraction

Includes the Smart Duration engine described above.

- Boundary refinement to sentence and silence boundaries, 15–90 s limits.
- Silence trimming, de-duplication.
- Plain horizontal preview clips for review.

Gate: no clip starts or ends mid-word across twenty generated clips; no two
clips show the same event.

## Phase 6 — Captions

- Word alignment with local Whisper on clip audio.
- ASS caption generator with the four presets; Viral Highlight matches the
  reference Short.
- Caption customisation in the Clip Editor; regenerate captions action.

Gate: side-by-side with the reference Short; highlight lands on the spoken
word in frame-stepped checks; low-confidence words are flagged, not invented.

## Phase 7 — Music

- Track library with mandatory licence fields; import UI.
- Mood matching, "no music" default for conversation, ducking, fades, beat cut.

Gate: with a handful of licensed tracks loaded, speech stays clearly audible
(measured loudness gap), and a track without licence data cannot be chosen.

## Phase 8 — Framing and rendering

- Face and facecam detection, smoothed crop paths, four layouts, reaction zoom.
- Final FFmpeg render: 1080×1920, H.264, AAC, source frame rate; Quick Sync
  when available.

Gate: rendered clips from a facecam gaming video and a talking-head video keep
the subject in frame throughout; captions never cover a face.

## Phase 9 — Quality control and export

- All QC checks, quality score, failure reasons on the clip card.
- Metadata generation (title, description, hashtags, pinned comment).
- Approve / reject / export one / export all, with `rights.json` sidecar.

Gate: deliberately broken renders (black bars, missing audio, loud music) are
caught; end-to-end run from one URL to a folder of approved Shorts.

## Phase 10 — YouTube publishing

- OAuth with the owner's channel via the YouTube Data API.
- Upload as private, publish only after explicit approval per clip.
- Hard refusal for sources with unverified rights.

Gate: one private upload to the owner's channel; an unverified-rights clip is
refused by the service.

## Later

Scheduling, multiple channels, batch VOD queues, analytics feedback into
scoring, thumbnail and title testing, more caption languages.

## Dependencies (all on D:)

Installed and verified 2026-11-14: the backend virtualenv with every Python
package below, the frontend packages, and FFmpeg/ffprobe. Still to fetch when
their phase starts: the Whisper model and the caption fonts.

| Item | Phase | Notes |
| --- | --- | --- |
| Python packages: fastapi, uvicorn, sqlalchemy, pydantic-settings, httpx | 1 | venv in `backend\.venv` |
| Node packages: react, vite, typescript, tailwindcss, react-router, tanstack-query | 1 | npm cache already on D: |
| FFmpeg + ffprobe (static build) | 1 | `tools\ffmpeg\` |
| yt-dlp | 2 | `tools\` |
| numpy, librosa, opencv-python-headless, scenedetect | 3 | |
| Gemini SDK or CLI bridge | 3 | needs API key or working CLI sign-in |
| faster-whisper (small model, ~500 MB) | 6 | model cache on D: |
| Fonts: Montserrat, Poppins, Anton | 6 | SIL Open Font License |

## Needed from the owner

1. Nothing yet for Gemini: Phase 3 first tests the already signed-in
   Antigravity CLI. Only if that cannot read video, a Gemini API key from
   Google AI Studio, typed into `.env` by the owner. Never a Google password.
2. Test videos the owner has the right to use, or from streamers who publicly
   allow clipping (before Phase 2 gate).
3. Music files the owner is allowed to use, for example from the YouTube Audio
   Library (before Phase 7 gate). Optional: Shorts can ship without music.
4. A Google Cloud project with the YouTube Data API enabled (before Phase 10).
