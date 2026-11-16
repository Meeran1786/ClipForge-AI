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

## Phase 5 — Clip extraction — built 2026-11-15

Includes the Smart Duration engine described above.

What was built: `duration.py`, a Smart Duration engine with no AI call
(sentence detection from word timings and pauses; ending and start quality;
Strict / Smart / Flexible; dead-air removal; context-aware part splitting; a
validation report per clip); a cutting stage that renders a plain preview per
clip; clip review (approve, reject, approve anyway); the Smart Duration panel
on New Project, project Clip settings and Settings; a clips list on the
project page. Changing duration settings re-cuts without asking the AI.

Results:

- The ten scenarios in [TASKS.md](TASKS.md) are automated tests, plus a
  randomised test that no valid clip ever cuts a word. 218 tests pass.
- Live run on a spoken test video (real speech model, real AI pick): re-cut
  under 30 combinations of target, mode and splitting; 87 clips checked
  against 390 real word timings by an independent script. 74 cut cleanly, 13
  refused with a stated reason, no problems: no cut inside a word, every valid
  clip on sentence boundaries, parts contiguous, no repeated stretch.
- Dashboard panel, clips list, previews and approve/undo checked in the
  browser.

Faults found by the tests and the live run, all fixed: a clip that stopped
before the payoff counted as valid; a clip that dropped most of the moment
counted as valid; neighbouring parts overlapped by 0.16 s; Strict padded a
clip with an unrelated line from after a pause; a missing full stop before a
long silence glued two beats into one sentence (clip started 17 s early); a
part opened on a two-word afterthought; wrong wording on parts.

Added 2026-11-16, on real speech: Tears of Steel (CC BY 3.0, Blender
Foundation), with the real speech model and the real AI pick, re-cut under the
same 30 combinations. 204 clips checked against 306 real word timings by the
independent script: 176 cut cleanly, 28 refused with a reason, 44 moments told
in parts, no problems. One fault found and fixed: when nothing complete fitted,
the clip offered for "Approve anyway" was the best fragment (1.7 s on one
moment whose payoff came after 47 s without dialogue); it is now the shortest
complete version of the whole moment, over the limit, with the reason.

Not done:

- No real stream or gaming footage yet, and the Phase 4 gate (owner rating
  picks on three own videos) is still open.
- Part labels and per-part titles and hashtags belong to Phases 8 and 9.
  Caption sync checks were built in Phase 6.
- "Several speakers at once" is judged from overlapping word timings only;
  there is no speaker identification.
- No manual adjustment of a clip's start or end yet (approve or reject only).
- In Smart mode a part can exceed the tolerance when the moment needs more
  parts than the maximum allows; the clip says so in its reasons.

[TASKS.md](TASKS.md) has the item-by-item state of the Smart Duration
request.

- Boundary refinement to sentence and silence boundaries, 15–90 s limits.
- Silence trimming, de-duplication.
- Plain horizontal preview clips for review.

Gate: no clip starts or ends mid-word across twenty generated clips; no two
clips show the same event.

## Phase 6 — Captions — done 2026-11-16

- Word alignment with local Whisper on clip audio.
- ASS caption generator with the four presets; Viral Highlight matches the
  reference Short.
- Caption customisation in the Clip Editor; regenerate captions action.

Gate: side-by-side with the reference Short; highlight lands on the spoken
word in frame-stepped checks; low-confidence words are flagged, not invented.

What was built: `captions.py` (words from the Phase 3 transcript moved onto
each clip's clock, short lines, one ASS event per word with the spoken word
coloured, four presets with overrides, timing and placement checks, exact line
widths from the font files); a `captions` stage after cutting that renders a
540×960 preview with captions burned in; one caption style per project, so all
parts match; a working Clip Editor (style controls, live sample in the real
font, re-caption all clips, caption checks, caption lines with unsure words
marked); captioned previews on each clip card; the caption style choice on New
Project and in the defaults. Fonts (Montserrat ExtraBold and Black, Anton,
Poppins SemiBold; SIL Open Font License) are in `tools\fonts`.

