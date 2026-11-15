"""Job pipelines.

`ingest_local` - a video file on this computer: read its facts, grab a poster
                 frame, make a 360p preview copy, extract 16 kHz mono audio.
`ingest_url`   - a link: look the video up, download it (only with a rights
                 record), then the same four stages.
`selftest`     - checks the machine can do what the app needs: FFmpeg present, a
             captioned vertical render works, storage is writable. Its last two
             stages only wait, so progress, cancel and resume-after-kill can be
             seen without a real video.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .ai import make_reasoner
from .analysis.audio_signals import analyse_audio
from .analysis.transcript import (
    make_transcriber,
    model_is_downloaded,
    offset_segments,
    plan_chunks,
    read_audio,
    tighten,
)
from .analysis.video_signals import analyse_video
from .ingest import PLATFORM_LABELS, IngestRefused, source_for
from .jobs.engine import Pipeline, StageContext, StageDef
from .captions import build_captions, clip_words
from .duration import ClipPlan, Context, DurationSettings, Word, plan_moment
from .media import (
    MediaError,
    MixSpec,
    extract_audio,
    make_proxy,
    make_thumbnail,
    probe_media,
    render_music_stem,
    render_ranges,
    render_vertical,
    run,
    tool_version,
)
from .models import Clip, Job, Moment, Project, Setting, Track, TranscriptSegment
from .music import (
    MIN_GAP_DB,
    TrackInfo,
    choose_track,
    clip_windows,
    is_plain_conversation,
    level_in_windows,
    licence_problems,
    merge_ranges,
    plan_mix,
    preferred_moods,
    requires_attribution,
    silence_gaps,
    to_clip_time,
    write_envelope,
)
from .schemas import ProjectOptions, normalise_options
from .moments import Line, clean_weights, detect_moments
from .moments import select as pick_selected
from .rights import download_blocker, write_rights_file


def default_link_title(kind: str) -> str:
    """Placeholder name for a link project until the platform tells us the real title."""
    return f"{PLATFORM_LABELS.get(kind, kind.capitalize())} video"

# ---- ingest -----------------------------------------------------------------

MIN_FREE_BYTES = 2 * 1024**3  # keep this much free after a download


def _source_file(ctx: StageContext) -> Path:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        if project is None or not project.source_path:
            raise MediaError("This project has no video file yet")
        path = Path(project.source_path)
    if not path.is_file():
        raise MediaError(f"The video file is missing: {path}")
    return path


def _media_info(ctx: StageContext) -> dict:
    info = ctx.results.get("probe")
    if info:
        return info
    with ctx.session() as s:
        return dict(s.get(Project, ctx.project_id).media or {})


def _metadata(ctx: StageContext) -> dict:
    """Look the link up on its platform. Nothing is downloaded here."""
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        url, kind = project.source_url, project.source_kind
        blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
    # The same check runs when the project is created; it is repeated here so no
    # code path can reach the download without a rights record.
    if blocker:
        raise IngestRefused(blocker, "rights", hint=False)
    source = source_for(url or "")
    if source is None:
        raise IngestRefused("This link is not from a supported platform.", "unsupported")

    ctx.report(0.1, f"Asking {source.label} about this video")
    meta = source.fetch_metadata(url, ctx.settings)

    free = shutil.disk_usage(ctx.settings.data_dir).free
    need = int((meta.est_bytes or 0) * 1.4) + MIN_FREE_BYTES
    if free < need:
        raise IngestRefused(
            f"Not enough disk space: this video needs about {need / 1024**3:.1f} GB free "
            f"and the data drive has {free / 1024**3:.1f} GB.",
            "disk", hint=False,
        )

    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.source_meta = meta.to_dict()
        if meta.title and project.title == default_link_title(kind):
            project.title = meta.title[:300]
        s.commit()
    ctx.publish_project()
    return meta.to_dict()


def _download(ctx: StageContext) -> dict:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        url = project.source_url
        blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
    if blocker:
        raise IngestRefused(blocker, "rights", hint=False)
    source = source_for(url or "")
    if source is None:
        raise IngestRefused("This link is not from a supported platform.", "unsupported")

    dest_dir = ctx.settings.project_dir(ctx.project_id)
    path = source.download(url, dest_dir, ctx.settings, ctx.report)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.source_path = str(path)
        s.commit()
    ctx.publish_project()
    return {"path": str(path), "bytes": path.stat().st_size}


def _probe(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    ctx.report(0.1, "Reading video information")
    info = probe_media(ctx.settings, path)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.media = info
        s.commit()
        write_rights_file(ctx.settings, project)
    ctx.publish_project()
    return info


def _thumbnail(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    duration = _media_info(ctx).get("duration") or 0
    at = min(duration * 0.1, 30.0) if duration else 0.0
    ctx.report(0.2, "Grabbing a poster frame")
    dest = ctx.settings.project_dir(ctx.project_id) / "thumbnail.jpg"
    make_thumbnail(ctx.settings, path, dest, at)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.thumbnail = str(dest)
        s.commit()
    ctx.publish_project()
    return {"thumbnail": str(dest)}


def _proxy(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    info = _media_info(ctx)
    dest = ctx.settings.project_dir(ctx.project_id) / "proxy.mp4"
    make_proxy(ctx.settings, path, dest, info, lambda f: ctx.report(f, "Making a small preview copy"))
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.proxy_path = str(dest)
        s.commit()
    ctx.publish_project()
    ctx.report(1.0, f"Preview copy: {dest.stat().st_size / 1e6:.0f} MB")
    return {"proxy": str(dest), "bytes": dest.stat().st_size}


def _audio(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    info = _media_info(ctx)
    if not info.get("has_audio"):
        ctx.report(1.0, "This video has no sound, so there is no audio to extract")
        return {"audio": None}
    dest = ctx.settings.project_dir(ctx.project_id) / "audio.wav"
    extract_audio(ctx.settings, path, dest, info, lambda f: ctx.report(f, "Extracting the audio track"))
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.audio_path = str(dest)
        s.commit()
    ctx.publish_project()
    ctx.report(1.0, f"Audio: {dest.stat().st_size / 1e6:.0f} MB")
    return {"audio": str(dest), "bytes": dest.stat().st_size}


# ---- analysis ---------------------------------------------------------------


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _note_analysis(ctx: StageContext, key: str, value: dict) -> None:
    """Merge one stage's summary into Project.analysis."""
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.analysis = {**(project.analysis or {}), key: value}
        s.commit()
    ctx.publish_project()


