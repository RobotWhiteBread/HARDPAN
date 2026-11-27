"""
soundlab Mix Stage: see where every part of a song sits, find the crowded spots and the empty
ones, and get sounds from your own library that fit the empty ones.

  python make_stage.py SONG.rpp              read a REAPER project (audio items, track volume and pan)
  python make_stage.py STEMS_FOLDER [--bpm 90]   or a folder of exported stems from any DAW (one WAV per part)

The picture follows the classic mixing stage: left to right is pan, low to high is pitch
(frequency), and size and brightness show how loud and how far forward each part is.
Reading a REAPER project uses the raw audio, so effects and MIDI instruments are not heard:
for an exact picture, render stems in REAPER (File, Render, Stems) and point this at that folder.
Writes galaxy/data/stage_data.js and opens galaxy/stage.html.
"""
import json, math, os, re, sys, webbrowser
import numpy as np
import soundfile as sf
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SR = 22050
MAX_S = 420                                     # analyze up to 7 minutes
EDGES = np.geomspace(30, 16000, 25)             # 24 log bands
CENT = np.sqrt(EDGES[:-1] * EDGES[1:])
PAN_BINS = 9
AUDIO = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg")


def local(p):
    """Let the same script read Windows paths from another machine (set SOUNDLAB_PATHMAP=C:\\=/mnt/c/)."""
    m = os.environ.get("SOUNDLAB_PATHMAP")
    if m and "=" in m:
        a, b = m.split("=", 1)
        if p.lower().startswith(a.lower()):
            p = b + p[len(a):].replace("\\", "/")
    return p


