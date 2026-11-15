"""Phase 5: the Smart Duration engine. The ten scenarios from docs/TASKS.md, plus its building blocks."""

from __future__ import annotations

import random

import pytest

from clipforge.duration import (
    ClipPlan,
    Context,
    DurationSettings,
    Word,
    build_sentences,
    ending_quality,
    plan_moment,
)


def say(start: float, text: str, seconds: float | None = None) -> list[Word]:
    """Words of one spoken line, spread evenly from `start` over `seconds`."""
    tokens = text.split()
    seconds = seconds if seconds is not None else 0.4 * len(tokens)
    step = seconds / len(tokens)
    return [Word(round(start + k * step, 2), round(start + (k + 1) * step - 0.06, 2), tok) for k, tok in enumerate(tokens)]


def script(*lines: tuple) -> list[Word]:
    words: list[Word] = []
    for line in lines:
        words += say(*line)
    return sorted(words, key=lambda w: w.s)


def one(ctx: Context, core_start: float, core_end: float, peak: float, **kw) -> ClipPlan:
    plans = plan_moment(ctx, core_start, core_end, peak, DurationSettings(**kw))
    assert len(plans) == 1
    return plans[0]


def no_word_is_cut(plan: ClipPlan, words: list[Word]) -> bool:
    """No word straddles the start or end of the clip, or the edge of a removed stretch."""
    edges = [plan.start, plan.end] + [t for seg in plan.segments for t in seg]
    return not any(w.s < t < w.e for w in words for t in edges)


# ---- 1. target 35 s, the sentence finishes at 37 s ---------------------------------


def test_01_the_clip_runs_to_37_seconds_so_the_sentence_can_finish():
    words = script(
        (100.0, "Okay so we are sneaking into the base now.", 4.0),
        (105.0, "Nobody has seen us and the alarm is off.", 5.0),
        (111.0, "I am going to open this door very quietly.", 6.0),
        (118.0, "There is a guard right there but he is asleep.", 6.0),
        (125.0, "Just one more step and we are inside.", 5.5),
        (131.5, "And he woke up, he woke up, everybody run for the helicopter right now!", 5.5),
        (150.0, "Anyway, back to the menu.", 2.0),
    )
    plan = one(Context(words, duration=200), 100.0, 137.0, 133.0, target=35, mode="smart")
    assert plan.valid
    assert plan.report["last_word"] == "now!" and plan.report["sentence_complete"]
    assert plan.end >= 137.0 - 0.06, "not cut at the 35 second mark"
    assert 36.5 <= plan.duration <= 38.5
    assert any("over the 35 s target" in r for r in plan.report["reasons"])
    assert no_word_is_cut(plan, words)


# ---- 2. target 30 s, the punchline lands at 34 s ------------------------------------


def test_02_setup_is_trimmed_so_the_punchline_at_34_seconds_stays_in():
    words = script(
        (200.0, "First I need to tell you about my neighbour.", 4.0),
        (205.0, "He has a parrot that copies everything.", 4.0),
        (210.0, "Yesterday I was practising my speech out loud.", 5.0),
        (216.0, "I said it maybe twenty times in the garden.", 6.0),
        (223.0, "This morning the whole street heard it again at six.", 8.0),
        (232.0, "The parrot gave my wedding speech before I did.", 4.0),
        (250.0, "So that happened.", 1.5),
    )
    plan = one(Context(words, duration=300), 200.0, 236.0, 234.0, target=30, mode="smart")
    assert plan.valid
    assert plan.report["last_word"] == "did.", "the punchline is in the clip"
    assert plan.duration <= 33.05, "inside the Smart window (30 s + 10%)"
    assert plan.start >= 204.0, "the first line of setup was dropped to make room"
    assert any("into the setup" in r for r in plan.report["reasons"])
    assert no_word_is_cut(plan, words)


# ---- 3. target 45 s, the reaction ends at 42 s ---------------------------------------