def _audio_signals(ctx: StageContext) -> dict:
    with ctx.session() as s:
        audio = s.get(Project, ctx.project_id).audio_path
    if not audio or not Path(audio).is_file():
        ctx.report(1.0, "This video has no sound to analyse")
        return {"skipped": True}
    data = analyse_audio(Path(audio), lambda f: ctx.report(f, "Measuring loudness and silences"))
    out = ctx.settings.project_dir(ctx.project_id) / "audio_signals.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    summary = {
        "events": len(data["events"]),
        "silences": len(data["silences"]),
        "silence_seconds": data.get("stats", {}).get("silence_seconds", 0),
    }
    _note_analysis(ctx, "audio", summary)
    ctx.report(1.0, f"{summary['events']} sudden loud moments, {_clock(summary['silence_seconds'])} of silence")
    return summary


def _video_signals(ctx: StageContext) -> dict:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        proxy, duration = project.proxy_path, (project.media or {}).get("duration")
    if not proxy or not Path(proxy).is_file():
        raise MediaError("The preview copy is missing; prepare the video again")
    data = analyse_video(ctx.settings, Path(proxy), duration, lambda f: ctx.report(f, "Looking for scene changes"))
    out = ctx.settings.project_dir(ctx.project_id) / "video_signals.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    summary = {"cuts": len(data["cuts"])}
    _note_analysis(ctx, "video", summary)
    ctx.report(1.0, f"{summary['cuts']} scene changes")
    return summary


def _transcript(ctx: StageContext) -> dict:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        audio = project.audio_path
        state = dict((project.analysis or {}).get("transcript") or {})
    if not audio or not Path(audio).is_file():
        ctx.report(1.0, "This video has no sound to transcribe")
        return {"skipped": True}

    signals_file = ctx.settings.project_dir(ctx.project_id) / "audio_signals.json"
    signals = json.loads(signals_file.read_text(encoding="utf-8")) if signals_file.is_file() else {}
    silences = signals.get("silences") or []
    plan = [tuple(c) for c in state.get("plan") or []]
    if not plan:
        duration = signals.get("duration") or _media_info(ctx).get("duration") or 0
        plan = plan_chunks(
            duration, signals.get("loudness") or [], signals.get("hop") or 0.5,
            float(ctx.settings.transcript_chunk_seconds),
        )
    total = plan[-1][1] if plan else 0.0
    done = int(state.get("chunks_done", 0))
    language = state.get("language") or (None if ctx.settings.language == "auto" else ctx.settings.language)

    if done < len(plan):
        if not model_is_downloaded(ctx.settings):
            ctx.report(0.0, "Downloading the speech model (first time only, about 500 MB)")
        else:
            ctx.report(plan[done][0] / total if total else 0.0, "Loading the speech model")
        transcriber = make_transcriber(ctx.settings)
        for i, (a, b) in enumerate(plan):
            if i < done:
                continue  # saved before an earlier interruption

            def tick(fraction: float, a: float = a, b: float = b) -> None:
                at = a + (b - a) * fraction
                ctx.report(min(0.99, at / total), f"Transcribing: {_clock(at)} of {_clock(total)}")

            tick(0.0)
            segments, heard = transcriber.transcribe(read_audio(audio, a, b), language, tick)
            language = language or heard
            rows = tighten(offset_segments(segments, a, total), silences)
            with ctx.session() as s:
                project = s.get(Project, ctx.project_id)
                s.add_all(
                    TranscriptSegment(project_id=project.id, chunk=i, start=r["start"], end=r["end"],
                                      text=r["text"], words=r["words"])
                    for r in rows
                )
                lines = int(state.get("lines", 0)) + len(rows)
                state = {
                    "plan": [list(c) for c in plan], "chunks_done": i + 1, "lines": lines,
                    "language": language, "model": transcriber.name, "done": i + 1 == len(plan),
                }
                # the chunk's lines and the "chunk finished" mark are saved together
                project.analysis = {**(project.analysis or {}), "transcript": state}
                s.commit()
            ctx.publish_project()

    if not plan:
        _note_analysis(ctx, "transcript", {"plan": [], "chunks_done": 0, "lines": 0, "language": language,
                                           "model": None, "done": True})
    lines = int(state.get("lines", 0))
    ctx.report(1.0, f"{lines} spoken lines" + (f", language: {language}" if language else ""))
    return {"lines": lines, "language": language}