def read(path, offs=0.0, rate=1.0):
    info = sf.info(path)
    start = int(max(0.0, offs) * info.samplerate)
    frames = min(info.frames - start, int((MAX_S * rate + 1) * info.samplerate))
    if frames <= 0:
        return np.zeros((2, 1), np.float32)
    y, sr = sf.read(path, start=start, frames=frames, dtype="float32", always_2d=True)
    y = y[:, :2] if y.shape[1] >= 2 else np.repeat(y, 2, axis=1)
    target = SR / rate                           # play rate r: the source is squeezed by r
    g = math.gcd(int(round(target)), int(sr))
    y = signal.resample_poly(y, int(round(target)) // g, int(sr) // g, axis=0)
    return y.T.astype(np.float32)


# ---------------------------------------------------------------- REAPER project
def parse_rpp(path):
    tracks, cur, item, depth_stack = [], None, None, []
    meta = {"bpm": 0.0, "markers": []}
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if len(depth_stack) == 1 and line.startswith("TEMPO "):
            meta["bpm"] = float(line.split()[1])
        if len(depth_stack) == 1 and line.startswith("MARKER "):
            m = re.match(r'MARKER\s+\d+\s+([\d.]+)\s+("([^"]*)"|(\S+))\s+(\d+)', line)
            if m and not (int(m.group(5)) & 1):         # skip region ends
                meta["markers"].append((float(m.group(1)), m.group(3) or m.group(4) or ""))
        if line.startswith("<"):
            tag = line[1:].split(" ", 1)[0]
            depth_stack.append(tag)
            if tag == "TRACK":
                cur = {"name": "", "vol": 1.0, "pan": 0.0, "mute": False, "items": [], "midi": False}
                tracks.append(cur)
            elif tag == "ITEM" and cur is not None:
                item = {"pos": 0.0, "len": 0.0, "loop": False, "vol": 1.0, "offs": 0.0, "rate": 1.0, "file": None, "mute": False}
                cur["items"].append(item)
            elif tag == "SOURCE" and cur is not None and "MIDI" in line:
                cur["midi"] = True
            continue
        if line == ">":
            tag = depth_stack.pop() if depth_stack else ""
            if tag == "ITEM":
                item = None
            continue
        parts = line.split(" ")
        key = parts[0]
        inside = depth_stack[-1] if depth_stack else ""
        if cur is None:
            continue
        if inside == "TRACK":
            if key == "NAME":
                cur["name"] = line[5:].strip().strip('"')
            elif key == "VOLPAN":
                cur["vol"], cur["pan"] = float(parts[1]), float(parts[2])
            elif key == "MUTESOLO":
                cur["mute"] = parts[1] == "1"
        elif item is not None:
            if inside == "ITEM":
                if key == "POSITION": item["pos"] = float(parts[1])
                elif key == "LENGTH": item["len"] = float(parts[1])
                elif key == "LOOP": item["loop"] = parts[1] == "1"
                elif key == "VOLPAN": item["vol"] = float(parts[1])
                elif key == "SOFFS": item["offs"] = float(parts[1])
                elif key == "PLAYRATE": item["rate"] = float(parts[1]) or 1.0
                elif key == "MUTE": item["mute"] = parts[1] == "1"
            elif key == "FILE":
                item["file"] = line[5:].strip().strip('"')
    return tracks, meta


def render_track(t, length):
    n = int(min(length, MAX_S) * SR) + 1
    buf = np.zeros((2, n), np.float32)
    cache, used = {}, 0
    for it in t["items"]:
        if it["mute"] or not it["file"]:
            continue
        p = local(it["file"])
        if not os.path.exists(p) or not p.lower().endswith(AUDIO):
            continue
        key = (p, round(it["offs"], 4), round(it["rate"], 4))
        if key not in cache:
            try:
                cache[key] = read(p, it["offs"], it["rate"])
            except Exception:
                continue
        src = cache[key]
        a = int(it["pos"] * SR)
        if a >= n:
            continue
        want = min(n - a, int(it["len"] * SR))
        if src.shape[1] >= want or not it["loop"]:
            seg = src[:, :want]
        else:
            seg = np.tile(src, int(math.ceil(want / src.shape[1])))[:, :want]
        buf[:, a:a + seg.shape[1]] += seg * it["vol"]
        used += 1
    p = t["pan"]                                # REAPER balance pan
    g = t["vol"]
    buf[0] *= g * min(1.0, 1 - p)
    buf[1] *= g * min(1.0, 1 + p)
    return buf, used


# ---------------------------------------------------------------- measures
def measure(buf):
    L, R = buf[0], buf[1]
    f, _, SL = signal.stft(L, SR, nperseg=2048, noverlap=1024, boundary=None)
    _, _, SRr = signal.stft(R, SR, nperseg=2048, noverlap=1024, boundary=None)
    PL, PR = np.abs(SL) ** 2, np.abs(SRr) ** 2
    M, S = np.abs(SL + SRr) ** 2 / 4, np.abs(SL - SRr) ** 2 / 4
    frame = (PL + PR).sum(0)
    if frame.max() <= 0:
        return None
    act = frame > frame.max() * 10 ** (-45 / 10)
    if act.sum() < 3:
        return None
    band = lambda P: np.array([P[(f >= EDGES[i]) & (f < EDGES[i + 1])][:, act].sum() for i in range(24)])
    bL, bR, bM, bS = band(PL), band(PR), band(M), band(S)
    tot = bL + bR + 1e-20
    spec = tot / tot.sum()
    cum = np.cumsum(spec)
    lo = float(CENT[np.searchsorted(cum, 0.10)]); hi = float(CENT[min(23, np.searchsorted(cum, 0.90))])
    cent = float(np.exp((spec * np.log(CENT)).sum()))
    pan_b = (bR - bL) / tot                      # pan per band
    pan = float((pan_b * spec).sum())
    width = float(min(1.5, math.sqrt(bS.sum() / (bM.sum() + 1e-20))))
    nb = min(len(act), len(L) // 1024)
    blocks = ((L[:nb * 1024] ** 2 + R[:nb * 1024] ** 2) / 2).reshape(nb, 1024).mean(1)
    on = blocks[act[:nb]] if act[:nb].any() else blocks
    db = 10 * math.log10(float(on.mean()) + 1e-20)        # level while playing, dBFS
    # depth: how dry and upfront it is (strong onsets, short tails) vs washed back
    env = np.sqrt(frame[act]); env = env / (env.max() + 1e-12)
    up = float(np.clip(np.mean(np.maximum(0, np.diff(env))) * 8, 0, 1))
    return {"spec": [round(float(x), 4) for x in spec], "pan_b": [round(float(x), 3) for x in pan_b],
            "lo": round(lo, 1), "hi": round(hi, 1), "cent": round(cent, 1), "pan": round(pan, 3),
            "width": round(width, 3), "db": round(db, 1), "active": round(float(act.mean()), 3), "attack": round(up, 3),
            "_bands": tot, "_act": act}


def occupancy(tracks):
    occ = np.zeros((24, PAN_BINS))
    xs = np.linspace(-1, 1, PAN_BINS)
    for t in tracks:
        m = t["m"]
        e = np.array(m["spec"]) * 10 ** (m["db"] / 10) * m["active"]
        for b in range(24):
            sig = 0.12 + m["width"] * 0.55
            w = np.exp(-0.5 * ((xs - m["pan_b"][b]) / sig) ** 2)
            occ[b] += e[b] * w / w.sum()
    return occ / (occ.max() + 1e-20)


def clashes(tracks):
    out = []
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            a, b = tracks[i]["m"], tracks[j]["m"]
            if abs(a["pan"] - b["pan"]) > 0.35 + 0.3 * min(a["width"], b["width"]):
                continue
            both = np.mean(a["_act"][: min(len(a["_act"]), len(b["_act"]))] & b["_act"][: min(len(a["_act"]), len(b["_act"]))])
            if both < 0.2 or abs(a["db"] - b["db"]) > 12:
                continue
            sa, sb = np.array(a["spec"]), np.array(b["spec"])
            ov = np.minimum(sa, sb)
            if ov.sum() < 0.25:
                continue
            k = int(np.argmax(ov))
            out.append({"a": tracks[i]["name"], "b": tracks[j]["name"], "hz": round(float(CENT[k])), "overlap": round(float(ov.sum()), 2),
                        "together": round(float(both), 2)})
    return sorted(out, key=lambda c: -c["overlap"] * c["together"])[:12]


def gaps(occ):
    xs = np.linspace(-1, 1, PAN_BINS)
    found = []
    o = occ.copy()
    lo_b, hi_b = 3, 22                          # about 60 Hz to 12 kHz
    for _ in range(4):
        best = None
        for b0 in range(lo_b, hi_b - 2):
            for p0 in range(1, PAN_BINS - 2):   # skip the hard-panned edges
                if EDGES[b0 + 3] < 300 and p0 not in (3, 4):
                    continue                    # low end stays near the center
                cell = o[b0:b0 + 3, p0:p0 + 2]
                s = cell.mean()
                if best is None or s < best[0]:
                    best = (s, b0, p0)
        s, b0, p0 = best
        if s > 0.06:
            break
        found.append({"lo": round(float(EDGES[b0])), "hi": round(float(EDGES[b0 + 3])), "pan0": round(float(xs[p0]), 2),
                      "pan1": round(float(xs[p0 + 1]), 2), "fill": round(float(s), 3)})
        o[max(0, b0 - 2):b0 + 5, max(0, p0 - 1):p0 + 3] = 1
    return found


def analyze(parts):
    """parts: list of {"name", "m"} measured over the same time span."""
    occ = occupancy(parts)
    return {"tracks": [{"name": t["name"], **{k: v for k, v in t["m"].items() if not k.startswith("_")}} for t in parts],
            "occ": [[round(float(v), 3) for v in row] for row in occ], "clashes": clashes(parts), "gaps": gaps(occ)}


def sections_from(markers, end):
    marks = sorted((t, n) for t, n in markers if 0 <= t < end - 4)
    if len(marks) >= 2 or (marks and marks[0][0] > 4):
        if marks[0][0] > 4:
            marks.insert(0, (0.0, "Start"))
        return [(n or f"Part {k + 1}", t, marks[k + 1][0] if k + 1 < len(marks) else end) for k, (t, n) in enumerate(marks)]
    step = 30.0
    out, t = [], 0.0
    while t < end - 4:
        out.append((f"{int(t // 60)}:{int(t % 60):02d}", t, min(end, t + step))); t += step
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    name = os.path.splitext(os.path.basename(src.rstrip("\\/")))[0]
    tracks, notes, meta = [], [], {"bpm": 0.0, "markers": []}
    if "--bpm" in sys.argv:
        meta["bpm"] = float(sys.argv[sys.argv.index("--bpm") + 1])
    if os.path.isdir(src):
        files = sorted(f for f in os.listdir(src) if f.lower().endswith(AUDIO))
        for f in files:
            buf = read(os.path.join(src, f))
            tracks.append({"name": os.path.splitext(f)[0], "buf": buf})
        notes.append(f"{len(files)} stems")
        end = max([t["buf"].shape[1] / SR for t in tracks] + [1])
    else:
        (parsed, m2), muted = parse_rpp(src), []
        meta["bpm"] = meta["bpm"] or m2["bpm"]; meta["markers"] = m2["markers"]
        end = max([it["pos"] + it["len"] for t in parsed for it in t["items"]] + [1])
        for t in parsed:
            if t["mute"]:
                muted.append(t["name"] or "unnamed"); continue
            buf, used = render_track(t, end)
            if not used:
                if t["items"]:
                    notes.append(f"{t['name'] or 'unnamed'}: " + ("MIDI or synth, render stems to include it" if t["midi"] else "audio files not found"))
                continue
            tracks.append({"name": t["name"] or "Track", "buf": buf})
        if muted:
            notes.append(f"{len(muted)} muted track{'s' if len(muted) > 1 else ''} left out")
        notes.append("Read from the project's audio files, so effects and MIDI instruments are not heard; render stems for an exact picture")
    end = min(end, MAX_S)
    secs = sections_from(meta["markers"], end)
    n = int(end * SR) + 1
    mix = np.zeros((2, n), np.float32)
    keep, per = [], [[] for _ in secs]
    for t in tracks:
        buf = t.pop("buf")[:, :n]
        mix[:, :buf.shape[1]] += buf
        m = measure(buf)
        if not m:
            continue
        t["m"] = m; keep.append(t)
        for k, (sn, a, b) in enumerate(secs):
            ms = measure(buf[:, int(a * SR):int(b * SR)])
            if ms and ms["active"] > 0.05:
                per[k].append({"name": t["name"], "m": ms})
    if not keep:
        raise SystemExit("Nothing to measure: no audio found.")
    data = {"song": name, "bpm": meta["bpm"], "length": round(end, 2), "bands": [round(float(c), 1) for c in CENT],
            "edges": [round(float(e), 1) for e in EDGES], **analyze(keep), "notes": notes,
            "sections": [{"name": sn, "t0": round(a, 2), "t1": round(b, 2), **analyze(per[k])} for k, (sn, a, b) in enumerate(secs) if per[k]]}
    # a quick mixdown so the page can play the song with a suggested sound in it
    peak = float(np.abs(mix).max()) or 1.0
    import mix_index
    sl = mix_index.slug(name)
    sdir = os.path.join(ROOT, "galaxy", "data", "stages")
    os.makedirs(sdir, exist_ok=True)
    mixfile = os.path.join(sdir, sl + "_mix.ogg")
    try:
        sf.write(mixfile, (mix.T / peak * 0.89).astype(np.float32), SR, format="OGG", subtype="VORBIS")
        data["mix"] = "data/stages/" + sl + "_mix.ogg"
    except Exception as e:
        notes.append(f"could not write the preview mix ({e})")
    blob = "window.STAGE = " + json.dumps(data, separators=(",", ":")) + ";\n"
    out = os.path.join(ROOT, "galaxy", "data", "stage_data.js")   # the newest song, opened by stage.html on its own
    for path in (out, os.path.join(sdir, sl + ".js")):            # plus one copy per song for the Mixes menu
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
    mix_index.register("stage", name, "stage.html?s=" + sl, f"{len(keep)} parts")
    print(f"{len(keep)} parts measured over {len(data['sections'])} sections, {len(data['clashes'])} clashes, {len(data['gaps'])} open spots -> {out}")
    for n in notes:
        print("  " + n)
    if "--no-open" not in sys.argv:
        webbrowser.open("file:///" + os.path.join(ROOT, "galaxy", "stage.html").replace("\\", "/").lstrip("/"))


if __name__ == "__main__":
    main()