def test_03_a_clip_that_is_done_at_42_seconds_is_not_padded_to_45():
    words = script(
        (300.0, "Watch this jump, nobody has ever made this jump.", 6.0),
        (307.0, "I have been practising for three whole days.", 7.0),
        (315.0, "Full speed, full speed, here we go.", 8.0),
        (324.0, "I am in the air, I am actually flying.", 8.0),
        (333.0, "I landed it! I cannot believe I landed it!", 9.0),
        (350.0, "Okay, next up is the shopping list.", 4.0),
    )
    plan = one(Context(words, duration=400), 300.0, 342.0, 335.0, target=45, mode="smart")
    assert plan.valid
    assert plan.report["last_word"] == "it!"
    assert 41.5 <= plan.duration <= 43.0
    assert plan.end < 350.0, "the unrelated line after it is not pulled in"


# ---- 4. target 60 s, a gameplay event runs past 60 s ---------------------------------


def event_script() -> list[Word]:
    return script(
        (400.0, "Right, the boss fight starts in this room.", 7.0),
        (408.0, "I have one health potion left and no armour.", 9.0),
        (418.0, "He always opens with the fire attack.", 9.0),
        (428.0, "Dodge left, dodge left, now hit him.", 9.0),
        (438.0, "He is almost down, one more hit.", 9.0),
        (448.0, "Come on, come on, come on.", 10.5),
        (459.0, "The whole ceiling just fell on both of us!", 4.0),
        (480.0, "Well.", 0.5),
    )


def test_04_smart_mode_waits_for_the_event_to_finish():
    words = event_script()
    ctx = Context(words, events=[459.5], duration=500)
    plan = one(ctx, 400.0, 463.0, 460.0, target=60, mode="smart")
    assert plan.valid and plan.report["event_complete"]
    assert plan.report["last_word"] == "us!"
    assert 62.5 <= plan.duration <= 66.0
    assert plan.end >= 463.0 - 0.06


def test_04_strict_mode_keeps_the_event_and_trims_the_start_instead():
    words = event_script()
    ctx = Context(words, events=[459.5], duration=500)
    plan = one(ctx, 400.0, 463.0, 460.0, target=60, mode="strict")
    assert plan.valid and plan.duration <= 60.0
    assert plan.report["last_word"] == "us!", "the event is never cut to fit"
    assert plan.start > 400.5


# ---- 5. a 105 second moment becomes connected parts ----------------------------------


def long_story() -> list[Word]:
    lines = [
        "So I joined this server to build a quiet little farm.",
        "I planted wheat, I built a fence, I named every chicken.",
        "Then a player called Gary moved in next door.",
        "Gary said he only wanted to borrow a bucket.",
        "The next day my fence was gone.",
        "The day after that, so were six chickens.",
        "I followed the trail of feathers to his house.",
        "He had built a throne out of my fence.",
        "He was sitting on it wearing a crown.",
        "I asked him very politely to give it all back.",
        "He said the chickens had chosen him as their king.",
        "So I flooded his entire kingdom with one bucket.",
        "The same bucket he borrowed from me.",
        "The chickens swam home on their own.",
        "And that is why I am banned from that server.",
    ]
    return script(*[(500.0 + 7.0 * k, text, 6.2) for k, text in enumerate(lines)])


