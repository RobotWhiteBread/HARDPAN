"""Render reference outputs from the Python prototype for the Rust port's golden tests.

Run from the project root:   .venv\\Scripts\\python plugin\\dsp\\tests\\make_golden.py

Writes raw little-endian float32 files (*.f32) and note-event lists (*.events,
one "sample,kind,note,accent" per line) to plugin/dsp/tests/data/. golden.rs
renders the same cases in Rust and checks that the samples match.
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import hardpan_proto as hp  # noqa: E402

OUT = Path(__file__).parent / "data"
SR = hp.SAMPLE_RATE


def save(name, samples):
    data = np.asarray(samples, dtype="<f4")
    (OUT / f"{name}.f32").write_bytes(data.tobytes())
    print(f"  {name}: {len(data)} samples, peak {np.max(np.abs(data)):.4f}")


def save_events(name, notes):
    lines = [f"{s},{k},{n},{int(a)}" for s, k, n, a in hp.note_events(notes)]
    (OUT / f"{name}.events").write_text("\n".join(lines) + "\n")


def run_synth(synth, notes, length):
    """Like render_patch, but for a fixed number of samples."""
    events = hp.note_events(notes)
    out = np.empty(length, dtype=np.float32)
    i = 0
    for n in range(length):
        while i < len(events) and events[i][0] == n:
            _, kind, note, accent = events[i]
            synth.note_on(note, accent) if kind else synth.note_off(note)
            i += 1
        out[n] = synth.tick()
    return out


def burst():
    """A decaying 220 Hz saw, then silence: input for the effects."""
    osc = hp.PolyBlepSaw()
    osc.set_frequency(220.0)
    out = np.zeros(SR, dtype=np.float32)
    for n in range(int(0.3 * SR)):
        out[n] = osc.tick() * np.float32(np.exp(-n / (0.08 * SR)))
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    for wave, make in (
        ("sine", lambda: hp.SineOscillator()),
        ("saw", lambda: hp.PolyBlepSaw()),
        ("square25", lambda: hp.PolyBlepSquare(width=0.25)),
    ):
        for hz in (110.0, 1000.0, 7040.0):
            osc = make()
            osc.set_frequency(hz)
            save(f"osc_{wave}_{int(hz)}", [osc.tick() for _ in range(2400)])

    env = hp.ADSR(0.01, 0.15, 0.7, 0.3)
    env.note_on()
    curve = [env.tick() for _ in range(int(0.4 * SR))]
    env.note_off()
    curve += [env.tick() for _ in range(int(0.4 * SR))]
    save("adsr", curve)

    for name, slope, cutoff, q in (("filter24", 24, 500.0, 3.0), ("filter12", 12, 2000.0, 0.7071)):
        f = hp.LowPassFilter(slope)
        f.set(cutoff, q)
        save(name, [f.tick(np.float32(1.0 if n == 0 else 0.0)) for n in range(4800)])

    for factor in (2, 4):
        osc = hp.PolyBlepSaw()
        osc.set_frequency(110.0)
        drive = hp.Drive(2.5, factor)
        save(f"drive{factor}x", [drive.tick(osc.tick()) for _ in range(4800)])

    x = burst()
    save("fx_input", x)
    left, right = hp.JunoChorus().process(x[: SR // 2])
    save("fx_chorus", np.stack([left, right], axis=1).ravel())
    left, right = hp.TempoDelay(110).process(x)
    save("fx_delay", np.stack([left, right], axis=1).ravel())
    left, right = hp.Reverb().process(x)
    save("fx_reverb", np.stack([left, right], axis=1).ravel())

    # Ten overlapping pad notes on eight voices: stealing, held and releasing.
    pad_notes = [(48 + 3 * k, 0.05 * k, 0.6) for k in range(10)]
    save_events("pad", pad_notes)
    save("pad", run_synth(hp.PolySynth(hp.PAD), pad_notes, int(1.5 * SR)))

    bass_notes = hp.sequence([hp.Step(45 + i, g, a) for i, g, a in hp.BASS_BAR], 110, swing=0.04)
    save_events("seq_bass", bass_notes)
    save("seq_bass", run_synth(hp.MonoSynth(hp.SEQ_BASS), bass_notes, int(2.3 * SR)))

    lead_notes = [(76, 0.0, 0.5), (72, 0.45, 0.6), (77, 1.0, 0.3), (74, 1.25, 0.6)]
    save_events("lead", lead_notes)
    save("lead", run_synth(hp.MonoSynth(hp.LEAD), lead_notes, int(2.0 * SR)))


if __name__ == "__main__":
    main()
