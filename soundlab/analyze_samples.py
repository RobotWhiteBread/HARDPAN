"""
soundlab sample analyzer
Step 2: measure every file in the library catalog (made by build_library.py) and write
features.csv in your library folder (one row per file). Resumable: rerun and it picks up
where it stopped. Needs: numpy, soundfile, scipy.

  python analyze_samples.py                      (everything)
  python analyze_samples.py --limit 300          (quick test)
  python analyze_samples.py --pack "SP 1200"     (only packs matching text)
"""
import argparse, csv, os, sys, time, math
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soundlab_config as SC
NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Krumhansl-Kessler key profiles
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

FIELDS = ["Path", "dur_s", "sr", "channels", "peak_db", "rms_db", "crest_db",
          "attack_ms", "decay_ms", "centroid_hz", "rolloff_hz", "flatness", "zcr",
          "sub_frac", "low_frac", "mid_frac", "high_frac", "onsets", "onsets_per_s",
          "tempo_bpm", "tempo_conf", "bars", "key", "key_conf", "f0_hz", "note",
          "stereo_width", "sound_guess", "character", "error"]

MAX_SECONDS = 30.0
N_FFT = 2048
HOP = 512


def db(x):
    return 20 * math.log10(max(float(x), 1e-9))


def stft_mag(y):
    if len(y) < N_FFT:
        y = np.pad(y, (0, N_FFT - len(y)))
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(N_FFT)[None, :]
    return np.abs(np.fft.rfft(frames, axis=1))  # (frames, bins)


def count_onsets(flux, sr):
    """Real hits only: strong peaks at least 70 ms apart."""
    if flux.size < 3:
        return 0
    thr = np.median(flux) + 2.0 * flux.std()
    gap = max(1, int(0.07 * sr / HOP))
    last, n = -gap, 0
    for i in range(1, flux.size - 1):
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and i - last >= gap:
            n += 1
            last = i
    return n


def estimate_tempo(flux, sr, dur):
    """Autocorrelation of the onset envelope, nudged toward whole bars for loops."""
    if dur < 1.8 or flux.size < 16:
        return None, 0.0, None
    env = flux - flux.mean()
    ac = np.correlate(env, env, mode="full")[env.size - 1:]
    if ac[0] <= 0:
        return None, 0.0, None
    ac = ac / ac[0]
    fps = sr / HOP
    best, best_score = None, -1.0
    for bpm in np.arange(60.0, 180.5, 0.5):
        lag = fps * 60.0 / bpm
        score = 0.0
        for mult, w in ((1, 1.0), (2, 0.5), (4, 0.25)):
            l = lag * mult
            i = int(l)
            if i + 1 >= ac.size:
                continue
            frac = l - i
            score += w * (ac[i] * (1 - frac) + ac[i + 1] * frac)
        beats = dur * bpm / 60.0
        bars = beats / 4.0
        if bars >= 0.9:
            off = abs(bars - round(bars))
            if off < 0.04:
                score += 0.35  # loop length is a whole number of bars at this tempo
        if score > best_score:
            best, best_score = bpm, score
    if best is None:
        return None, 0.0, None
    bars = dur * best / 240.0
    bars_r = round(bars) if abs(bars - round(bars)) < 0.06 and round(bars) >= 1 else None
    return round(float(best), 1), round(float(best_score), 3), bars_r


def estimate_key(mag, sr):
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    sel = (freqs >= 55) & (freqs <= 5000)
    if not sel.any():
        return None, 0.0
    pc = np.mod(np.round(12 * np.log2(freqs[sel] / 440.0)) + 9, 12).astype(int)
    energy = (mag[:, sel] ** 2).sum(axis=0)
    chroma = np.bincount(pc, weights=energy, minlength=12)
    if chroma.sum() <= 0:
        return None, 0.0
    chroma = chroma / chroma.max()
    scores = []
    for i in range(12):
        scores.append((np.corrcoef(chroma, np.roll(MAJ, i))[0, 1], NOTES[i]))
        scores.append((np.corrcoef(chroma, np.roll(MIN, i))[0, 1], NOTES[i] + "m"))
    scores.sort(reverse=True)
    margin = scores[0][0] - scores[1][0]
    if scores[0][0] < 0.5 or margin < 0.02:
        return None, 0.0
    return scores[0][1], round(float(scores[0][0]), 3)


