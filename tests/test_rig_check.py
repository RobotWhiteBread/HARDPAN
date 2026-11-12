"""Tests for rig_check.py: the parts that do not need the hardware."""

import re
import sys
from pathlib import Path

import mido
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rig_check  # noqa: E402


def loopback(sent, delay, gain=0.5, noise=0.0, seed=1):
    received = np.zeros(len(sent), np.float32)
    received[delay:] = sent[: len(sent) - delay] * gain
    return received + noise * np.random.default_rng(seed).standard_normal(len(sent)).astype(np.float32)


def test_delay_is_found_to_the_sample():
    sent = rig_check.test_signal(48000)
    for delay in (0, 1, 311, 777, 4096):
        found, confidence = rig_check.measure_delay(sent, loopback(sent, delay))
        assert found == delay and confidence > 100


def test_delay_survives_noise_a_quiet_return_and_inverted_polarity():
    sent = rig_check.test_signal(48000)
    found, confidence = rig_check.measure_delay(sent, loopback(sent, 600, gain=-0.05, noise=0.01))
    assert found == 600 and confidence > 30


def test_no_cable_reads_as_low_confidence():
    sent = rig_check.test_signal(48000)
    silence = 0.001 * np.random.default_rng(3).standard_normal(len(sent)).astype(np.float32)
    assert rig_check.measure_delay(sent, silence)[1] < 30


def test_the_test_signal_is_quiet_and_starts_and_ends_silent():
    sent = rig_check.test_signal(48000)
    assert sent.dtype == np.float32 and np.abs(sent).max() <= 0.125
    assert not sent[:1000].any() and not sent[-1000:].any()


def test_messages_are_described_by_what_hardpan_does_with_them():
    assert "cutoff" in rig_check.describe(mido.Message("control_change", control=74, value=100))
    assert "not used" in rig_check.describe(mido.Message("control_change", control=3, value=1))
    assert "note on   A2" in rig_check.describe(mido.Message("note_on", note=45, velocity=90))
    assert "accented" in rig_check.describe(mido.Message("note_on", note=45, velocity=110))
    assert "note off" in rig_check.describe(mido.Message("note_on", note=45, velocity=0))
    assert "+2.00 semitones" in rig_check.describe(mido.Message("pitchwheel", pitch=8191)).replace("+2.00", "+2.00")


def test_the_control_table_matches_the_plugin():
    """rig_check's table and the plugin's CC_MAP are two copies of one mapping."""
    source = (ROOT / "plugin" / "src" / "lib.rs").read_text(encoding="utf-8")
    table = source[source.index("const CC_MAP") : source.index("];", source.index("const CC_MAP"))]
    plugin = [int(n) for n in re.findall(r"\((\d+), Target::", table)]
    assert len(plugin) == 12
    assert set(plugin) == set(rig_check.CONTROLS) - {1, 64, 120, 123}
