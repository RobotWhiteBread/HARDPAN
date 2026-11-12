"""Tests for SILT phase S1: ingest conversion and the library.

Run from the project folder:   .venv\\Scripts\\python -m pytest tests
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import silt  # noqa: E402

SR = 48000


def tone(freq, rate, seconds, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def frequency(x, rate):
    """From interpolated rising zero crossings over the whole signal."""
    x = x.astype(np.float64)
    i = np.flatnonzero((x[:-1] < 0) & (x[1:] >= 0))
    t = i + (-x[i] / (x[i + 1] - x[i]))
    return rate * (len(t) - 1) / (t[-1] - t[0])


def rms(x):
    return math.sqrt(float(np.mean(x.astype(np.float64) ** 2)))


def level_db_at(x, rate, freq, reference_freq):
    """Level at `freq` relative to the level at `reference_freq`, in dB."""
    window = np.blackman(len(x))
    spectrum = np.abs(np.fft.rfft(x.astype(np.float64) * window))
    bins = np.fft.rfftfreq(len(x), 1 / rate)

    def peak(f):
        near = np.abs(bins - f) <= 5 * rate / len(x)
        return spectrum[near].max()

    return 20 * math.log10(peak(freq) / peak(reference_freq))


# Conversion ------------------------------------------------------------------


def test_conversion_keeps_pitch_level_and_length():
    stereo = np.stack([tone(1000, 44100, 2.0), tone(5000, 44100, 2.0)], axis=1)
    out = silt.resample(stereo, 44100, SR)
    assert out.dtype == np.float32 and out.shape[1] == 2
    assert abs(len(out) - round(len(stereo) * SR / 44100)) <= 1
    middle = out[SR // 4 : -SR // 4]  # away from the filter's edges
    for channel, f in ((0, 1000.0), (1, 5000.0)):
        assert abs(frequency(middle[:, channel], SR) - f) < 0.01
        assert abs(rms(middle[:, channel]) / (0.5 / math.sqrt(2)) - 1) < 1e-4  # within 0.001 dB


def test_going_down_in_rate_removes_what_would_alias():
    # 30 kHz is fine at 96 kHz but above 48 kHz's Nyquist; kept, it would fold to 18 kHz.
    x = (tone(1000, 96000, 1.0) + tone(30000, 96000, 1.0))[:, None]
    out = silt.resample(x, 96000, SR)[:, 0]
    assert level_db_at(out, SR, 18000, 1000) < -90


def test_going_up_in_rate_removes_the_images():
    # Upsampling 22.05 kHz audio leaves a mirror image of a 5 kHz tone at 17.05 kHz
    # unless the filter removes it.
    x = tone(5000, 22050, 1.0)[:, None]
    out = silt.resample(x, 22050, SR)[:, 0]
    assert level_db_at(out, SR, 17050, 5000) < -90
    assert abs(frequency(out[SR // 4 : -SR // 4], SR) - 5000) < 0.01


def test_same_rate_is_a_copy():
    x = tone(440, SR, 0.1)[:, None]
    out = silt.resample(x, SR, SR)
    assert np.array_equal(out, x) and out is not x


def test_more_than_two_channels_fold_to_stereo():
    quad = np.stack([np.full(10, v, np.float32) for v in (0.1, 0.2, 0.3, 0.4)], axis=1)
    folded = silt.to_internal(quad)
    assert folded.shape == (10, 2)
    assert np.allclose(folded[0], [0.2, 0.3])


# Ingest ----------------------------------------------------------------------


@pytest.fixture
def library(tmp_path):
    return tmp_path / "library"


def write(path, audio, rate, **kw):
    sf.write(str(path), audio, rate, **kw)
    return path


def test_ingest_converts_and_keeps_the_original(tmp_path, library):
    source = write(tmp_path / "Field Drone.wav", np.stack([tone(1000, 44100, 1.0)] * 2, axis=1), 44100, subtype="PCM_16")
    item = silt.ingest_file(source, library, "own", artist="me")
    folder = library / item["id"]
    assert (folder / "original.wav").read_bytes() == source.read_bytes()
    info = sf.info(str(folder / "audio.wav"))
    assert (info.samplerate, info.channels, info.subtype) == (48000, 2, "FLOAT")
    assert abs(info.frames - round(44100 * 48000 / 44100)) <= 1
    assert item["title"] == "Field Drone"
    assert item["original"]["sample_rate"] == 44100
    assert item["original"]["sha256"] == silt.sha256(source)


@pytest.mark.parametrize(
    "suffix, kwargs, tolerance_db",
    [
        (".flac", {"format": "FLAC"}, 0.01),
        (".ogg", {"format": "OGG", "subtype": "VORBIS"}, 0.5),
        (".mp3", {"format": "MP3", "subtype": "MPEG_LAYER_III"}, 0.5),
    ],
)
def test_each_format_converts(tmp_path, library, suffix, kwargs, tolerance_db):
    path = tmp_path / f"tone{suffix}"
    try:
        write(path, tone(1000, 44100, 2.0)[:, None], 44100, **kwargs)
    except (sf.LibsndfileError, RuntimeError, TypeError) as error:
        pytest.skip(f"this libsndfile cannot write {suffix} test files: {error}")
    item = silt.ingest_file(path, library, "own")
    audio, rate = sf.read(str(library / item["id"] / "audio.wav"), dtype="float32")
    assert rate == 48000
    middle = audio[SR // 2 : -SR // 2]
    assert abs(frequency(middle, SR) - 1000) < 0.05
    assert abs(20 * math.log10(rms(middle) / (0.5 / math.sqrt(2)))) < tolerance_db


def test_mono_stays_mono(tmp_path, library):
    item = silt.ingest_file(write(tmp_path / "m.wav", tone(220, 32000, 0.5)[:, None], 32000), library, "own")
    assert item["audio"]["channels"] == 1


def test_cleared_for_release_defaults_by_source(tmp_path, library):
    expected = {"own": True, "ai": False, "commercial": False, "pack": True}
    for k, (source, cleared) in enumerate(expected.items()):
        path = write(tmp_path / f"{source}.wav", tone(300 + 50 * k, 44100, 0.2)[:, None], 44100)
        item = silt.ingest_file(path, library, source, artist="someone", prompt="slow dark drone")
        assert item["cleared_for_release"] is cleared, source
        assert item["prompt"] == ("slow dark drone" if source == "ai" else None)
    override = silt.ingest_file(write(tmp_path / "x.wav", tone(900, 44100, 0.2)[:, None], 44100),
                                library, "own", cleared=False)
    assert override["cleared_for_release"] is False


def test_the_same_file_is_not_imported_twice(tmp_path, library):
    path = write(tmp_path / "a.wav", tone(440, 44100, 0.2)[:, None], 44100)
    assert silt.ingest_file(path, library, "own") is not None
    assert silt.ingest_file(path, library, "own") is None
    assert len(silt.load_items(library)) == 1


def test_folders_are_searched_recursively_for_audio_only(tmp_path):
    (tmp_path / "set" / "sub").mkdir(parents=True)
    write(tmp_path / "set" / "a.wav", tone(440, 44100, 0.1)[:, None], 44100)
    write(tmp_path / "set" / "sub" / "b.flac", tone(550, 44100, 0.1)[:, None], 44100, format="FLAC")
    (tmp_path / "set" / "notes.txt").write_text("not audio")
    assert [p.name for p in silt.gather([tmp_path / "set"])] == ["a.wav", "b.flac"]


def test_list_search_tag_and_set_from_the_command_line(tmp_path, library, capsys):
    path = write(tmp_path / "Cellar Hum.wav", tone(110, 44100, 0.3)[:, None], 44100)
    silt.main(["--library", str(library), "ingest", str(path), "--source", "ai",
               "--artist", "SomeTool", "--prompt", "subterranean hum", "--tag", "drone"])
    item_id = silt.load_items(library)[0]["id"]

    silt.main(["--library", str(library), "search", "subterranean"])
    assert item_id in capsys.readouterr().out
    silt.main(["--library", str(library), "list", "--tag", "drone"])
    assert item_id in capsys.readouterr().out
    silt.main(["--library", str(library), "list", "--source", "own"])
    assert "Nothing found" in capsys.readouterr().out

    silt.main(["--library", str(library), "tag", item_id, "--add", "dark", "loop", "--remove", "drone"])
    assert silt.find(library, item_id)["tags"] == ["dark", "loop"]

    silt.main(["--library", str(library), "set", item_id, "--cleared", "yes", "--license", "tool terms allow release"])
    item = silt.find(library, item_id)
    assert item["cleared_for_release"] is True and item["license_notes"] == "tool terms allow release"
    assert "Make sure you hold the permissions" in capsys.readouterr().out


# Hardware export -------------------------------------------------------------


def test_hardware_export_is_mono_16_bit_32k_and_keeps_pitch_and_level(tmp_path, library):
    stereo = np.stack([tone(1000, 44100, 3.0), tone(1000, 44100, 3.0)], axis=1)
    item = silt.ingest_file(write(tmp_path / "Hum.wav", stereo, 44100), library, "own")
    (result,) = silt.hardware_export(library, [item["id"]], tmp_path / "out")
    info = sf.info(str(result["path"]))
    assert (info.samplerate, info.channels, info.subtype) == (32000, 1, "PCM_16")
    assert abs(info.frames - 3 * 32000) <= 1
    assert result["written"] and not result["truncated"] and result["cleared"]
    audio, _ = sf.read(str(result["path"]), dtype="float32")
    middle = audio[8000:-8000]
    assert abs(frequency(middle, 32000) - 1000) < 0.01
    assert abs(20 * math.log10(rms(middle) / (0.5 / math.sqrt(2)))) < 0.01


def test_hardware_export_removes_what_32k_cannot_hold():
    # 20 kHz is above the 16 kHz Nyquist of a 32 kHz file; kept, it would fold to 12 kHz.
    x = (tone(1000, SR, 1.0) + tone(20000, SR, 1.0))[:, None]
    out = silt.to_hardware(x).astype(np.float64) / 32768
    assert level_db_at(out, 32000, 12000, 1000) < -85


def test_hardware_export_caps_at_24_seconds_and_fades_the_cut(tmp_path, library):
    item = silt.ingest_file(write(tmp_path / "long.wav", tone(200, SR, 30.0)[:, None], SR), library, "commercial")
    (result,) = silt.hardware_export(library, [item["id"]], tmp_path / "out", start=2.0)
    audio, _ = sf.read(str(result["path"]), dtype="float32")
    assert len(audio) == 24 * 32000
    assert result["truncated"] and not result["cleared"]
    assert abs(audio[0]) < 1e-3 and abs(audio[-1]) < 1e-3  # both ends were cut, so both fade
    assert np.abs(audio[16000:-16000]).max() > 0.49


def test_hardware_export_folds_stereo_and_protects_against_clipping():
    left = tone(300, SR, 0.5, amp=0.8)
    assert np.abs(silt.to_hardware(np.stack([left, -left], axis=1))).max() <= 2  # opposite phase cancels; dither remains
    loud = (2.0 * tone(300, SR, 0.5, amp=1.0))[:, None]
    assert np.abs(silt.to_hardware(loud)).max() >= 32000  # scaled down to full scale, not wrapped
    quiet = tone(300, SR, 0.5, amp=0.1)[:, None]
    assert np.abs(silt.to_hardware(quiet)).max() < 3400
    assert np.abs(silt.to_hardware(quiet, normalize=True)).max() >= 32000


def test_hardware_export_counts_against_the_budget(tmp_path, library, capsys):
    ids = []
    for k in range(10):
        path = write(tmp_path / f"take{k}.wav", tone(100 + 10 * k, SR, 24.0)[:, None], SR)
        ids.append(silt.ingest_file(path, library, "own")["id"])
    out = tmp_path / "out"
    results = silt.hardware_export(library, ids[:8], out)
    assert all(r["written"] for r in results) and abs(results[-1]["used"] - 192.0) < 0.01
    # 18 s of the 210 are left: a 24 s item does not fit, an 18 s excerpt does.
    (refused,) = silt.hardware_export(library, [ids[8]], out)
    assert not refused["written"] and "would not fit" in refused["reason"]
    (fits,) = silt.hardware_export(library, [ids[8]], out, seconds=18.0)
    assert fits["written"] and abs(fits["used"] - 210.0) < 0.01
    # Exporting an item again replaces its file instead of counting it twice.
    (again,) = silt.hardware_export(library, [ids[0]], out)
    assert again["written"] and abs(again["used"] - 210.0) < 0.01
    assert len(list(out.glob("*.wav"))) == 9

    silt.main(["--library", str(library), "hardware", ids[9], "--out", str(out)])
    printed = capsys.readouterr().out
    assert "SKIPPED" in printed and "210.0 s of 210 s used" in printed
