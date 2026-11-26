"""
Builds galaxy/data/galaxy_data.js (and galaxy_thumbs.js if waveforms were made)
from the library catalog and features.csv so index.html can show every sample
in 3D, with similarity islands, cluster names and duplicates.

  python make_galaxy.py
"""
import argparse, csv, json, math, os, re, sys, time
from collections import Counter, defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import soundlab_config as SC

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


# Inside a big tie (many sounds measured exactly the same), order sounds by a related measure
# so the tie becomes a band instead of a flat wall: (other measure, direction, wording for the page)
TIEBREAK = {"body": ("bright", -1, "brightest at the edge"), "snap": ("punch", 1, "punchiest at the edge")}


def rank_quantize(vals, second=None):
    """1..255 by rank, so sounds bunched at one end of a scale still spread out.
    Sounds equal on both the measure and the tie-break measure share a rank."""
    keep = [i for i, v in enumerate(vals) if v is not None]
    out = [0] * len(vals)
    if not keep:
        return out
    prim = np.array([vals[i] for i in keep], dtype=float)
    sec = np.array([second[i] if second and second[i] is not None else 0.0 for i in keep], dtype=float)
    order = np.lexsort((sec, prim))
    sp, ss = prim[order], sec[order]
    new = np.r_[True, (sp[1:] != sp[:-1]) | (ss[1:] != ss[:-1])]
    gid = np.cumsum(new) - 1
    starts = np.flatnonzero(new)
    ends = np.r_[starts[1:], len(sp)] - 1
    avg = (starts + ends) / 2.0
    q = np.empty(len(sp), dtype=int)
    q[order] = 1 + np.round(254 * avg[gid] / max(len(sp) - 1, 1)).astype(int)
    for k, i in enumerate(keep):
        out[i] = int(q[k])
    return out


def tie_bands(vals, shown, n):
    """Values shared by more than 1% of sounds: where they sit on the page's 1..255 scale."""
    cnt = Counter(v for v in vals if v is not None)
    bands = []
    for v, c in cnt.most_common(4):
        if c < 0.01 * n:
            break
        qs = [shown[i] for i, x in enumerate(vals) if x == v]
        bands.append([min(qs), max(qs), c])
    return bands


def vibe_matrix(rows):
    """7 measures per sound, standardized, for family and bridge matching."""
    X = np.array([[math.log(max(f(r["centroid_hz"]), 20)), f(r["crest_db"]), f(r["low_frac"]) + f(r["sub_frac"]),
                   math.log(max(f(r["dur_s"]), 0.01)), math.sqrt(max(f(r["flatness"]), 0)),
                   min(f(r["stereo_width"]), 1.5), -math.log(max(f(r["attack_ms"]), 1))] for r in rows], dtype=np.float32)
    return (X - X.mean(0)) / (X.std(0) + 1e-9)


def families(rows, X, pack_of, eps=0.03, min_overlap=0.4):
    """Near-identical versions of one sound inside a pack (tape, saturated, 15 ips, pitched copies).
    A sound joins a family when it measures within eps of the family's first member and shares
    at least min_overlap of the words in its file name. Returns the family leader for every sound."""
    words = lambda n: set(re.findall(r"[a-z]+", n.lower().rsplit(".", 1)[0]))
    names = [SC.basename(r["Path"]) for r in rows]
    W = [words(n) for n in names]
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(pack_of[i], r["sound_guess"])].append(i)
    fam = list(range(len(rows)))
    for g in groups.values():
        g.sort(key=lambda i: (len(names[i]), names[i]))          # the plainest name leads its family
        leaders, LX = [], np.zeros((0, X.shape[1]), dtype=np.float32)
        for i in g:
            if leaders:
                d = ((LX - X[i]) ** 2).sum(1)
                for j in np.argsort(d)[:5]:
                    if d[j] >= eps:
                        break
                    a, b = W[i], W[leaders[j]]
                    if a and b and len(a & b) / len(a | b) >= min_overlap:
                        fam[i] = leaders[j]
                        break
                if fam[i] != i:
                    continue
            leaders.append(i)
            LX = np.vstack([LX, X[i:i + 1]])
    return fam


def bridges(rows, X, source_of):
    """For every sound, the closest-measuring sound of the same type from a different vendor."""
    near, dist = np.full(len(rows), -1, dtype=np.int64), np.full(len(rows), np.inf, dtype=np.float32)
    by_type = defaultdict(list)
    for i, r in enumerate(rows):
        by_type[r["sound_guess"]].append(i)
    sq = (X ** 2).sum(1)
    for idx in by_type.values():
        idx = np.array(idx)
        src = np.array([source_of[i] for i in idx])
        for s_ in set(src):
            a, b = idx[src == s_], idx[src != s_]
            if not len(b):
                continue
            for k in range(0, len(a), 1500):
                aa = a[k:k + 1500]
                d = sq[aa, None] - 2 * X[aa] @ X[b].T + sq[None, b]
                j = d.argmin(1)
                near[aa] = b[j]
                dist[aa] = np.maximum(d[np.arange(len(aa)), j], 0)
    return near, dist


