# Phase 11 — ClipForge Intelligence & Creator Studio: implementation plan

Status: **plan for the owner's approval. Nothing in this document is built.**
Written 2026-11-17 after inspecting the repository as it stands after Phase 10.
YouTube Shorts is the only target; no other platform is planned.

## 0. Before Phase 11 starts

The brief says Phase 11 begins only when Phases 1–10 are complete, tested,
documented and approved. Today that is not fully true:

| Item | State | What closes it |
| --- | --- | --- |
| Phase 10 gate: one private upload to your channel | Waiting on you | Your Google Cloud OAuth client (Desktop app) and your own sign-in; then you pick the Short |
| Phase 4 gate: rate the top-10 picks on three of your videos | Open since Phase 4 | Your rating (≥ 7 of 10 worth clipping) |
| Real stream footage, real music | Never tested | Videos and tracks you may use |

Recommendation: approve this plan now, close the Phase 10 upload first, and
start 11.1 after it. The Phase 4 rating matters most for 11.1 and 11.8, which
change how moments are scored. Its result should shape the new weights rather
than be measured after them.

## 1. What already exists (the starting point)

| Area | Today | Where |
| --- | --- | --- |
| Moments | 9 categories; 7 AI sub-scores 0–10 (entertainment, emotion, surprise, retention, context, replay, standalone); viral score computed in code from editable weights; a picture check on still-frame sheets; duplicates removed | `moments.py`, `schemas.CATEGORIES`, Settings → weights |
| Signals | loudness, silences, sudden loud moments, scene cuts, motion | `analysis/` |
| Cutting | Smart Duration: whole sentences, payoff kept, parts, validation report | `duration.py` |
| Faces | YuNet faces at 5 fps, facecam (pooled, edge-snapped), 4 layouts, camera path, zoom, text placed off faces | `framing.py` |
| Captions | word-level karaoke ASS, 4 presets, unsure words flagged, never corrected | `captions.py` |
| Music | licensed library, mood by category, ducking, beat alignment | `music.py` |
| Render/QC | compositor, final 1080×1920, −14 LUFS, 16 file checks, score | `media.py`, `qc.py` |
| Text | one AI request per render: title, description, hashtags, pinned comment; credit by code | `metadata.py` |
| Export / YouTube | folder export with sidecars; private upload, confirmed publish, rights refusal | `exports.py`, `youtube.py` |
| Jobs | SQLite queue, one worker thread, oldest first, resumable stages, cancel/retry | `jobs/engine.py` |
| Dashboard | Dashboard, New project, Analysis, Project, Clip editor, Generated Shorts, Settings | `frontend/src/pages` |

Rules that Phase 11 must keep (from `CLAUDE.md`): rights gate; credit is not
permission; captions show what was heard; no unlicensed music; tests with no
real side effects; database changes as new nullable columns; heavy files on D:;
small changes to working code; the clip's own sound is never processed.

## 2. Points in the brief that need your decision

These conflict with an existing rule or with what is technically available.
My proposal is given for each; I will follow your answer.

1. **"Automatic punctuation correction" vs "captions show what was heard".**
   Proposal: allowed for punctuation and capital letters only, as a display
   layer. The words, their order and their timing stay exactly as heard, and a
   test proves the word list is unchanged. No word is ever added, dropped or
   respelled.
2. **Profanity filter.** Proposal: off by default; when on, a word is masked
   on screen (`f**k`), never removed or replaced, so the timing and word count
   stay true. The filtered list is configurable.
3. **Translated captions.** Word-by-word timing cannot be carried across
   languages: word order and length change. Proposal: translated captions are
   timed per phrase (each original caption line), clearly labelled
   "Translated from <language> by AI", and kept apart from the original
   captions, which stay the default. Translation is done by the AI provider
   already in use, which means the transcript is sent to it; this is already
   true for moment picking, but needs your consent to extend. Local
   translation models are large and slow on this PC and are not proposed.
4. **Thumbnails for Shorts.** YouTube applies custom thumbnails to Shorts
   only in some places, and setting them through the API needs a verified
   channel. Proposal:
   - build the Thumbnail Studio (frames ranked from the real video, text,
     logo, preview, export) and upload the thumbnail when the channel allows
     it;
   - say plainly that it may not be shown on the Shorts feed.
