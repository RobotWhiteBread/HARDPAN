"""
Machine Tour: writes one kit per classic drum machine for the Kit Builder.
Drums come from that machine's Samples From Mars pack; a melodic loop near the
target tempo; bass and keys whose notes sit in the loop's key.

  python make_kits.py
EXAMPLE: needs the Samples From Mars packs (any of the machine packs below) in
one of your sample folders. Kits land in <projects>/kits; build them all in
REAPER with Soundlab_Build_All_Kits.lua.
"""
import argparse, csv, os, random, re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import soundlab_config as SC
from collections import defaultdict

NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
KEYS = NOTES + [n + "m" for n in NOTES]
ALIAS = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}
MAJ, MIN = [0, 2, 4, 5, 7, 9, 11], [0, 2, 3, 5, 7, 8, 10]

TOUR = [  # save name, title, mars pack folder, style, bpm
    ("machine_tour1_sp1200", "Machine Tour 1: SP-1200 Dust", "sp1200_from_mars", "boombap", 90),
    ("machine_tour2_mpc60", "Machine Tour 2: MPC60 Swing", "mpc60_from_mars", "boombap", 94),
    ("machine_tour3_linndrum", "Machine Tour 3: LinnDrum Neon", "lindrum_from_mars", "electro", 108),
    ("machine_tour4_909", "Machine Tour 4: 909 Warehouse", "909_from_mars", "house", 122),
    ("machine_tour5_808", "Machine Tour 5: 808 Night Drive", "808-from-mars", "halftime", 140),
    ("machine_tour6_dmx", "Machine Tour 6: DMX Street", "dmx_from_mars", "electro", 112),
    ("machine_tour7_cr78", "Machine Tour 7: CR-78 Slow Funk", "cr78_from_mars", "boombap", 96),
    ("machine_tour8_drumulator", "Machine Tour 8: Drumulator Arcade", "drumulator_from_mars", "electro", 104),
]
DRUM_PACKS = ("_from_mars\\", "-from-mars\\")
SYNTH_HINTS = ("juno", "jupiter", "dx", "emulator", "mirage", "ob_", "soviet", "acid", "arp", "2600", "mellotron",
               "101", "kawaii", "indie_tapes", "vp", "ekko", "found_sounds")
TEXTURE_HINTS = ("cassette", "vinyl", "tape", "found_sounds")


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def key_index(k):
    k = (k or "").strip()
    if not k:
        return -1
    minor = k.endswith("m") or "min" in k.lower()
    root = k[:2] if len(k) > 1 and k[1] in "#b" else k[:1]
    root = ALIAS.get(root, root)
    if root not in NOTES:
        return -1
    return NOTES.index(root) + (12 if minor else 0)


def note_pc(n):
    if not n:
        return -1
    root = n[:2] if len(n) > 1 and n[1] in "#b" else n[:1]
    root = ALIAS.get(root, root)
    return NOTES.index(root) if root in NOTES else -1


def tempo_ok(a, b, tol=0.06):
    return a and b and any(abs(a * m - b) / b <= tol for m in (1, 2, 0.5))