One change from the plan: the speech model is not run again on each clip. The
word timings from the whole-video transcript are already word-level and from
the same model, so re-running would add minutes per clip for no gain.

Gate results, on Tears of Steel (real dialogue, CC BY 3.0), 10 clips:

- All 10 clips passed every timing and placement check.
- Frame by frame: for each of 237 words, the frame shown while it is said was
  taken from the rendered preview and the yellow highlight located. It was on
  the right word in 237 of 237. In 29 of 29 pauses between lines, nothing was
  on screen.
- Side by side with the reference Short: same look (heavy white capitals,
  black outline, spoken word yellow, about three quarters down); ours is
  slightly larger and a few percent lower, both adjustable.
- Unsure words: the speech model's confidence is shown, not acted on. Words
  under 0.5 agreed with the film's own published captions only 31–43% of the
  time, against 91% for confident words, so the flag points at the words most
  likely to be wrong. 26 of 242 words were flagged.
- 251 automated tests pass, including one that renders real frames and checks
  the highlight moves from word to word.

Faults found and fixed: line widths were worked out from the font's letter
size, but the renderer sizes text by the font's full line height, so
Montserrat drew 36% narrower than predicted (found by the frame check: 27
highlights sat nearer the centre than expected); lines now break and are
checked against the true width.

Not done, and limits:

- Five words of 40–80 ms were highlighted for under two frames, too briefly
  to see. Their timing was left as heard rather than stretched.
- Framing is a plain centre crop, which can leave a speaker out of shot. Face
  and action framing, and keeping captions off faces, is Phase 8.
- The Part 1 / Part 2 label is not drawn yet (Phase 8); captions are kept out
  of the area reserved for it.
- Captions cannot be edited word by word; an unsure word can only be heard
  and the clip rejected.
- One language per video (whatever the speech model detected).

## Phase 7 — Music — built 2026-11-16, tested on synthetic music only

- Track library with mandatory licence fields; import UI.
- Mood matching, "no music" default for conversation, ducking, fades, beat cut.

Gate: with a handful of licensed tracks loaded, speech stays clearly audible
(measured loudness gap), and a track without licence data cannot be chosen.

What was built: `music.py` (licence rules, track analysis, mood matching, beat
alignment, ducking envelope), a `tracks` table and music library (import,
edit licence, play, remove), a `music` stage that chooses each clip's track
(parts of one moment share one track and the music carries on from part to
part), music mixed into the render under every spoken word, a measured
speech-to-music gap recorded for every clip, a per-clip choice in the Clip
Editor (automatic, none, or a licensed track), and project settings for music
mode and volume. Changing music settings re-mixes only; the cuts are kept.

Rules enforced in code: a track without complete licence details (source,
licence, a link or note, and a credit line when the licence needs one) cannot
be chosen automatically or by hand, and is checked again when a clip is
rendered. The credit line of a CC BY track is attached to every clip that uses
it, for the description (Phase 9).

Gate results. Test material was four original tracks I synthesised (bouncy,
tense, driving, soft; one mono, three stereo) plus a fifth with no licence
details, and a spoken test video put through the real speech model and real AI
pick (two moments, one told in three parts):

- Speech stays clearly audible: the music was measured 24 dB under the speech
  on all four clips (the requirement is 12 dB). With music added, the speech
  itself changed by at most 0.15 dB (limit 0.6 dB), measured on the rendered
  files against the same clips rendered without music. In pauses the music is
  clearly present (25 to 28 dB louder than without it).
- The unlicensed track, which had the same mood as the funny clips, was never
  chosen. Choosing it by hand was refused with the missing items listed.
  Removing a used track's licence made its clips render without music, with
  the reason.
- Moods matched: the funny story got the comedic track in all three parts, the
  "unexpected" clip got the dramatic one.
- Beat timing on the tracks I made: after correction every track averages
  within 6 ms of its true beat. The worst single beat was 3 to 16 ms off on
  tracks with sharp hits and 35 ms off on the soft pad track.
- 355 automated tests pass, including real renders that measure the gap, the
  untouched speech, fades, and a beat landing on the key moment within 20 ms.

