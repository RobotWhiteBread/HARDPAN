"""HARDPAN phase 2: why naive saw and square waves alias, and how much PolyBLEP helps.

Run from the project folder:   .venv\\Scripts\\python compare_aliasing.py

It renders a rising sweep from 100 Hz to 8 kHz through each oscillator in
hardpan_proto.py, measures steady tones at eight pitches, and writes:

    aliasing_comparison.png    spectrograms of the four sweeps, and how much
                               aliasing each oscillator leaves at each pitch
    sweep_naive_saw.wav        the two saw sweeps, to hear the difference.
    sweep_polyblep_saw.wav     Listen for faint tones that fall while the
                               sweep rises: those are the aliases.

The synthesis is all in hardpan_proto.py. This file only analyses it: numpy's
FFT computes the spectra, and matplotlib draws the figure.
"""

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # draw straight to a file, no window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from hardpan_proto import F32, SAMPLE_RATE, NaiveSaw, NaiveSquare, PolyBlepSaw, PolyBlepSquare  # noqa: E402

HERE = Path(__file__).parent
NYQUIST = SAMPLE_RATE / 2
SWEEP_START, SWEEP_END, SWEEP_SECONDS = 100.0, 8000.0, 6.0
TONE_LENGTH = 1 << 16  # samples per steady-tone measurement, about 1.4 s
TONE_TARGETS = [55.0, 110.0, 220.0, 440.0, 880.0, 1760.0, 3520.0, 7040.0]  # A1 to A8
WAVEFORMS = [("Sawtooth", NaiveSaw, PolyBlepSaw), ("Square", NaiveSquare, PolyBlepSquare)]

