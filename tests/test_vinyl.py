"""Tests for VINYL_ARCHIVE phase A1: naming, and the recorder fed with made-up
audio in place of a sound card."""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vinyl  # noqa: E402

RATE = vinyl.SAMPLE_RATE


def as_24_bit(x):
    """Float audio as the interface delivers it: 24-bit values in the top of an int32."""
    return (np.round(np.asarray(x, np.float64) * (2**23 - 1)).astype(np.int64) << 8).astype(np.int32)


def stereo_tone(seconds, level, freq=440.0):
    t = np.arange(int(seconds * RATE)) / RATE
    x = level * np.sin(2 * np.pi * freq * t)
    return as_24_bit(np.stack([x, x], axis=1))


def feed(recorder, audio, block=vinyl.BLOCK):
    for start in range(0, len(audio), block):
        recorder.feed(audio[start : start + block])
        if recorder.finished:
            break


# File naming -------------------------------------------------------------------


def test_masters_are_filed_by_artist_and_album(tmp_path):
    flac, sidecar = vinyl.master_paths(tmp_path, "Nick Cave", "Push the Sky Away", "a")
    assert flac == tmp_path / "masters" / "Nick Cave" / "Push the Sky Away" / "Nick Cave - Push the Sky Away - Side A.flac"
    assert sidecar == flac.with_suffix(".json")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("AC/DC", "AC-DC"),
        ("What's Going On?", "What's Going On"),
        ("Vol. 4: Live", "Vol. 4 - Live"),
        ('The "Best" <Of>', "The 'Best' (Of)"),
        ("  spaced   out  ", "spaced out"),
        ("R.E.M.", "R.E.M"),  # Windows drops a trailing dot, so the name must not rely on it
        ("CON", "_CON"),
        ("...", "_"),
        ("Björk", "Björk"),
    ],
)
def test_names_windows_refuses_are_changed_as_little_as_possible(text, expected):
    assert vinyl.safe_name(text) == expected


def test_every_name_can_actually_be_created(tmp_path):
    for artist in ["AC/DC", "???", "a" * 300, "NUL", "tab\there", "R.E.M."]:
        flac, _ = vinyl.master_paths(tmp_path, artist, "Album: One", "B")
        flac.parent.mkdir(parents=True, exist_ok=True)
        flac.write_bytes(b"")
        assert flac.exists()


def test_side_labels():
    assert [vinyl.side_label(s) for s in ("a", "Side b", "side C", " 1 ")] == ["A", "B", "C", "1"]
    with pytest.raises(ValueError):
        vinyl.side_label("side ")


# The recorder ------------------------------------------------------------------


def test_the_file_holds_exactly_the_24_bit_samples_it_was_given(tmp_path):
    rng = np.random.default_rng(5)
    audio = as_24_bit(rng.uniform(-0.9, 0.9, (RATE, 2)))
    recorder = vinyl.SideRecorder(tmp_path / "side.flac")
    feed(recorder, audio, block=1000)  # an odd block size: nothing may depend on it
    recorder.close()
    info = sf.info(str(tmp_path / "side.flac"))
    assert (info.format, info.subtype, info.samplerate, info.channels) == ("FLAC", "PCM_24", 48000, 2)
    back, _ = sf.read(str(tmp_path / "side.flac"), dtype="int32")
    assert np.array_equal(back, audio)
    assert recorder.seconds == 1.0


def test_peaks_are_tracked_per_channel(tmp_path):
    audio = stereo_tone(1.0, 0.5)
    audio[:, 1] //= 2
    recorder = vinyl.SideRecorder(tmp_path / "side.flac")
    feed(recorder, audio)
    recorder.close()
    assert abs(vinyl.db(recorder.peak[0]) - -6.02) < 0.01
    assert abs(vinyl.db(recorder.peak[1]) - -12.04) < 0.01
    assert not recorder.clips
    assert "L " in vinyl.meter_line(recorder) and "0:01" in vinyl.meter_line(recorder)