def test_05_a_105_second_moment_is_split_into_connected_parts():
    words = long_story()
    ctx = Context(words, duration=700)
    peak = 500.0 + 7.0 * 11 + 1.0  # "So I flooded his entire kingdom..."
    plans = plan_moment(ctx, 500.0, 605.0, peak, DurationSettings(target=45, mode="smart", split=True, max_parts=3))

    assert len(plans) == 3, "two parts of ~52 s would break the 49.5 s Smart limit"
    assert [p.part for p in plans] == [1, 2, 3] and all(p.parts == 3 for p in plans)
    assert all(p.valid for p in plans)
    assert all(18.0 <= p.duration <= 49.5 for p in plans)

    sentences = build_sentences(words)
    # consecutive whole sentences: nothing repeated, nothing skipped
    said = [w.t for p in plans for w in words if p.start <= w.s and w.e <= p.end]
    assert said == [w.t for w in words], "every word appears exactly once across the parts"
    for a, b in zip(plans, plans[1:]):
        assert a.end <= b.start, "parts do not overlap"
        assert b.report["continues_from_previous"] and b.report["gap_from_previous_seconds"] < 1.5
    assert all(p.report["sentence_complete"] and not p.report["repeats_dialogue"] for p in plans)

    # the line before the punchline stays in the same part as the punchline
    key = next(k for k, s in enumerate(sentences) if s.start <= peak <= s.end)
    holder = next(p for p in plans if p.start <= sentences[key].start and sentences[key].end <= p.end)
    assert holder.start <= sentences[key - 1].start
    assert all(no_word_is_cut(p, words) for p in plans)
    assert "Part 2 of 3" in plans[1].report["reasons"][0]
    # a part that is shorter than the target says the story continues, not that nothing follows
    every_reason = " ".join(r for p in plans for r in p.report["reasons"])
    assert "not part of this moment" not in every_reason
    assert "it continues in the next part" in " ".join(plans[0].report["reasons"])
    assert "it continues in the next part" not in " ".join(plans[-1].report["reasons"])


def test_05_without_splitting_a_clip_that_would_lose_most_of_the_story_is_refused():
    """Found while building: nine of fifteen sentences were dropped and the result was called valid."""
    words = long_story()
    plan = one(Context(words, duration=700), 500.0, 605.0, 578.0, target=45, mode="smart")
    assert not plan.valid
    assert "No complete version of this moment fits" in plan.report["no_fit"]
    assert "part splitting" in plan.report["no_fit"]
    # what is offered instead is the whole story, over the limit, never a shortened fragment
    assert plan.duration == pytest.approx(plan.report["shortest_complete_seconds"])
    assert plan.duration > 49.5 and plan.report["last_word"] == "server."


def test_a_refused_moment_offers_the_whole_moment_not_a_fragment():
    """Found on real speech (Tears of Steel): the payoff came after 47 s with no dialogue, nothing
    fitted, and the clip offered for 'Approve anyway' was a 1.7 s fragment."""
    words = script(
        (224.0, "Nothing to worry about.", 2.0),
        (230.0, "You love her.", 1.5),
        (233.0, "Be tender to her.", 1.5),
        (236.0, "Remind her what love is.", 2.0),
        (239.0, "Memory", 0.5),
        (286.0, "override in progress.", 1.5),
        (290.0, "You are a jerk, Tom!", 1.5),
    )
    plans = plan_moment(Context(words, duration=600), 224.0, 287.5, 286.5,
                        DurationSettings(target=30, mode="smart", split=True, max_parts=3, trim_silence=False))
    [plan] = plans
    assert not plan.valid and "could not be split" in plan.report["no_fit"]
    assert plan.duration > 33, "the offered clip is the complete moment, over the limit"
    # the shortest complete version: from "Memory" (kept with its payoff) to "progress."
    assert plan.report["last_word"] == "progress." and plan.start <= 239.0 and plan.duration >= 48
    assert plan.duration == pytest.approx(plan.report["shortest_complete_seconds"])


def test_05_a_moment_that_fits_is_not_split_even_when_splitting_is_on():
    words = script((10.0, "This is short.", 2.0), (13.0, "It ends here.", 2.0))
    plans = plan_moment(Context(words, duration=60), 10.0, 15.0, 13.5,
                        DurationSettings(target=30, mode="flexible", split=True))
    assert len(plans) == 1 and plans[0].valid and plans[0].parts == 1


def test_05_no_split_falls_where_a_loud_moment_is_under_way():
    words = long_story()
    sentences = build_sentences(words)
    # put a loud moment right at the end of the 5th and 10th sentences, where an even split would land
    events = [sentences[4].end + 0.3, sentences[9].end + 0.3]
    plans = plan_moment(Context(words, events=events, duration=700), 500.0, 605.0, 578.0,
                        DurationSettings(target=45, mode="smart", split=True, max_parts=3))
    assert len(plans) >= 2 and all(p.valid for p in plans)
    ends = [p.end for p in plans[:-1]]
    for e in events:
        assert not any(e - 2.0 <= end <= e + 0.3 for end in ends)


