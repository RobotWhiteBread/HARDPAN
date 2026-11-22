"""
soundlab project audit
Reads every REAPER project, finds the samples each one uses, checks key and
tempo fit, and picks two alternatives from the library for every sample.

Writes, next to the projects:
  _upgrades\\<project>.tsv     alternatives for Soundlab_Upgrade_Project.lua
  _upgrades\\audit.csv         one row per project (open in Excel)
  _upgrades\\projects.js       which sounds each project uses (for the Sound Galaxy)

  python project_audit.py
"""
import argparse, csv, json, math, os, re
from collections import Counter, defaultdict
import numpy as np
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import soundlab_config as SC

KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
KEYS = KEYS + [k + "m" for k in KEYS]
TONAL = {"bass", "melodic", "loop"}
VIBE = ["centroid_hz", "crest_db", "low", "dur_s", "flatness", "stereo_width", "attack_ms"]


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def key_compat(a, b):
    if a < 0 or b < 0:
        return False
    am, bm, ar, br = a >= 12, b >= 12, a % 12, b % 12
    if am == bm:
        return ar == br or (ar + 7) % 12 == br or (br + 7) % 12 == ar
    rel = lambda r, minor: (r + 3) % 12 if minor else r     # minor key -> its relative major
    return rel(ar, am) == rel(br, bm)


def tempo_compat(a, b, tol=0.06):
    return bool(a and b) and any(abs(a * m - b) / b <= tol for m in (1, 2, 0.5))


def parse_rpp(path):
    tempo, files, tracks = 0.0, [], []
    track = None
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if s.startswith("TEMPO ") and not tempo:
            tempo = fl(s.split()[1])
        elif s.startswith("<TRACK"):
            track = "track"
        elif s.startswith("NAME ") and track == "track":
            track = s[5:].strip().strip('"')
            tracks.append(track)
        elif s.startswith('FILE "'):
            files.append((track or "", s[6:s.rindex('"')]))
    return tempo, files