5. **"Generated thumbnail designs" with image generation.** No image
   generation model is available in the current stack (the AI route returns
   text), and generated imagery risks inventing people or events. Proposal:
   designs are layouts composed from real frames of the clip, plus text,
   logo and brand colours, made locally with FFmpeg. No generated imagery.
   An image model could be a later, separately approved feature.
6. **Visual events (explosions, wins, losses, celebrations).** Detecting these
   needs a vision model. Proposal:
   - use the AI provider on still-frame sheets, as the picture check already
     does, and say plainly that it sees sampled frames, not video;
   - put it behind a `VisionProvider` interface, so a local ONNX model (the
     runtime is already installed) can be added later.

   Detection will be "where supported", with a confidence and a reason, never
   claimed as full video understanding.
7. **Active speaker.** YuNet gives five landmarks per face (eyes, nose, mouth
   corners), not lip shapes. Proposal: a heuristic, where mouth-corner movement
   that rises and falls with the speech level marks the likely speaker.
   - shown as "likely speaker" with a confidence;
   - used only to choose whom the camera follows when two people do not fit;
   - no speaker identification.
8. **Analytics.** Needs the YouTube Analytics scope
   (`yt-analytics.readonly`), so a second, separate consent. Proposal:
   - off by default; the data is stored locally and never sent to the AI
     without your consent;
   - recommendations are descriptive comparisons with sample sizes, labelled
     "correlation, not cause", and hidden until enough Shorts exist (for
     example 10 per group);
   - weight changes are only suggested, and you apply them.
9. **Milestone order.** Proposal: 11.9 first (small, finishes the publishing
   work), then 11.1, 11.2, 11.3, 11.5, 11.6, 11.4, 11.7, 11.8, 11.10. Reason:
   the editor (11.5) and brand kit (11.6) feed the thumbnail studio (11.4);
   analytics (11.8) needs published Shorts to exist.

## 3. Milestones

Each milestone ends with: tests (unit, integration, and a live check on real
output), the full existing suite passing, and all docs updated with what was
and was not done.

### 11.1 Advanced viral intelligence

**Architecture.** Extend `moments.py`, do not replace it.

- **Categories.** Keep the 9 and add `suspense`, `celebration`, `punchline` and
  `gameplay_outcome`. "Unexpected gameplay outcome" and "funny without
  dialogue" become flags on a moment rather than more categories. Old projects
  keep their categories.
- **Scores.** Each score is labelled as estimated by the AI or measured by
  code:

  | Score | Source |
  | --- | --- |
  | Hook strength | AI sub-score, plus a measured check that the first 3 s has speech or a loud or motion peak |
  | Entertainment | Existing AI sub-score |
  | Emotional impact | Existing "emotion" sub-score |
  | Context completeness | AI "context" combined with code: setup sentences, payoff inside, no question left hanging, from the Smart Duration report |
  | Audio quality | Measured: speech level against background, clipping, silence share |
  | Visual quality | Measured: sharpness, brightness, faces visible, frozen share |
  | Overall potential | Weighted sum, weights editable as today |

- **Moments without dialogue.** Candidates from sound and motion peaks are
  sent to the picture check with a "no speech" note. Reaction moments
  (face-heavy shots plus a loudness surge) get a reaction flag.
- **Context.** The AI prompt gets the lines before and after. Code refuses a
  moment whose payoff falls outside the cut (this already exists) and a
  conversation cut mid-exchange (the existing overlap rules).
- **Duplicates.** Existing time-overlap rule, plus word similarity between
  moments.
- **UI.** Every score shows "estimated" and what it is based on. No wording
  promises virality.

**Files:** `moments.py`, `schemas.py` (categories, weights), `analysis/audio_signals.py` (quality measures), `framing.py` (reuse face scan for visual quality), `api/system.py` (weights), `MomentsPanel.tsx`, Settings weights.
**Database:** `moments.scores` JSON, `moments.flags` JSON, `moments.quality` JSON (all nullable). The existing `subscores` stay readable.
**API:** `/moments` returns the new fields; `/settings/weights` accepts the new keys (old saved weights migrate).
**Acceptance:**
- the same transcript and the same AI answers always give the same scores;
- changing a weight re-ranks without an AI call;
- every score shows its source;
- a sound-only moment can be picked;
- context failures are listed with a reason.