# ---- 6. the speaker pauses before finishing a sentence -------------------------------


def test_06_a_pause_in_the_middle_of_a_sentence_is_not_an_ending():
    words = script(
        (600.0, "Let me tell you the one rule of this game.", 4.0),
        (605.0, "What I was going to say is,", 3.0),
        (610.5, "you should never trust a goat with your lunch.", 4.0),
        (630.0, "Moving on.", 1.0),
    )
    sentences = build_sentences(words)
    assert [s.text.split()[-1] for s in sentences] == ["game.", "lunch.", "on."], "the pause did not end a sentence"

    ctx = Context(words, silences=[(608.0, 610.5)], duration=700)
    plan = one(ctx, 600.0, 614.5, 611.0, target=15, mode="smart")
    assert plan.valid and plan.report["last_word"] == "lunch."
    assert plan.report["dead_air_removed"] == pytest.approx(1.9, abs=0.15)
    assert len(plan.segments) == 2, "the dead air is removed, the words on both sides are kept"
    assert no_word_is_cut(plan, words)


def test_06_a_long_pause_before_a_capital_letter_does_end_a_sentence():
    words = script((0.0, "We made it out alive", 2.0), (5.0, "Anyway here is the map", 2.0))
    sentences = build_sentences(words)
    assert len(sentences) == 2
    assert sentences[0].terminal, "three seconds of silence after a complete-sounding word is a full stop"


def test_06_a_short_pause_before_a_capital_is_a_boundary_but_not_trusted_as_an_ending():
    words = script((0.0, "We made it out alive", 2.0), (3.4, "Anyway here is the map.", 2.0))
    sentences = build_sentences(words)
    assert len(sentences) == 2 and not sentences[0].terminal


def test_06_a_missing_full_stop_before_a_long_silence_does_not_glue_two_beats_together():
    """Found in live testing: the speech model wrote no full stop before a 12 s pause and lower-cased
    the next word, so an unrelated line and the story's opening became one 'sentence' and the clip
    started 17 seconds early."""
    words = script(
        (133.0, "chat is asking what my favourite car is probably the blue one", 5.0),
        (150.0, "chat I have to tell you what happened on the farming server.", 5.0),
        (157.0, "I planted wheat and I named every chicken.", 5.0),
        (180.0, "Anyway.", 0.5),
    )
    sentences = build_sentences(words)
    assert [s.text.split()[0] for s in sentences] == ["chat", "chat", "I", "Anyway."]
    assert sentences[0].terminal and sentences[0].gap_after > 10

    ctx = Context(words, silences=[(138.2, 149.9)], duration=200)
    plan = one(ctx, 150.0, 162.0, 158.0, target=15, mode="smart", context="full")
    assert plan.valid and plan.start >= 149.5, "the unrelated line before the silence is not pulled in"
    assert plan.report["first_word"] == "chat" and plan.report["dead_air_removed"] == 0


def test_06_four_seconds_of_silence_ends_a_sentence_even_after_a_comma():
    words = script((0.0, "So what I think is,", 2.0), (7.0, "never mind, look at this.", 2.0))
    assert len(build_sentences(words)) == 2


def test_a_short_afterthought_stays_with_the_line_it_follows():
    """Found in live testing: Part 2 opened on 'Completely gone.'"""
    words = script(
        (0.0, "I built a fence around the whole farm.", 6.0),
        (7.0, "It took me three entire days to finish.", 6.0),
        (14.0, "The next morning my fence was gone.", 5.0),
        (19.3, "Completely gone.", 1.5),
        (22.0, "So I followed the trail of feathers.", 6.0),
        (29.0, "And there he was, sitting on a throne.", 6.0),
        (36.0, "A throne made entirely out of my fence.", 6.0),
        (60.0, "Okay.", 0.5),
    )
    ctx = Context(words, duration=80)
    sentences = build_sentences(words)
    quality_before, problems = ending_quality(sentences, 2, ctx)
    quality_after, _ = ending_quality(sentences, 3, ctx)
    assert any("short follow-up" in p for p in problems) and quality_before < quality_after

    plans = plan_moment(ctx, 0.0, 42.0, 30.0, DurationSettings(target=20, mode="smart", split=True, max_parts=3))
    assert len(plans) >= 2 and all(p.valid for p in plans)
    assert not any(p.report["first_word"] == "Completely" for p in plans)
    holder = next(p for p in plans if p.start <= 14.0 <= p.end)
    assert holder.end >= 20.7, "'was gone.' and 'Completely gone.' are in the same part"