def estimate_f0(y, sr):
    """Harmonic product spectrum over the steadiest part of a one-shot."""
    seg = y[: int(sr * 1.5)]
    if seg.size < 4096:
        seg = np.pad(seg, (0, 4096 - seg.size))
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
    freqs = np.fft.rfftfreq(seg.size, 1.0 / sr)
    hps = spec.copy()
    for h in (2, 3, 4):
        dec = spec[::h]
        hps[: dec.size] *= dec
    sel = (freqs >= 30) & (freqs <= 2000)
    if not sel.any():
        return None, None
    i = np.argmax(hps[sel])
    f0 = float(freqs[sel][i])
    if f0 <= 0:
        return None, None
    midi = 69 + 12 * math.log2(f0 / 440.0)
    note = NOTES[int(round(midi)) % 12] + str(int(round(midi)) // 12 - 1)
    return round(f0, 1), note


def guess(f):
    d, low, sub, high, mid = f["dur_s"], f["low_frac"], f["sub_frac"], f["high_frac"], f["mid_frac"]
    att, flat, cen, ops = f["attack_ms"], f["flatness"], f["centroid_hz"], f["onsets_per_s"]
    busy = f["onsets"] >= 4 and ops >= 1.2
    if d >= 1.8 and busy and (f["bars"] or d > 4):
        return "loop"
    if d <= 1.2:
        if low + sub >= 0.5 and cen < 900:
            return "kick"
        if high >= 0.5 and flat > 0.15:
            return "hat"
        if mid >= 0.3 and flat > 0.06:
            return "snare_clap"
        return "perc"
    if low + sub >= 0.6 and flat < 0.05:
        return "bass"
    if flat > 0.3:
        return "fx_noise"
    if flat < 0.08:
        return "melodic"
    return "texture"


def character(f):
    tags = []
    tags.append("dark" if f["centroid_hz"] < 900 else "bright" if f["centroid_hz"] > 3500 else "warm")
    if f["crest_db"] >= 18 and f["attack_ms"] <= 10:
        tags.append("punchy")
    elif f["crest_db"] < 9 and f["dur_s"] > 1.0:
        tags.append("squashed")
    if f["sr"] and f["rolloff_hz"] < 4500:
        tags.append("lofi")
    if f["flatness"] > 0.25:
        tags.append("noisy")
    if f["sub_frac"] > 0.25:
        tags.append("subby")
    if f["stereo_width"] is not None and f["stereo_width"] > 0.35:
        tags.append("wide")
    return " ".join(tags)


def analyze(path):
    out = {k: "" for k in FIELDS}
    out["Path"] = path
    try:
        info = sf.info(path)
        frames = int(min(info.frames, info.samplerate * MAX_SECONDS))
        data, sr = sf.read(path, frames=frames, dtype="float32", always_2d=True)
        if data.size == 0:
            raise ValueError("empty file")
        ch = data.shape[1]
        y = data.mean(axis=1)
        width = None
        if ch >= 2:
            l, r = data[:, 0], data[:, 1]
            m, s = (l + r) / 2, (l - r) / 2
            width = float(np.sqrt((s ** 2).mean()) / (np.sqrt((m ** 2).mean()) + 1e-9))
        dur = info.frames / float(info.samplerate)
        peak = float(np.abs(y).max())
        rms = float(np.sqrt((y ** 2).mean()))

        mag = stft_mag(y)
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
        power = mag ** 2
        tot = power.sum() + 1e-12
        band = lambda lo, hi: float(power[:, (freqs >= lo) & (freqs < hi)].sum() / tot)
        spec_mean = mag.mean(axis=0) + 1e-12
        centroid = float((freqs * spec_mean).sum() / spec_mean.sum())
        cum = np.cumsum(spec_mean ** 2)
        rolloff = float(freqs[np.searchsorted(cum, 0.85 * cum[-1])])
        flatness = float(np.exp(np.log(spec_mean).mean()) / spec_mean.mean())
        zcr = float(np.mean(np.abs(np.diff(np.sign(y))) > 0))

        env = np.sqrt((power).mean(axis=1))
        pk = int(np.argmax(env))
        attack_ms = pk * HOP / sr * 1000
        after = env[pk:]
        below = np.where(after < env[pk] * 0.1)[0]
        decay_ms = (below[0] if below.size else after.size) * HOP / sr * 1000

        flux = np.maximum(np.diff(np.log1p(mag), axis=0), 0).sum(axis=1)
        onsets = count_onsets(flux, sr)

        out.update(dur_s=round(dur, 3), sr=sr, channels=ch, peak_db=round(db(peak), 1),
                   rms_db=round(db(rms), 1), crest_db=round(db(peak) - db(rms), 1),
                   attack_ms=round(attack_ms, 1), decay_ms=round(decay_ms, 1),
                   centroid_hz=round(centroid), rolloff_hz=round(rolloff),
                   flatness=round(flatness, 4), zcr=round(zcr, 4),
                   sub_frac=round(band(20, 60), 3), low_frac=round(band(60, 250), 3),
                   mid_frac=round(band(250, 2500), 3), high_frac=round(band(2500, sr / 2), 3),
                   onsets=onsets, onsets_per_s=round(onsets / max(min(dur, MAX_SECONDS), 0.01), 2),
                   stereo_width=round(width, 3) if width is not None else None)

        bpm, conf, bars = estimate_tempo(flux, sr, min(dur, MAX_SECONDS) if dur <= MAX_SECONDS else dur)
        if dur > MAX_SECONDS:
            bars = None
        out.update(tempo_bpm=bpm or "", tempo_conf=conf if bpm else "", bars=bars or "")
        if flatness < 0.2:
            k, kc = estimate_key(mag, sr)
            out.update(key=k or "", key_conf=kc if k else "")
            if dur <= 6:
                f0, note = estimate_f0(y, sr)
                out.update(f0_hz=f0 or "", note=note or "")
        g = dict(out)
        g["bars"] = bars
        g["tempo_bpm"] = bpm
        out["sound_guess"] = guess(g)
        out["character"] = character(g)
    except Exception as e:  # keep going no matter what one file does
        out["error"] = str(e)[:200]
    if out["stereo_width"] is None:
        out["stereo_width"] = ""
    return out


def main():
    ap = argparse.ArgumentParser()
    cfg = SC.load()
    ap.add_argument("--catalog", default=SC.lib_file("catalog.csv", cfg))
    ap.add_argument("--out", default=SC.lib_file("features.csv", cfg))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pack", default="")
    ap.add_argument("--map-from", default="")  # for testing on another machine
    ap.add_argument("--map-to", default="")
    a = ap.parse_args()

    with open(a.catalog, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if a.pack:
        rows = [r for r in rows if a.pack.lower() in r["Pack"].lower()]
    rows = [r for r in rows if not SC.excluded(r["Path"], cfg)]

    done = set()
    if os.path.exists(a.out):
        with open(a.out, newline="", encoding="utf-8") as fh:
            done = {r["Path"] for r in csv.DictReader(fh)}
    todo = [r["Path"] for r in rows if r["Path"] not in done]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(rows)} in catalog, {len(done)} already analyzed, {len(todo)} to go, {a.workers} workers")
    if not todo:
        return

    def local(p):
        if a.map_from:
            p = p.replace(a.map_from, a.map_to).replace("\\", "/")
        return p

    new_file = not os.path.exists(a.out)
    t0 = time.time()
    with open(a.out, "a", newline="", encoding="utf-8") as fh, ProcessPoolExecutor(a.workers) as ex:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        futs = {ex.submit(analyze, local(p)): p for p in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            r["Path"] = futs[fut]  # always store the Windows path
            w.writerow(r)
            if n % 500 == 0 or n == len(todo):
                fh.flush()
                rate = n / (time.time() - t0)
                left = (len(todo) - n) / rate / 60 if rate else 0
                print(f"  {n} of {len(todo)}  ({rate:.1f} files/s, about {left:.0f} min left)")
    print(f"Done. Features in {a.out}")


if __name__ == "__main__":
    main()
