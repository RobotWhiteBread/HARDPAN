"""
Mars remixes: copies of your songs with the drums rebuilt from Samples From Mars.
  * every drum hit (kick, snare, clap, hat, perc) goes to its closest Mars hit
  * every break or drum loop goes to a Mars drum loop near the same tempo, and
    each chop is re-timed so it still lands on the same beat
Loops of keys, stems and your own recordings stay as they are. Originals are
never touched; copies go to projects\\remix_mars\\<song>_mars.rpp.

  python remix_projects.py              (the default set of songs)
  python remix_projects.py --all        (every song)
"""
import argparse, csv, math, os, random, re
import numpy as np
import soundfile as sf

DEFAULT = ["afterdark2_satin_sheets-1", "atmos1_overcast-1", "dance5_bring_it_back-1", "deltron1_epic-1",
           "dilla_monks-1", "jazz_hop_pines-1", "lucy_ford_take2_porch_light-1", "nightscore2_glass_room-1",
           "spacecowboy1_saucer_sandhills-1", "machine2_power_grid-1", "octagon_giants-1", "aesop1_blockhead-1"]
DRUMWORDS = r"kick|snare|clap|hat|\bhh\b|\bbd\b|\bsd\b|perc|drum|break|beat|groove|shaker|tamb|conga|bongo|tom\b|rim|cymbal|ride|crash"
MELODIC = r"melod|keys|piano|rhodes|organ|guitar|bass(?! ?drum)|synth|pad|string|vocal|vox|chord|stem|horn|brass|sax|flute"


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def name_bpm(path):
    name = path.rsplit("\\", 1)[-1]
    for pat in (r"(\d{2,3})\s?bpm", r"(?:^|[ _\-\[])(\d{2,3})(?:\s?bpm)?[\]\s]*\.(?:wav|aiff?)$"):
        m = re.search(pat, name, re.I)
        if m and 60 <= fl(m.group(1)) <= 180:
            return fl(m.group(1))
    m = re.search(r"(\d{2,3})\s?bpm", path, re.I)        # folder like "91 BPM Am Smoker"
    return fl(m.group(1)) if m and 60 <= fl(m.group(1)) <= 180 else 0.0