Faults found and fixed along the way: beats were found about 25 ms late on
every track measured (now corrected; a first attempt that snapped every beat
to the nearest bump made the two soft-attack tracks worse, so it only snaps to
clearly sharp attacks); a drone with no beat was treated as having one; the
tempo label was off (117.5 for a true 120; now taken from the beat spacing);
and FFmpeg's default mono-to-stereo conversion silently cut mono tracks and
mono speech by 3 dB, which made the music quieter than planned.

Not done, and limits:

- **No real music has been tested.** Every track was synthetic. How well beats
  are found and lined up on real songs, and how the mood tags behave on them,
  is unmeasured. The 25 ms correction was measured on synthetic tracks only.
- Beat sync lines a bar line up with the key moment at the start only. The
  music ends with a fade, not a cut on a beat, and later parts of a connected
  moment carry on without beat alignment.
- A tracker can follow every second beat (two of the four tracks were found
  at half tempo); the beats it finds are still real beats.
- "Plain conversation" (almost non-stop talking with nothing else happening)
  gets no music in Automatic; the threshold (90% of the clip) is a judgement
  that needs tuning on real footage. "Always" overrides it.
- The mood comes from the moment's category only, not from the sound or the
  picture.
- Music is chosen per moment; manual choices are lost when the clips are cut
  again (a change to duration or selection settings).
- The music level is set from the speech and the gap setting; the finished
  mix is brought to −14 LUFS in the final render (Phase 8).

## Phase 8 — Framing and rendering — done 2026-11-16

- Face and facecam detection, smoothed crop paths, four layouts, reaction zoom.
- Final FFmpeg render: 1080×1920, H.264, AAC, source frame rate; Quick Sync
  when available.

Gate: rendered clips from a facecam gaming video and a talking-head video keep
the subject in frame throughout; captions never cover a face.

What was built: `framing.py` (YuNet face scan, facecam found once per video
and snapped to the overlay's edges, four layouts with an automatic choice, a
camera path smoothed per shot that jumps at cuts, a push-in on the key moment,
caption height and PART label placed off faces) and a frame compositor that
pipes OpenCV-built frames into FFmpeg. A `framing` stage between music and
rendering; previews are now framed. A `final` job renders approved clips at
1080×1920 (Quick Sync, AAC 48 kHz) with one loudness gain to −14 LUFS, a
peak limiter, and a check list per file. Dashboard: layout, zoom and part-label
settings; per-clip layout, framing explanation, final render and download in
the Clip editor; "Render approved Shorts" on the project. Details in
[ARCHITECTURE.md](ARCHITECTURE.md), section 5.

Gate results. Two videos through the real app (real speech model, real AI
pick): a 6-minute facecam "gaming" video I composed from two Blender films
(Big Buck Bunny as the game, Tears of Steel in a 648×274 facecam at the bottom
right, its sound as the streamer's), and Tears of Steel itself as the
talking-head film. Both CC BY 3.0, Blender Foundation. Nine clips in all, one
moment told in two parts. Checked by a separate script, not the app's own
report:

- **Subject in frame: 470 of 470 face samples.** In split screen the
  streamer was in the top panel at every sample (160/160); in the film the
  people worth framing were in the crop at every sample (310/310). The
  facecam was found at the bottom right (a face in it 82% of the time) and
  its box snapped to within 4 px of the overlay on all four sides. The film
  had no facecam and every clip went to full screen following the speaker.
- **Captions never covered a face.** The face model was run again on the
  rendered final Shorts, 5 frames a second: in 302 frames with a caption on
  screen no real face was under it. Two frames were flagged; I looked at both
  and they were a hand and a neck (false detections). In split screen the
  captions moved from 76% to 46% down (just under the streamer panel), or to
  38% for one clip. The PART 1/2 and 2/2 labels touched no face in any of 26
  frames.
- **Final files:** seven Shorts, all 1080×1920 H.264 (Quick Sync) / AAC,
  at the source frame rate (30 and 24 fps). Loudness −14.1 to −14.9 LUFS,
  one at −15.6 LUFS held back so its peaks stay under the limit (the film's
  own mix already peaks at 0 dB); true peak −1.1 to −4.7 dBTP. Every file
  passed its checks.
- 391 automated tests pass, including real renders that check the size,
  the joined ranges, −14 LUFS with one gain for quiet and loud passages
  alike, and that re-encoding the sound leaves the picture untouched.

Faults found and fixed along the way: in one short window the bunny's face
outweighed the facecam (fixed by deciding the facecam from every clip
together); the facecam box guessed from faces showed gameplay above the
streamer (fixed by snapping to the overlay's edges, then by searching between
the faces and the guess, because a face cut off by the overlay's edge gets a
box that reaches past it); a film clip took an actor standing to one side for a
facecam; the camera panned across shot changes the scene detector had missed,
flipped between two people of similar size and chased a tiny background face;
a face half out of the crop was ignored when placing captions; loudness was
measured on the mono sound but delivered in stereo (3 dB too loud); AAC pushed
a dense mix 1.8 dB over the limiter's ceiling (now measured after encoding and
corrected by re-encoding the sound only); the zoom moved 2% a frame (now about
1%).