# ---- 7. several speakers talk at once ------------------------------------------------


def test_07_the_clip_does_not_end_while_someone_is_talking_over_the_last_words():
    words = script(
        (700.0, "I am telling you this bridge is completely safe.", 5.0),
        (706.0, "Look, I will drive across it right now.", 6.05),  # ends at 712.0
    ) + [
        # a second voice starts before the first has finished
        Word(711.7, 712.2, "No"), Word(712.25, 712.6, "way"), Word(712.65, 713.0, "that"),
        Word(713.05, 713.3, "just"), Word(713.35, 714.0, "happened!"),
    ] + say(730.0, "Okay.", 0.5)
    words.sort(key=lambda w: w.s)
    ctx = Context(words, duration=800)
    plan = one(ctx, 700.0, 712.0, 708.0, target=15, mode="smart")
    assert plan.valid
    assert plan.report["last_word"] == "happened!", "it waits for the second speaker to finish"
    assert not plan.report["speakers_overlap_at_end"]

    sentences = build_sentences(words)
    quality, problems = ending_quality(sentences, 1, ctx)
    assert quality < 40 and any("talking over" in p for p in problems)


# ---- 8. a caption would overlap the end of the video ----------------------------------


def test_08_the_clip_never_runs_past_the_video_and_the_last_word_is_fully_inside():
    words = script(
        (40.0, "This is the very last thing in the recording.", 8.0),
        (49.0, "And then the stream just ended like that.", 10.8),  # last word ends at 59.74
    )
    plan = one(Context(words, duration=60.0), 40.0, 59.8, 52.0, target=20, mode="smart")
    assert plan.valid
    assert plan.end <= 60.0
    assert plan.report["last_word_fully_inside"]
    assert words[-1].e <= plan.end and words[0].s >= plan.start


def test_08_a_word_timed_past_the_end_of_the_video_is_flagged_not_hidden():
    words = script((40.0, "Nearly done here.", 3.0)) + [Word(59.0, 60.6, "Goodbye.")]
    plan = one(Context(words, duration=60.0), 40.0, 60.0, 59.2, target=20, mode="smart")
    assert not plan.report["last_word_fully_inside"]
    assert not plan.valid


# ---- 9. a natural stopping point comes before the target ------------------------------


def test_09_a_moment_that_ends_early_stays_short_and_says_why():
    words = script(
        (780.0, "Something unrelated from earlier.", 3.0),
        (800.0, "I bet I can beat this level with my eyes closed.", 9.0),
        (810.0, "Eyes are closed, hands on the keys.", 9.0),
        (820.0, "I think I am doing really well.", 8.0),
        (829.0, "I have been walking into a wall for a full minute.", 9.0),
        (870.0, "Right, a different topic now.", 3.0),
    )
    plan = one(Context(words, silences=[(838.2, 870.0)], duration=900), 800.0, 838.0, 831.0, target=60, mode="smart")
    assert plan.valid
    assert 37.5 <= plan.duration <= 39.5
    assert plan.end < 870.0 and plan.start > 783.5
    assert any("natural stopping point" in r for r in plan.report["reasons"])


# ---- 10. nothing complete fits Strict mode --------------------------------------------


def strict_case() -> list[Word]:
    return script(
        (900.0, "Here is the plan.", 3.0),
        (904.0, "We go in through the roof, down the vent, past the lasers, grab the diamond, "
                "swap it for the fake, back up the vent, across the roof and into the van before anyone notices "
                "that the entire building is, in fact, the wrong building.", 28.0),
        (950.0, "Okay.", 0.5),
    )