def project_use(cfg, rows):
    """Which library sounds each REAPER project uses: {sound index: [project numbers]}."""
    idx = {r["Path"].lower(): i for i, r in enumerate(rows)}
    skip = {"backups", "_upgrades"}
    names, use, seen = [], defaultdict(list), 0
    for root in cfg.get("project_folders") or [cfg["projects"]]:
        root = os.path.normpath(os.path.expanduser(root))
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d.lower() not in skip and not d.startswith(".")]
            for n in sorted(fn):
                if not n.lower().endswith(".rpp"):
                    continue
                p = os.path.join(dp, n)
                if SC.excluded(p, cfg) or any(re.search(rx, n, re.I) for rx in cfg.get("exclude_projects", [])):
                    continue
                seen += 1
                hits = set()
                for line in open(p, encoding="utf-8", errors="replace"):
                    t = line.strip()
                    if t.startswith('FILE "'):
                        j = idx.get(t[6:t.rindex('"')].lower())
                        if j is not None:
                            hits.add(j)
                if hits:
                    k = len(names)
                    names.append(os.path.relpath(p, root)[:-4].replace("\\", "/"))
                    for j in hits:
                        use[j].append(k)
    return seen, names, {str(j): v for j, v in use.items()}


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
    cfg = SC.load()
    ap.add_argument("--out", default=os.path.join(here, "data", "galaxy_data.js"))
    a = ap.parse_args()
    lib = cfg["library"]
    t0 = time.time()

    cat = {}
    with open(os.path.join(lib, "catalog.csv"), newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            cat[r["Path"]] = r

    rows = []
    with open(os.path.join(lib, "features.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            name = SC.basename(r["Path"])
            if r["error"] or name.startswith("._") or SC.excluded(r["Path"], cfg):
                continue
            rows.append(r)

    types, packs, dirs, chars, notes = {}, {}, {}, {}, {}
    idx = lambda d, k: d.setdefault(k, len(d))
    out = {"t": [], "p": [], "d": [], "n": [], "bpm": [], "key": [], "note": [], "ch": [], "bars": [], "midi": []}
    for r in rows:
        c = cat.get(r["Path"], {})
        cut = max(r["Path"].rfind("\\"), r["Path"].rfind("/")) + 1
        d, name = r["Path"][:cut], r["Path"][cut:]          # folder keeps its trailing separator
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

    raw = {}
    for key, label, fn in FEATS:
        vals = []
        for r in rows:
            try:
                vals.append(fn(r))
            except Exception:
                vals.append(None)
        raw[key] = vals
    feats, shown, bands = {}, {}, {}
    for key in raw:
        feats[key] = quantize(raw[key])      # islands
        tb = TIEBREAK.get(key)               # what the page shows: rank, ties ordered by a related measure
        second = [None if v is None else tb[1] * v for v in raw[tb[0]]] if tb else None
        shown[key] = rank_quantize(raw[key], second)
        bands[key] = tie_bands(raw[key], shown[key], len(rows))
    # raw value at each of 256 quantiles, so the page can place a dropped-in file on the same rank scales
    featQ = {}
    for key in raw:
        v = np.array(sorted(x for x in raw[key] if x is not None), dtype=float)
        featQ[key] = [round(float(v[min(len(v) - 1, int(q * (len(v) - 1)))]), 5) for q in np.linspace(0, 1, 256)] if len(v) else []

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

    X = vibe_matrix(rows)
    packs_of = [pack_names[p] for p in out["p"]]
    fam = families(rows, X, packs_of)
    nfam = sum(1 for i, l in enumerate(fam) if i == l)
    print(f"  {nfam} sound families (near-identical versions grouped) from {len(rows)} sounds")
    srcs = [pack_names[p].split("|")[0].strip() for p in out["p"]]
    bnear, bdist = bridges(rows, X, srcs)
    print(f"  vendor bridges found for {int((bnear >= 0).sum())} sounds")
    n_proj, proj_names, use = project_use(cfg, rows)
    print(f"  {len(use)} sounds used across {len(proj_names)} of {n_proj} REAPER projects")

    data = {
        "count": len(rows), "types": type_names, "packs": pack_names, "dirs": inv(dirs),
        "notes": inv(notes), "chars": char_names, "keys": KEYS,
        "featLabels": {k: l for k, l, _ in FEATS}, "feats": shown, "tieBands": bands, "featQ": featQ,
        "mine": sorted(set(["Mine"] + list(cfg.get("public_allow", [])) + list(cfg.get("mine", [])))),
        "tieBreak": {k: [G_, w] for k, (G_, _, w) in TIEBREAK.items()}, **out,
        "projects": proj_names, "projectsScanned": n_proj, "use": use,
        "fam": fam, "br": bnear.tolist(),
        "brd": [255 if not np.isfinite(d) else int(min(255, round(math.sqrt(d) * 100))) for d in bdist],
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