Not done, and limits:

- **No real stream footage yet.** The facecam video is a composite with a
  clean rectangular overlay; real facecams with rounded corners, transparent
  backgrounds, chroma-keyed streamers or moving overlays are untested.
- The gameplay panel is a centred crop (moved off the facecam); it does not
  follow the action on screen. Only faces steer the camera.
- With two people too far apart to fit, the camera stays with one of them; it
  does not cut between speakers by who is talking.
- Text placement avoids faces only, not the game's HUD or on-screen text.
- A layout chosen by hand is lost when the clips are cut again.
- The final render runs at about 1 to 1.8 times the clip's length on this
  machine (the frames are built in Python); previews at about half the length.
- The facecam reaction and inset layouts were exercised by tests and
  renders, but the gate ran with the automatic choice (split screen and full
  screen).

## Phase 9 — Quality control and export — done 2026-11-17

- All QC checks, quality score, failure reasons on the clip card.
- Metadata generation (title, description, hashtags, pinned comment).
- Approve / reject / export one / export all, with `rights.json` sidecar.

Gate: deliberately broken renders (black bars, missing audio, loud music) are
caught; end-to-end run from one URL to a folder of approved Shorts.

What was built:

- `qc.py`: sixteen checks on every final Short, measured on the rendered file.
  Size and codecs, length, black bars, black frames, frozen picture, sound
  present and not silent, true peak, loudness, music under the speech, long
  silence, caption checks, unclear caption words, whole-sentence cut, repeat
  of another clip, and rights. A score of 100, less 30 per fail and 8 per
  warning. A fail stops export unless the owner exports anyway.
- The music check works on the file itself. The clip's own sound reaches the
  Short unchanged apart from one gain, so taking it away from the file's sound
  leaves only what was added; its level while someone speaks is the gap.
