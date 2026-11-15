# ClipForge AI — Planned feature tasks

Requirements that are agreed but not built yet. The phase plan is in
[ROADMAP.md](ROADMAP.md).

## Smart Duration and context-aware clip splitting

Requested 2026-11-15. **Not started. Do not begin until Phase 4 is finished and
tested.** Principle: the chosen duration guides the editor; it never forces a
cut through a sentence, a punchline, a reaction or an event.

### Where each part lands

| Part | Lands in | Needs first |
| --- | --- | --- |
| Target duration, modes, ending detection, part splitting | Phase 5 (clip extraction) | Phase 3 word timings, silences, scene changes; Phase 4 moments |
| Caption start/end checks, part labels clear of captions | Phase 6 (captions) | Phase 5 boundaries |
| Part labels placed off faces and gameplay, consistent layout across parts | Phase 8 (framing and rendering) | Phase 6 captions |
| Consistent music and audio levels across parts | Phase 7 (music) and Phase 8 | Phase 5 parts |
| Pre-export validation report, per-part titles and hashtags | Phase 9 (QC and export) | Phases 5–8 |
| Smart Duration panel | New Project (Phase 5), Clip Editor (Phases 6–8) | Phase 5 engine |

The duration engine is one reusable module that takes a detected moment plus
the transcript, word timings, silences and scene changes, and returns
boundaries with reasons. It is never a plain time cut-off in FFmpeg.

### Tasks

Duration choice
- [ ] Presets: 15, 20, 30, 35, 45, 60 and 90 seconds, plus a custom value.
- [ ] Replace the current shortest/longest pair in project settings with a
      target duration and a mode, keeping old projects working.

Modes
- [ ] **Strict:** the clip stays within the target. Find a complete standalone
      moment that fits; never cut dialogue or an event to fit. If none fits,
      say that no valid clip was found.
- [ ] **Smart (default):** the target is an aim with a configurable tolerance
      (for example 35 s gives 33–38 s). Prefer natural endings.
- [ ] **Flexible:** the target is a preference. The clip may run longer to keep
      the whole moment (for example 45 s becomes 52 s), with the reason stated.

Ending detection, checked before a clip is final
- [ ] The speaker finished the sentence.
- [ ] A question got its answer.
- [ ] A joke reached its punchline.
- [ ] A reaction finished.
- [ ] No important gameplay event is still in progress.
- [ ] Another speaker is not about to respond.
- [ ] The conversation reached a natural stopping point.
- [ ] If the target ends mid-sentence: extend when possible; otherwise move
      the start or pick another moment. Never export an unintended half sentence.

Part splitting (Part 1, Part 2, …)
- [ ] Option to split a long moment into connected Shorts, with target
      duration, automatic or fixed number of parts, and a maximum.
- [ ] Split only at natural boundaries: not mid-sentence, not during a
      reaction, not before a punchline, not during an important event. Adjust
      part lengths to reach such a boundary.
- [ ] Drop a final part that is not meaningful on its own.
- [ ] Continuity across parts: numbering, same layout, same caption style,
      same audio levels, same music where used, no repeated dialogue, no missing
      dialogue, no unexplained jumps.
- [ ] Optional on-screen PART 1 / PART 2 / PART 3 label that covers neither
      faces nor important gameplay.
- [ ] Titles and hashtags per part; continuation titles only when the parts
      really belong together.

Caption and video sync
- [ ] First caption starts at the right time.
- [ ] Last spoken word is shown in full; no caption ends before the speaker.
- [ ] No caption lingers after the dialogue or is cut off by the video ending.
- [ ] Captions never overlap the part label.

Pre-export validation
- [ ] Report per clip: requested duration, estimated actual duration, first
      spoken word, last spoken word, sentence completion, event completion,
      ending quality score, part continuity.
- [ ] Reject clips with incomplete dialogue or broken continuity unless the
      user explicitly overrides the warning.

Interface
- [ ] Smart Duration panel on New Project and in the Clip Editor: duration
      selector, custom input, Strict / Smart / Flexible, part splitting switch,
      maximum parts, context preservation setting.
- [ ] Show the estimate before rendering, with the reason for any difference
      (for example "Target 35 s, estimated 38 s: the final sentence and
      reaction need 3 more seconds"), and let the user approve or adjust.

Test cases (each must be an automated test with known timings)
- [ ] 1. Target 35 s, sentence finishes at 37 s.
- [ ] 2. Target 30 s, punchline at 34 s.
- [ ] 3. Target 45 s, reaction ends at 42 s.
- [ ] 4. Target 60 s, gameplay event continues past 60 s.
- [ ] 5. A 105 s moment split into connected parts.
- [ ] 6. A speaker pauses before finishing a sentence.
- [ ] 7. Several speakers talking at once.
- [ ] 8. A caption overlapping the end of the video.
- [ ] 9. A natural stopping point before the target.
- [ ] 10. No valid standalone moment fits Strict mode.

### Known limits to plan around

- There is no speaker identification yet (Phase 3), so "another speaker is
  about to respond" and "several speakers at once" can only be judged from
  the transcript text and overlapping word timings until that exists.
- "Joke reached its punchline" and "question got its answer" are judgements
  by the AI model; the word timings and silences then place the exact cut.
