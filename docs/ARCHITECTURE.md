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

As built in Phase 6 (`captions.py`), with two differences from the plan above:

- Word timings come from the whole-video transcript made in Phase 3 (same
  model, already word-level), moved onto each clip's clock across any removed
  dead air. The model is not run again per clip.
- Placement did not avoid faces until Phase 8 (see section 5).
- Since 2026-11-17 (the owner's choice), every preset puts captions at 60%
  down, just below the middle. Captions must stay clear of YouTube's own
  controls drawn over a Short:
  - lines are limited to the centre 740 of 1080 px, leaving 170 px each side,
    so the like/comment/share column on the right is never reached; a line
    with one word too long for that is drawn smaller;
  - captions end above 78%, where the channel name and title begin;
  - captions stay below the top 18%, kept for the PART label.

  The on-screen credit only uses spots on the left, away from the top-right
  icons. Before this change captions sat at 72–80% and could run nearly edge
  to edge.

Lines hold up to the preset's word count, break at pauses, sentence and clause
ends, removed dead air and the frame width, and stay up 0.3 s after their last
word unless the next line starts sooner. Each word is one ASS event with only
that word coloured, so the highlight changes exactly on the word timings.
Line widths are measured from the font files using the renderer's own sizing
rule (font size = winAscent + winDescent). Every clip gets a caption report:
first caption on time, last word shown in full, no line ending early or
lingering, every word once and in order, clear of the label and button areas,
fits the width, and the words the speech model was unsure of. The `captions`
stage renders a 540×960 preview from the original video (framed as in section 5) with
the captions burned in; one caption style per project keeps parts consistent,
and changing it re-renders through the `caption` pipeline without re-cutting.

## 5. Framing and layouts

- Face detection with OpenCV's YuNet model on the proxy at 2–5 fps; positions
  are smoothed into a crop path so the frame glides rather than jitters.
- For gaming sources, a facecam detector looks for a face that stays inside a
  small fixed rectangle across the video.
- Layouts: full-screen gameplay, gameplay with facecam inset, facecam-focused
  reaction, split-screen (gameplay top, streamer bottom). The scorer suggests a
  layout per clip; the user can change it in the editor.
- Reaction zoom: a slow push-in keyed to the scored peak of the clip.

As built in Phase 8 (`framing.py`, `media.render_framed`), with these
differences from the plan above:

- Faces are found on the original video (decoded at up to 1280 px wide), not
  the 360p proxy, at 5 samples a second over each clip's ranges. The samples
  are kept in the project's `framing\` folder, keyed by the video and the
  ranges, so changing the layout or cutting again with the same ranges never
  scans twice. Without the model file (`tools\models`, MIT licence) the
  centre is kept and the clip says why.
- The facecam is decided once for the whole video, from the samples of every
  clip together: a cluster of faces that keeps to one off-centre region and is
  there at least 35% of the time. One clip on its own can be dominated by a
  face in the game. Its box is then snapped to the overlay's real edges:
  averaged over about 30 frames, the overlay's border is a straight line that
  stays put while the picture moves on both sides. A side with no clear line
  keeps the estimate from the faces.
- The four layouts are `full` (a 9:16 crop that follows the largest face, or
  the centre), `split` (streamer 40% on top, filled from inside the facecam;
  gameplay below, slid up to 15% of the width away from the facecam so it is
  not shown twice), `facecam_inset` (centre gameplay with the facecam in the
  top-right corner) and `reaction` (the facecam across the frame over a dimmed,
  blurred copy, or a tight crop on the speaker). Automatic picks split screen
  for a facecam and full screen otherwise; a layout that needs a facecam falls
  back to full screen with the reason. The owner can choose per clip (all
  parts of a moment together).
- The camera path is smoothed per shot (median, then Gaussian, then a speed
  limit of 0.35 crop widths a second) and jumps at scene cuts and at removed
  dead air rather than gliding across them.
- The zoom is a 15% push-in starting 0.4 s before the moment's peak, held
  1.5 s and eased back over 1.2 s.
- The face positions are carried through the layout into the Short's own
  pixels. The caption height is the style's own unless that crosses a face
  (no tolerance since Phase 9); then the first clear height from 54, 66, 48,
  70, 42, 36, 30% down is used (nearest the middle first, since 2026-11-17),
  and the clip says it moved. The PART label is placed
  where it touches no face during its 2.5 s and misses the caption band.

Rendering is done frame by frame: FFmpeg decodes the clip's ranges to raw
frames, Python composes each 9:16 frame with OpenCV, and the frames are piped
into a second FFmpeg that burns in the captions and mixes the clip's sound
with its music. Previews are 540×960 at up to 30 fps with libx264. The final
render is 1080×1920 at the source frame rate (up to 60 fps), H.264 through
Quick Sync (`h264_qsv`, 10 Mb/s) when it works on the machine and libx264
CRF 18 otherwise, AAC 192 kb/s at 48 kHz. Loudness: the finished mix is
measured first (stereo, as delivered) and one gain is applied to reach
−14 LUFS, capped at +15 dB, followed by a limiter at −1.5 dBFS that only
touches peaks; the speech and the music keep their balance and nothing else is
processed. The file is probed and measured again and gets a check list (size,
codecs, length, loudness, true peak). Final files go to the project's
`final\` folder, named after the project, the clip and the part. Only approved
clips are rendered.

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

Both sit behind the same interface. Route 1 was tested on 2026-11-15 and is
still inconclusive: the tool opened the video file, then the service returned
"no capacity" on two Gemini tiers. Each attempt carried about 25,000 tokens of
overhead before any video was looked at. Phase 4 starts by settling this, and
falls back to route 2 if route 1 cannot see video or stays unreliable.

Settled in Phase 4 (2026-11-15): the sign-in route works, but not with Gemini.
Gemini tiers returned an empty reply or timed out; `claude-sonnet-4-6` on the
same subscription answered reliably and can read images, though not video. So
`ai/providers.py` runs the Antigravity tool once per request, in its read-only
planning mode, in an empty folder holding only the task and its images, and
tries the models in `CLIPFORGE_AGY_MODELS` in order. The API-key provider is
written to the same interface but untested. Because video cannot be sent, the
"multimodal check" in section 3 is done on a sheet of six frames per moment
instead of the proxy clip.

As built in Phase 5, `duration.py` is the Smart Duration engine. It is pure
logic with no AI call and no video work: from a moment's picked span
(`core_start`, `core_end`, `peak`) and the word timings, silences, scene
changes and loud moments, it returns one clip, several connected parts, or a
refusal with a reason. Words are grouped into sentences by punctuation and by
pauses (two seconds of silence after a word with no comma counts as a full
stop, because speech-to-text sometimes omits it). Every start and end
candidate is scored; a clip is valid only if it ends on a finished sentence,
at or after the moment's payoff, with nobody talking over it, and keeps at
least half of the moment. Parts are found by dynamic programming over
sentence boundaries. The `clips` stage runs the engine for each selected
moment and renders a preview from the 360p copy with `render_ranges`; the
`cut` pipeline re-runs just that stage when duration settings change.

As built in Phase 4, `moments.py` holds the detector: the task text sent to
the model, validation of its answer (line numbers outside the transcript are
dropped, never guessed), the rough length fit, duplicate removal, and the
selection rule. The viral score is `sum(weight × sub-score) × 10`, computed in
code, so changing weights or project settings re-ranks stored moments with no
AI call.

As built in Phase 3, the whole-video work is local and needs no AI service:
`analysis/audio_signals.py` (loudness, silences, sudden loud moments, all
judged relative to the recording's own level), `analysis/video_signals.py`
(scene changes and motion from four tiny frames a second),
`analysis/transcript.py` (faster-whisper in ten-minute chunks cut at pauses,
each chunk saved as it finishes, word timings corrected against the measured
silences). Results live in `audio_signals.json`, `video_signals.json` and the
`transcript_segments` table.

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

As built in Phase 10 (`youtube.py`, `api/youtube.py`): `publish_blocker`
runs in the upload endpoint and again inside the `upload` stage before any
request is sent; making a video public or unlisted checks the rights again
and needs `confirm: true` with the exact title the owner saw. Uploads are
always private. The OAuth flow uses the owner's own "Desktop app" client,
PKCE, and a loopback return address on this app's own server; the token lives
in `data\youtube\token.json`. Every call to Google goes through
`youtube.make_http()`, which the tests replace with a stand-in.

Phase 11.9 adds `youtube.shorts_checklist()`, the one set of YouTube Shorts
rules (size, length, encoding, sound, quality, title, description, hashtags,
credit, music licence, kids declaration, channel). `publish_blocker` is that
checklist without the channel item, so the dashboard's checklist, the upload
endpoint, the upload stage and the publish step cannot disagree. Only the
quality item can be passed with "anyway".
- The app shows YouTube's reused-content risk plainly and never states that
  editing makes footage fair use or that monetisation is guaranteed.

### Creator credit

Every clip from someone else's footage credits the creator automatically:

- an on-screen tag (for example `@StreamerName` with the platform icon) in a
  corner that does not collide with captions or the facecam;
- a credit line with the creator's name and the original video link at the top
  of the generated description;
- the same details in the `rights.json` sidecar.

As built in Phase 9: the on-screen tag is `@Name` (the credit the owner
entered, else the channel name) for the whole clip, placed in a top corner, or
above YouTube's title on the left, wherever no face, caption or PART label is.
There is no platform icon. The description's first lines are
`Credit: Name on Platform` and `Original video: link`, written by code.

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

As built in Phase 7 (`music.py`), with differences from the plan above:

- Ducking is an envelope worked out from the word timings, not
  `sidechaincompress`: the speech is already known to the word, so the music is
  lowered 10 dB under every stretch of speech (merged across gaps under 0.5 s,
  0.1 s attack, 0.45 s release) with FFmpeg's `amultiply`. The result is exact
  and measurable, and game sound is not mistaken for speech.
- The music's open level is set from the speech level of the clip (default 14
  dB under it, plus the 10 dB duck, about 24 dB under the speech while someone
  talks). The gap is measured on the music as mixed and recorded per clip; a
  clip below 12 dB is flagged.
- The clip's own sound is never processed. Music is added with `amix`
  (no normalisation) and a limiter at 0.95 only to stop clipping. Mono sounds
  are copied to both channels unchanged, because FFmpeg's own conversion
  lowers each channel by 3 dB.
- Mood comes from the moment's category (`CATEGORY_MOODS`); there is no AI
  call. Auto mode skips music for non-stop talking (90% of the clip) with
  nothing else happening; Always and a per-clip choice override that.
- Beats come from librosa at an 11.6 ms frame, corrected for the tracker's
  25 ms lag (snapped to a clearly sharp attack, otherwise shifted by the lag),
  and only believed where onsets are stronger on the beats than between them.
  The music starts at the earliest bar line that puts a beat on the clip's key
  moment. Parts of one moment share a track and the music continues from where
  the previous part's ended.
- Tracks are copied into `data\music` and analysed on import. Selection is
  refused in the API, in the planner and again at render time if licence
  details are incomplete.
- Music is mixed into the same render as the captions (`captions` stage,
  labelled "Add captions and music"). A `music` stage before it chooses and
  plans; `remix` re-runs both after a music setting changes; `caption` with
  `clip_ids` re-renders chosen clips only.

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

As built in Phase 9 (`qc.py`), with these differences from the table above:

- The picture checks run in one FFmpeg pass over a 270×480 copy of the
  file: `cropdetect` (accumulated over the whole Short, so only edges black
  throughout count as bars), `blackdetect`, `freezedetect`, and on the sound
  `silencedetect` and `volumedetect`. True peak and loudness come from the
  `loudnorm` measurement already made for the final render.
- "Music below speech" is measured on the file, not on stems. The clip's own
  sound is rendered again with the final's single gain, lined up with the
  file's sound to the sample, and taken away from it. What remains is
  everything that was added (the music), and its level during the spoken
  words, against the speech, is the gap. If no music was added and the
  remainder is still loud, the speech itself was changed, and that fails too.
- "Captions match speech" uses the words the speech model was unsure of
  (more than 15% is a warning); "no cut words" uses the cut's own report;
  "not a duplicate" compares the words of the other clips of the same video.
- Rights: unconfirmed rights are a warning, not a fail, because exporting
  is allowed with a warning; publishing them is refused in Phase 10.
- Status per check is ok, warn or fail. The score is 100, less 30 per fail
  and 8 per warning. Results are stored with the final render
  (`clip.final.qc`); there is no separate `qc_results` table.

Titles and descriptions (`metadata.py`) are written once per final render
(clips without text) in one AI request for all the clips, or on request
("Write again"). Credit lines, part numbering, #Shorts and length limits are
applied by code after the AI answers, never left to it. Export
(`exports.py`) copies the final file and writes a `.txt` and a `.json`
sidecar beside it, plus the project's `rights.json`; a re-export replaces the
previous files of the same clip.

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