def test_clipping_is_flagged_at_once_with_its_time_and_channel(tmp_path):
    audio = stereo_tone(6.0, 0.5)
    audio[int(2.25 * RATE) : int(2.25 * RATE) + 500, 1] = vinyl.CLIP_LEVEL  # one clipped passage, right channel
    audio[int(5.0 * RATE), 0] = -(2**31)  # and one clipped sample, left, negative
    recorder = vinyl.SideRecorder(tmp_path / "side.flac")
    reported = []
    for start in range(0, len(audio), vinyl.BLOCK):
        recorder.feed(audio[start : start + vinyl.BLOCK])
        reported += [(c["seconds"], c["channel"], recorder.seconds) for c in recorder.new_clips]
        recorder.new_clips.clear()
    recorder.close()
    assert [(s, c) for s, c, _ in reported] == [(2.25, 1), (5.0, 0)]
    # "The moment it happens": known by the end of the 50 ms block it happened in.
    assert all(known_at - at <= vinyl.BLOCK / RATE + 1e-9 for at, _, known_at in reported)
    assert len(recorder.clips) == 2  # 500 clipped samples are one event, not 500


def test_a_loud_but_unclipped_recording_is_not_flagged(tmp_path):
    recorder = vinyl.SideRecorder(tmp_path / "side.flac")
    feed(recorder, stereo_tone(1.0, 0.99))
    recorder.close()
    assert not recorder.clips


def test_it_stops_by_itself_in_the_runout_groove_but_not_before_the_music(tmp_path):
    rng = np.random.default_rng(2)
    groove = lambda seconds: as_24_bit(0.002 * rng.standard_normal((int(seconds * RATE), 2)))  # noqa: E731
    side = np.concatenate([groove(8.0), stereo_tone(12.0, 0.3), groove(3.0), stereo_tone(6.0, 0.3), groove(30.0)])
    recorder = vinyl.SideRecorder(tmp_path / "side.flac", stop_after=5.0)
    feed(recorder, side)
    recorder.close()
    # Not during the 8 s lead-in, not in the 3 s gap between tracks: 5 s into the runout.
    music_ends = 8.0 + 12.0 + 3.0 + 6.0
    assert recorder.finished and music_ends + 5.0 <= recorder.seconds <= music_ends + 6.0

    untouched = vinyl.SideRecorder(tmp_path / "manual.flac")  # the default: never stops by itself
    feed(untouched, side)
    untouched.close()
    assert not untouched.finished and untouched.seconds == len(side) / RATE


def test_a_master_is_never_overwritten(tmp_path):
    vinyl.SideRecorder(tmp_path / "side.flac").close()
    with pytest.raises(OSError, match="File exists"):
        vinyl.SideRecorder(tmp_path / "side.flac")


def test_the_sidecar_records_the_session(tmp_path):
    flac, sidecar = vinyl.master_paths(tmp_path, "AC/DC", "Back in Black", "side b")
    flac.parent.mkdir(parents=True)
    recorder = vinyl.SideRecorder(flac)
    audio = stereo_tone(2.0, 0.5)
    audio[RATE, 0] = vinyl.CLIP_LEVEL
    feed(recorder, audio)
    recorder.close()
    vinyl.write_sidecar(sidecar, recorder, "AC/DC", "Back in Black", "side b", "both at 11 o'clock",
                        "skip in track 3", "MOTU M Series (ASIO)", datetime(2026, 11, 16, 20, 30))
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["artist"] == "AC/DC" and data["side"] == "B"  # the real names, not the file-safe ones
    assert data["file"] == "AC-DC - Back in Black - Side B.flac"
    assert data["recorded"] == "2026-11-16T20:30:00"
    assert data["gain_note"] == "both at 11 o'clock" and data["notes"] == "skip in track 3"
    assert data["seconds"] == 2.0 and data["bit_depth"] == 24
    assert data["clipped"] == [{"seconds": 1.0, "channel": 0}]
    assert data["cleared_for_release"] is False and data["source_type"] == "commercial"
