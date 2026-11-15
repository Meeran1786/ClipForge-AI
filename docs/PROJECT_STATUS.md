# ClipForge AI — Project status

Updated 2026-11-16, after Phase 8. Detail for each phase is in
[ROADMAP.md](ROADMAP.md); feature items are in [TASKS.md](TASKS.md).

## Where it stands

The app takes a video file or a permitted YouTube or Twitch link, analyses
it, has an AI pick and score the best moments, and cuts each one into a clip
on whole sentences. Each clip is framed for 9:16: split screen when the
streamer has a facecam, otherwise full screen following the speaker's face.
It gets word-synced captions kept off faces, a PART label on connected parts,
and licensed background music. Approved clips are rendered as finished
1080×1920 Shorts levelled to YouTube's loudness, ready to download. Quality
checks, titles and descriptions, export and publishing are still to come.

| Phase | What | State |
| --- | --- | --- |
| 1 | Foundation, dashboard, job queue, launcher | Done |
| 2 | Ingestion and rights record | Done (Kick: file only) |
| 3 | Sound and picture analysis, transcript | Done |
| 4 | AI moment detection and viral score | Built; owner's check open |
| 5 | Clip extraction with Smart Duration and part splitting | Done |
| 6 | Captions | Done |
| 7 | Music | Built; tested on synthetic music only |
| 8 | Vertical framing and rendering | Done (facecam tested on a composite, not a real stream) |
| 9 | Quality checks and export | Next |
| 10 | YouTube publishing | Not started |

## Open items that need the owner

1. **Phase 4 check.** Watch the top ten picks on three of your own videos and
   judge whether at least seven are worth clipping. Nothing has yet run on
   real stream or gaming footage.
2. **Test videos** you are allowed to use, for that check and for the later
   phases. A real stream with a facecam would show how framing copes with
   overlays that are not clean rectangles.
3. **A few real music tracks** you are allowed to use (for example from the
   YouTube Audio Library), to test beat finding and mood matching on real
   songs. Phase 7 was tested on synthetic music only. Optional: Shorts can be
   made without music.
4. **A Google Cloud project with the YouTube Data API**, before Phase 10.

## How it has been verified

- 391 automated tests, using stand-ins for the platforms, the speech model
  and the AI.
- Live runs with the real speech model and the real AI on: synthetic spoken
  test videos with known answers, and Tears of Steel (12 minutes of real
  dialogue, CC BY 3.0, Blender Foundation).
- Clip cuts checked by an independent script against the real word timings,
  under every duration preset, mode and splitting setting (204 clips on real
  speech, no problems).
- Captions checked frame by frame on the rendered previews: the highlight was
  on the word being said in 237 of 237 frames, and nothing showed during
  29 of 29 pauses.
- Music checked on the rendered files: 24 dB under the speech on every clip,
  the speech changed by at most 0.15 dB when music was added, and the
  unlicensed track was never used (synthetic tracks, spoken test video).
- Framing checked on a composed facecam "gaming" video and on Tears of Steel,
  by running the face model again on the rendered Shorts: the subject was in
  frame at 470 of 470 face samples, no caption covered a face in 302 frames,
  and no PART label touched one. Seven final Shorts at 1080×1920, −14.1 to
  −15.6 LUFS, peaks at −1.1 dBTP or lower.

## Known limits

- The AI that picks moments is `claude-sonnet-4-6` through the owner's
  Antigravity sign-in, because the Gemini tiers there returned empty replies
  or timed out. The Gemini API-key route is written but has never been run.
- The AI sees still frames, not moving video or sound. Each call carries about
  28,000 tokens of fixed overhead.
- No speaker identification; who is talking is inferred from timing.
- Framing follows faces only: the gameplay panel is a centred crop and does
  not follow the action; with two people too far apart the camera stays with
  one. Captions avoid faces, not the game's HUD. Real facecam overlays
  (rounded, transparent, keyed) are untested.
- The final render takes about 1 to 1.8 times the clip's length here.
- No real music has been tested: beat finding and mood matching are measured
  on synthetic tracks only (beats within 6 ms on average, worst case 35 ms).
- About one caption word in nine on real film dialogue is flagged as one the
  speech model was unsure of; captions cannot yet be corrected by hand.
- Kick recordings cannot be downloaded; add them as files.
- This PC has no graphics card: speech-to-text runs at about ten times real
  time, so a three-hour stream takes roughly 20–40 minutes.
