"""
Builds galaxy/data/galaxy_data.js (and galaxy_thumbs.js if waveforms were made)
from the library catalog and features.csv so index.html can show every sample
in 3D, with similarity islands, cluster names and duplicates.

  python make_galaxy.py
  python make_galaxy.py --samples C:\\HARDPAN\\studio_setup\\samples
"""
import argparse, csv, json, math, os, time
from collections import Counter, defaultdict
import numpy as np

FEATS = [  # name, label, how to read it from a features row
    ("bright", "Brightness", lambda r: math.log(max(f(r["centroid_hz"]), 20))),
    ("punch", "Punch", lambda r: f(r["crest_db"])),
    ("body", "Low end", lambda r: f(r["low_frac"]) + f(r["sub_frac"])),
    ("length", "Length", lambda r: math.log(max(f(r["dur_s"]), 0.01))),
    ("noise", "Noisiness", lambda r: math.sqrt(max(f(r["flatness"]), 0))),
    ("width", "Stereo width", lambda r: min(f(r["stereo_width"]), 1.5)),
    ("snap", "Snap (fast attack)", lambda r: -math.log(max(f(r["attack_ms"]), 1))),
    ("tempo", "Tempo", lambda r: f(r["tempo_bpm"]) or None),
]
KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
KEYS = KEYS + [k + "m" for k in KEYS]
PLURAL = {"kick": "kicks", "snare_clap": "snares and claps", "hat": "hats", "perc": "perc", "bass": "bass",
          "melodic": "melodic", "loop": "loops", "texture": "textures", "fx_noise": "FX and noise", "other": "other"}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def quantize(vals):
    present = sorted(v for v in vals if v is not None)
    if not present:
        return [0] * len(vals)
    lo = present[int(len(present) * 0.01)]
    hi = present[int(len(present) * 0.99) - 1] if len(present) > 1 else lo + 1
    span = (hi - lo) or 1.0
    return [0 if v is None else max(1, min(255, int(1 + 254 * (v - lo) / span))) for v in vals]


def note_midi(s):
    if not s:
        return -1
    name, octv = (s[:2], s[2:]) if len(s) > 1 and s[1] in "#b" else (s[:1], s[1:])
    if name not in KEYS[:12]:
        return -1
    try:
        return 12 * (int(octv) + 1) + KEYS.index(name)
    except ValueError:
        return -1


# ---------- similarity islands ----------
def assign(X, C):
    out, cc = np.empty(len(X), dtype=np.int32), (C ** 2).sum(1)
    for s in range(0, len(X), 20000):
        x = X[s:s + 20000]
        out[s:s + 20000] = (cc[None, :] - 2 * x @ C.T).argmin(1)
    return out


def kmeans(X, k, iters=25, seed=0):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)].copy()
    for _ in range(iters):
        lab = assign(X, C)
        sums = np.zeros_like(C)
        np.add.at(sums, lab, X)
        cnt = np.bincount(lab, minlength=k)
        empty = cnt == 0
        C[~empty] = sums[~empty] / cnt[~empty, None]
        C[empty] = X[rng.integers(len(X), size=empty.sum())]
    return assign(X, C), C


def tsne(X, dims=3, perp=20, iters=900, seed=1):
    n = len(X)
    D = ((X[:, None] - X[None]) ** 2).sum(-1)
    P, target = np.zeros((n, n)), math.log(perp)
    for i in range(n):
        di = np.delete(D[i], i)
        lo, hi, beta = 0.0, np.inf, 1.0
        for _ in range(64):
            p = np.exp(-(di - di.min()) * beta)
            s = p.sum()
            H = math.log(s) + beta * ((di - di.min()) * p).sum() / s
            if abs(H - target) < 1e-4:
                break
            if H > target:
                lo = beta
                beta = beta * 2 if hi == np.inf else (beta + hi) / 2
            else:
                hi = beta
                beta = (beta + lo) / 2
        P[i, np.arange(n) != i] = p / s
    P = np.maximum((P + P.T) / (2 * n), 1e-12)
    rng = np.random.default_rng(seed)
    Y, V = rng.normal(0, 1e-4, (n, dims)), np.zeros((n, dims))
    for it in range(iters):
        ex = 4.0 if it < 120 else 1.0
        sq = ((Y[:, None] - Y[None]) ** 2).sum(-1)
        num = 1 / (1 + sq)
        np.fill_diagonal(num, 0)
        Q = np.maximum(num / num.sum(), 1e-12)
        PQ = (ex * P - Q) * num
        grad = 4 * (PQ.sum(1)[:, None] * Y - PQ @ Y)
        V = (0.5 if it < 250 else 0.8) * V - 100 * grad
        Y += V
        Y -= Y.mean(0)
    return Y


