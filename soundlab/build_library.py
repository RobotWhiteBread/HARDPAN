"""
Step 1: build the library.

Finds every sample in your sample folders (from soundlab.json) and writes
  <library>/catalog.csv          one row per file: source, pack, type, machine, BPM, key, path
  <library>/By Type/...          browse folders made of HARD LINKS (no extra disk space,
  <library>/Loops by BPM/...     deleting them never deletes your originals)
  <library>/By Key/...
  <library>/By Machine/...
Your measurements (features.csv) are kept between rebuilds.

  python build_library.py
  python build_library.py --no-links      (catalog only, no browse folders)
"""
import argparse, csv, os, re, shutil, sys, time
from collections import Counter
import soundlab_config as SC

AUDIO = (".wav", ".aif", ".aiff", ".flac")
LINK_DIRS = ("By Type", "Loops by BPM", "By Key", "By Machine")
TONAL = {"Loops & Breaks", "Stems", "Melodic", "Vocals", "Bass"}

# first match wins; read from the path below the pack folder
TYPE_RULES = [
    ("Stems", r"\bstems?\b|composition"),
    ("Loops & Breaks", r"\bloops?\b|\bbreaks?\b|\bbpm\b|\d{2,3}\s?bpm|groove"),
    ("Kicks", r"\bkicks?\b|\bkik\b|\bbd\b|bass ?drum"),
    ("Snares", r"\bsnares?\b|\bsd\b|\brim"),
    ("Claps", r"\bclaps?\b|\bsnaps?\b"),
    ("Hats & Cymbals", r"\bhats?\b|hi-?hat|\bhh\b|\boh\b|\bch\b|cymbal|\bride\b|crash"),
    ("Perc", r"\btoms?\b|perc|conga|bongo|shaker|tamb|cowbell|clave|block"),
    ("Bass", r"\b808\b|\bbass\b|\bsub\b"),
    ("Vocals", r"vocal|\bvox\b|adlib|choir|voice"),
    ("FX & Texture", r"\bfx\b|riser|noise|vinyl|crackle|foley|tape|ambien|atmos|texture|sweep|impact"),
    ("Melodic", r"\bpads?\b|chord|keys|piano|rhodes|organ|synth|lead|stab|strings|guitar|horn|brass|trumpet|flute|bell|arp|melod"),
]
MACHINE_RULES = [
    ("SP 1200", r"SP ?1200"), ("MPC60", r"MPC ?60"), ("MPC3000", r"MPC ?3000"), ("MPC2000", r"MPC ?2000"),
    ("LinnDrum", r"LINDRUM|LINNDRUM|LIN LOOPS"), ("Linn LM-1", r"\bLM1\b"), ("Linn 9000-60", r"LINN60"),
    ("TR-808", r"\b808\b"), ("TR-909", r"\b909\b"), ("TR-707-727", r"\b70?7\b|\b727\b"), ("TR-606", r"\b606\b"),
    ("CR-78", r"CR-?78"), ("Oberheim DMX", r"\bDMX\b"), ("Drumulator", r"DRUMULATOR"), ("Drumtraks", r"DRUMTRAX"),
    ("Akai S612-S950", r"S612|S950"), ("Juno", r"JUNO"), ("Emulator", r"EMULATOR"), ("DX7-DX100", r"\bDX\b|DX100"),
    ("Minimoog", r"\bMINI FROM"), ("ARP", r"ARP|2600"), ("Mirage", r"MIRAGE"), ("Vinyl", r"VINYL"), ("Tape", r"TAPE|CASSETTE"),
]
KEY_RE = re.compile(r"(?<![A-Za-z])([A-G])\s?(#|b|sharp|flat|Sharp|Flat)?\s?(minor|Minor|MINOR|min|Min|MIN|m|major|Major|MAJOR|maj|Maj|MAJ)?(?![A-Za-z])")


def get_bpm(s, looped=False):
    m = re.search(r"(\d{2,3})\s?-?bpm", s, re.I) or re.search(r"bpm\s?-?(\d{2,3})", s, re.I)
    if m and 55 <= int(m.group(1)) <= 200:
        return int(m.group(1))
    if looped:                                  # loops often carry a bare tempo: "Guitar_Loop_01_120_G#min", "[85] Kit"
        for n in reversed(re.findall(r"(?:^|[ _\-\[(])(\d{2,3})(?=$|[ _\-\])]|[A-G][#b]?(?:m|min|maj)?\b)", s)):
            if 60 <= int(n) <= 180:
                return int(n)
    return None


def get_key(s):
    """Capital note letter on purpose, so words like "ab" or "bpm" are not read as keys."""
    for m in KEY_RE.finditer(s):
        n, acc, q = m.group(1), m.group(2), m.group(3)
        if not acc and not q:
            continue                                   # a lone letter is too risky to call a key
        acc = "#" if acc and "sharp" in acc.lower() else "b" if acc and "flat" in acc.lower() else (acc or "")
        minor = bool(q) and q.lower() in ("m", "min", "minor") and q != "M"
        return f"{n}{acc}{'m' if minor else ''}"
    return None