WEIGHTS_KEY = "score_weights"


def load_weights(s: Session) -> dict[str, float]:
    row = s.get(Setting, WEIGHTS_KEY)
    return clean_weights(row.value if row else None)


def _moments(ctx: StageContext) -> dict:
    """Pick and score the moments worth clipping. Needs an AI provider."""
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        options = dict(project.options or {})
        proxy = Path(project.proxy_path) if project.proxy_path else None
        duration = float((project.media or {}).get("duration") or 0)
        weights = load_weights(s)
        rows = s.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == ctx.project_id)
            .order_by(TranscriptSegment.start, TranscriptSegment.id)
        ).all()
        lines = [Line(i, r.start, r.end, r.text) for i, r in enumerate(rows, start=1)]
    signals_file = ctx.settings.project_dir(ctx.project_id) / "audio_signals.json"
    events = json.loads(signals_file.read_text(encoding="utf-8")).get("events", []) if signals_file.is_file() else []

    if not lines and not events:
        with ctx.session() as s:
            s.execute(delete(Moment).where(Moment.project_id == ctx.project_id))
            s.commit()
        _note_analysis(ctx, "moments", {"found": 0, "selected": 0, "note": "no speech and no loud moments"})
        ctx.report(1.0, "No speech and no loud moments in this video, so there is nothing to pick from")
        return {"found": 0}

    ctx.report(0.0, "Connecting to the AI")
    reasoner = make_reasoner(ctx.settings)
    ranked, notes = detect_moments(
        reasoner, ctx.settings,
        lines=lines, events=events, duration=duration, proxy=proxy, options=options, weights=weights,
        work_dir=ctx.settings.project_dir(ctx.project_id) / "sheets",
        report=ctx.report, check=ctx.check,
    )
    shutil.rmtree(ctx.settings.project_dir(ctx.project_id) / "sheets", ignore_errors=True)

    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        s.execute(delete(Moment).where(Moment.project_id == ctx.project_id))
        s.add_all(
            Moment(
                project_id=ctx.project_id, start=c.start, end=c.end, peak=c.peak, category=c.category,
                score=c.score, subscores=c.subscores, reason=c.reason, excerpt=c.excerpt, visual=c.visual,
                visual_checked=c.visual_checked, source=c.source, rank=c.rank, selected=c.selected,
                core_start=c.core_start, core_end=c.core_end,
            )
            for c in ranked
        )
        project.analysis = {**(project.analysis or {}), "moments": notes}
        s.commit()
    ctx.publish_project()

    message = f"{notes['found']} moments found, {notes['selected']} selected"
    if notes["visual_failed"]:
        message += f" ({notes['visual_failed']} picture check(s) failed; those scores come from the words alone)"
    ctx.report(1.0, message)
    return notes


CLIP_OVERLAP_LIMIT = 0.5  # two clips sharing more than this much of the shorter one show the same event


def _clip_dir(settings, project_id: int) -> Path:
    return settings.project_dir(project_id) / "clips"


def clear_clips(s: Session, settings, project_id: int) -> None:
    s.execute(delete(Clip).where(Clip.project_id == project_id))
    shutil.rmtree(_clip_dir(settings, project_id), ignore_errors=True)


def _plan_without_speech(start: float, end: float, st: DurationSettings, total: float) -> ClipPlan:
    """A moment nobody speaks in (found by sound alone) is cut by time: there are no sentences to respect."""
    end = min(end, start + st.high, total) if total else min(end, start + st.high)
    length = round(end - start, 2)
    return ClipPlan(round(start, 2), round(end, 2), [(round(start, 2), round(end, 2))], length, length >= 3.0, {
        "requested_seconds": st.target, "actual_seconds": length, "mode": st.mode,
        "first_word": None, "last_word": None, "sentence_complete": None,
        "question_answered": True, "event_complete": None, "speakers_overlap_at_end": False,
        "ending_quality": None, "start_quality": None, "last_word_fully_inside": True,
        "setup_sentences_dropped": 0, "dead_air_removed": 0,
        "reasons": ["Nobody speaks in this stretch, so it is cut by time around the loud moment."],
        "problems": [],
    })