- `metadata.py`: one AI request writes the title, a short description, 3–6
  hashtags and a pinned comment for every clip being rendered. Code then adds
  everything that must be right: the creator credit and original link first
  in the description (any video but the owner's own), the music credit a CC BY
  track needs, "(Part n/m)" and a part line, #Shorts, and YouTube's length
  limits. Without an AI, plain text is made from the words said, and the clip
  says so. The owner can edit the text or ask for it again.
- An on-screen creator credit ("@Name") for the whole clip, in a corner clear
  of faces, captions and the PART label (project setting, on by default).
- `exports.py`: approved, rendered Shorts are copied to the export folder
  (`data\exports\NNN_project`, or `CLIPFORGE_EXPORT_DIR`). Each gets its `.mp4`,
  a paste-ready `.txt` and a `.json` sidecar (text, rights record, music
  licence, quality report, source times), with the project's `rights.json`
  beside them. Unconfirmed rights are exported with a warning in both files.
- The final job is now: write the text (only clips without any), render,
  check. The Generated Shorts page lists every approved or rendered clip with
  its quality report, editable text, approve/reject, render again, export,
  download, and per project "Export all ready" and "Open folder". Clip cards
  show the score and the reasons for any problem.

Gate results:

- **Broken renders are caught.** One real final Short from the end-to-end
  run passed every check (score 100). Deliberately broken copies of it all
  failed, for the right reason:
  - black bars (12% top and bottom): fail;
  - sound track removed: fail;
  - silent sound track: three fails;
  - the same clip rendered with music 4 dB louder than the speech: fail.

  The automated tests add black frames, a frozen picture, wrong size and
  length, and quiet music that must pass.
- **One link to a folder of approved Shorts.** A YouTube link to Tears of
  Steel (CC BY 3.0, rights recorded as licensed with the source) went through
  download, analysis, the real AI pick (4 moments, 3 told in two parts, so 7
  clips), framing, captions, approval, the final render with real AI text,
  and export. The final step took 2.8 minutes for 7 Shorts (133 s of video),
  writing included. The folder holds 7 MP4s at 1080×1920, 7 `.txt`, 7 `.json`
  and `rights.json`. Every Short passed: four scored 100, three 92 (a warning
  for unclear caption words, or loudness 1.1 LU under target). The titles say
  what happens ("He finally admits he's freaked out by the robot hand (Part
  1/2)"), every description starts with the credit and the original link, and
  #Shorts is always there.
- 417 automated tests pass. They use stand-ins for the AI and the platforms,
  and real renders for the quality checks.

Faults found and fixed along the way:

- The new text stage was first named like the link lookup stage and silently
  replaced it, so link projects kept their placeholder title (caught by the
  tests).
- A title opening with a quotation lost its first quote mark.
- Exported file names repeated the part number.
- The loud-music message said "−4 dB under the speech" instead of "4 dB louder".
- Transcription ran out of memory when the test suite ran at the same time
  with C: almost full. Tests now keep their files on D:.

Not done, and limits:

- **Extreme close-ups.** When a face is bigger than the frame (cut off top and
  bottom), the face model does not find it, so captions can sit over it and
  the checks do not notice. I saw one such frame in the gate.
- The title and description are only as good as the transcript and the AI's
  reading of it. They should be read before publishing; the app does not
  check facts.
- The hashtags always include #Shorts, even after the owner edits them.
- The duplicate check compares the words of a project's clips, not clips from
  different videos.
- Export copies files; nothing is uploaded (Phase 10).

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
package below, the frontend packages, and FFmpeg/ffprobe. The speech models
(Phase 3, `data\cache\models`) and the caption fonts (Phase 6, `tools\fonts`)
have since been fetched.

| Item | Phase | Notes |
| --- | --- | --- |
| Python packages: fastapi, uvicorn, sqlalchemy, pydantic-settings, httpx | 1 | venv in `backend\.venv` |
| Node packages: react, vite, typescript, tailwindcss, react-router, tanstack-query | 1 | npm cache already on D: |
| FFmpeg + ffprobe (static build) | 1 | `tools\ffmpeg\` |
| yt-dlp | 2 | `tools\` |
| numpy, librosa, opencv-python-headless, scenedetect | 3 | |
| Gemini SDK or CLI bridge | 3 | needs API key or working CLI sign-in |
| faster-whisper (small model, ~500 MB) | 3 | model cache on D: |
| Fonts: Montserrat, Poppins, Anton | 6 | SIL Open Font License; `tools\fonts`, licences alongside |
| YuNet face model (`face_detection_yunet_2023mar.onnx`, 228 KB) | 8 | MIT licence, OpenCV Zoo; `tools\models`, licence alongside |

## Needed from the owner

1. Nothing yet for Gemini: Phase 3 first tests the already signed-in
   Antigravity CLI. Only if that cannot read video, a Gemini API key from
   Google AI Studio, typed into `.env` by the owner. Never a Google password.
2. Test videos the owner has the right to use, or from streamers who publicly
   allow clipping (before Phase 2 gate).
3. Music files the owner is allowed to use, for example from the YouTube Audio
   Library. The Phase 7 gate was run on synthetic music, so a few real
   licensed tracks are still needed to test beat finding and mood matching on
   real songs. Optional: Shorts can ship without music.
4. A Google Cloud project with the YouTube Data API enabled (before Phase 10).