def role(path):
    tail = "\\".join(path.lower().rsplit("\\", 3)[-3:])
    looped = re.search(r"loop|break|\bbeat|groove|\d{2,3} ?bpm", tail)
    if looped:
        if re.search(DRUMWORDS, tail) and not re.search(MELODIC, tail):
            return "drum_loop"
        return None
    if re.search(r"kick|bass drum|\\bd|\bbd\b", tail):
        return "kick"
    if re.search(r"snare|clap|\bsd\b|\\sd|side ?stick|rim", tail):
        return "snare_clap"
    if re.search(r"hi ?hat|\bhh\b|\\ch|\\oh|\bch\b|\boh\b|\bhats?\b|open hat|closed", tail):
        return "hat"
    if re.search(r"perc|shaker|tamb|conga|bongo|\btoms?\b|cowbell|clave|cabasa|block|cymbal|ride|crash", tail):
        return "perc"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=r"C:\HARDPAN\studio_setup\projects")
    ap.add_argument("--lib", default=r"C:\HARDPAN\studio_setup\samples\_library")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--map-from", default="")
    ap.add_argument("--map-to", default="")
    a = ap.parse_args()
    local = lambda p: p.replace(a.map_from, a.map_to).replace("\\", "/") if a.map_from else p
    pdir = local(a.projects)
    out_dir = os.path.join(pdir, "remix_mars")
    os.makedirs(out_dir, exist_ok=True)

    named = {}
    with open(local(a.lib + "\\catalog.csv"), newline="", encoding="utf-8-sig") as fh:
        for c in csv.DictReader(fh):
            if c["BPM"]:
                named[c["Path"].lower()] = fl(c["BPM"])
    rows, seen = [], set()
    with open(local(a.lib + "\\features.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["error"] or r["Path"].rsplit("\\", 1)[-1].startswith("._") or "powwow" in r["Path"].lower():
                continue
            rows.append(r)
    lookup = {r["Path"].lower(): i for i, r in enumerate(rows)}
    raw = np.array([[math.log(max(fl(r["centroid_hz"]), 20)), fl(r["crest_db"]), fl(r["low_frac"]) + fl(r["sub_frac"]),
                     math.log(max(fl(r["dur_s"]), 0.01)), math.sqrt(max(fl(r["flatness"]), 0)),
                     min(fl(r["stereo_width"]), 1.5), -math.log(max(fl(r["attack_ms"]), 1))] for r in rows])
    lo, hi = np.percentile(raw, 1, axis=0), np.percentile(raw, 99, axis=0)
    X = np.clip((raw - lo) / (hi - lo + 1e-9), 0, 1)
    bpm = np.array([named.get(r["Path"].lower()) or name_bpm(r["Path"]) or fl(r["tempo_bpm"]) for r in rows])

    pools = {}
    for i, r in enumerate(rows):
        if "\\mars\\" not in r["Path"].lower():
            continue
        sig = (r["dur_s"], r["rms_db"], r["peak_db"], r["centroid_hz"])
        if sig in seen:
            continue
        seen.add(sig)
        ro = role(r["Path"])
        if ro == "drum_loop" and not (named.get(r["Path"].lower()) or name_bpm(r["Path"])):
            continue                                     # only loops whose tempo is labeled
        if ro:
            pools.setdefault(ro, []).append(i)
    pools = {k: np.array(v) for k, v in pools.items()}
    print("Mars pools: " + ", ".join(f"{k} {len(v)}" for k, v in pools.items()))

    def match(i, ro, rnd, taken):
        cand = pools.get(ro)
        if cand is None or not len(cand):
            return None
        d = ((X[cand] - X[i]) ** 2).sum(1)
        if ro == "drum_loop":
            b0 = bpm[i]
            if not b0:
                return None
            ratio = np.array([min(abs(b0 / bpm[j] - 1), abs(b0 / (bpm[j] * 2) - 1), abs(b0 * 2 / bpm[j] - 1)) for j in cand])
            d = d + np.where(ratio <= 0.08, 0, 10) + ratio * 3
        order = [j for j in cand[np.argsort(d)][:6] if j not in taken] or list(cand[np.argsort(d)][:1])
        return int(order[min(len(order) - 1, rnd.randrange(3))])

    names = sorted(n[:-4] for n in os.listdir(pdir) if n.endswith("-1.rpp")) if a.all else DEFAULT
    names = [n for n in names if not n.startswith("spirit")]
    total_hits = total_loops = 0
    for name in names:
        rpp = os.path.join(pdir, name + ".rpp")
        if not os.path.exists(rpp):
            print(f"  skip {name}: not found")
            continue
        rnd = random.Random(name)
        lines = open(rpp, encoding="utf-8", errors="replace").read().split("\n")
        swap, taken = {}, set()
        for ln in lines:
            m = re.search(r'FILE "([^"]+)"', ln)
            if not m or m.group(1).lower() in swap:
                continue
            f = m.group(1)
            i = lookup.get(f.lower())
            ro = role(f)
            if i is None or ro is None:
                continue
            j = match(i, ro, rnd, taken)
            if j is None:
                continue
            taken.add(j)
            b0 = named.get(f.lower()) or name_bpm(f) or bpm[i]
            b1 = bpm[j]
            if ro == "drum_loop":
                for m_ in (1, 2, 0.5):                  # use the half or double tempo when that is closer
                    if abs(b0 / (b1 * m_) - 1) <= 0.08:
                        b1 = b1 * m_
                        break
            try:
                srclen = sf.info(local(rows[j]["Path"])).duration
            except Exception:
                srclen = 0
            swap[f.lower()] = (rows[j]["Path"], ro, b0, b1, srclen)

        # rewrite FILE lines; for loops also re-time that item's PLAYRATE and SOFFS
        hits = loops = 0
        item_start = None
        out = list(lines)
        for k, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("<ITEM"):
                item_start = k
            m = re.search(r'FILE "([^"]+)"', ln)
            if not m or m.group(1).lower() not in swap:
                continue
            new, ro, b0, b1, srclen = swap[m.group(1).lower()]
            out[k] = ln.replace(m.group(1), new)
            if ro != "drum_loop":
                hits += 1
                continue
            loops += 1
            if item_start is None:
                continue
            block = range(item_start, k)
            if any(lines[q].strip().startswith("TAKE") for q in block):
                continue                                 # multi-take item: leave timing alone
            f_ = b0 / b1
            for q in block:
                t = out[q].strip()
                if t.startswith("PLAYRATE "):
                    parts = t.split()
                    parts[1] = f"{fl(parts[1]) * f_:.6f}"
                    if len(parts) > 2:
                        parts[2] = "1"                   # keep pitch while stretching
                    out[q] = out[q][: len(out[q]) - len(out[q].lstrip())] + " ".join(parts)
                elif t.startswith("SOFFS "):
                    parts = t.split()
                    so = fl(parts[1]) * f_
                    if srclen:
                        so = so % srclen
                    parts[1] = f"{so:.6f}"
                    out[q] = out[q][: len(out[q]) - len(out[q].lstrip())] + " ".join(parts)
            for q in block:                              # loops should wrap if the new one is shorter
                if out[q].strip().startswith("LOOP "):
                    out[q] = out[q].replace("LOOP 0", "LOOP 1")
        dest = os.path.join(out_dir, name.replace("-1", "") + "_mars.rpp")
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out))
        total_hits += hits
        total_loops += loops
        kinds = {}
        for v in swap.values():
            kinds[v[1]] = kinds.get(v[1], 0) + 1
        print(f"  {name:34s} " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + f"  ({hits} hits, {loops} loop items)")
    print(f"done: {total_hits} hits and {total_loops} loop items rebuilt across {len(names)} songs -> {out_dir}")


if __name__ == "__main__":
    main()