**Tests:**
- scoring consistency (same input, same output);
- weight migration;
- context completeness on crafted transcripts (setup missing, payoff outside, question unanswered);
- duplicate detection by words;
- audio and visual quality on synthetic clips (clipped, noisy, black, blurred);
- fake AI answers that are missing or malformed.

**Risks:**
- the AI's 0–10 marks are not calibrated, and the Phase 4 owner rating is still open;
- a longer prompt costs time on every call (about 28k tokens of overhead already).

### 11.2 Scene understanding

**Architecture.** A `scene.py` module with a `VisionProvider` interface:

- `LocalSignals`: today's cuts and motion, plus YuNet faces;
- `AIFrames`: still-frame sheets sent to the current AI;
- a later `OnnxModel`, not built now.

**What it finds:**
- people: count over time, and faces tracked across samples by position and
  size (simple IoU tracking);
- facecam: the existing pooled detection;
- the likely active speaker: the heuristic in section 2.7;
- transitions: hard cuts (existing), fades, and sudden camera moves from
  global motion;
- visual events: the AI labels each sheet with events from a fixed list
  (explosion, win, loss, failure, celebration, reaction), each with a
  confidence and "seen in sampled frames".

The results feed 11.1 (visual and reaction scores) and framing (who to follow).

**Files:** new `scene.py`; `framing.py` (tracks, speaker choice); `analysis/video_signals.py` (fades, camera moves); `moments.py` (uses events).
**Database:** `projects.analysis.scene` summary; per-clip `clips.framing.tracks`.
**API:** `GET /projects/{id}/scene`.
**Acceptance:**
- every event has a source and a confidence;
- if the vision provider fails, the clip is still framed and scored from local signals, and the UI says vision was unavailable;
- nothing claims to understand the video as a whole.

**Tests:**
- tracking across crossing faces;
- the speaker heuristic on synthetic mouth movement correlated with sound;
- fade detection on generated fades;
- the provider failing, timing out, or returning junk;
- a regression on the Phase 8 framing gate numbers.

**Risks:**
- the speaker heuristic is weak with five landmarks;
- AI event labels on sampled frames will miss short events.

### 11.3 Caption intelligence

**Architecture.** Extend `captions.py`.

- **Presets.** Add Podcast and Storytelling to the existing four; Minimal
  Professional and Gaming already exist.
- **Emphasis.**
  - important-word highlighting: the AI marks key words per line; the code
    accepts only words that exist in the line;
  - emotion-aware emphasis: the moment's category and sudden loud moments
    drive a colour or scale "punch" on the word said at that time.
- **Emoji (optional).** The AI suggests at most one per line, drawn next to
  the line, never replacing a word.
- **Punctuation.** As in section 2.1. The profanity filter as in 2.2.
- **Uncertain words.** Existing flags; also an option to show them dimmed,
  and an editor list to accept or correct them by hand (covered in 11.5;
  only the owner can change a word).
- **Language.** Detected language (already stored). A target caption language:
  translated, phrase-timed and labelled, as in 2.3. The original captions stay
  available, and the burned-in track is either original or translated, never
  mixed.
- Word-level sync is kept and checked by the existing caption checks.

**Files:** `captions.py`, `metadata.py` (shares the AI request), `pipelines.py` (`_captions`), `CaptionStyleForm.tsx`, Clip editor.
**Database:** `clips.captions.emphasis`, `clips.captions.translation` (in existing JSON); project option `caption_language`, `profanity_filter`, `emoji`.
**API:** `PATCH /captions` gains the options; `POST /projects/{id}/translate` (a job: AI).
**Acceptance:**
- the word list on screen equals the spoken words (masking excepted, and flagged);
- the highlight stays on the spoken word in a frame check, as in Phase 6;
- translated captions carry the label and never replace the original record;
- with the AI off, captions still render.

**Tests:**
- the word list is unchanged under punctuation fixes;
- masking keeps the word count;
- emoji never replace words;
- translation is timed to the original lines;
- uncertain-word handling;
- a translation failure falls back to the original;
- the Phase 6 frame-sync regression.

**Risks:**
- translation quality cannot be measured automatically;
- sending the transcript out again needs your consent.

### 11.4 Title, description and thumbnail studio

**Architecture.** A "Studio" workspace per Short.

