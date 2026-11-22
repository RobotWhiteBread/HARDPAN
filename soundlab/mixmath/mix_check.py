"""
soundlab mix check
Measures a song or bounce and writes an HTML report with a spinnable 3D
spectrogram, loudness, tonal balance, low end, clipping and (with stems)
kick and bass clash.

  python mix_check.py song.wav
  python mix_check.py song.wav --refs refs     (compare to reference tracks)
  python mix_check.py song.wav --kick kick.wav --bass bass.wav          (clash check)

The report opens in your browser. Reference tracks are for comparison only and
never leave this machine.
"""
import argparse, json, math, os, sys, webbrowser
import numpy as np
import soundfile as sf
from scipy import signal

BANDS = [("Sub", 20, 60), ("Bass", 60, 250), ("Low mids", 250, 500), ("Mids", 500, 2000),
         ("Upper mids", 2000, 4000), ("Presence", 4000, 8000), ("Air", 8000, 16000)]
# Rough guide for a full modern mix: each band's share of total energy, in dB.
# Wide on purpose. Reference tracks replace this when you pass --refs.
GUIDE = {"Sub": (-14, -5), "Bass": (-6, -1), "Low mids": (-13, -7), "Mids": (-14, -7),
         "Upper mids": (-21, -13), "Presence": (-25, -15), "Air": (-33, -20)}


def load(path, mono=False):
    y, sr = sf.read(path, dtype="float64", always_2d=True)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    y = y[:, :2]
    return (y.mean(axis=1) if mono else y), sr


# ---------- loudness (ITU-R BS.1770 / EBU R128) ----------
def k_weight(y, sr):
    # high shelf
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    y = signal.lfilter(b, a, y, axis=0)
    # high pass
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    a = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return signal.lfilter([1, -2, 1], a, y, axis=0)


def block_loudness(yk, sr, win, hop):
    n, h = int(win * sr), int(hop * sr)
    out = []
    for s in range(0, max(1, len(yk) - n + 1), h):
        z = (yk[s:s + n] ** 2).mean(axis=0).sum()
        out.append(-0.691 + 10 * math.log10(max(z, 1e-12)))
    return np.array(out)


def integrated(yk, sr):
    L = block_loudness(yk, sr, 0.4, 0.1)
    L = L[L > -70]
    if not L.size:
        return -70.0
    rel = 10 * math.log10(np.mean(10 ** (L / 10))) - 10
    L = L[L > rel]
    return float(10 * math.log10(np.mean(10 ** (L / 10))))


def loudness_range(st):
    st = st[st > -70]
    if st.size < 4:
        return 0.0
    rel = 10 * math.log10(np.mean(10 ** (st / 10))) - 20
    st = np.sort(st[st > rel])
    return float(st[int(len(st) * 0.95) - 1] - st[int(len(st) * 0.10)])


def true_peak(y):
    up = signal.resample_poly(y, 4, 1, axis=0)
    return 20 * math.log10(max(np.abs(up).max(), 1e-9))


# ---------- spectrum ----------
def band_db(mono, sr):
    f, P = signal.welch(mono, sr, nperseg=8192)
    tot = P[(f >= 20) & (f <= 16000)].sum() + 1e-20
    return {n: 10 * math.log10(P[(f >= lo) & (f < hi)].sum() / tot + 1e-12) for n, lo, hi in BANDS}


