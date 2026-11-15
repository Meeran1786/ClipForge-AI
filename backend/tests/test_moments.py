"""Phase 4: moment detection, viral score, selection, AI provider plumbing."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from clipforge import moments as m
from clipforge.ai import AIError, AIUnavailable
from clipforge.ai import providers
from clipforge.ai.base import extract_json
from clipforge.ai.providers import AgyReasoner, parse_agy_output
from clipforge.app import create_app
from clipforge.moments import SUBSCORES, Candidate, Line

from .conftest import wait_for

ALL = m.CATEGORIES


def scores(value: int = 5, **overrides: int) -> dict[str, int]:
    return {**{k: value for k in SUBSCORES}, **overrides}


def lines_every(seconds: float, count: int, length: float = 3.0) -> list[Line]:
    return [Line(i + 1, i * seconds, i * seconds + length, f"line {i + 1}") for i in range(count)]


def cand(start: float, end: float, score: int, category: str = "funny", **kw) -> Candidate:
    return Candidate(start=start, end=end, peak=(start + end) / 2, category=category,
                     subscores=scores(), reason="", score=score, **kw)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def wait_status(client, project_id: int, status: str) -> dict:
    return wait_for(
        lambda: (p := client.get(f"/api/projects/{project_id}").json())["status"] == status and p,
        timeout=90,
    )


def wait_job_done(client, project_id: int) -> dict:
    return wait_for(
        lambda: (j := client.get(f"/api/jobs?project_id={project_id}").json()[0])["status"]
        in ("done", "failed", "cancelled") and j,
        timeout=90,
    )


# ---- the score ---------------------------------------------------------------


def test_default_weights_cover_every_part_and_sum_to_one():
    assert set(m.DEFAULT_WEIGHTS) == set(SUBSCORES)
    assert sum(m.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_viral_score_is_the_weighted_sum_scaled_to_100():
    w = m.clean_weights(None)
    assert m.viral_score(scores(10), w) == 100
    assert m.viral_score(scores(0), w) == 0
    assert m.viral_score(scores(5), w) == 50
    # only surprise is high: its weight is 0.18, so 10 there and 0 elsewhere is 18
    assert m.viral_score(scores(0, surprise=10), w) == 18


def test_weights_are_normalised_and_bad_values_fall_back():
    w = m.clean_weights({"entertainment": 2, "surprise": 2, "emotion": "x", "retention": -5})
    assert sum(w.values()) == pytest.approx(1.0, abs=0.001)
    assert w["entertainment"] == w["surprise"] > w["emotion"]
    assert w["retention"] == 0
    assert m.clean_weights({k: 0 for k in SUBSCORES}) == m.DEFAULT_WEIGHTS


def test_scores_from_the_model_are_clamped():
    cleaned = m.clean_subscores({"entertainment": 14, "emotion": -3, "surprise": "7", "retention": None})
    assert cleaned["entertainment"] == 10 and cleaned["emotion"] == 0 and cleaned["surprise"] == 7
    assert cleaned["retention"] == 0 and cleaned["standalone"] == 0
    assert set(cleaned) == set(SUBSCORES)


# ---- what the model is shown ---------------------------------------------------


def test_transcript_is_split_into_overlapping_stretches():
    lines = lines_every(30, 100)  # 50 minutes
    chunks = m.chunk_lines(lines)
    assert len(chunks) == 3
    assert chunks[0][0].n == 1 and chunks[-1][-1].n == 100
    for a, b in zip(chunks, chunks[1:]):
        assert b[0].n <= a[-1].n, "stretches overlap so a moment on the edge is seen whole"
        assert a[-1].start - b[0].start <= m.CHUNK_OVERLAP + 30
    assert m.chunk_lines([]) == []


def test_pauses_and_loud_moments_are_marked_for_the_model():
    lines = [Line(1, 0, 3, "we are loading in"), Line(2, 10, 12, "what was that"), Line(3, 12.5, 14, "run")]
    text = m.annotate(lines, [{"t": 10.5, "kind": "surge_after_silence", "rise_db": 20}])
    rows = text.splitlines()
    assert rows[0] == "L1 [0:00] we are loading in"
    assert rows[1].strip() == "(7 second pause)"
    assert rows[2] == "L2 [0:10] what was that   <sudden loud moment after silence>"
    assert rows[3] == "L3 [0:12] run"


def test_the_task_states_the_rules_that_matter():
    task = m.text_task("L1 [0:00] hi", ["funny", "rage"], 20, 60, 6)
    assert "funny, rage" in task and "20 to 60 seconds" in task
    assert "Never describe something the lines do not show" in task
    assert "return an empty list" in task
    assert all(k in task for k in SUBSCORES)


# ---- reading the model's answer --------------------------------------------------


def test_moments_are_built_from_line_numbers():
    chunk = lines_every(10, 12)  # L1 at 0 s ... L12 at 110 s, each 3 s long
    answer = {"moments": [{
        "start_line": 3, "end_line": 6, "peak_line": 5, "category": "shocking",
        "scores": scores(8), "reason": "He says 'run' and the building falls.",
    }]}
    [c] = m.parse_text_moments(answer, chunk, ALL, 20, 60, 600)
    assert (c.start, c.end, c.peak) == (20.0, 53.0, 40.0)
    assert c.category == "shocking" and c.subscores["surprise"] == 8
    assert c.excerpt == "line 3 line 4 line 5 line 6"
    assert c.source == "ai_text" and not c.visual_checked


def test_answers_that_point_outside_the_transcript_are_dropped_not_guessed():
    chunk = lines_every(10, 5)
    answer = {"moments": [
        {"start_line": 2, "end_line": 99, "peak_line": 2, "category": "funny", "scores": scores(), "reason": ""},
        {"start_line": 4, "end_line": 2, "peak_line": 3, "category": "funny", "scores": scores(), "reason": ""},
        {"start_line": 1, "end_line": 3, "peak_line": 2, "category": "boring", "scores": scores(), "reason": ""},
        "not even an object",
        {"start_line": 1, "end_line": 4, "peak_line": 2, "category": "funny", "scores": scores(), "reason": "ok"},
    ]}
    found = m.parse_text_moments(answer, chunk, ALL, 20, 60, 600)
    assert [c.reason for c in found] == ["ok"]


def test_a_category_the_project_did_not_ask_for_is_not_returned():
    chunk = lines_every(10, 5)
    answer = {"moments": [{"start_line": 1, "end_line": 4, "peak_line": 2, "category": "horror",
                           "scores": scores(), "reason": ""}]}
    assert m.parse_text_moments(answer, chunk, ["funny"], 20, 60, 600) == []


@pytest.mark.parametrize(
    ("start", "end", "peak", "expected"),
    [
        (100, 140, 120, (100, 140)),  # already inside 20-60 s: untouched
        (100, 110, 105, (94, 114)),  # too short: grows, more before than after
        (2, 8, 5, (0, 20)),  # too short at the very start of the video
        (100, 220, 200, (160, 220)),  # too long, key moment near the end: keep the end
        (100, 220, 110, (100, 160)),  # too long, key moment near the start: keep the start
        (590, 598, 595, (580, 600)),  # too short at the very end of a 600 s video
    ],
)
def test_rough_length_fit_keeps_the_key_moment(start, end, peak, expected):
    got = m.fit_length(start, end, peak, 20, 60, 600)
    assert got == pytest.approx(expected)
    assert got[0] <= peak <= got[1]


# ---- ranking -----------------------------------------------------------------


def test_overlapping_moments_keep_only_the_better_one():
    kept = m.drop_duplicates([cand(0, 40, 60), cand(10, 50, 80), cand(100, 130, 50), cand(128, 160, 70)])
    assert [(c.start, c.score) for c in kept] == [(10, 80), (128, 70), (100, 50)]


def test_selection_follows_the_project_settings():
    pool = [cand(0, 30, 90, "funny"), cand(100, 130, 85, "horror"), cand(200, 230, 70, "funny"),
            cand(300, 330, 65, "rage"), cand(400, 430, 40, "funny")]
    ranked = m.select(pool, {"categories": ["funny", "rage"], "min_score": 60, "max_clips": 2})
    assert [c.rank for c in ranked] == [1, 2, 3, 4, 5]
    assert [c.score for c in ranked] == [90, 85, 70, 65, 40]
    # horror is not wanted; only two clips are allowed; 40 is under the minimum
    assert [c.selected for c in ranked] == [True, False, True, False, False]


def test_loud_moments_already_covered_are_not_added_again():
    events = [{"t": 25.0, "kind": "surge", "rise_db": 12}, {"t": 300.0, "kind": "surge", "rise_db": 20},
              {"t": 304.0, "kind": "surge", "rise_db": 11}]
    extra = m.signal_candidates(events, [cand(10, 40, 80)], [], 20, 60, 600)
    assert len(extra) == 1, "25 s is inside an existing moment; 304 s is the same event as 300 s"
    assert extra[0].peak == 300.0 and extra[0].source == "signals"
    assert extra[0].start < 300 < extra[0].end and extra[0].length == pytest.approx(40)


# ---- the whole detection, with a stand-in model ----------------------------------


def run_detection(settings, fake_ai, sample_video, tmp_path, **kw):
    progress: list[tuple[float, str]] = []
    args = dict(
        lines=lines_every(0.4, 5, 0.3), events=[], duration=2.0, proxy=sample_video,
        options={"categories": ALL, "min_score": 0, "max_clips": 8, "min_duration": 1, "max_duration": 2},
        weights=m.clean_weights(None), work_dir=tmp_path / "sheets",
        report=lambda p, msg: progress.append((p, msg)),
    )
    args.update(kw)
    ranked, notes = m.detect_moments(fake_ai, settings, **args)
    return ranked, notes, progress


def test_detection_reads_the_words_then_checks_the_picture(settings, fake_ai, sample_video, tmp_path):
    ranked, notes, progress = run_detection(settings, fake_ai, sample_video, tmp_path)

    assert len(fake_ai.text_tasks) == 1 and len(fake_ai.visual_tasks) == 1
    assert fake_ai.images_seen == [["moment_1_1.jpg"]]
    assert "Image file: moment_1_1.jpg" in fake_ai.visual_tasks[0]
    assert "Describe only what is visible in the frames" in fake_ai.visual_tasks[0]

    [moment] = ranked
    assert moment.visual_checked and moment.visual == "A person reacts at a desk."
    assert moment.subscores == scores(6), "the picture check's scores replace the words-only ones"
    assert moment.score == 60 and moment.rank == 1 and moment.selected
    assert notes == {"text_calls": 1, "visual_calls": 1, "visual_failed": 0, "provider": "fake-ai",
                     "from_words": 1, "from_sound": 0, "from_sound_kept": 0, "picture_added_nothing": 0,
                     "found": 1, "selected": 1, "visually_checked": 1}
    assert not list((tmp_path / "sheets").glob("*.jpg")), "frame sheets are removed after use"
    assert all(0 <= p <= 1 for p, _ in progress)


def test_a_failed_picture_check_keeps_the_words_only_scores(settings, fake_ai, sample_video, tmp_path):
    fake_ai.fail_visual = AIError("no capacity")
    ranked, notes, _ = run_detection(settings, fake_ai, sample_video, tmp_path)
    [moment] = ranked
    assert not moment.visual_checked and moment.visual is None
    assert moment.subscores == scores(7) and moment.score == 70
    assert notes["visual_failed"] == 1 and notes["visually_checked"] == 0


def test_a_picture_that_adds_nothing_never_removes_or_marks_down_a_moment_from_the_words(
    settings, fake_ai, sample_video, tmp_path
):
    """Found in live testing: a test-pattern picture made the check throw away every moment."""
    fake_ai.visual_answer = lambda ids: {"moments": [
        {"id": i, "seen": "A colour test pattern.", "picture_useful": False, "keep": False,
         "category": "horror", "scores": scores(1)}
        for i in ids
    ]}
    ranked, notes, _ = run_detection(settings, fake_ai, sample_video, tmp_path)
    [moment] = ranked
    assert moment.subscores == scores(7) and moment.score == 70, "the words-only scores stand"
    assert moment.category == "funny"
    assert moment.visual == "A colour test pattern." and moment.visual_checked
    assert notes["picture_added_nothing"] == 1 and notes["found"] == 1


def test_a_useful_picture_can_lower_a_moment_from_the_words_but_not_delete_it(settings, fake_ai, sample_video, tmp_path):
    fake_ai.visual_answer = lambda ids: {"moments": [
        {"id": i, "seen": "He is reading a menu, not reacting.", "picture_useful": True, "keep": False,
         "category": "funny", "scores": scores(3)}
        for i in ids
    ]}
    ranked, _, _ = run_detection(settings, fake_ai, sample_video, tmp_path)
    [moment] = ranked
    assert moment.score == 30


def test_a_loud_moment_with_no_speech_is_kept_only_if_the_picture_backs_it(settings, fake_ai, sample_video, tmp_path):
    fake_ai.text_answer = lambda _numbers: {"moments": []}
    events = [{"t": 1.0, "kind": "surge_after_silence", "rise_db": 25}]

    def visual(seen: str, useful: bool, keep: bool, value: int):
        return lambda ids: {"moments": [
            {"id": i, "seen": seen, "picture_useful": useful, "keep": keep, "category": "horror",
             "scores": scores(value)}
            for i in ids
        ]}

    fake_ai.visual_answer = visual("A figure jumps out of the dark.", True, True, 8)
    ranked, notes, _ = run_detection(settings, fake_ai, sample_video, tmp_path, events=events)
    [moment] = ranked
    assert moment.source == "signals" and moment.category == "horror" and moment.score == 80
    assert notes["from_sound"] == 1 and notes["from_sound_kept"] == 1

    fake_ai.visual_answer = visual("A test pattern.", False, True, 8)
    ranked, notes, _ = run_detection(settings, fake_ai, sample_video, tmp_path, events=events)
    assert ranked == [] and notes["from_sound_kept"] == 0, "a picture that shows nothing cannot vouch for it"

    fake_ai.visual_answer = visual("A door slams, nobody reacts.", True, False, 3)
    ranked, _, _ = run_detection(settings, fake_ai, sample_video, tmp_path, events=events)
    assert ranked == []

    fake_ai.fail_visual = AIError("no capacity")
    ranked, _, _ = run_detection(settings, fake_ai, sample_video, tmp_path, events=events)
    assert ranked == [], "with no words and no look at the picture there is nothing to vouch for it"


def test_no_proxy_means_words_only(settings, fake_ai, tmp_path, sample_video):
    ranked, notes, _ = run_detection(settings, fake_ai, sample_video, tmp_path, proxy=None)
    assert fake_ai.visual_tasks == [] and notes["visual_calls"] == 0
    assert ranked[0].score == 70 and not ranked[0].visual_checked


# ---- through the app -------------------------------------------------------------


def test_a_new_project_ends_with_scored_moments(client, sample_video, fake_ai):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    ready = wait_status(client, project["id"], "ready")

    assert ready["status_detail"] is None
    assert ready["analysis"]["moments"]["found"] == 1 and ready["analysis"]["moments"]["provider"] == "fake-ai"
    [moment] = client.get(f"/api/projects/{project['id']}/moments").json()
    assert moment["rank"] == 1 and moment["selected"] and moment["visual_checked"]
    assert moment["score"] == 60 and moment["category"] == "funny"
    assert set(moment["subscores"]) == set(SUBSCORES)
    assert moment["reason"] == "They say hello to chat."
    assert "L1 [0:00] hello chat" in fake_ai.text_tasks[0]


def test_changing_project_settings_reranks_without_asking_the_ai(client, sample_video, fake_ai):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    calls = len(fake_ai.text_tasks) + len(fake_ai.visual_tasks)

    r = client.patch(f"/api/projects/{project['id']}/options", json={"min_score": 90})
    assert r.status_code == 200 and r.json()["analysis"]["moments"]["selected"] == 0
    assert client.get(f"/api/projects/{project['id']}/moments").json()[0]["selected"] is False

    r = client.patch(f"/api/projects/{project['id']}/options", json={"min_score": 50, "categories": ["funny"]})
    assert r.json()["analysis"]["moments"]["selected"] == 1
    assert len(fake_ai.text_tasks) + len(fake_ai.visual_tasks) == calls


def test_changing_weights_rescores_stored_moments(client, sample_video, fake_ai):
    fake_ai.visual_answer = lambda ids: {"moments": [
        {"id": i, "seen": "x", "picture_useful": True, "keep": True, "category": "funny",
         "scores": scores(2, surprise=10)}
        for i in ids
    ]}
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    before = client.get(f"/api/projects/{project['id']}/moments").json()[0]["score"]
    assert before == 34  # 2 everywhere (20) plus 8 extra points of surprise at weight 0.18

    current = client.get("/api/settings/weights").json()
    assert current["weights"] == current["defaults"] and "surprise" in current["meaning"]

    saved = client.put("/api/settings/weights", json={"surprise": 1})  # only surprise counts
    assert saved.status_code == 200 and saved.json()["weights"]["surprise"] == 1.0
    assert client.get(f"/api/projects/{project['id']}/moments").json()[0]["score"] == 100

    assert client.put("/api/settings/weights", json={"nonsense": 1}).status_code == 422
    assert client.put("/api/settings/weights", json={"surprise": 0}).status_code == 422


def test_moments_can_be_picked_again(client, sample_video, fake_ai):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    assert client.post(f"/api/projects/{project['id']}/detect").status_code == 200
    wait_job_done(client, project["id"])
    assert len(fake_ai.text_tasks) == 2
    assert len(client.get(f"/api/projects/{project['id']}/moments").json()) == 1, "old picks are replaced"


def test_detect_is_refused_before_analysis(client):
    project = client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x"}).json()
    r = client.post(f"/api/projects/{project['id']}/detect")
    assert r.status_code == 409 and "not been analysed" in r.json()["error"]


def test_without_an_ai_provider_the_video_is_still_ready(client, sample_video, monkeypatch, fake_ai):
    from clipforge import pipelines

    def none_available(_settings):
        raise AIUnavailable("The Antigravity command-line tool was not found.")

    monkeypatch.setattr(pipelines, "make_reasoner", none_available)
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    job = wait_job_done(client, project["id"])
    assert job["status"] == "failed"
    stage = next(s for s in job["stages"] if s["name"] == "moments")
    assert stage["status"] == "failed" and stage["attempts"] == 1
    assert job["stages"][-1]["name"] == "clips" and job["stages"][-1]["status"] == "pending"

    after = client.get(f"/api/projects/{project['id']}").json()
    assert after["status"] == "ready", "the video, timeline and transcript are usable"
    assert "Moments were not picked" in after["status_detail"] and "Antigravity" in after["status_detail"]
    assert after["analysis"]["transcript"]["done"]
    assert client.get(f"/api/projects/{project['id']}/moments").json() == []

    # once a provider exists, Retry finishes the job
    monkeypatch.setattr(pipelines, "make_reasoner", lambda _settings: fake_ai)
    client.post(f"/api/jobs/{job['id']}/retry")
    assert wait_job_done(client, project["id"])["status"] == "done"
    done = client.get(f"/api/projects/{project['id']}").json()
    assert done["status"] == "ready" and done["status_detail"] is None
    assert len(client.get(f"/api/projects/{project['id']}/moments").json()) == 1


def test_an_ai_hiccup_is_retried_once(client, sample_video, fake_ai):
    calls = {"n": 0}
    original = fake_ai.ask_json

    def flaky(task, schema, images=None, check=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AIError("No capacity available")
        return original(task, schema, images=images, check=check)

    fake_ai.ask_json = flaky
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    ready = wait_status(client, project["id"], "ready")
    assert ready["analysis"]["moments"]["found"] == 1
    stages = client.get(f"/api/jobs?project_id={project['id']}").json()[0]["stages"]
    assert next(s for s in stages if s["name"] == "moments")["attempts"] == 2


# ---- the AI plumbing -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here you go:\n```\n{"a": {"b": [1, 2]}}\n```\nDone.', {"a": {"b": [1, 2]}}),
        ('```json\n{"line": 3}\n```{"moments": []}', {"line": 3}),
        ("no json here", None),
        ("[1, 2, 3]", None),
    ],
)
def test_json_is_found_in_a_chatty_reply(text, expected):
    assert extract_json(text) == expected


def test_the_tools_structured_answer_is_preferred():
    out = json.dumps({"status": "SUCCESS", "response": "```json\n{\"wrong\": 1}\n```",
                      "structured_output": {"moments": [{"line": 3}]}, "usage": {"input_tokens": 28000}})
    assert parse_agy_output(out, 0) == {"moments": [{"line": 3}]}


def test_a_reply_without_a_structured_answer_falls_back_to_the_text():
    out = json.dumps({"status": "SUCCESS", "response": "```json\n{\"moments\": []}\n```"})
    assert parse_agy_output(out, 0) == {"moments": []}


@pytest.mark.parametrize(
    ("stdout", "needle"),
    [
        (json.dumps({"status": "ERROR", "error": "UNAVAILABLE (code 503): No capacity available"}), "No capacity"),
        (json.dumps({"status": "SUCCESS", "response": ""}), "no answer"),
        ("", "unreadable"),
        ("Traceback: boom", "unreadable"),
    ],
)
def test_failed_replies_are_reported_as_errors(stdout, needle):
    with pytest.raises(AIError, match=needle):
        parse_agy_output(stdout, 3)


def test_the_next_model_is_tried_when_one_fails_and_the_working_one_is_remembered(settings, tmp_path, monkeypatch):
    exe = tmp_path / "agy.exe"
    exe.write_bytes(b"")
    settings.agy_path = str(exe)
    settings.agy_models = "model-a, model-b"
    used: list[str] = []
    seen_files: list[set[str]] = []

    def fake_run(cmd, cwd, timeout, check):
        model = cmd[cmd.index("--model") + 1]
        used.append(model)
        seen_files.append({p.name for p in cwd.iterdir()})
        assert "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "plan"
        if model == "model-a":
            return 3, json.dumps({"status": "ERROR", "error": "No capacity available"})
        return 0, json.dumps({"status": "SUCCESS", "structured_output": {"ok": True}})

    monkeypatch.setattr(providers, "run_cli", fake_run)
    image = tmp_path / "moment_1_1.jpg"
    image.write_bytes(b"\xff\xd8fake")

    reasoner = AgyReasoner(settings)
    assert reasoner.ask_json("the task", {"type": "object"}, images=[image]) == {"ok": True}
    assert used == ["model-a", "model-b"] and reasoner.name == "agy:model-b"
    assert seen_files[0] == {"task.md", "schema.json", "moment_1_1.jpg"}

    assert reasoner.ask_json("again", {"type": "object"}) == {"ok": True}
    assert used == ["model-a", "model-b", "model-b"], "the model that worked is tried first next time"
    assert not list(settings.tmp_dir.glob("ai_*")), "each request's folder is removed afterwards"


def test_all_models_failing_is_one_clear_error(settings, tmp_path, monkeypatch):
    exe = tmp_path / "agy.exe"
    exe.write_bytes(b"")
    settings.agy_path = str(exe)
    settings.agy_models = "model-a,model-b"
    monkeypatch.setattr(providers, "run_cli", lambda *a, **k: (3, json.dumps({"status": "ERROR", "error": "503"})))
    with pytest.raises(AIError, match="No AI model answered.*model-a.*model-b"):
        AgyReasoner(settings).ask_json("t", {})


def test_a_missing_tool_is_reported_as_unavailable(settings, tmp_path, monkeypatch):
    settings.agy_path = str(tmp_path / "missing.exe")
    monkeypatch.setattr(providers.shutil, "which", lambda _name: None)
    with pytest.raises(AIUnavailable, match="not found"):
        AgyReasoner(settings)


def test_the_api_route_needs_a_key(settings, monkeypatch):
    settings.gemini_route = "api"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(AIUnavailable, match="GEMINI_API_KEY"):
        providers.make_reasoner(settings)