- **Text.** 5–10 title suggestions, a description, hashtags, 2–3 pinned
  comment options and 3 hooks, all in one AI request, with the existing credit
  and part rules applied by code. Each suggestion is checked against the
  transcript: no names or numbers that are not in the clip, no ALL CAPS
  claims. Every field is editable, and the chosen title stays the publish
  title.
- **Thumbnails.**
  - candidate frames sampled from the final Short;
  - ranked by measured sharpness, a face present and its size, eyes open (from
    landmarks), brightness, and nothing covered by captions;
  - frame chosen by hand;
  - designs composed with FFmpeg (frame, text, outline, logo, brand colours)
    at 1280×720 and 1080×1920, with a preview;
  - exported with the Short, and uploaded through `thumbnails.set` only when
    the channel allows it (section 2.4).
- No generated imagery (section 2.5).

**Files:** `metadata.py`, new `thumbnails.py`, `exports.py` (thumbnail file), `youtube.py` (`thumbnails.set`), new `Studio.tsx`.
**Database:** `clips.meta.suggestions`, `clips.thumbnail` JSON.
**API:**
- `POST /clips/{id}/suggest` (job, AI);
- `GET /clips/{id}/thumbnail-candidates`;
- `POST /clips/{id}/thumbnail` (choose and design);
- `GET /clips/{id}/thumbnail.jpg`.

**Acceptance:**
- every candidate frame is a real frame of the Short;
- suggestions that fail the transcript check are dropped with the reason;
- the thumbnail meets YouTube's file rules (JPEG/PNG, at most 2 MB, 16:9 at
  1280×720);
- an upload that the channel refuses is reported, not retried.

**Tests:**
- frame ranking on synthetic frames (blurred, dark, face, no face);
- the overlay renders text and logo at the chosen place;
- file size and dimensions;
- suggestions with invented names are rejected;
- fake-AI failure;
- `thumbnails.set` refused by the fake Google.

**Risks:** whether Shorts thumbnails are visible depends on YouTube (section 2.4).

### 11.5 Manual clip editor

**Architecture.** Non-destructive: edits are stored as an "edit layer" over
the AI version. The source video is never touched.

- **Start and end.** The new boundaries are validated by the Smart Duration
  rules. A mid-word or mid-sentence cut is refused unless "cut here anyway" is
  chosen, and then it is recorded.
- **Caption text.** Editing a word is marked "edited by owner" and keeps that
  word's timing.
- **Style and position.** Per clip; today the style is per project, so a clip
  setting overrides the project's.
- **Crop and framing.** Layout (exists), a manual camera offset, and the zoom
  level and timing.
- **Music.** Choice (exists), plus volume (gap in dB) per clip.
- **Transitions.** For multi-segment clips, at the joins: cut, crossfade or
  dip to black.
- **Preview.** The fast 540×960 render (exists).
- **Save and history.** Each save is a revision (JSON), and the clip can be
  restored to the AI version or to any revision.

The edits are inputs to the existing render functions, so there is no second
render path.

**Files:** `pipelines.py` (`_render_clip` reads the edit layer), `duration.py` (validate manual bounds), `captions.py` (word overrides), `framing.py` (manual offset and zoom), `media.py` (crossfade joins), `ClipEditor.tsx` (timeline and controls).
**Database:** `clips.edits` JSON (the current edit layer), table `clip_revisions` (id, clip_id, created_at, edits JSON). New table only; existing data untouched.
**API:**
- `GET`/`PUT /clips/{id}/edits`;
- `POST /clips/{id}/edits/validate`;
- `GET /clips/{id}/revisions`;
- `POST /clips/{id}/restore`.

**Acceptance:**
- the source file's hash is unchanged after any edit;
- a mid-word cut needs an explicit override;
- restore returns the exact AI version;
- edited clips pass through the same QC;
- re-cutting the project keeps manual edits or warns before discarding them.

**Tests:**
- boundary validation at word edges;
- the override is recorded;
- revisions are kept and restored;
- a caption word edit keeps its timing;
- transitions render the right length;
- the source hash is unchanged;
- a corrupt edit layer falls back to the AI version.

**Risks:**
- this is the largest UI piece; a timeline needs care at phone width;
- manual edits and automatic re-cuts interact (the re-cut warning above).