def islands(feats, types, chars, packs_of, type_names, pack_names, k=360):
    keys = [k_ for k_, _, _ in FEATS if k_ != "tempo"]
    X = np.stack([np.array(feats[k_], dtype=np.float32) / 255 for k_ in keys], axis=1)
    T = np.zeros((len(types), len(type_names)), dtype=np.float32)
    T[np.arange(len(types)), types] = 0.55          # keeps islands mostly one kind of sound
    XT = np.concatenate([X, T], axis=1)
    k = min(k, max(8, len(X) // 50))
    lab, C = kmeans(XT, k)
    Y = tsne(C)
    Y = (Y - Y.min(0)) / (np.ptp(Y, axis=0) + 1e-9) * 200 - 100
    # members spread around their island center along the main ways sounds differ
    R = X - C[lab, :X.shape[1]]
    _, _, Vt = np.linalg.svd(R[:: max(1, len(R) // 20000)], full_matrices=False)
    off = R @ Vt[:3].T
    nd = np.sort(((Y[:, None] - Y[None]) ** 2).sum(-1) ** 0.5, axis=1)[:, 1]
    scale = 0.45 * np.median(nd) / (np.percentile(np.linalg.norm(off, axis=1), 95) + 1e-9)
    pos = Y[lab] + off * scale
    clusters = []
    for c in range(k):
        m = np.where(lab == c)[0]
        if not len(m):
            clusters.append({"label": "", "n": 0, "x": 0, "y": 0, "z": 0, "packs": []})
            continue
        tcount = Counter(types[i] for i in m)
        top_t = type_names[tcount.most_common(1)[0][0]]
        tags = Counter(t for i in m for t in chars[i].split() if t not in ("warm",))
        words = [t for t, n in tags.most_common(2) if n >= 0.3 * len(m)]
        pk = Counter(packs_of[i] for i in m).most_common(3)
        clusters.append({"label": " ".join(words + [PLURAL.get(top_t, top_t)]), "n": int(len(m)),
                         "x": round(float(Y[c, 0]), 1), "y": round(float(Y[c, 1]), 1), "z": round(float(Y[c, 2]), 1),
                         "packs": [[pack_names[p].split("|")[-1].strip(), n] for p, n in pk]})
    return lab, pos, clusters


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=r"C:\HARDPAN\studio_setup\samples")
    ap.add_argument("--out", default=os.path.join(here, "data", "galaxy_data.js"))
    a = ap.parse_args()
    lib = os.path.join(a.samples, "_library")
    t0 = time.time()

    cat = {}
    with open(os.path.join(lib, "catalog.csv"), newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            cat[r["Path"]] = r

    rows = []
    with open(os.path.join(lib, "features.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            name = r["Path"].rsplit("\\", 1)[-1]
            if r["error"] or name.startswith("._") or "powwow" in r["Path"].lower():
                continue
            rows.append(r)

    types, packs, dirs, chars, notes = {}, {}, {}, {}, {}
    idx = lambda d, k: d.setdefault(k, len(d))
    out = {"t": [], "p": [], "d": [], "n": [], "bpm": [], "key": [], "note": [], "ch": [], "bars": [], "midi": []}
    for r in rows:
        c = cat.get(r["Path"], {})
        d, name = r["Path"].rsplit("\\", 1)
        out["t"].append(idx(types, r["sound_guess"] or "other"))
        out["p"].append(idx(packs, (c.get("Source", "") + " | " + c.get("Pack", "")).strip(" |")))
        out["d"].append(idx(dirs, d))
        out["n"].append(name)
        out["bpm"].append(int(round(f(r["tempo_bpm"]))))
        out["key"].append(KEYS.index(r["key"]) if r["key"] in KEYS else -1)
        out["note"].append(idx(notes, r["note"]))
        out["ch"].append(idx(chars, r["character"]))
        out["bars"].append(int(f(r["bars"])))
        out["midi"].append(note_midi(r["note"]))

    feats = {}
    for key, label, fn in FEATS:
        vals = []
        for r in rows:
            try:
                vals.append(fn(r))
            except Exception:
                vals.append(None)
        feats[key] = quantize(vals)

    inv = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: kv[1])]
    type_names, pack_names, char_names = inv(types), inv(packs), inv(chars)
    print(f"read {len(rows)} sounds in {time.time() - t0:.0f}s, finding similarity islands...")

    lab, pos, clusters = islands(feats, out["t"], [char_names[c] for c in out["ch"]], out["p"], type_names, pack_names)
    print(f"  {len(clusters)} islands in {time.time() - t0:.0f}s")

    # duplicates: same length, level, peak and brightness to the decimal
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(r["dur_s"], r["rms_db"], r["peak_db"], r["centroid_hz"], r["sr"])].append(i)
    dup = [-1] * len(rows)
    ng = 0
    for g in groups.values():
        if len(g) > 1:
            for i in g:
                dup[i] = ng
            ng += 1
    print(f"  {ng} duplicate groups covering {sum(1 for d in dup if d >= 0)} files")

    data = {
        "count": len(rows), "types": type_names, "packs": pack_names, "dirs": inv(dirs),
        "notes": inv(notes), "chars": char_names, "keys": KEYS,
        "featLabels": {k: l for k, l, _ in FEATS}, "feats": feats, **out,
        "cl": lab.tolist(), "clusters": clusters, "dup": dup,
        "sx": np.round(pos[:, 0] * 10).astype(int).tolist(),
        "sy": np.round(pos[:, 1] * 10).astype(int).tolist(),
        "sz": np.round(pos[:, 2] * 10).astype(int).tolist(),
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("window.GALAXY = ")
        json.dump(data, fh, separators=(",", ":"))
        fh.write(";\n")
    print(f"{len(rows)} sounds -> {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")

    # waveform pictures, if make_thumbs.py has run
    cache = os.path.join(os.path.dirname(a.out), "thumbs_cache.tsv")
    if os.path.exists(cache):
        th = {}
        with open(cache, encoding="utf-8") as fh:
            for line in fh:
                p, _, h = line.rstrip("\n").partition("\t")
                th[p] = h
        blank = "0" * 64
        s = "".join(th.get(r["Path"], blank) for r in rows)
        with open(os.path.join(os.path.dirname(a.out), "galaxy_thumbs.js"), "w", encoding="utf-8") as fh:
            fh.write('window.THUMBS = "' + s + '";\n')
        print(f"waveforms: {sum(1 for r in rows if r['Path'] in th)} of {len(rows)}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