def test_10_strict_mode_reports_that_no_complete_clip_fits():
    plan = one(Context(strict_case(), duration=1000), 900.0, 932.0, 925.0, target=15, mode="strict")
    assert not plan.valid
    assert "No complete version of this moment fits 15 s" in plan.report["no_fit"]
    assert plan.report["shortest_complete_seconds"] == pytest.approx(28.2, abs=0.6)
    assert "Flexible" in plan.report["no_fit"]


def test_10_splitting_cannot_rescue_one_sentence_that_is_too_long():
    plans = plan_moment(Context(strict_case(), duration=1000), 900.0, 932.0, 925.0,
                        DurationSettings(target=15, mode="strict", split=True, max_parts=3))
    assert len(plans) == 1 and not plans[0].valid
    assert "could not be split" in plans[0].report["no_fit"]


def test_10_flexible_mode_takes_the_same_moment_whole():
    plan = one(Context(strict_case(), duration=1000), 904.0, 932.0, 925.0, target=20, mode="flexible")
    assert plan.valid and plan.duration == pytest.approx(28.2, abs=0.8)
    assert any("over the 20 s target" in r for r in plan.report["reasons"])


# ---- the modes -----------------------------------------------------------------------


def test_the_three_modes_set_different_limits():
    assert DurationSettings(target=35, mode="strict").high == 35
    smart = DurationSettings(target=35, mode="smart", tolerance=0.10)
    assert (smart.low, smart.high) == pytest.approx((31.5, 38.5))
    assert DurationSettings(target=45, mode="flexible").high == pytest.approx(72)


def test_flexible_mode_explains_a_longer_result():
    words = script(
        (0.0, "I have a good feeling about this door.", 9.0),
        (10.0, "It is definitely the exit.", 9.0),
        (20.0, "I checked the map twice.", 9.0),
        (30.0, "Opening it now.", 8.0),
        (39.0, "That is not the exit, that is a dragon.", 6.0),
        (46.0, "Why is there always a dragon?", 5.5),
        (80.0, "Reloading.", 1.0),
    )
    ctx = Context(words, duration=120)
    flexible = one(ctx, 0.0, 51.5, 41.0, target=45, mode="flexible")
    assert flexible.valid and 51.0 <= flexible.duration <= 52.5
    assert flexible.report["reasons"][0].startswith("Runs 7 s over the 45 s target")

    strict = one(ctx, 0.0, 51.5, 41.0, target=45, mode="strict")
    assert strict.valid and strict.duration <= 45.0
    assert strict.report["last_word"] == "dragon?", "strict trims the start, never the ending"


def test_a_question_is_kept_with_its_answer():
    words = script(
        (0.0, "We finally reached the vault.", 4.0),
        (5.0, "Do you know the code?", 3.0),
        (8.6, "I thought you knew the code.", 3.0),
        (30.0, "Great.", 0.5),
    )
    ctx = Context(words, duration=60)
    sentences = build_sentences(words)
    quality, problems = ending_quality(sentences, 1, ctx)
    assert any("question whose answer follows" in p for p in problems) and quality < 60
    # the moment as picked stops at the question (8 s); the engine carries on to the answer
    plan = one(ctx, 0.0, 8.0, 6.0, target=12, mode="smart")
    assert plan.valid and plan.report["last_word"] == "code." and plan.report["question_answered"]
    assert plan.end > 11.0

    # with only 10 s allowed the whole exchange does not fit, and it says so instead of cutting the answer
    tight = one(ctx, 0.0, 8.0, 6.0, target=10, mode="smart")
    assert not tight.valid and "No complete version of this moment fits 11 s" in tight.report["no_fit"]