def _clips(ctx: StageContext) -> dict:
    """Place each selected moment's exact cut with the Smart Duration engine and render a preview. No AI."""
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        options = ProjectOptions(**normalise_options(project.options))
        proxy = Path(project.proxy_path) if project.proxy_path else None
        media = dict(project.media or {})
        chosen = s.scalars(
            select(Moment).where(Moment.project_id == ctx.project_id, Moment.selected.is_(True)).order_by(Moment.rank)
        ).all()
        moments = [
            {"id": m.id, "rank": m.rank, "category": m.category, "score": m.score, "peak": m.peak,
             "start": m.core_start if m.core_start is not None else m.start,
             "end": m.core_end if m.core_end is not None else m.end}
            for m in chosen
        ]
        rows = s.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == ctx.project_id)
            .order_by(TranscriptSegment.start, TranscriptSegment.id)
        ).all()
        words = sorted(
            (Word(float(w["s"]), float(w["e"]), str(w["w"])) for r in rows for w in (r.words or []) if w.get("w")),
            key=lambda w: (w.s, w.e),
        )
    folder = ctx.settings.project_dir(ctx.project_id)

    def load(name: str) -> dict:
        path = folder / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    audio, video = load("audio_signals.json"), load("video_signals.json")
    total = float(media.get("duration") or audio.get("duration") or 0)
    context = Context(
        words=words,
        silences=[(float(a), float(b)) for a, b in audio.get("silences") or []],
        cuts=[float(t) for t in video.get("cuts") or []],
        events=[float(e["t"]) for e in audio.get("events") or []],
        duration=total,
    )
    st = options.duration_settings()

    planned: list[tuple[dict, ClipPlan]] = []
    accepted: list[ClipPlan] = []
    duplicates = 0
    for moment in moments:
        ctx.check()
        spoken = any(moment["start"] - 1.0 <= w.s <= moment["end"] + 1.0 for w in words)
        plans = (
            plan_moment(context, moment["start"], moment["end"], moment["peak"], st)
            if spoken else [_plan_without_speech(moment["start"], moment["end"], st, total)]
        )
        # two moments can be refined onto the same stretch: keep the better-ranked one
        clash = any(
            min(p.end, a.end) - max(p.start, a.start) > CLIP_OVERLAP_LIMIT * min(p.end - p.start, a.end - a.start)
            for p in plans if p.valid for a in accepted
        )
        if clash:
            duplicates += 1
            continue
        accepted += [p for p in plans if p.valid]
        planned += [(moment, p) for p in plans]

    with ctx.session() as s:
        clear_clips(s, ctx.settings, ctx.project_id)
        s.commit()

    out_dir = _clip_dir(ctx.settings, ctx.project_id)
    made = 0
    records: list[Clip] = []
    for n, (moment, plan) in enumerate(planned):
        ctx.report(n / max(len(planned), 1), f"Cutting clip {n + 1} of {len(planned)}")
        preview = None
        if proxy and proxy.is_file() and plan.duration >= 1.0:
            dest = out_dir / f"clip_{moment['rank']:02d}_part{plan.part}.mp4"
            render_ranges(ctx.settings, proxy, dest, plan.segments, has_audio=bool(media.get("has_audio", True)))
            preview = str(dest)
        made += plan.valid
        records.append(Clip(
            project_id=ctx.project_id, status="cut", moment_id=moment["id"], rank=moment["rank"],
            part=plan.part, parts=plan.parts, start=plan.start, end=plan.end,
            segments=[list(seg) for seg in plan.segments], duration=plan.duration,
            category=moment["category"], score=moment["score"], valid=plan.valid, report=plan.report,
            preview=preview, review="pending", overridden=False,
        ))

    summary = {
        "made": made,
        "not_valid": len(records) - made,
        "duplicates_dropped": duplicates,
        "multi_part_moments": len({r.moment_id for r in records if (r.parts or 1) > 1}),
        "target_seconds": st.target, "mode": st.mode, "split": st.split,
    }
    with ctx.session() as s:
        s.add_all(records)
        project = s.get(Project, ctx.project_id)
        project.analysis = {**(project.analysis or {}), "clips": summary}
        s.commit()
    ctx.publish_project()

    message = f"{made} clip(s) cut"
    if summary["not_valid"]:
        message += f", {summary['not_valid']} could not be cut cleanly"
    if duplicates:
        message += f", {duplicates} dropped as repeats of a better clip"
    ctx.report(1.0, message)
    return summary


def track_info(t: Track) -> TrackInfo:
    """A library track as the music engine sees it, with whatever still stops it being usable."""
    problems = licence_problems(t.source, t.licence_name, t.licence_url, t.licence_note, t.attribution)
    if not t.file or not Path(t.file).is_file():
        problems = [*problems, "The track's file is missing from the music folder."]
    return TrackInfo(
        id=t.id, title=t.title, file=t.file, duration=float(t.duration or 0),
        loudness_db=float(t.loudness_db if t.loudness_db is not None else -20.0), moods=list(t.moods or []),
        usable=not problems, problems=problems, bpm=t.bpm, beats=list(t.beats or []),
        phase=int(t.phase or 0), regularity=float(t.regularity or 0.0),
        licence_name=t.licence_name, source=t.source, attribution=t.attribution, channels=int(t.channels or 2),
    )