def main():
    ap = argparse.ArgumentParser()
    cfg = SC.load()
    ap.add_argument("--lib", default=cfg["library"])
    ap.add_argument("--out", default=os.path.join(cfg["projects"], "kits"))
    ap.add_argument("--map-from", default="")
    ap.add_argument("--map-to", default="")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    local = lambda p: p.replace(a.map_from, a.map_to).replace("\\", "/") if a.map_from else p
    rnd = random.Random(a.seed)

    named = {}
    with open(local(os.path.join(a.lib, "catalog.csv")), newline="", encoding="utf-8-sig") as fh:
        for c in csv.DictReader(fh):
            named[c["Path"]] = (fl(c["BPM"]), key_index(c["Key"]))
    rows, seen = [], set()
    with open(local(os.path.join(a.lib, "features.csv")), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            name = SC.basename(r["Path"])
            sig = (r["dur_s"], r["rms_db"], r["peak_db"], r["centroid_hz"])
            if r["error"] or name.startswith("._") or SC.excluded(r["Path"], cfg) or sig in seen:
                continue
            seen.add(sig)
            nb, nk = named.get(r["Path"], (0, -1))
            m = re.search(r"(?:^|[ _\-\[])(\d{2,3})(?:\s?bpm)?[\]\s]*\.(?:wav|aiff?)$", name, re.I) or re.search(r"(\d{2,3})\s?bpm", name, re.I)
            tb = fl(m.group(1)) if m and 60 <= fl(m.group(1)) <= 180 else 0
            r["_bpm"] = nb or tb or fl(r["tempo_bpm"])
            r["_key"] = nk if nk >= 0 else key_index(r["key"])
            mn = re.search(r"(?:^|[ _\-])([A-G](?:#|b)?)(?:-?\d)(?:[ _\-]|\.)", name.rsplit(".", 1)[0] + ".")
            r["_pc"] = note_pc(mn.group(1)) if mn else note_pc(r["note"])
            r["_low"] = r["Path"].lower()
            rows.append(r)

    def best(cands, score, n=1, top=8):
        cands = sorted(cands, key=score, reverse=True)[:top]
        rnd.shuffle(cands)
        return cands[:n]


    def has(r, *words):
        return any(re.search(w, r["_low"]) for w in words)
    DRUMWORDS = (r"drums?\\", r"beats?\\", r"\bbeat\b", r"groove", r"accent", r"perc", r"kick", r"snare", r"\bbd\b", r"bass drum", r"hi ?hat", r"\bhh\b", r"break", r"\bsd\b", r"cymbal")
    loops = [r for r in rows if "\\mars\\" in r["_low"] and has(r, r"loop") and not has(r, *DRUMWORDS)
             and r["_key"] >= 0 and r["_bpm"] and 2 <= fl(r["dur_s"]) <= 40]
    basses = [r for r in rows if "\\mars\\" in r["_low"] and has(r, r"\\bass\\", r"\bbass\b", r"\bsub\b")
              and not has(r, *DRUMWORDS) and not has(r, r"loop") and r["_pc"] >= 0]
    keys_ = [r for r in rows if "\\mars\\" in r["_low"] and has(r, r"piano", r"keys", r"\bpads?\b", r"chord", r"rhodes", r"organ",
              r"string", r"bell", r"\blead", r"brass", r"mellotron", r"\bep\b", r"e piano", r"clav", r"choir")
             and not has(r, r"\bbass\b", r"\bsub\b", r"loop", *DRUMWORDS) and r["_pc"] >= 0 and fl(r["dur_s"]) >= 1]
    textures = [r for r in rows if has(r, r"crackle", r"hiss", r"vinyl noise", r"tape noise", r"ambien", r"atmos", r"texture", r"room tone", r"noise floor")
                and not has(r, *DRUMWORDS) and fl(r["dur_s"]) >= 4]
    fx = [r for r in rows if "databenders" in r["_low"] and has(r, r"\\fx") and 0.5 <= fl(r["dur_s"]) <= 4]
    print(f"pool: {len(loops)} melodic loops, {len(basses)} bass, {len(keys_)} keys, {len(textures)} textures, {len(fx)} fx")

    os.makedirs(local(a.out), exist_ok=True)
    used = set()
    for save, title, pack, style, bpm in TOUR:
        drums = [r for r in rows if ("\\mars\\" + pack + "\\").lower() in r["_low"]]
        by = defaultdict(list)
        for r in drums:
            low = r["_low"].rsplit("\\", 3)
            low = "\\".join(low[-3:])
            if re.search(r"loop", low):
                continue
            role = ("kick" if re.search(r"kick|bass drum|\\bd|\bbd\b", low) else
                    "snare_clap" if re.search(r"snare|clap|\bsd\b|\\sd", low) else
                    "hat" if re.search(r"hi ?hat|\bhh\b|\bch\b|\boh\b|\\ch|\\oh|closed|open hat", low) else
                    "perc" if re.search(r"perc|conga|bongo|tom|cowbell|tamb|shaker|rim|clave|cabasa|block", low) else None)
            if role and r["sound_guess"] not in ("loop", "texture"):
                by[role].append(r)
        kit = []
        kit += [("kick", r) for r in best(by["kick"], lambda r: fl(r["low_frac"]) + fl(r["sub_frac"]) + fl(r["crest_db"]) / 40, 2)]
        kit += [("snare_clap", r) for r in best(by["snare_clap"], lambda r: fl(r["crest_db"]) / 30 + fl(r["mid_frac"]) + (2 if "snare" in r["_low"].rsplit("\\", 1)[-1] else 0), 2)]
        kit += [("hat", r) for r in best(by["hat"], lambda r: fl(r["high_frac"]) - fl(r["dur_s"]) / 2, 2)]
        kit += [("perc", r) for r in best(by["perc"], lambda r: fl(r["crest_db"]), 1)]
        lp = [r for r in loops if tempo_ok(bpm, r["_bpm"]) and r["Path"] not in used]
        lp = best(lp, lambda r: -abs(r["_bpm"] - bpm) / 10 + (1 if r["Path"] in named and named[r["Path"]][1] >= 0 else 0), 1, 12)
        key = lp[0]["_key"] if lp else (9 + 12)          # A minor if no loop fits
        root, minor = key % 12, key >= 12
        scale = [(root + s) % 12 for s in (MIN if minor else MAJ)]
        chord = [root, (root + (3 if minor else 4)) % 12, (root + 7) % 12]
        for r in lp:
            kit.append(("loop", r))
            used.add(r["Path"])
        bs = best([r for r in basses if r["_pc"] in (root, (root + 7) % 12) and r["Path"] not in used],
                  lambda r: fl(r["low_frac"]) + fl(r["sub_frac"]), 1, 10)
        ks = best([r for r in keys_ if r["_pc"] in chord and r["Path"] not in used], lambda r: -fl(r["flatness"]), 1, 15)
        tx = best([r for r in textures if r["Path"] not in used], lambda r: rnd.random(), 1, 30)
        fxs = best([r for r in fx if r["Path"] not in used], lambda r: rnd.random(), 2, 30)
        for t, lst in (("bass", bs), ("melodic", ks), ("texture", tx), ("fx_noise", fxs)):
            for r in lst:
                kit.append((t, r))
                used.add(r["Path"])
        kname = KEYS[key]
        path = os.path.join(local(a.out), save + ".txt")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"# title={title}\n# style={style}\n# bpm={bpm}\n# save={save}\n# key={kname}\n")
            fh.write("type\tbpm\tkey\tpath\n")
            for t, r in kit:
                fh.write(f"{t}\t{round(r['_bpm']) if r['_bpm'] else ''}\t{KEYS[r['_key']] if r['_key'] >= 0 else ''}\t{r['Path']}\n")
        counts = defaultdict(int)
        for t, _ in kit:
            counts[t] += 1
        print(f"{save:28s} {style:9s} {bpm} BPM  key {kname:4s} " + " ".join(f"{t}:{n}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
