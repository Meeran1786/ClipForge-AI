"""Speech-to-text running out of memory (found on the owner's PC, 2026-11-17: "Unable to allocate 188 MiB" and
"mkl_malloc: failed to allocate memory"). A stretch is done in halves, the model is always released, and a computer
that really has no memory left gets a plain message instead of a cryptic one."""

from __future__ import annotations

import gc
import weakref
import wave

import numpy as np
import pytest

from clipforge import memory
from clipforge.analysis import transcript as t
from clipforge.analysis.transcript import OutOfMemory, Segment, Word


@pytest.fixture
def audio_file(tmp_path):
    """Four minutes of quiet audio with one very quiet second at 100 s and another at 140 s."""
    rate, seconds = 16000, 240
    x = np.full(rate * seconds, 3000, dtype=np.int16)
    x[100 * rate:101 * rate] = 0
    x[140 * rate:141 * rate] = 50  # a little louder than the one at 100 s
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return str(path)


LOUDNESS = [-20.0] * 480  # 0.5 s hop over 240 s, with the quiet places below
LOUDNESS[200] = -90.0  # 100 s
LOUDNESS[280] = -60.0  # 140 s


class Limited:
    """A transcriber that cannot cope with more than `limit` seconds at once."""

    name = "limited"

    def __init__(self, limit, error=MemoryError("Unable to allocate 188. MiB for an array")):
        self.limit, self.error, self.calls, self.closed = limit, error, [], False

    def transcribe(self, audio, language, on_progress=None):
        seconds = len(audio) / 16000
        self.calls.append(round(seconds, 1))
        if seconds > self.limit:
            raise self.error
        if on_progress:
            on_progress(1.0)
        return [Segment(0.5, 1.5, "hello", [Word(0.5, 1.5, "hello")])], language or "en"


def run(transcriber, audio_file, a=0.0, b=240.0, **kw):
    progress, splits = [], []
    rows, heard = t.transcribe_span(transcriber, audio_file, a, b, None, progress.append, 240.0, LOUDNESS, 0.5,
                                    lambda *s: splits.append(s), **kw)
    return rows, heard, progress, splits


# ---- what counts as running out of memory -----------------------------------------------------------


@pytest.mark.parametrize("error", [
    MemoryError(),
    np._core._exceptions._ArrayMemoryError((1, 61581, 400), np.dtype("float64")),
    RuntimeError("mkl_malloc: failed to allocate memory"),
    RuntimeError("CUDA out of memory"),
])
def test_memory_failures_are_recognised(error):
    assert t.is_memory_error(error)


@pytest.mark.parametrize("error", [RuntimeError("speech engine crashed"), ValueError("bad audio"), KeyError("x")])
def test_other_failures_are_not_mistaken_for_memory(error):
    assert not t.is_memory_error(error)


# ---- doing a stretch in halves ----------------------------------------------------------------------


def test_a_stretch_that_fits_is_done_in_one_go(audio_file):
    ai = Limited(limit=300)
    rows, heard, _progress, splits = run(ai, audio_file)
    assert ai.calls == [240.0] and splits == [] and heard == "en" and len(rows) == 1


def test_when_memory_runs_out_the_stretch_is_split_at_the_quietest_point_near_the_middle(audio_file):
    ai = Limited(limit=145)
    rows, _heard, progress, splits = run(ai, audio_file)
    assert len(ai.calls) == 3 and ai.calls[0] == 240.0, "the whole, then the two halves"
    assert 99.5 < ai.calls[1] < 101.5 and ai.calls[1] + ai.calls[2] == pytest.approx(240.0, abs=0.2), \
        "split at the quiet second near 100 s, the quietest place near the middle"
    assert splits[0][0] == 0.0 and splits[0][1] == 240.0 and 99.5 < splits[0][2] < 101.5
    assert rows[0]["start"] == 0.5 and rows[1]["start"] == pytest.approx(ai.calls[1] + 0.5, abs=0.1), \
        "each half's lines are on the whole video's clock (the second half starts where the first ends)"
    assert max(progress) <= 1.0 and progress == sorted(progress), "progress never goes backwards"