# Chart styling (light surface, one blue ramp for magnitude, two series colours)
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
POLYBLEP_COLOR, NAIVE_COLOR = "#2a78d6", "#eb6834"
MAGNITUDE = LinearSegmentedColormap.from_list(
    "blue_ramp", [SURFACE, "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
)
FLOOR_DB = -110.0


def render_sweep(osc_class):
    """An exponential sweep: the frequency is multiplied by the same factor on
    every sample, so each octave takes the same time, which is how pitch is
    heard. Setting a new frequency on every sample is the per-sample modulation
    the phase accumulator was built for."""
    osc = osc_class()
    n = int(SWEEP_SECONDS * SAMPLE_RATE)
    growth = (SWEEP_END / SWEEP_START) ** (1.0 / n)
    out = np.empty(n, dtype=F32)
    f = SWEEP_START
    for i in range(n):
        osc.set_frequency(f)
        out[i] = osc.tick()
        f *= growth
    return out


def sweep_time(f):
    """When the sweep passes frequency f, in seconds."""
    return SWEEP_SECONDS * math.log(f / SWEEP_START) / math.log(SWEEP_END / SWEEP_START)


def blackman_harris(n):
    """The 4-term Blackman-Harris window.

    Why a window matters: the FFT treats each frame as one period of a signal
    that repeats forever. Cut a frame out with straight edges and its end does
    not meet its start. That mismatch is a jump, and a jump contains every
    frequency, so each strong harmonic leaks across the whole spectrum. With
    straight edges (a rectangular window) the leakage is only 13 dB below the
    harmonic. The aliases this script looks for are 40 to 100 dB below, so they
    would vanish under it. A window tapers the frame to zero at both ends, so
    there is no jump. Hann, the usual choice, still leaks at 31 dB down;
    Blackman-Harris leaks at 92 dB down. The price is a wider peak: each pure
    tone spreads over about 8 frequency bins instead of 2.
    """
    k = np.arange(n) / n
    return (
        0.35875
        - 0.48829 * np.cos(2 * np.pi * k)
        + 0.14128 * np.cos(4 * np.pi * k)
        - 0.01168 * np.cos(6 * np.pi * k)
    )


def spectrogram(x, frame=2048, hop=512):
    """Magnitude in dB, relative to the loudest point, for overlapping windowed frames."""
    window = blackman_harris(frame)
    starts = np.arange(0, len(x) - frame + 1, hop)
    mags = np.array([np.abs(np.fft.rfft(x[s : s + frame].astype(np.float64) * window)) for s in starts]).T
    db = 20 * np.log10(np.maximum(mags / mags.max(), 1e-12))
    return db, (starts + frame / 2) / SAMPLE_RATE, np.fft.rfftfreq(frame, 1 / SAMPLE_RATE)


def coherent_pitch(target):
    """A pitch near `target` that fits a whole, odd number of cycles into one
    measurement.

    Then every true harmonic lands exactly on an FFT bin (a multiple of the
    cycle count), nothing leaks, and no window is needed. Because the count is
    odd and the length is a power of two, no alias can land on a harmonic's
    bin either. So everything not on a harmonic bin is aliasing, measured
    exactly. As a bonus, the phase step comes out as a whole number over 65536,
    which float32 holds exactly, so the oscillator repeats perfectly.
    """
    cycles = round(target * TONE_LENGTH / SAMPLE_RATE)
    if cycles % 2 == 0:
        cycles += 1
    return cycles * SAMPLE_RATE / TONE_LENGTH, cycles


AUDIBLE_BAND = 10000.0  # aliases below this land where the ear is far more sensitive


def measure_tone(osc_class, target):
    """(pitch, all aliasing relative to the note's power in dB, aliasing below
    AUDIBLE_BAND relative to the note's power in dB, loudest single alias
    relative to the fundamental in dB, that alias's frequency, spectrum)."""
    f0, cycles = coherent_pitch(target)
    osc = osc_class()
    osc.set_frequency(f0)
    x = np.array([osc.tick() for _ in range(TONE_LENGTH)], dtype=np.float64)
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    is_note = np.zeros(len(spectrum), dtype=bool)
    is_note[0] = True  # DC belongs to the waveform (pulse widths other than 0.5 have it)
    is_note[cycles::cycles] = True  # the harmonics
    alias = np.where(is_note, 0.0, spectrum)
    note_power = spectrum[is_note].sum()
    below = np.arange(len(spectrum)) * SAMPLE_RATE / TONE_LENGTH < AUDIBLE_BAND
    worst = int(np.argmax(alias))
    return (
        f0,
        10 * math.log10(alias.sum() / note_power),
        10 * math.log10(alias[below].sum() / note_power),
        10 * math.log10(alias[worst] / spectrum[cycles]),
        worst * SAMPLE_RATE / TONE_LENGTH,
        spectrum,
    )


def spread_labels(ys, gap):
    """Nudge label heights apart so no two are closer than `gap`, keeping order."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = list(ys)
    for prev, cur in zip(order, order[1:]):
        placed[cur] = max(placed[cur], placed[prev] + gap)
    return placed


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, labelsize=9)


def main():
    plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
    fig = plt.figure(figsize=(13, 12), facecolor=SURFACE)
    grid = fig.add_gridspec(3, 3, width_ratios=[1, 1, 0.035], height_ratios=[1, 1, 0.9], hspace=0.42, wspace=0.12)
    fig.suptitle("Aliasing: naive versus PolyBLEP oscillators", x=0.07, ha="left", fontsize=16, color=INK, y=0.975)
    fig.text(
        0.07, 0.945,
        "A sweep from 100 Hz to 8 kHz. True harmonics rise with the sweep; aliases fold back down from 24 kHz "
        "(half the sample rate) and fall.",
        fontsize=10.5, color=INK_2,
    )

    tick_freqs = [100, 200, 500, 1000, 2000, 5000, 8000]
    image = None
    for row, (name, naive_class, blep_class) in enumerate(WAVEFORMS):
        for col, (label, cls) in enumerate((("naive", naive_class), ("PolyBLEP", blep_class))):
            audio = render_sweep(cls)
            if name == "Sawtooth":
                # A full-scale saw sweep is unpleasantly loud, so these play at half scale.
                sf.write(HERE / f"sweep_{label.lower()}_saw.wav", audio * F32(0.5), SAMPLE_RATE, subtype="FLOAT")
            db, times, freqs = spectrogram(audio)
            ax = fig.add_subplot(grid[row, col])
            image = ax.pcolormesh(times, freqs / 1000, db, cmap=MAGNITUDE, vmin=FLOOR_DB, vmax=0, shading="auto", rasterized=True)
            style_axes(ax)
            ax.set_title(f"{name}, {label}", loc="left", fontsize=12, color=INK)
            ax.set_xticks([sweep_time(f) for f in tick_freqs])
            ax.set_xticklabels([f"{f // 1000}k" if f >= 1000 else str(f) for f in tick_freqs])
            ax.set_ylim(0, NYQUIST / 1000)
            if col == 0:
                ax.set_ylabel("Frequency in the output (kHz)", color=INK_2, fontsize=10)
            else:
                ax.set_yticklabels([])
            if row == 1:
                ax.set_xlabel("Fundamental of the sweep (Hz)", color=INK_2, fontsize=10)
            if row == 0 and col == 0:
                # The 6th harmonic passes 24 kHz when the note reaches 4 kHz and folds
                # back: at 5 kHz it is heard at 48 - 6 x 5 = 18 kHz, and falling.
                ax.annotate(
                    "6th harmonic, folded back:\nfalls while the note rises",
                    xy=(sweep_time(5000), 18.0), xytext=(sweep_time(260), 20.2),
                    fontsize=9.5, color=INK, va="center",
                    bbox=dict(boxstyle="round,pad=0.35", fc=SURFACE, ec=AXIS, lw=0.6),
                    arrowprops=dict(arrowstyle="-", color=INK, lw=0.9),
                )
    bar = fig.colorbar(image, cax=fig.add_subplot(grid[0:2, 2]))
    bar.set_label("Level relative to the loudest point (dB)", color=INK_2, fontsize=10)
    bar.ax.tick_params(colors=MUTED, labelcolor=INK_2, labelsize=9)
    bar.outline.set_edgecolor(AXIS)

    # Steady tones: how much aliasing is left, and where it is worst.
    results = {}
    for name, naive_class, blep_class in WAVEFORMS:
        for label, cls in (("naive", naive_class), ("PolyBLEP", blep_class)):
            results[name, label] = [measure_tone(cls, f) for f in TONE_TARGETS]

    y_low = math.floor(min(r[2] for rows in results.values() for r in rows) / 10) * 10 - 5
    for col, (name, _, _) in enumerate(WAVEFORMS):
        ax = fig.add_subplot(grid[2, col])
        style_axes(ax)
        ax.grid(True, which="major", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ends, texts = [], []
        for label, color in (("PolyBLEP", POLYBLEP_COLOR), ("naive", NAIVE_COLOR)):
            rows = results[name, label]
            pitches = [r[0] for r in rows]
            for index, band, style in ((1, "all", "-"), (2, "<10 kHz", "--")):
                values = [r[index] for r in rows]
                ax.plot(pitches, values, color=color, linewidth=2, linestyle=style, marker="o", markersize=6,
                        markeredgecolor=SURFACE, markeredgewidth=1.5, label=f"{label}, {band}")
                ends.append(values[-1])
                texts.append(f"{label}, {band}")
        for text, y in zip(texts, spread_labels(ends, gap=5.5)):
            ax.annotate(text, xy=(pitches[-1], y), xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=9.5, color=INK)
        ax.set_xscale("log")
        ax.set_xticks([55, 110, 220, 440, 880, 1760, 3520, 7040])
        ax.set_xticklabels(["55", "110", "220", "440", "880", "1.8k", "3.5k", "7k"])
        ax.minorticks_off()
        ax.set_xlim(45, 40000)
        ax.set_ylim(y_low, 0)
        ax.set_title(f"{name}: aliasing left in a steady tone", loc="left", fontsize=12, color=INK)
        ax.set_xlabel("Pitch (Hz)", color=INK_2, fontsize=10)
        if col == 0:
            ax.set_ylabel("Alias power relative to the note (dB)", color=INK_2, fontsize=10)
        else:
            ax.set_yticklabels([])
        handles, labels = ax.get_legend_handles_labels()
    # Both panels show the same four series, so one legend serves the pair.
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.46, 0.025), frameon=False, fontsize=9.5,
               labelcolor=INK_2, ncol=4, handlelength=2.6, columnspacing=1.6)

    out = HERE / "aliasing_comparison.png"
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    print(f"Wrote {out.name}, sweep_naive_saw.wav, sweep_polyblep_saw.wav\n")

    print("Steady tones: aliasing as a share of the note's power, all of it and the part")
    print("that lands below 10 kHz, where the ear is far more sensitive.\n")
    for name, _, _ in WAVEFORMS:
        print(f"{name:10s}      naive: all   <10 kHz    PolyBLEP: all   <10 kHz    improvement: all  <10 kHz")
        for naive, blep in zip(results[name, "naive"], results[name, "PolyBLEP"]):
            print(
                f"  {naive[0]:7.1f} Hz     {naive[1]:7.1f}   {naive[2]:7.1f} dB      {blep[1]:7.1f}   {blep[2]:7.1f} dB"
                f"          {naive[1] - blep[1]:5.1f}   {naive[2] - blep[2]:5.1f} dB"
            )
        print()

    worst = max(((name, row) for name, _, _ in WAVEFORMS for row in results[name, "PolyBLEP"]), key=lambda nr: nr[1][1])
    name, (f0, total, below, loudest, at, _) = worst
    print(
        f"Worst case for PolyBLEP: {name.lower()} at {f0:.0f} Hz. Aliasing totals {total:.1f} dB "
        f"({below:.1f} dB below 10 kHz), and the loudest alias is {loudest:.1f} dB, at {at:.0f} Hz."
    )

    # What PolyBLEP costs: it softens the top harmonics a little. An ideal saw's
    # nth harmonic is 1/n of the fundamental.
    f0, cycles = coherent_pitch(440.0)
    spectrum = results["Sawtooth", "PolyBLEP"][3][5]
    losses = []
    for f in (5000, 10000, 15000, 20000):
        n = round(f / f0)
        measured = math.sqrt(spectrum[n * cycles] / spectrum[cycles])
        losses.append(f"{n * f0 / 1000:.1f} kHz {20 * math.log10(measured * n):+.1f} dB")
    print(f"PolyBLEP saw at {f0:.0f} Hz, harmonics against an ideal saw: " + ", ".join(losses))


if __name__ == "__main__":
    main()
