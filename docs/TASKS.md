# ClipForge AI — Feature tasks

Requirements that were agreed as a feature, with what is built and what is
still to come. The phase plan is in [ROADMAP.md](ROADMAP.md).

## Smart Duration and context-aware clip splitting

Requested 2026-11-15. **The core was built in Phase 5 (2026-11-15).** The
parts that depend on captions, framing, music and export are still open and
are marked with the phase they belong to. Principle: the chosen duration
guides the editor; it never forces a cut through a sentence, a punchline, a
reaction or an event.

`[x]` built and tested · `[~]` built in part, see the note · `[ ]` not built

### Where each part lands

| Part | Lands in | State |
| --- | --- | --- |
| Target duration, modes, ending detection, part splitting | Phase 5 | Built |
| Smart Duration panel on New Project, project settings, Settings | Phase 5 | Built |
| Caption start/end checks, part labels clear of captions | Phase 6 | Built (label itself: Phase 8) |
| Part labels placed off faces and gameplay, consistent layout across parts | Phase 8 | Not built |
| Consistent music and audio levels across parts | Phase 7 (music), Phase 8 (levels) | Music built (synthetic tracks only); final levels Phase 8 |
| Per-part titles and hashtags | Phase 9 | Not built |
| Smart Duration panel in the Clip Editor, manual start/end adjustment | Phases 6–8 | Not built |

The engine is `backend/clipforge/duration.py`. It takes a picked moment plus
the word timings, silences, scene changes and loud moments, and returns
boundaries with a validation report. It makes no AI call and cuts no video;
it is never a plain time cut-off in FFmpeg.

### Tasks

Duration choice
- [x] Presets: 15, 20, 30, 35, 45, 60 and 90 seconds, plus a custom value
      (10 to 180 seconds).
- [x] The old shortest/longest pair is replaced by a target and a mode;
      projects saved with the old pair still open (the longest becomes the
      target).

Modes
- [x] **Strict:** never longer than the target. Setup is trimmed from the
      start if that makes a complete clip fit; otherwise it reports that no
      valid clip was found, with the shortest complete length.
- [x] **Smart (default):** the target is an aim with a tolerance (default
      10%, so 35 s gives about 32–39 s). Prefers natural endings.
- [x] **Flexible:** the target is a preference; a clip may run to 1.6 times
      the target to keep the whole moment, and states why.

Ending detection, checked before a clip is final
- [x] The speaker finished the sentence. A full stop, question mark or
      exclamation mark ends one; so does two seconds of silence after a word
      with no comma, because speech-to-text sometimes omits the full stop.
- [x] A question got its answer: a clip never ends on a question when a reply
      follows within 2.5 seconds.
- [~] A joke reached its punchline. The AI marks where the payoff is when it
      picks the moment; the engine never ends before that line and never
      splits right before it. The engine itself does not judge humour.
- [~] A reaction finished. The engine carries on to lines that follow the
      payoff without a pause, and will not end while a sudden loud moment is
      under way. It cannot hear laughter or see a face.
- [~] No important gameplay event is still in progress. Judged from sudden
      loud moments only, not from what is on screen.
- [~] Another speaker is not about to respond. Judged from word timings
      (the next words start with no pause, or overlap the last ones). There
      is no speaker identification.
- [x] The conversation reached a natural stopping point: a clip ends early
      instead of padding itself with a line from after a pause.
- [x] If the target ends mid-sentence: extend when the mode allows;
      otherwise move the start; otherwise refuse with a reason. A clip that
      would drop more than half of the moment is refused, not shortened.

Part splitting (Part 1, Part 2, …)
- [x] Option to split a long moment into connected parts, with a target
      duration and a maximum number of parts (2 to 5). The number of parts is
      chosen automatically. With splitting on, the AI is asked for moments
      long enough to be worth splitting.
- [x] Splits fall only where a sentence has finished: never mid-sentence,
      never on a question whose answer follows, never while a loud moment is
      under way, never right before the key line, and never before a short
      afterthought to the previous line.
- [x] A final part too short to stand alone is not produced; the lines join
      the part before.
- [x] Part numbering; no repeated dialogue, no missing dialogue, no overlap:
      parts are consecutive runs of whole sentences.
- [~] Same caption style across parts: the style is set once per project,
      so every clip and every part uses it (Phase 6).
- [x] Same music across parts: parts of one moment share one track and the
      music carries on from where the previous part's ended; the same gap
      under the speech is used for every part (Phase 7).
- [ ] Same layout and final audio levels across parts (Phase 8).
- [ ] Optional on-screen PART 1 / PART 2 / PART 3 label that covers neither
      faces nor gameplay (Phase 8).
- [ ] Titles and hashtags per part (Phase 9).

Caption and video sync (Phase 6)
- [x] Engine side: the clip always runs past the end of its last word and
      starts before its first, so a caption for either can be shown in full;
      a word timed past the end of the video is flagged.
- [x] First caption starts at the right time (checked per clip, to one frame).
- [x] No caption ends before the speaker or lingers more than 0.3 s after
      the dialogue; a line gives way to the next one instead of overlapping.
- [x] The last caption is never cut off by the end of the clip.
- [~] Captions never overlap the part label: every clip is checked to keep
      its captions out of the top 18% of the frame, which is reserved for the
      label. The label itself is drawn in Phase 8.

Pre-export validation
- [x] Report per clip: requested duration, actual duration, first spoken
      word, last spoken word, sentence completion, question answered, event
      completion, speakers overlapping at the end, ending quality score, and
      for parts whether each continues cleanly from the one before.
- [x] A clip that fails is marked "did not pass" and cannot be approved
      without an explicit "Approve anyway".

Interface
- [x] Smart Duration panel on New Project, in a project's Clip settings and
      in Settings (defaults): duration selector, custom input, Strict / Smart
      / Flexible, tolerance, part splitting, maximum parts, context before
      the moment, dead-air removal.
- [x] Each clip shows target against actual length with the reason for any
      difference, a preview, and Approve / Reject.
- [~] "Adjust the result" is done by changing the settings, which re-cuts at
      once without the AI. Dragging a clip's own start or end is not built.
- [ ] The Smart Duration panel inside the Clip Editor. The Clip Editor now
      exists (Phase 6) but holds caption style only; duration is still set in
      the project's Clip settings.

Test cases (automated, in `backend/tests/test_duration.py`)
- [x] 1. Target 35 s, sentence finishes at 37 s.
- [x] 2. Target 30 s, punchline at 34 s.
- [x] 3. Target 45 s, reaction ends at 42 s.
- [x] 4. Target 60 s, gameplay event continues past 60 s.
- [x] 5. A 105 s moment split into connected parts.
- [x] 6. A speaker pauses before finishing a sentence.
- [x] 7. Several speakers talking at once.
- [x] 8. A caption overlapping the end of the video: the clip never runs
      past the video, the last word is fully inside, and the last caption's
      hold is shortened so it ends with the clip (`test_captions.py`).
- [x] 9. A natural stopping point before the target.
- [x] 10. No valid standalone moment fits Strict mode.

Also tested: a randomised check over many generated transcripts that no valid
clip ever cuts a word or exceeds its mode's limit, and a live run of 87 clips
under 30 setting combinations checked against real word timings.

### Known limits

- No speaker identification, so anything about "who is speaking" is inferred
  from timing.
- Checked on one synthetic spoken video. Not yet run on real stream footage.
- In Smart mode a part can exceed the tolerance when a moment needs more
  parts than the maximum allows. The clip says so in its reasons.