def test_context_setting_controls_how_much_comes_before_the_moment():
    words = script(
        (0.0, "Earlier line one.", 2.0), (3.0, "Earlier line two.", 2.0), (6.0, "Earlier line three.", 2.0),
        (10.0, "The moment starts here.", 3.0), (14.0, "And it pays off here.", 3.0), (40.0, "Later.", 0.5),
    )
    ctx = Context(words, duration=60)
    starts = {
        level: one(ctx, 10.0, 17.0, 15.0, target=30, mode="flexible", context=level).start
        for level in ("tight", "balanced", "full")
    }
    assert starts["tight"] >= 9.7
    assert starts["balanced"] < starts["tight"] or starts["balanced"] == starts["tight"]
    assert starts["full"] <= starts["balanced"] <= starts["tight"]


def test_dead_air_is_only_removed_where_nobody_speaks():
    words = script((0.0, "Before the gap.", 2.0), (8.0, "After the gap.", 2.0), (30.0, "Later.", 0.5))
    # the loudness analysis claims silence over a stretch that actually contains a word
    ctx = Context(words, silences=[(1.0, 9.0)], duration=60)
    plan = one(ctx, 0.0, 10.0, 8.5, target=10, mode="flexible")
    assert plan.segments == [(plan.start, plan.end)], "a 'silence' that overlaps speech is left alone"

    off = one(Context(words, silences=[(2.2, 7.9)], duration=60), 0.0, 10.0, 8.5, target=10, mode="flexible",
              trim_silence=False)
    assert len(off.segments) == 1 and off.report["dead_air_removed"] == 0


def test_no_transcript_means_no_clip_and_a_plain_reason():
    plan = one(Context([], duration=60), 10.0, 40.0, 20.0, target=30)
    assert not plan.valid and "no transcript" in plan.report["no_fit"]


# ---- property: whatever the input, valid clips begin and end on whole sentences --------


@pytest.mark.parametrize("seed", range(40))
def test_valid_plans_never_cut_a_word_and_respect_the_mode_limit(seed):
    rng = random.Random(seed)
    lines, t = [], 5.0
    for k in range(rng.randint(8, 22)):
        n = rng.randint(2, 12)
        text = " ".join(f"w{k}_{i}" for i in range(n - 1)) + " " + rng.choice(["end.", "end?", "end!", "end."])
        seconds = n * rng.uniform(0.25, 0.6)
        lines.append((round(t, 2), text.capitalize(), round(seconds, 2)))
        t += seconds + rng.choice([0.2, 0.4, 0.8, 1.5, 3.0, 6.0])
    words = script(*lines)
    total = t + 5
    a, b = sorted(rng.sample(range(len(lines)), 2))
    core_start, core_end = lines[a][0], lines[b][0] + lines[b][2]
    peak = rng.uniform(core_start, core_end)
    silences = [(w1.e + 0.05, w2.s - 0.05) for w1, w2 in zip(words, words[1:]) if w2.s - w1.e > 2.5]
    st = DurationSettings(
        target=rng.choice([15, 20, 30, 35, 45, 60, 90]), mode=rng.choice(["strict", "smart", "flexible"]),
        split=rng.random() < 0.5, max_parts=rng.choice([2, 3, 4]),
        context=rng.choice(["tight", "balanced", "full"]),
    )
    plans = plan_moment(Context(words, silences=silences, duration=total), core_start, core_end, peak, st)
    assert plans
    sentence_starts = {s.start for s in build_sentences(words)}
    for p in plans:
        assert no_word_is_cut(p, words)
        assert p.duration == pytest.approx(sum(e - s for s, e in p.segments), abs=0.02)
        if not p.valid:
            assert p.report.get("no_fit") or p.report.get("problems") is not None
            continue
        inside = [w for w in words if p.start <= w.s and w.e <= p.end]
        assert inside and inside[0].s in sentence_starts, "starts at the beginning of a sentence"
        assert inside[-1].t.endswith((".", "?", "!")), "ends on a finished sentence"
        limit = st.high if p.parts == 1 or st.mode == "strict" else st.target * 1.6
        assert p.duration <= limit + 0.06
    if len(plans) > 1:
        for x, y in zip(plans, plans[1:]):
            assert x.end <= y.start + 1e-6