### 11.6 Creator brand kit

**Architecture.** A `brand_profiles` table and a profile chosen per project
(optional; none means today's defaults).

- **A profile holds:** creator name, logo file and watermark (corner, opacity,
  size), brand colours, preferred fonts (from the installed fonts), a caption
  preset with its highlight colour, default music moods, preferred layout,
  title style (tone hints for the AI) and thumbnail style.
- **Files.** Logos are copied into `data\brand\<id>`.
- **Rendering.** The watermark is an overlay in the compositor, placed off
  faces, captions and the credit by the same placement code.

**Files:** new `brand.py`, `models.py`, `schemas.py` (`ProjectOptions.brand_profile_id`), `pipelines.py`, `captions.py` (font and colour from the profile), new `BrandKit.tsx`.
**Database:** new table `brand_profiles`; nullable `projects.brand_profile_id` (inside options JSON, so no column change).
**API:**
- `GET`/`POST /api/brand`;
- `GET`/`PATCH`/`DELETE /api/brand/{id}`;
- `POST /api/brand/{id}/logo`.

**Acceptance:**
- profiles survive restarts and are reused across projects;
- with no profile, renders are byte-identical in settings to today;
- deleting a profile in use asks first and falls back to the defaults;
- the logo is never placed over a face or caption.

**Tests:**
- persistence and migration;
- a profile applied or not;
- watermark placement against faces;
- invalid logo files (not an image, too large);
- deleting while in use;
- a regression showing default renders unchanged.

**Risks:**
- fonts are limited to the bundled OFL fonts unless the owner adds licensed ones.

### 11.7 Batch processing

**Architecture.** Keep the single worker (one heavy job at a time avoids
overload on this 4-core, 16 GB machine; Phase 9 showed two at once can run
out of memory). Add:

- **Priority:** a `jobs.priority` column; the queue is ordered by priority,
  then age.
- **Queue position** in the API and UI.
- **Pause:** stops the job at the next stage boundary, or chunk boundary for
  transcription. It keeps its stage results and resumes later, using the
  existing resumable stages.
- **Duplicates:** a source fingerprint (size, duration, a hash of the first and
  last MB, or the platform video id); a second project from the same source
  asks first.
- **Adding several videos:** several files or links in one form, each its own
  project with the same options and rights record.
- **Resources:** refuse to start a heavy job while free space is under the
  threshold, and say why.

**Files:** `jobs/engine.py`, `models.py`, `api/jobs.py`, `api/projects.py` (batch create, fingerprint), `NewProject.tsx`, new `Queue.tsx`.
**Database:** `jobs.priority`, `jobs.paused_at`, `projects.fingerprint` (nullable).
**API:**
- `POST /api/projects/batch`;
- `POST /api/jobs/{id}/pause|resume|priority`;
- `GET /api/jobs` returns the queue position.

**Acceptance:**
- five queued projects run one at a time in priority order;
- a paused job resumes from its stage;
- a crash mid-batch resumes all jobs;
- a duplicate is caught;
- each project's data stays separate.

**Tests:**
- priority ordering;
- pausing at a stage boundary;
- recovery after a simulated crash (kill mid-batch, restart);
- duplicate fingerprints;
- the disk-space refusal;
- rights still checked for each link in a batch.

**Risks:**
- a long transcription blocks the queue; pausing only takes effect at chunk boundaries.

### 11.8 Performance feedback loop

**Architecture.** An optional `analytics.py`, off by default.

- **Consent.** A separate, explicit grant of `yt-analytics.readonly` (and
  `youtube.readonly` for statistics).
- **Data.** Fetched on request or daily for Shorts ClipForge uploaded:
  - views, likes, comments, average view duration, average percentage viewed,
    where available;
  - joined locally with what ClipForge knows: category, duration, caption
    preset, hook, music used, layout, title and thumbnail.
- **Reports.** Groups with medians, ranges and counts, labelled "correlation,
  not cause", shown only when each group has enough Shorts.
- **Suggestions.** Proposed weight changes are shown as a diff for you to
  apply or ignore; nothing changes on its own.
- **Privacy.** No analytics data goes to the AI unless you allow it for that
  request. Collection can be turned off and its data deleted.

**Files:** new `analytics.py`, `youtube.py` (extra scope consent), new `Analytics.tsx`.
**Database:** new table `video_stats` (clip_id, fetched_at, metrics JSON); setting `analytics_enabled`.
**API:**
- `POST /api/analytics/connect`;
- `POST /api/analytics/fetch`;
- `GET /api/analytics/report`;
- `DELETE /api/analytics`.

**Acceptance:**
- nothing is fetched while it is off;
- reports state their sample sizes;
- no automatic weight change;
- deleting removes all stored metrics.

**Tests:**
- the fake Analytics API (normal answers, missing metrics, quota, revoked consent);
- grouping maths;
- minimum sample sizes;
- turning collection off stops fetches;
- nothing goes to the AI without consent.

**Risks:**
- YouTube Analytics data arrives with a 1–2 day delay;
- small channels will not have enough data for a long time.

### 11.9 YouTube Shorts optimisation

**Architecture.** A `shorts_checks()` publishing checklist, computed by the
server (today the checklist is assembled in the browser):

- vertical 1080×1920;
- duration at most 3 minutes, with a warning over 60 s;
- H.264/AAC, loudness and peaks;
- title 1–100 characters without `<` `>`;
- description at most 5,000;
- hashtags (YouTube ignores all hashtags when there are more than 60);
- thumbnail rules when there is a thumbnail;
- the rights record;
- the made-for-kids declaration;
- the credit present for others' videos.

**Enforcement.**
- The checklist is required by the upload and publish endpoints; a failed
  item blocks unless it is a warning.
- Metadata stays editable until publishing.
- The existing confirmation stays, and "never publish without explicit
  confirmation" stays in code.

**Files:** `youtube.py`, `api/youtube.py`, `Shorts.tsx` (renders the server checklist).
**Database:** none.
**API:** `GET /clips/{id}/publish-checklist`.
**Acceptance:**
- each failing item blocks with its reason;
- the checklist matches what the endpoints enforce;
- publishing still needs a confirmation.

**Tests:** each rule, passing and failing; the endpoints refuse a failing checklist; a regression of the Phase 10 tests.

**Risks:** YouTube changes its limits; they will be kept in one table.

### 11.10 Unified creator studio

**Architecture.** Reorganise the navigation without rewriting pages:

| Section | Today | Change |
| --- | --- | --- |
| Overview | Dashboard | add the queue and recent Shorts |
| Projects | Analysis / project page | rename only |
| Processing queue | — | new (11.7) |
| Detected moments | Moments panel | new page wrapping the panel |
| Clip editor | Clip editor | extended (11.5) |
| Captions studio | inside the Clip editor | own tab (11.3) |
| Music library | inside Settings | own page |
| Thumbnail studio | — | new (11.4) |
| Brand kit | — | new (11.6) |
| YouTube publishing | Generated Shorts | rename; checklist (11.9) |
| Performance analytics | — | new (11.8) |
| Settings | Settings | slimmer |

Existing components (`Section`, `Badge`, `JobPanel`, `QualityList` and the
rest) are reused. Every page gets loading, empty, success and failure states,
and phone width is checked in the browser.

**Files:** `App.tsx` (navigation, grouped), new pages listed above.
**Database / API:** none of its own.
**Acceptance:**
- every section reachable in two clicks;
- no horizontal scroll at 375 px;
- each state shown;
- the existing flows still work end to end.

**Tests:**
- frontend type check and build;
- a browser walk-through of each section with screenshots;
- the end-to-end flow from Phase 9 run again.

**Risks:** visual consistency across many new pages.

## 4. Things this plan does not do

- Other platforms (Reels, TikTok), multi-platform export, voice cloning or
  dubbing.
- Image generation for thumbnails (section 2.5).
- Automatic changes to detection weights.
- Claims that anything predicts virality.

## 5. Size and order

Rough effort, in sessions of the kind used for Phases 8–10:

| Milestones | Effort |
| --- | --- |
| 11.9, 11.6, 11.10 | about one session each |
| 11.1, 11.3, 11.4, 11.7 | one to two each |
| 11.2, 11.5, 11.8 | two each |

At the end of each milestone you get the push commands, as in every phase so
far.

## 6. What I need from you to start

1. Approval of this plan, with any changes.
2. Answers to section 2: items 1–9, or "use your proposals".
3. Whether to close the Phase 10 upload before starting 11.1, as recommended.