def load_track_infos(s: Session) -> list[TrackInfo]:
    return [track_info(t) for t in s.scalars(select(Track).order_by(Track.id)).all()]


def plan_project_music(
    s: Session, settings, project_id: int, only_keys: set | None = None, forced: dict | None = None
) -> dict[int, dict]:
    """Decide the music for each moment's clips: {clip id: record}. Parts of one moment share one track.

    `forced` maps a moment key to the owner's choice ({"mode": "none"} or {"mode": "track", "track_id": n}).
    """
    project = s.get(Project, project_id)
    options = ProjectOptions(**normalise_options(project.options))
    clips = list(s.scalars(select(Clip).where(Clip.project_id == project_id).order_by(Clip.rank, Clip.part, Clip.id)).all())
    tracks = {t.id: t for t in load_track_infos(s)}
    moments = {m.id: m for m in s.scalars(select(Moment).where(Moment.project_id == project_id)).all()}
    rows = s.scalars(
        select(TranscriptSegment).where(TranscriptSegment.project_id == project_id)
        .order_by(TranscriptSegment.start, TranscriptSegment.id)
    ).all()
    words = [w for r in rows for w in (r.words or []) if w.get("w")]
    signals = settings.project_dir(project_id) / "audio_signals.json"
    events = [float(e["t"]) for e in json.loads(signals.read_text(encoding="utf-8")).get("events", [])] if signals.is_file() else []
    audio = Path(project.audio_path) if project.audio_path and Path(project.audio_path).is_file() else None

    groups: dict[int, list[Clip]] = {}
    for c in clips:
        groups.setdefault(c.moment_id if c.moment_id is not None else -c.id, []).append(c)
    out: dict[int, dict] = {}
    uses: dict[int, int] = {}
    for key, group in groups.items():
        if only_keys is not None and key not in only_keys:
            continue
        group.sort(key=lambda c: (c.part or 1, c.id))
        choice = (forced or {}).get(key)
        category = group[0].category
        windows: list[tuple[float, float]] = []
        for c in group:
            windows += clip_windows(words, c.segments or [])
        total_len = sum(float(c.duration or 0) for c in group)
        speech_time = sum(b - a for a, b in merge_ranges(windows, 0.0))
        coverage = speech_time / total_len if total_len else 0.0
        has_event = any(a <= e <= b for c in group for a, b in (c.segments or []) for e in events)

        track, manual = None, choice is not None
        if choice and choice.get("mode") == "none":
            reason = "You chose no music for this clip."
        elif choice and choice.get("mode") == "track":
            track = tracks.get(choice.get("track_id"))
            if track is None or not track.usable:
                reason = "That track cannot be used" + (": " + " ".join(track.problems) if track else ": it is not in the library.")
                track = None
            else:
                reason = f"You chose “{track.title}”."
        elif options.music_mode == "off":
            reason = "Music is turned off for this project."
        elif options.music_mode == "auto" and is_plain_conversation(coverage, has_event):
            reason = (f"Plain conversation: talking fills {coverage:.0%} of the clip with nothing else happening, "
                      "so music would only get in the way.")
        else:
            track, reason = choose_track(list(tracks.values()), category, max(float(c.duration or 0) for c in group), uses)

        if track is None:
            for c in group:
                out[c.id] = {"used": False, "mode": "manual" if manual else "auto", "reason": reason}
            continue
        uses[track.id] = uses.get(track.id, 0) + 1
        matched = next((m for m in preferred_moods(category) if m in track.moods), None)
        prev_end = None
        for c in group:
            segs = [tuple(x) for x in (c.segments or [])]
            moment = moments.get(c.moment_id)
            anchor = to_clip_time(moment.peak, segs) if moment else None
            spoken = clip_windows(words, segs)
            speech_db = level_in_windows(audio, spoken) if audio and spoken else None
            overall = level_in_windows(audio, segs) if audio else None
            mix = plan_mix(track, float(c.duration or 0), anchor, speech_db,
                           overall if overall is not None else -30.0, options.music_gap_db, continue_from=prev_end)
            prev_end = mix["ends_at"]
            out[c.id] = {
                "used": True, "mode": "manual" if manual else "auto", "track_id": track.id, "title": track.title,
                "mood": matched, "reason": reason, "speech_db": None if speech_db is None else round(speech_db, 1),
                "gap_db": options.music_gap_db, **mix,
                "licence_name": track.licence_name, "source": track.source,
                "credit": track.attribution if requires_attribution(track.licence_name) else None,
            }
    return out


