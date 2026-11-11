"""HARDPAN phase 3b: what oversampling does for the drive.

Run from the project folder:   .venv\\Scripts\\python compare_drive.py

A sine wave is pushed hard through the tanh drive in hardpan_proto.py, run at
1, 2 and 4 times the sample rate. A pure sine has no harmonics of its own, so
everything else in the output was made by the drive, and anything that is not
a whole multiple of the sine's frequency is aliasing. It writes:

    drive_comparison.png    spectrograms of a 100 Hz to 8 kHz sweep at each
                            rate, and the aliasing measured in steady tones

and prints the measurements and what each rate costs per sample.
"""

import math
import time

import numpy as np

from compare_aliasing import (  # also switches matplotlib to drawing straight to a file
    AUDIBLE_BAND,
    AXIS,
    FLOOR_DB,
    GRID,
    HERE,
    INK,
    INK_2,
    MAGNITUDE,
    MUTED,
    NYQUIST,
    SURFACE,
    SWEEP_END,
    SWEEP_SECONDS,
    SWEEP_START,
    TONE_LENGTH,
    TONE_TARGETS,
    coherent_pitch,
    spectrogram,
    spread_labels,
    style_axes,
    sweep_time,
)
from hardpan_proto import F32, SAMPLE_RATE, Drive, PolyBlepSaw, SineOscillator

import matplotlib.pyplot as plt  # noqa: E402

DRIVE_GAIN = 6.0  # hard: a sine pushed this far into tanh comes out close to a square wave
RATES = [(1, "1x, no oversampling", "#2a78d6"), (2, "2x", "#eb6834"), (4, "4x", "#1baf7a")]


def render_sweep(factor):
    """The phase 2 sweep, as a sine, through the drive."""
    osc = SineOscillator()
    drive = Drive(DRIVE_GAIN, factor)
    n = int(SWEEP_SECONDS * SAMPLE_RATE)
    growth = (SWEEP_END / SWEEP_START) ** (1.0 / n)
    out = np.empty(n, dtype=F32)
    f = SWEEP_START
    for i in range(n):
        osc.set_frequency(f)
        out[i] = drive.tick(osc.tick())
        f *= growth
    return out


def measure(factor, target, source=SineOscillator, gain=DRIVE_GAIN):
    """(pitch, all aliasing and aliasing below 10 kHz relative to the output's
    harmonic power in dB, seconds per sample). Uses the same whole-number-of-
    cycles trick as compare_aliasing.py, so no window is needed. factor 0 means
    no drive at all: the source alone."""
    f0, cycles = coherent_pitch(target)
    osc = source()
    osc.set_frequency(f0)
    if factor:
        drive = Drive(gain, factor)

        def tick():
            return drive.tick(osc.tick())
    else:
        tick = osc.tick
    for _ in range(256):  # let the resampling filters fill up first
        tick()
    started = time.perf_counter()
    x = np.array([tick() for _ in range(TONE_LENGTH)], dtype=np.float64)
    per_sample = (time.perf_counter() - started) / TONE_LENGTH
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    is_note = np.zeros(len(spectrum), dtype=bool)
    is_note[0] = True
    is_note[cycles::cycles] = True
    alias = np.where(is_note, 0.0, spectrum)
    below = np.arange(len(spectrum)) * SAMPLE_RATE / TONE_LENGTH < AUDIBLE_BAND
    note_power = spectrum[is_note].sum()
    return (
        f0,
        10 * math.log10(alias.sum() / note_power),
        10 * math.log10(max(alias[below].sum(), 1e-30) / note_power),
        per_sample,
    )