def main():
    ap = argparse.ArgumentParser()
    cfg = SC.load()
    ap.add_argument("--projects", default=cfg["projects"])
    ap.add_argument("--features", default=SC.lib_file("features.csv", cfg))
    ap.add_argument("--catalog", default=SC.lib_file("catalog.csv", cfg))
    ap.add_argument("--map-from", default="")   # testing on another machine
    ap.add_argument("--map-to", default="")
    a = ap.parse_args()
    local = lambda p: p.replace(a.map_from, a.map_to).replace("\\", "/") if a.map_from else p
    out_dir = os.path.join(local(a.projects), "_upgrades")
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    with open(local(a.features), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["error"] or SC.basename(r["Path"]).startswith("._") or SC.excluded(r["Path"], cfg):
                continue
            rows.append(r)
    idx = {r["Path"].lower(): i for i, r in enumerate(rows)}
    raw = np.array([[math.log(max(fl(r["centroid_hz"]), 20)), fl(r["crest_db"]), fl(r["low_frac"]) + fl(r["sub_frac"]),
                     math.log(max(fl(r["dur_s"]), 0.01)), math.sqrt(max(fl(r["flatness"]), 0)),
                     min(fl(r["stereo_width"]), 1.5), -math.log(max(fl(r["attack_ms"]), 1))] for r in rows])
    lo, hi = np.percentile(raw, 1, axis=0), np.percentile(raw, 99, axis=0)
    X = np.clip((raw - lo) / (hi - lo + 1e-9), 0, 1)
    typ = np.array([r["sound_guess"] for r in rows])
    # the pack makers' labels (in file and folder names) beat the audio guesses
    named, src_of = {}, {}
    with open(local(a.catalog), newline="", encoding="utf-8-sig") as fh:
        for c in csv.DictReader(fh):
            if c["BPM"] or c["Key"]:
                named[c["Path"]] = (c["BPM"], c["Key"])
            src_of[c["Path"]] = (c["Source"], c["Pack"])
    def nkey(r):
        k = named.get(r["Path"], ("", ""))[1].strip()
        k = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}.get(k[:2], k[:2] if len(k) > 1 and k[1] in "#b" else k[:1]) + ("m" if k.endswith("m") or "min" in k.lower() else "") if k else ""
        return KEYS.index(k) if k in KEYS else (KEYS.index(r["key"]) if r["key"] in KEYS else -1)
    key = np.array([nkey(r) for r in rows])
    bpm = np.array([fl(named.get(r["Path"], ("", ""))[0]) or fl(r["tempo_bpm"]) for r in rows])
    print(f"{sum(1 for r in rows if r['Path'] in named)} sounds carry a tempo or key in their name")
    lofi = np.array(["lofi" in r["character"] for r in rows])
    sig = [(r["dur_s"], r["rms_db"], r["peak_db"], r["centroid_hz"]) for r in rows]
    by_type = defaultdict(list)
    for i, t in enumerate(typ):
        by_type[t].append(i)
    by_type = {t: np.array(v) for t, v in by_type.items()}
    pack_of = lambda p: src_of.get(p, ("", SC.dirname(p)))
    source_of = lambda p: src_of.get(p, ("", ""))[0]

    def alternatives(i, pkey, ptempo, used):
        cand = by_type[typ[i]]
        d = ((X[cand] - X[i]) ** 2).sum(1)
        d = d + np.where(lofi[cand] != lofi[i], 0.15, 0)
        if typ[i] in TONAL and pkey >= 0:
            d = d + np.array([0 if key_compat(pkey, key[j]) else 0.6 for j in cand])
        if typ[i] == "loop" and ptempo:
            d = d + np.array([0 if tempo_compat(ptempo, bpm[j], 0.12) else 0.6 for j in cand])
        order = cand[np.argsort(d)]
        picks = []
        for want_fresh in (False, True):
            for j in order[:4000]:
                if j == i or j in used or sig[j] == sig[i] or j in picks:
                    continue
                if want_fresh and source_of(rows[j]["Path"]) == source_of(rows[i]["Path"]):
                    continue                    # second pick: from a different sample source altogether
                if not want_fresh and pack_of(rows[j]["Path"]) == pack_of(rows[i]["Path"]):
                    continue                    # first pick: same feel from a different pack
                picks.append(j)
                break
            if not want_fresh and not picks:    # small library: settle for the closest sound in the same pack
                for j in order[:50]:
                    if j != i and j not in used and sig[j] != sig[i]:
                        picks.append(j)
                        break
        return picks

    audit, gal, totals = [], {}, Counter()
    names = sorted(n for n in os.listdir(local(a.projects)) if n.lower().endswith(".rpp"))
    for name in names:
        tempo, files = parse_rpp(os.path.join(local(a.projects), name))
        used = {}
        outside = 0
        for track, f in files:
            j = idx.get(f.lower())
            if j is None:
                outside += 1
            else:
                used.setdefault(j, track)
        # only loops and stems carry a key; a one-shot note fits many keys
        tonal = [j for j in used if key[j] >= 0 and named.get(rows[j]["Path"], ("", ""))[1].endswith("m") or
                 (key[j] >= 0 and named.get(rows[j]["Path"], ("", ""))[0] and named.get(rows[j]["Path"], ("", ""))[1])]
        pkey = Counter(key[j] for j in tonal).most_common(1)[0][0] if tonal else -1
        clashes = sorted({SC.basename(rows[j]["Path"]) for j in tonal if pkey >= 0 and not key_compat(pkey, key[j])})
        stretched = sorted({SC.basename(rows[j]["Path"]) for j in used
                            if typ[j] == "loop" and bpm[j] and tempo and not tempo_compat(tempo, bpm[j], 0.12)})
        packs = Counter(source_of(rows[j]["Path"]) or "other" for j in used)
        totals.update(packs)
        with open(os.path.join(out_dir, os.path.splitext(name)[0] + ".tsv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("original\ttype\talt\talt_bpm\talt_type\n")
            taken = set(used)
            for j, track in used.items():
                for k in alternatives(j, pkey, tempo, taken):
                    taken.add(k)
                    fh.write(f"{rows[j]['Path']}\t{typ[j]}\t{rows[k]['Path']}\t{bpm[k] or ''}\t{typ[k]}\n")
        audit.append({"project": name, "tempo": tempo, "key": KEYS[pkey] if pkey >= 0 else "",
                      "library_sounds": len(used), "other_audio": outside,
                      "sources_used": "; ".join(f"{k} {v}" for k, v in packs.most_common()),
                      "key_clashes": "; ".join(clashes[:6]), "loops_far_off_tempo": "; ".join(stretched[:6])})
        gal[name[:-4]] = [rows[j]["Path"] for j in used]

    with open(os.path.join(out_dir, "audit.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(audit[0].keys()))
        w.writeheader()
        w.writerows(audit)
    with open(os.path.join(out_dir, "projects.js"), "w", encoding="utf-8") as fh:
        fh.write("window.PROJECTS = " + json.dumps(gal, separators=(",", ":")) + ";\n")
    n_alt = sum(len(v) for v in gal.values())
    print(f"{len(names)} projects, {n_alt} library sounds in use, alternatives written to {out_dir}")
    clash_n = sum(1 for r in audit if r["key_clashes"])
    tempo_n = sum(1 for r in audit if r["loops_far_off_tempo"])
    print(f"  {clash_n} projects have a sound in a clashing key, {tempo_n} have a loop stretched more than 12%")
    print("  sounds used by source: " + ", ".join(f"{k} {v}" for k, v in totals.most_common()))
    unused = [s["name"] for s in cfg["sample_folders"] if s["name"] not in totals]
    if unused:
        print("  never used in a project yet: " + ", ".join(sorted(set(unused))))


if __name__ == "__main__":
    main()