def _music(ctx: StageContext) -> dict:
    """Choose each clip's background music (or none) and where in the track it starts. No AI."""
    ctx.report(0.0, "Choosing music")
    with ctx.session() as s:
        plans = plan_project_music(s, ctx.settings, ctx.project_id)
        for c in s.scalars(select(Clip).where(Clip.project_id == ctx.project_id)).all():
            if c.id in plans:
                c.music = plans[c.id]
        project = s.get(Project, ctx.project_id)
        mode = ProjectOptions(**normalise_options(project.options)).music_mode
        used = sum(1 for p in plans.values() if p.get("used"))
        summary = {"mode": mode, "with_music": used, "without_music": len(plans) - used}
        project.analysis = {**(project.analysis or {}), "music": summary}
        s.commit()
    ctx.publish_project()
    ctx.report(1.0, f"{used} clip(s) with music, {len(plans) - used} without" if plans else "No clips to score")
    return summary


def _prepare_mix(settings, out_dir: Path, stem: str, music: dict | None, tracks: dict[int, TrackInfo],
                 speech: list[tuple[float, float]], length: float) -> tuple[MixSpec | None, dict | None]:
    """The mix to render for a clip, and its record (which changes if the track can no longer be used)."""
    if not music or not music.get("used"):
        return None, music
    track = tracks.get(music.get("track_id"))
    if track is None or not track.usable:
        why = ("The chosen track is no longer in the library." if track is None
               else "The chosen track no longer has complete licence details.")
        return None, {**music, "used": False, "reason": why}
    envelope = write_envelope(out_dir / f"{stem}_duck.wav", speech, length)
    return MixSpec(Path(track.file), float(music["offset"]), bool(music["loop"]), float(music["gain_db"]),
                   float(music["fade_in"]), float(music["fade_out"]), envelope, track.channels), music


def _measure_mix(settings, out_dir: Path, stem: str, spec: MixSpec, music: dict,
                 speech: list[tuple[float, float]], length: float) -> dict:
    """How far under the speech the music really is, measured on the music as mixed."""
    stem_wav = render_music_stem(settings, spec, length, out_dir / f"{stem}_music.wav")
    try:
        in_speech = level_in_windows(stem_wav, speech)
        open_level = level_in_windows(stem_wav, silence_gaps(speech, length))
    finally:
        stem_wav.unlink(missing_ok=True)
    speech_db = music.get("speech_db")
    gap = None if in_speech is None or speech_db is None else round(speech_db - in_speech, 1)
    return {
        "speech_db": speech_db,
        "music_in_speech_db": None if in_speech is None else round(in_speech, 1),
        "music_open_db": None if open_level is None else round(open_level, 1),
        "gap_db": gap, "required_gap_db": MIN_GAP_DB, "ok": gap is None or gap >= MIN_GAP_DB,
    }