def spectrogram(mono, sr, t_bins=220, f_bins=110):
    f, t, Z = signal.stft(mono, sr, nperseg=4096, noverlap=3072)
    mag = 20 * np.log10(np.abs(Z) + 1e-9)
    edges = np.geomspace(30, min(16000, sr / 2 - 1), f_bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (f >= lo) & (f < hi)
        rows.append(mag[sel].max(axis=0) if sel.any() else mag[np.argmin(abs(f - lo))])
    S = np.array(rows)  # freq x time
    cols = np.array_split(np.arange(S.shape[1]), min(t_bins, S.shape[1]))
    S = np.stack([S[:, c].max(axis=1) for c in cols], axis=1)
    top = np.percentile(S, 99.5)
    S = np.clip(S - top + 70, 0, 70) / 70.0  # 0..1 over a 70 dB window
    return {"z": np.round(S, 3).tolist(), "freqs": np.round(edges[:-1]).tolist(),
            "seconds": round(len(mono) / sr, 2)}


# ---------- stereo ----------
def low_mono_corr(y, sr, cutoff=120):
    sos = signal.butter(4, cutoff, "low", fs=sr, output="sos")
    lo = signal.sosfilt(sos, y, axis=0)
    l, r = lo[:, 0], lo[:, 1]
    d = math.sqrt((l * l).sum() * (r * r).sum())
    return float((l * r).sum() / d) if d > 0 else 1.0


def width(y):
    m, s = (y[:, 0] + y[:, 1]) / 2, (y[:, 0] - y[:, 1]) / 2
    return float(math.sqrt((s * s).mean()) / (math.sqrt((m * m).mean()) + 1e-12))


# ---------- kick and bass clash ----------
def clash(kick, bass, sr):
    f, t, K = signal.stft(kick, sr, nperseg=4096, noverlap=3072)
    _, _, B = signal.stft(bass, sr, nperseg=4096, noverlap=3072)
    n = min(K.shape[1], B.shape[1])
    K, B = np.abs(K[:, :n]) ** 2, np.abs(B[:, :n]) ** 2
    sel = (f >= 30) & (f <= 250)
    f, K, B = f[sel], K[sel], B[sel]
    kthr, bthr = K.max() * 10 ** (-30 / 10), B.max() * 10 ** (-30 / 10)
    both = (K > kthr) & (B > bthr)
    overlap = both.sum(axis=1) / max(n, 1)
    worst = int(np.argmax(overlap * np.minimum(K.mean(axis=1), B.mean(axis=1))))
    kpk, bpk = float(f[np.argmax(K.sum(axis=1))]), float(f[np.argmax(B.sum(axis=1))])
    return {"overlap_pct": round(float(both.any(axis=0).mean() * 100), 1),
            "worst_hz": round(float(f[worst])), "kick_peak_hz": round(kpk), "bass_peak_hz": round(bpk)}


# ---------- verdicts ----------
def verdict(ok, warn=False):
    return "good" if ok else ("watch" if warn else "fix")


def analyze(path, refs=None, kick=None, bass=None):
    y, sr = load(path)
    was_mono = sf.info(path).channels == 1
    mono = y.mean(axis=1)
    yk = k_weight(y[:, :1] if was_mono else y, sr)
    lufs = integrated(yk, sr)
    st = block_loudness(yk, sr, 3.0, 0.5)
    tp = true_peak(y)
    peak = 20 * math.log10(max(np.abs(y).max(), 1e-9))
    clips = int((np.abs(y) >= 0.999).sum())
    plr = tp - lufs
    lmc = low_mono_corr(y, sr)
    wid = width(y)
    bands = band_db(mono, sr)

    target, tsrc = GUIDE, "rough guide"
    if refs:
        vals = {}
        for rp in refs:
            try:
                rm, rsr = load(rp, mono=True)
                for k, v in band_db(rm, rsr).items():
                    vals.setdefault(k, []).append(v)
            except Exception as e:
                print("skipped reference", rp, e)
        if vals:
            target = {k: (min(v) - 1.5, max(v) + 1.5) for k, v in vals.items()}
            tsrc = f"{len(next(iter(vals.values())))} reference track(s)"

    checks = []
    checks.append(("Loudness", f"{lufs:.1f} LUFS",
                   verdict(-11 <= lufs <= -6, -14 <= lufs < -11),
                   "Loud hip hop and pop masters usually land around -9 to -7 LUFS. Streaming turns everything down to about -14, so louder is not better past that. Quieter is fine for a demo or a dynamic track."))
    checks.append(("True peak", f"{tp:.1f} dBTP", verdict(tp <= -1.0, tp <= 0.0),
                   "Keep at or below -1 dBTP so streaming encoders do not distort it. A limiter with a -1 dB ceiling fixes this."))
    checks.append(("Clipped samples", f"{clips:,}", verdict(clips == 0, clips < 50),
                   "Samples stuck at full scale. Turn the master down or use a limiter instead of clipping."))
    checks.append(("Punch (peak to loudness)", f"{plr:.1f} dB", verdict(8 <= plr <= 14, 6 <= plr < 8),
                   "Below about 7 dB the mix is squashed and the drums lose their hit. 8 to 12 dB is typical for punchy commercial mixes."))
    lra = loudness_range(st)
    checks.append(("Loudness range", f"{lra:.1f} LU", verdict(3 <= lra <= 12, lra < 3),
                   "How much the song breathes between quiet and loud parts. Very low means every section is the same level."))
    checks.append(("Low end in mono", f"{lmc:.2f}", verdict(lmc >= 0.8, lmc >= 0.5),
                   "Correlation of left and right below 120 Hz. Under 0.8 the bass and kick will thin out on mono speakers and clubs. Keep low parts centered."))
    checks.append(("Stereo width", f"{wid:.2f}", verdict(0.15 <= wid <= 0.7, wid < 0.15),
                   "Side vs middle energy. Near 0 is mono; above about 0.8 can sound hollow."))

    tonal = []
    for name, lo, hi in BANDS:
        v = bands[name]
        tlo, thi = target[name]
        tonal.append({"band": name, "db": round(v, 1), "lo": round(tlo, 1), "hi": round(thi, 1),
                      "state": "good" if tlo <= v <= thi else ("high" if v > thi else "low")})

    out = {"file": os.path.basename(path), "sr": sr, "seconds": round(len(y) / sr, 1),
           "peak": round(peak, 1), "checks": checks, "tonal": tonal, "tonal_source": tsrc,
           "curve": [round(float(v), 1) for v in st], "curve_hop": 0.5,
           "spec": spectrogram(mono, sr)}
    if kick and bass:
        k, ksr = load(kick, mono=True)
        b, bsr = load(bass, mono=True)
        if ksr != bsr:
            b = signal.resample_poly(b, ksr, bsr)
        out["clash"] = clash(k, b, ksr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--refs", help="folder of reference tracks (wav, flac, aif)")
    ap.add_argument("--kick")
    ap.add_argument("--bass")
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    refs = None
    if a.refs:
        refs = [os.path.join(a.refs, n) for n in sorted(os.listdir(a.refs))
                if n.lower().endswith((".wav", ".flac", ".aif", ".aiff"))]
    data = analyze(a.file, refs, a.kick, a.bass)
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = open(os.path.join(here, "report_template.html"), encoding="utf-8").read()
    html = tpl.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    if not a.out:                               # reports go to <projects>/mix_checks, never into sample folders
        try:
            sys.path.insert(0, os.path.dirname(here))
            import soundlab_config as SC
            base = os.path.join(SC.load(required=False)["projects"], "mix_checks")
        except Exception:
            base = os.getcwd()
        os.makedirs(base, exist_ok=True)
    out = a.out or os.path.join(base, os.path.splitext(os.path.basename(a.file))[0] + "_mixcheck.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    for name, val, state, _ in data["checks"]:
        print(f"  {name:28s} {val:>14s}   {state}")
    print("Report:", out)
    if not a.no_open:
        webbrowser.open("file:///" + os.path.abspath(out).replace("\\", "/"))


if __name__ == "__main__":
    main()