def test_it_keeps_halving_until_the_pieces_fit_and_the_language_carries_on(audio_file):
    ai = Limited(limit=70)
    rows, heard, _p, splits = run(ai, audio_file)
    assert max(ai.calls[1:]) <= 140 and len(rows) >= 4 and len(splits) >= 3 and heard == "en"
    starts = [r["start"] for r in rows]
    assert starts == sorted(starts), "lines stay in order"


def test_an_engine_that_cannot_do_even_a_small_piece_says_the_computer_is_out_of_memory(audio_file):
    ai = Limited(limit=5, error=RuntimeError("mkl_malloc: failed to allocate memory"))
    with pytest.raises(OutOfMemory) as caught:
        run(ai, audio_file)
    assert "not enough free memory" in str(caught.value) and "Retry" in str(caught.value)
    assert caught.value.no_retry, "retrying at once cannot help"
    assert min(ai.calls) >= t.MIN_SPAN / 1.0 - 1, "it stopped splitting at the smallest piece"


def test_a_failure_that_is_not_about_memory_is_not_hidden_or_split(audio_file):
    ai = Limited(limit=5, error=RuntimeError("speech engine crashed"))
    with pytest.raises(RuntimeError, match="speech engine crashed"):
        run(ai, audio_file)
    assert ai.calls == [240.0]


def test_without_loudness_the_cut_falls_in_the_middle():
    assert t.split_point(0.0, 100.0, [], 0.5) == 50.0
    assert 49 <= t.split_point(0.0, 100.0, [-20.0] * 200, 0.5) <= 51


# ---- giving the model's memory back ----------------------------------------------------------------


def test_closing_releases_the_model_and_a_closed_one_cannot_be_used():
    class Model:
        pass

    transcriber = object.__new__(t.WhisperTranscriber)
    model = Model()
    transcriber._model = model
    ref = weakref.ref(model)
    del model
    assert ref() is not None
    transcriber.close()
    gc.collect()
    assert ref() is None, "nothing keeps the model alive after close()"
    with pytest.raises(RuntimeError, match="released"):
        transcriber.transcribe(np.zeros(16000, np.float32), None)
    transcriber.close()  # closing twice is fine


def test_a_model_that_cannot_be_loaded_for_lack_of_memory_says_so(monkeypatch, settings):
    import faster_whisper

    def no_memory(*_a, **_k):
        raise RuntimeError("mkl_malloc: failed to allocate memory")

    monkeypatch.setattr(faster_whisper, "WhisperModel", no_memory)
    with pytest.raises(OutOfMemory, match="the speech model"):
        t.WhisperTranscriber(settings)


def test_the_message_says_how_much_memory_is_left_and_what_to_do(monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: (1300, 200))
    monkeypatch.setattr(memory, "system_drive_free_gb", lambda: 0.17)
    text = memory.low_memory_message("speech-to-text")
    assert "only 200 MB left" in text and "free RAM 1,300 MB" in text and "Close other programs" in text
    assert "Drive C: has only 0.2 GB free" in text
    monkeypatch.setattr(memory, "system_drive_free_gb", lambda: 40.0)
    assert "Drive C:" not in memory.low_memory_message("x")


def test_reading_the_real_memory_figures_works_here():
    left = memory.available_mb()
    assert left is None or (left[0] > 0 and left[1] > 0)


# ---- through the app --------------------------------------------------------------------------------


def test_a_video_whose_chunk_runs_out_of_memory_is_still_transcribed_and_the_engine_is_released(
    settings, fake_stt, sample_video, monkeypatch
):
    from fastapi.testclient import TestClient

    from clipforge.app import create_app

    from .test_moments import wait_status

    monkeypatch.setattr(t, "MIN_SPAN", 0.4)  # the test video is only two seconds long
    fake_stt.memory_limit_seconds = 1.5  # the whole 2 s clip "does not fit"; halves do
    with TestClient(create_app(settings, configure_logging=False)) as client:
        project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
        ready = wait_status(client, project["id"], "ready")
        assert ready["analysis"]["transcript"]["done"] and ready["analysis"]["transcript"]["lines"] >= 2
        assert fake_stt.closed, "the speech engine was released when the stage ended"
        assert max(fake_stt.calls[1:]) <= 1.5 < fake_stt.calls[0]