def safe(s, n=90):
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip()
    return s[:n]


def long_path(p):
    if os.name == "nt" and not p.startswith("\\\\?\\") and len(p) > 240:
        return "\\\\?\\" + os.path.abspath(p)
    return p


def walk_audio(root):
    for d, dirs, files in os.walk(long_path(root)):
        dirs[:] = [x for x in dirs if x != "__MACOSX" and not x.startswith(".") and x != "_library"]
        for f in files:
            if f.lower().endswith(AUDIO) and not f.startswith("._"):
                p = os.path.join(d, f)
                yield p[4:] if p.startswith("\\\\?\\") else p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args()
    cfg = SC.load()
    lib = cfg["library"]
    os.makedirs(lib, exist_ok=True)
    for d in LINK_DIRS:                                # remove old links only; never the originals or measurements
        shutil.rmtree(long_path(os.path.join(lib, d)), ignore_errors=True)
    t0 = time.time()
    rows, used, made = [], set(), set()
    link_errors = 0
    cross_drive = False

    def link(folder, name, target):
        nonlocal link_errors, cross_drive
        if a.no_links or cross_drive:
            return
        folder = re.sub(r'[*?"<>|]', "-", folder)
        name = re.sub(r'[\\/:*?"<>|]', "-", name)
        if folder not in made:
            os.makedirs(long_path(folder), exist_ok=True)
            made.add(folder)
        stem, ext = os.path.splitext(name)
        dest, n = os.path.join(folder, name), 2
        while dest.lower() in used:
            dest = os.path.join(folder, f"{stem} ({n}){ext}")
            n += 1
        used.add(dest.lower())
        try:
            os.link(long_path(target), long_path(dest))
        except OSError as e:
            link_errors += 1
            if getattr(e, "winerror", None) == 17 or e.errno == 18:   # different drive: hard links cannot cross
                cross_drive = True
                print("  note: library and samples are on different drives, so browse folders are skipped (catalog still built)")

    for src in cfg["sample_folders"]:
        base = src["path"]
        if not os.path.isdir(base):
            print(f"not found yet: {base}")
            continue
        roots = [base]
        if src.get("only"):
            roots = [os.path.join(base, d) for d in sorted(os.listdir(base))
                     if os.path.isdir(os.path.join(base, d)) and re.search(src["only"], d)]
        files = [p for r in roots for p in walk_audio(r)]
        print(f"Indexing {len(files)} files from {src['name']}...")
        for i, full in enumerate(files, 1):
            if i % 20000 == 0:
                print(f"  ...{i} of {len(files)}")
            rel = os.path.relpath(full, base)
            parts = re.split(r"[\\/]", rel)
            pack = parts[0] if len(parts) > 1 else src["name"]
            fname = parts[-1]
            stem = os.path.splitext(fname)[0]
            typ = next((t for t, rx in TYPE_RULES if re.search(rx, rel, re.I)), "Other")
            machine = next((m for m, rx in MACHINE_RULES if re.search(rx, pack, re.I)), "")
            looped = typ in ("Loops & Breaks", "Stems")
            bpm = get_bpm(stem, looped) or get_bpm(rel)
            key = get_key(stem)
            short = safe(f"{safe(pack, 30)} - {fname}", 120)
            link(os.path.join(lib, "By Type", typ), short, full)
            if bpm and typ in TONAL:
                lo = bpm // 10 * 10
                link(os.path.join(lib, "Loops by BPM", f"{lo:03d}-{lo + 9:03d}"), short, full)
            if key and typ in TONAL:
                link(os.path.join(lib, "By Key", key), short, full)
            if machine:
                link(os.path.join(lib, "By Machine", machine), short, full)
            rows.append({"Source": src["name"], "Pack": pack, "Type": typ, "Machine": machine,
                         "BPM": bpm or "", "Key": key or "", "File": fname, "Path": full})

    with open(os.path.join(lib, "catalog.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["Source", "Pack", "Type", "Machine", "BPM", "Key", "File", "Path"], quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    print(f"\nLibrary built: {len(rows)} files in {time.time() - t0:.0f}s, no extra disk space used.")
    for t, n in Counter(r["Type"] for r in rows).most_common():
        print(f"  {t:16s} {n}")
    print(f"With a BPM: {sum(1 for r in rows if r['BPM'])}   With a key: {sum(1 for r in rows if r['Key'])}   Link errors: {link_errors}")
    print(f"Browse it in REAPER's Media Explorer at {lib}")
    print("Next: python analyze_samples.py")


if __name__ == "__main__":
    main()