def _captions(ctx: StageContext) -> dict:
    """Build captions for each clip, mix in its music, and render the vertical preview. No AI.

    With `clip_ids` in the job's parameters only those clips are rendered again.
    """
    only = {int(i) for i in (ctx.params.get("clip_ids") or [])}
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        options = ProjectOptions(**normalise_options(project.options))
        media = dict(project.media or {})
        source = Path(project.source_path) if project.source_path else None
        proxy = Path(project.proxy_path) if project.proxy_path else None
        clips = [
            (c.id, c.rank or 0, c.part or 1, [tuple(seg) for seg in (c.segments or [])], float(c.duration or 0),
             dict(c.music) if c.music else None)
            for c in s.scalars(
                select(Clip).where(Clip.project_id == ctx.project_id).order_by(Clip.rank, Clip.part, Clip.id)
            ).all()
        ]
        rows = s.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == ctx.project_id)
            .order_by(TranscriptSegment.start, TranscriptSegment.id)
        ).all()
        words = [w for r in rows for w in (r.words or []) if w.get("w")]
        tracks = {t.id: t for t in load_track_infos(s)}
    # Render from the original when it is still there: the 360p copy is too soft for a 9:16 crop.
    video = source if source and source.is_file() else proxy
    if video is None or not video.is_file():
        raise MediaError("Neither the original video nor its preview copy is available")
    style = options.caption_style
    has_audio = bool(media.get("has_audio", True))
    out_dir = _clip_dir(ctx.settings, ctx.project_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    todo = [c for c in clips if not only or c[0] in only]
    for n, (clip_id, rank, part, segments, length, music) in enumerate(todo):
        ctx.report(n / max(len(todo), 1), f"Rendering clip {n + 1} of {len(todo)}")
        if not segments or length < 1.0:
            continue
        lo, hi = segments[0][0], segments[-1][1]
        nearby = [w for w in words if float(w["e"]) > lo and float(w["s"]) < hi]
        result = build_captions(nearby, [list(seg) for seg in segments], length, style)
        stem = f"clip_{rank:02d}_part{part}"
        ass_path = None
        if result.ass:
            ass_path = out_dir / f"{stem}.ass"
            ass_path.write_text(result.ass, encoding="utf-8")
        speech = [(w.s, w.e) for w in clip_words(nearby, [list(seg) for seg in segments])]
        spec, music = _prepare_mix(ctx.settings, out_dir, stem, music, tracks, speech, length)
        dest = out_dir / f"{stem}_vertical.mp4"
        try:
            render_vertical(ctx.settings, video, dest, segments, ass_path, has_audio=has_audio, music=spec,
                            audio_channels=int(media.get("audio_channels") or 2))
            if spec is not None and music is not None:
                music = {**music, "measured": _measure_mix(ctx.settings, out_dir, stem, spec, music, speech, length)}
        finally:
            if spec is not None and spec.envelope is not None:
                spec.envelope.unlink(missing_ok=True)
        with ctx.session() as s:
            clip = s.get(Clip, clip_id)
            if clip is not None:
                clip.vertical = str(dest)
                clip.music = music
                clip.captions = {**result.report(), "lines": result.lines,
                                 "style": style.model_dump(), "framing": "centre crop (Phase 8 adds smart framing)"}
                s.commit()

    with ctx.session() as s:  # the summary covers every clip, not just the ones rendered this time
        done = [c for c in s.scalars(select(Clip).where(Clip.project_id == ctx.project_id)).all() if c.vertical]
        made = len(done)
        passed = sum(1 for c in done if (c.captions or {}).get("passed"))
        flagged = sum(len((c.captions or {}).get("flagged_words") or []) for c in done)
        summary = {"made": made, "passed": passed, "flagged_words": flagged,
                   "preset": style.preset, "enabled": style.enabled}
        project = s.get(Project, ctx.project_id)
        project.analysis = {**(project.analysis or {}), "captions": summary}
        s.commit()
    ctx.publish_project()
    message = f"{len(todo)} clip(s) rendered"
    if made - passed:
        message += f", {made - passed} with a caption check to look at"
    if flagged:
        message += f", {flagged} word(s) the speech model was unsure of"
    ctx.report(1.0, message)
    return summary


def reselect(s: Session, project: Project) -> None:
    """Re-rank and re-mark a project's moments after its settings or the weights change. No AI call."""
    rows = list(s.scalars(select(Moment).where(Moment.project_id == project.id)).all())
    pick_selected(rows, project.options or {})
    summary = dict((project.analysis or {}).get("moments") or {})
    if summary:
        summary.update(found=len(rows), selected=sum(r.selected for r in rows))
        project.analysis = {**(project.analysis or {}), "moments": summary}


def reset_analysis(s: Session, settings, project: Project) -> None:
    """Forget a previous analysis so it can be run again from scratch."""
    s.execute(delete(TranscriptSegment).where(TranscriptSegment.project_id == project.id))
    s.execute(delete(Moment).where(Moment.project_id == project.id))
    clear_clips(s, settings, project.id)
    project.analysis = None
    for name in ("audio_signals.json", "video_signals.json"):
        (settings.project_dir(project.id) / name).unlink(missing_ok=True)


def _ingest_finished(s: Session, job: Job) -> None:
    project = s.get(Project, job.project_id) if job.project_id else None
    if project is None:
        return
    if job.status == "done":
        project.status = "ready"
        project.status_detail = None
    elif job.status == "failed":
        failed_stage = next((st.name for st in job.stages if st.status == "failed"), None)
        if failed_stage == "moments":
            # The video is prepared and analysed; only the AI pick is missing.
            project.status = "ready"
            project.status_detail = (
                f"Moments were not picked: {job.error}. The video, timeline and transcript are ready. "
                "Use Retry to try again."
            )
        elif failed_stage == "clips":
            project.status = "ready"
            project.status_detail = (
                f"Clips were not cut: {job.error}. The picked moments are still there. Use Retry to try again."
            )
        elif failed_stage in ("captions", "music"):
            project.status = "ready"
            project.status_detail = (
                f"Captions and music were not added: {job.error}. The clips are cut and can be reviewed. "
                "Use Retry to try again."
            )
        else:
            project.status = "failed"
            project.status_detail = job.error
    elif job.status == "cancelled":
        project.status = "failed"
        project.status_detail = "Cancelled. Use Resume to continue from where it stopped."


def _ingest_retried(s: Session, job: Job) -> None:
    project = s.get(Project, job.project_id) if job.project_id else None
    if project is not None:
        project.status = "processing"
        project.status_detail = None


_PREPARE = [
    StageDef("probe", "Read video information", _probe),
    StageDef("thumbnail", "Create poster frame", _thumbnail),
    StageDef("proxy", "Make preview copy", _proxy),
    StageDef("audio", "Extract audio", _audio),
]

_ANALYSE = [
    StageDef("audio_signals", "Analyse sound", _audio_signals),
    StageDef("video_signals", "Analyse picture", _video_signals),
    StageDef("transcript", "Transcribe speech", _transcript),
]

_MUSIC = [
    StageDef("music", "Choose music", _music),
]

# Rendering is where captions and music are laid on the clip, so it is one stage.
_CAPTION = [
    StageDef("captions", "Add captions and music", _captions),
]

_CUT = [
    StageDef("clips", "Cut the clips", _clips),
    *_MUSIC,
    *_CAPTION,
]

_DETECT = [
    StageDef("moments", "Find the best moments", _moments),
    *_CUT,
]


# ---- selftest ---------------------------------------------------------------

_TEST_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: D,Arial,90,&H00FFFFFF,&H00000000,&H80000000,1,1,6,2,2,40,40,420,1

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,D,SELF {\\c&H00FFFF&}TEST{\\c&HFFFFFF&} OK
"""


def _check_tools(ctx: StageContext) -> dict:
    ffmpeg = tool_version(ctx.settings.ffmpeg)
    ffprobe = tool_version(ctx.settings.ffprobe)
    if not ffmpeg or not ffprobe:
        raise MediaError("FFmpeg or ffprobe is missing from tools\\ffmpeg")
    ctx.report(1.0, f"FFmpeg {ffmpeg}")
    return {"ffmpeg": ffmpeg, "ffprobe": ffprobe}


def _check_render(ctx: StageContext) -> dict:
    """Render one second of 1080x1920 H.264/AAC with a burned-in caption."""
    ctx.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="selftest_", dir=ctx.settings.tmp_dir))
    try:
        (work / "cap.ass").write_text(_TEST_ASS, encoding="utf-8")
        ctx.report(0.2, "Rendering a test Short")
        # cwd is the work folder so the caption file needs no path escaping
        run(
            [ctx.settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=1",
             "-f", "lavfi", "-i", "sine=f=440:d=1",
             "-vf", "crop=608:1080,scale=1080:1920,ass=cap.ass",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", "out.mp4"],
            timeout=180, cwd=work,
        )
        ctx.report(0.8, "Checking the result")
        info = probe_media(ctx.settings, work / "out.mp4")
        if (info["width"], info["height"]) != (1080, 1920):
            raise MediaError(f"Expected 1080x1920, got {info['width']}x{info['height']}")
        if info["video_codec"] != "h264" or info["audio_codec"] != "aac":
            raise MediaError(f"Unexpected codecs: {info['video_codec']}/{info['audio_codec']}")
        ctx.report(1.0, "1080x1920 H.264/AAC with captions")
        return {"width": info["width"], "height": info["height"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _check_storage(ctx: StageContext) -> dict:
    ctx.settings.ensure_dirs()
    probe = ctx.settings.tmp_dir / "write_test.txt"
    probe.write_text("ok", encoding="utf-8")
    if probe.read_text(encoding="utf-8") != "ok":
        raise OSError("Data folder is not readable after writing")
    probe.unlink()
    free_gb = shutil.disk_usage(ctx.settings.data_dir).free / 1024**3
    if free_gb < 2:
        raise OSError(f"Only {free_gb:.1f} GB free on the data drive; at least 2 GB is needed")
    ctx.report(1.0, f"{free_gb:.1f} GB free")
    return {"free_gb": round(free_gb, 1)}


def _wait_stage(label: str):
    def fn(ctx: StageContext) -> dict:
        seconds = float(ctx.params.get("stage_seconds", 6))
        steps = max(1, int(seconds / 0.25))
        for i in range(steps):
            ctx.sleep(seconds / steps)
            ctx.report((i + 1) / steps, f"{label}: {int((i + 1) / steps * 100)}%")
        return {"seconds": seconds}

    return fn


PIPELINES: dict[str, Pipeline] = {
    "ingest_local": Pipeline(
        stages=[*_PREPARE, *_ANALYSE, *_DETECT], on_finish=_ingest_finished, on_retry=_ingest_retried
    ),
    "analyze": Pipeline(
        stages=[*_ANALYSE, *_DETECT], on_finish=_ingest_finished, on_retry=_ingest_retried
    ),
    "detect": Pipeline(stages=list(_DETECT), on_finish=_ingest_finished, on_retry=_ingest_retried),
    # cutting needs no AI, so it is re-run on its own whenever the duration settings change
    "cut": Pipeline(stages=list(_CUT), on_finish=_ingest_finished, on_retry=_ingest_retried),
    # restyling captions re-renders the previews only; the cuts and the music choices stay as they are
    "caption": Pipeline(stages=list(_CAPTION), on_finish=_ingest_finished, on_retry=_ingest_retried),
    # changing the music settings chooses music again and re-renders; the cuts stay as they are
    "remix": Pipeline(stages=[*_MUSIC, *_CAPTION], on_finish=_ingest_finished, on_retry=_ingest_retried),
    "ingest_url": Pipeline(
        stages=[
            StageDef("metadata", "Look up the video", _metadata, max_attempts=3),
            StageDef("download", "Download the video", _download, max_attempts=3),
            *_PREPARE,
            *_ANALYSE,
            *_DETECT,
        ],
        on_finish=_ingest_finished,
        on_retry=_ingest_retried,
    ),
    "selftest": Pipeline(
        stages=[
            StageDef("tools", "Check FFmpeg", _check_tools, max_attempts=1),
            StageDef("render", "Render a captioned test Short", _check_render, max_attempts=1),
            StageDef("storage", "Check storage", _check_storage, max_attempts=1),
            StageDef("wait_a", "Timed stage A (no real work)", _wait_stage("Stage A"), max_attempts=1),
            StageDef("wait_b", "Timed stage B (no real work)", _wait_stage("Stage B"), max_attempts=1),
        ],
    ),
}