def main():
    plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
    fig = plt.figure(figsize=(14, 10.5), facecolor=SURFACE)
    outer = fig.add_gridspec(2, 1, height_ratios=[1, 0.95], hspace=0.38)
    top = outer[0].subgridspec(1, 4, width_ratios=[1, 1, 1, 0.04], wspace=0.1)
    bottom = outer[1].subgridspec(1, 2, wspace=0.12)
    fig.suptitle(
        f"Drive: tanh at a gain of {DRIVE_GAIN:g}, with and without oversampling",
        x=0.07, ha="left", fontsize=16, color=INK, y=0.975,
    )
    fig.text(
        0.07, 0.94,
        "A sine swept from 100 Hz to 8 kHz. The drive's harmonics rise with it; aliases fold back down from "
        "24 kHz and fall.",
        fontsize=10.5, color=INK_2,
    )

    tick_freqs = [100, 200, 500, 1000, 2000, 5000, 8000]
    image = None
    for col, (factor, label, _) in enumerate(RATES):
        db, times, freqs = spectrogram(render_sweep(factor))
        ax = fig.add_subplot(top[0, col])
        image = ax.pcolormesh(times, freqs / 1000, db, cmap=MAGNITUDE, vmin=FLOOR_DB, vmax=0, shading="auto", rasterized=True)
        style_axes(ax)
        ax.set_title(label, loc="left", fontsize=12, color=INK)
        ax.set_xticks([sweep_time(f) for f in tick_freqs])
        ax.set_xticklabels([f"{f // 1000}k" if f >= 1000 else str(f) for f in tick_freqs])
        ax.set_ylim(0, NYQUIST / 1000)
        ax.set_xlabel("Sine frequency (Hz)", color=INK_2, fontsize=10)
        if col == 0:
            ax.set_ylabel("Frequency in the output (kHz)", color=INK_2, fontsize=10)
        else:
            ax.set_yticklabels([])
    bar = fig.colorbar(image, cax=fig.add_subplot(top[0, 3]))
    bar.set_label("Level relative to the loudest point (dB)", color=INK_2, fontsize=10)
    bar.ax.tick_params(colors=MUTED, labelcolor=INK_2, labelsize=9)
    bar.outline.set_edgecolor(AXIS)

    results = {factor: [measure(factor, f) for f in TONE_TARGETS] for factor, _, _ in RATES}

    y_low = math.floor(min(r[2] for rows in results.values() for r in rows) / 10) * 10 - 5
    panels = [(1, "All aliasing"), (2, "Aliasing that lands below 10 kHz")]
    for col, (index, title) in enumerate(panels):
        ax = fig.add_subplot(bottom[0, col])
        style_axes(ax)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ends, texts = [], []
        for factor, label, color in RATES:
            rows = results[factor]
            pitches, values = [r[0] for r in rows], [r[index] for r in rows]
            ax.plot(pitches, values, color=color, linewidth=2, marker="o", markersize=6,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=label)
            ends.append(values[-1])
            texts.append(label.split(",")[0])
        for text, y in zip(texts, spread_labels(ends, gap=5.5)):
            ax.annotate(text, xy=(pitches[-1], y), xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=10, color=INK)
        ax.set_xscale("log")
        ax.set_xticks([55, 110, 220, 440, 880, 1760, 3520, 7040])
        ax.set_xticklabels(["55", "110", "220", "440", "880", "1.8k", "3.5k", "7k"])
        ax.minorticks_off()
        ax.set_xlim(45, 14000)
        ax.set_ylim(y_low, 0)
        ax.set_title(title, loc="left", fontsize=12, color=INK)
        ax.set_xlabel("Sine frequency (Hz)", color=INK_2, fontsize=10)
        if col == 0:
            ax.set_ylabel("Alias power relative to the drive's harmonics (dB)", color=INK_2, fontsize=10)
            ax.text(52, -12, "2x and 4x overlap up to 3.5 kHz here: what is left\n"
                    "is the 48 / 96 kHz filter's transition band, which\nthey share.",
                    fontsize=9, color=INK_2, va="top")
        else:
            ax.set_yticklabels([])
        handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.47, 0.01), frameon=False,
               fontsize=9.5, labelcolor=INK_2, ncol=3, handlelength=2.6, columnspacing=1.6)

    out = HERE / "drive_comparison.png"
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    print(f"Wrote {out.name}\n")

    print(f"A sine through tanh at a gain of {DRIVE_GAIN:g}: aliasing as a share of the drive's harmonic")
    print("power, all of it and the part below 10 kHz.\n")
    print("  sine          " + "".join(f"{label.split(',')[0]:>22s}" for _, label, _ in RATES))
    print("                " + "        all   <10 kHz" * len(RATES))
    for i, f in enumerate(TONE_TARGETS):
        row = [results[factor][i] for factor, _, _ in RATES]
        print(f"  {row[0][0]:7.1f} Hz  " + "".join(f"   {r[1]:7.1f} {r[2]:8.1f} dB" for r in row))
    print()
    for factor, label, _ in RATES:
        cost = np.mean([r[3] for r in results[factor]]) * 1e6
        print(f"  {label:22s} {cost:5.1f} microseconds per sample in this Python, {cost * SAMPLE_RATE / 1e6:.2f} s per second of audio")

    # The patches drive saws, not sines, and a saw brings its own high harmonics
    # (and PolyBLEP's leftover aliasing) into the drive. This is the bass's drive.
    print("\nThe case the patches actually use: a PolyBLEP saw into the bass's drive (gain 2.5).")
    print("Aliasing relative to the note, all / below 10 kHz.\n")
    print("  saw          saw alone          1x                 2x                 4x")
    for target in (55.0, 110.0, 220.0, 440.0, 880.0):
        rows = [measure(factor, target, PolyBlepSaw, 2.5) for factor in (0, 1, 2, 4)]
        print(f"  {rows[0][0]:6.1f} Hz" + "".join(f"   {r[1]:6.1f} / {r[2]:6.1f}" for r in rows))
    print("\n2x already gets everything oversampling the drive can: the rest is aliasing the saw")
    print("brought with it, which the drive mixes down into the audible band.")


if __name__ == "__main__":
    main()
