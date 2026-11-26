"""
EXAMPLE: Sound Galaxy demo maker (edit PARTS below to point at sounds you may share publicly): a beat built from the library, a vertical
Reel where every sound lights up its spot in the Galaxy as it plays, and
square-ish photo posts.

  python make_demo.py audio
  python make_demo.py frames --start 0 --end 300
  python make_demo.py video
  python make_demo.py photos
"""
import argparse, json, math, os, subprocess, sys
import numpy as np
import soundfile as sf
from scipy import ndimage, signal
from PIL import Image, ImageDraw, ImageFont

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import soundlab_config as SC
_cfg = SC.load()
S = os.environ.get("HP_SAMPLES", _cfg["sample_folders"][0]["path"])
GAL = os.environ.get("HP_GALAXY", os.path.join(SC.HERE, "galaxy", "data"))
OUT = os.environ.get("HP_DEMOS", os.path.join(_cfg["projects"], "demos"))
FRAMES = os.environ.get("HP_FRAMES", os.path.join(OUT, "_frames"))
SR, BPM, FPS = 44100, 91, 30
BAR = 240 / BPM
BARS = 12
W, H = 720, 1280                                   # rendered size, scaled to 1080x1920 on encode
FONT = os.environ.get("HP_FONT", r"C:\Windows\Fonts\segoeuib.ttf")
FONT_R = os.environ.get("HP_FONT_R", r"C:\Windows\Fonts\segoeui.ttf")
TYPE_COLOR = {"kick": "#ff5a4e", "snare_clap": "#ffb347", "hat": "#fff27a", "perc": "#c58bff", "bass": "#4ea8ff",
              "melodic": "#4ef0b0", "loop": "#ff7bd5", "texture": "#8fa3b8", "fx_noise": "#7ee0ff", "other": "#666666"}

# Public images and videos only show sounds from sources listed in "public_allow" in soundlab.json:
# your own recordings (for example "Mine"), openly licensed sets, or vendors that said yes in writing.
# Everything else is left out, names and sounds alike. An empty list means nothing from any vendor.
PUBLIC_ALLOW = [x.lower() for x in _cfg.get("public_allow", [])]
PARTS = {   # point these at sounds you may share publicly (your own recordings, CC0 sets), for example:
    # "kick": (r"D:\Recordings\kick.wav", -2.0),
    # "rhodes": (r"D:\Recordings\rhodes_Am_91.wav", -5.0),
}


def local(p):
    """Windows path -> path on this machine (lets the same script run in a Linux helper)."""
    if os.name == "nt":
        return p
    return p.replace(S, os.environ.get("HP_SAMPLES_LOCAL", S)).replace("\\", "/")


def read(p):
    y, sr = sf.read(local(p), dtype="float32", always_2d=True)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    if sr != SR:
        y = signal.resample_poly(y, SR, sr, axis=0).astype("float32")
    return y[:, :2]


# ---------------------------------------------------------------- audio
def make_audio():
    os.makedirs(OUT, exist_ok=True)
    n = int(BARS * BAR * SR)
    mix = np.zeros((n, 2), dtype="float32")
    duck = np.ones(n, dtype="float32")
    events = []
    g = {k: 10 ** (db / 20) for k, (_, db) in PARTS.items()}
    hits = {k: read(PARTS[k][0]) for k in ("kick", "snare", "hat")}
    step = BAR / 16

    def t_of(bar, st):
        t = bar * BAR + st * step
        if st % 4 == 2:
            t += 0.08 * 4 * step                     # 58% swing on the off eighths
        return t

    def put(name, t, vel=1.0):
        s = int(t * SR)
        y = hits[name][: n - s] * g[name] * vel
        if name == "hat":
            y = y * np.array([0.7, 1.0], dtype="float32")      # hats a little right
        mix[s:s + len(y)] += y
        events.append([round(t, 4), name, vel])
        if name == "kick":                           # gentle duck of the music under each kick
            env = 1 - 0.35 * np.exp(-np.arange(int(0.18 * SR)) / (0.06 * SR))
            e = env[: n - s]
            duck[s:s + len(e)] = np.minimum(duck[s:s + len(e)], e)

    for bar in range(BARS):
        if bar < 2:
            continue                                 # two bars of keys before the drums drop
        last = bar == BARS - 1
        for st, v in {0: 1.0, 7: 0.7, 10: 0.9, **({3: 0.5} if bar % 4 == 3 else {})}.items():
            if not (last and st > 8):
                put("kick", t_of(bar, st), v)
        for st in (4, 12):
            put("snare", t_of(bar, st), 1.0)
        for st in range(0, 16, 2):
            put("hat", t_of(bar, st), (0.9 if st % 4 == 0 else 0.55) * (0.85 + 0.3 * ((bar * 7 + st * 3) % 5) / 5))

    off = int(16 * BAR * SR)                         # start 16 bars into the stems
    music = np.zeros((n, 2), dtype="float32")
    for k in ("rhodes", "bass", "nylon"):
        y = read(PARTS[k][0])[off:off + n] * g[k]
        if k == "bass":
            y[: int(2 * BAR * SR)] *= 0.0            # bass waits for the drums
        if k == "nylon":                             # widen: 14 ms delay on one side
            dly = int(0.014 * SR)
            y = np.stack([y[:, 0] * 0.9, np.concatenate([np.zeros(dly, dtype="float32"), y[:-dly, 1]])], axis=1)
        if k == "rhodes":
            dly = int(0.009 * SR)
            y = np.stack([np.concatenate([np.zeros(dly, dtype="float32"), y[:-dly, 0]]), y[:, 1] * 0.95], axis=1)
        music[: len(y)] += y
    mix += music * duck[:, None]
    v = read(PARTS["vinyl"][0])
    reps = int(math.ceil(n / len(v)))
    mix += np.tile(v, (reps, 1))[:n] * g["vinyl"]
    for k, bar in (("rhodes", 0), ("nylon", 0), ("bass", 2)):
        events.append([round(bar * BAR, 4), k, 1.0])
    for bar in range(0, BARS, 2):
        events.append([round(bar * BAR, 4), "rhodes", 0.7])
    # bus: soft saturation, fade, then peak to -1 dB
    mix *= 10 ** (-1 / 20) / (np.abs(mix).max() + 1e-9)
    mix = np.tanh(mix * 2.2) / np.tanh(2.2)          # glue and loudness
    fade = int(BAR * 0.75 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    mix *= 10 ** (-1 / 20) / (np.abs(mix).max() + 1e-9)
    sf.write(os.path.join(OUT, "hardpan_galaxy_demo.wav"), mix, SR, subtype="PCM_24")
    events.sort()
    json.dump({"events": events, "seconds": n / SR, "parts": {k: v[0] for k, v in PARTS.items()}},
              open(os.path.join(OUT, "demo_events.json"), "w"))
    print(f"beat: {n / SR:.1f} s at {BPM} BPM, {len(events)} hits -> {OUT}")


# ---------------------------------------------------------------- galaxy
def load_galaxy():
    s = open(os.path.join(GAL, "galaxy_data.js"), encoding="utf-8").read()
    G = json.loads(s[s.index("{"): s.rindex("}") + 1])
    if True:                                        # keep only sources cleared for public use
        src = [p.split("|")[0].strip().lower() for p in G["packs"]]
        keep = [i for i, p in enumerate(G["p"]) if src[p] in PUBLIC_ALLOW]
        for k in ("t", "p", "d", "n", "cl", "sx", "sy", "sz"):
            G[k] = [G[k][i] for i in keep]
        counts = np.bincount(np.array(G["cl"]), minlength=len(G["clusters"]))
        for i, c in enumerate(G["clusters"]):
            c["n"] = int(counts[i])
        G["count"] = len(keep)
        print(f"public images: {len(keep):,} sounds from {', '.join(PUBLIC_ALLOW) or 'no sources'} (set public_allow in soundlab.json)")
        if not keep:
            raise SystemExit("Nothing is cleared for public images yet. Add your own folder (or a vendor that said yes) to public_allow.")
    P = np.stack([np.array(G["sx"]), np.array(G["sy"]), np.array(G["sz"])], axis=1) / 10.0
    pal = {t: np.array([int(TYPE_COLOR.get(t, "#888888")[i:i + 2], 16) for i in (1, 3, 5)]) / 255 for t in G["types"]}
    C = np.stack([pal[G["types"][t]] for t in G["t"]])
    path = [G["dirs"][d] + "\\" + n for d, n in zip(G["d"], G["n"])]
    return G, P, C, path


def camera(P, t, total):
    a = 0.4 + 2 * math.pi * 0.55 * t / total        # a bit over half a turn across the clip
    el = 0.32 + 0.1 * math.sin(t / total * math.pi)
    dist = 340 - 45 * math.sin(min(t / total, 1) * math.pi * 0.9)
    ca, sa, ce, se = math.cos(a), math.sin(a), math.cos(el), math.sin(el)
    x = P[:, 0] * ca - P[:, 2] * sa
    z = P[:, 0] * sa + P[:, 2] * ca
    y = P[:, 1] * ce - z * se
    z = P[:, 1] * se + z * ce
    zz = z + dist
    f = 1.25 * W
    return x / zz * f + W / 2, -y / zz * f + H * 0.46, zz


def splat(img, x, y, c, w):
    xi, yi = x.astype(int), y.astype(int)
    m = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    for ch in range(3):
        np.add.at(img[:, :, ch], (yi[m], xi[m]), c[m, ch] * w[m])


def font(size, bold=True):
    try:
        return ImageFont.truetype(FONT if bold else FONT_R, size)
    except OSError:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def neighbors_of(G, idx, k=10):
    keys = [k_ for k_ in G["feats"] if k_ != "tempo"]
    F = np.stack([np.array(G["feats"][k_], dtype=np.float32) for k_ in keys], axis=1)
    T = np.array(G["t"])
    out = {}
    for name, i in idx.items():
        d = ((F - F[i]) ** 2).sum(1) + (T != T[i]) * 1e6
        d[i] = 1e12
        out[name] = np.argsort(d)[:k].tolist()
    return out


def render_frames(a0, a1):
    G, P, C, path = load_galaxy()
    ev = json.load(open(os.path.join(OUT, "demo_events.json")))
    total = ev["seconds"]
    lookup = {p.lower(): i for i, p in enumerate(path)}
    idx = {k: lookup.get(p.lower()) for k, p in ev["parts"].items()}
    idx = {k: v for k, v in idx.items() if v is not None}
    nb = neighbors_of(G, idx)
    audio, _ = sf.read(os.path.join(OUT, "hardpan_galaxy_demo.wav"), dtype="float32")
    mono = audio.mean(axis=1)
    os.makedirs(FRAMES, exist_ok=True)
    nfr = int(total * FPS)
    base_w = np.full(len(P), 0.30, dtype=np.float32)
    big, small = font(46), font(28, False)
    for fi in range(a0, min(a1, nfr)):
        t = fi / FPS
        x, y, z = camera(P, t, total)
        depth = np.clip(1.6 - z / 400, 0.25, 1.4)
        img = np.zeros((H, W, 3), dtype=np.float32)
        splat(img, x, y, C, base_w * depth)
        core = ndimage.gaussian_filter(img, (0.7, 0.7, 0))
        glow = ndimage.gaussian_filter(img, (7, 7, 0))
        img = core * 1.6 + glow * 3.0
        img = 1 - np.exp(-img * 1.3)                 # soft tone curve keeps dense islands from blowing out
        bg = np.linspace(0.02, 0.06, H)[:, None, None] * np.array([0.35, 0.45, 0.7])
        img = np.clip(img + bg, 0, 1)
        im = Image.fromarray((img * 255).astype(np.uint8))
        d = ImageDraw.Draw(im, "RGBA")
        # sounds that just played flash, with lines to their closest neighbors
        for et, name, vel in ev["events"]:
            age = t - et
            if name not in idx or age < 0 or age > 0.9:
                continue
            k = math.exp(-age / 0.22) * vel
            i = idx[name]
            col = tuple(int(c * 255) for c in C[i])
            px, py = float(x[i]), float(y[i])
            for j in nb[name]:
                d.line([px, py, float(x[j]), float(y[j])], fill=col + (int(160 * k),), width=2)
                d.ellipse([x[j] - 3, y[j] - 3, x[j] + 3, y[j] + 3], fill=col + (int(220 * k),))
            r = 6 + 26 * k
            d.ellipse([px - r, py - r, px + r, py + r], outline=col + (int(255 * k),), width=3)
            d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(255, 255, 255, int(255 * min(1, k * 1.5))))
            if age < 0.5 and name in ("kick", "snare", "rhodes", "bass"):
                label = {"kick": "SP-1200 kick", "snare": "SP-1200 snare", "rhodes": "Rhodes stem", "bass": "piano bass"}[name]
                a_ = int(230 * min(1, k * 2)); tw_ = d.textlength(label, font=small)
                d.rounded_rectangle([px + 30, py - 20, px + 46 + tw_, py + 18], 8, fill=(7, 9, 13, int(a_ * 0.8)))
                d.text((px + 38, py - 17), label, font=small, fill=(255, 255, 255, a_))
        # sound wave strip
        s0 = int(t * SR)
        seg = mono[max(0, s0 - SR // 2): s0 + SR // 2]
        if len(seg) > 10:
            seg = seg[:: max(1, len(seg) // W)][:W]
            ys = H - 150 + seg * 90
            pts = [(i, float(v)) for i, v in enumerate(ys)]
            d.line(pts, fill=(242, 169, 59, 200), width=2)
        d.line([(W // 2, H - 205), (W // 2, H - 95)], fill=(255, 255, 255, 60), width=1)
        # captions
        def caption(text, yy, f, a):
            if a <= 0:
                return
            tw = d.textlength(text, font=f)
            d.text(((W - tw) / 2, yy), text, font=f, fill=(255, 255, 255, int(255 * a)))
        fade = lambda t0, t1: max(0.0, min(1.0, (t - t0) / 0.4, (t1 - t) / 0.4))
        caption("169,000 samples.", 90, big, fade(0.2, 5.0))
        caption("One map.", 150, big, fade(0.8, 5.0))
        caption("Every dot is a sound.", 100, font(36), fade(5.3, 11.5))
        caption("Similar sounds sit together.", 148, font(36), fade(5.8, 11.5))
        caption("Each hit lights up its spot", 100, font(36), fade(12, 19))
        caption("and its closest matches.", 148, font(36), fade(12.4, 19))
        caption("Built from my own library.", 100, font(36), fade(19.5, 26))
        caption("Analyzed in 35 minutes.", 148, font(36), fade(19.9, 26))
        caption("SOUND", H * 0.40, font(72), fade(total - 4.2, total + 1))
        caption("GALAXY", H * 0.40 + 84, font(40), fade(total - 3.8, total + 1))
        im.save(os.path.join(FRAMES, f"f{fi:05d}.png"))
    print(f"frames {a0}..{min(a1, nfr) - 1} of {nfr}")


def make_video():
    out = os.path.join(OUT, "hardpan_galaxy_reel.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(FRAMES, "f%05d.png"),
           "-i", os.path.join(OUT, "hardpan_galaxy_demo.wav"), "-vf", "scale=1080:1920:flags=lanczos",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    print("reel:", out)


# ---------------------------------------------------------------- photos
def make_photos():
    G, P, C, path = load_galaxy()
    PW, PH = 1080, 1350
    def still(t, cam_t=10.0, total=31.6):
        global W, H
        W0, H0 = W, H
        W, H = PW, PH
        x, y, z = camera(P, cam_t, total)
        img = np.zeros((H, W, 3), dtype=np.float32)
        splat(img, x, y, C, np.full(len(P), 0.28) * np.clip(1.6 - z / 400, 0.25, 1.4))
        img = ndimage.gaussian_filter(img, (0.8, 0.8, 0)) * 1.6 + ndimage.gaussian_filter(img, (9, 9, 0)) * 3.0
        img = np.clip(1 - np.exp(-img * 1.3) + np.linspace(0.02, 0.06, H)[:, None, None] * np.array([0.35, 0.45, 0.7]), 0, 1)
        im = Image.fromarray((img * 255).astype(np.uint8))
        W, H = W0, H0
        return im, x, y
    os.makedirs(OUT, exist_ok=True)

    im, x, y = still(0, 9.0)
    d = ImageDraw.Draw(im, "RGBA")
    for yy_ in range(0, 260):
        d.line([(0, yy_), (PW, yy_)], fill=(7, 9, 13, int(235 * min(1, (260 - yy_) / 60))))
    for txt, yy, f in (("169,000 samples.", 70, font(70)), ("One map.", 150, font(70))):
        d.text(((PW - d.textlength(txt, font=f)) / 2, yy), txt, font=f, fill="white")
    f = font(30, False)
    legend = [("kick", "Kicks"), ("snare_clap", "Snares"), ("hat", "Hats"), ("bass", "Bass"), ("melodic", "Melodic"), ("loop", "Loops")]
    lx = 60
    for t, name in legend:
        d.ellipse([lx, PH - 92, lx + 18, PH - 74], fill=TYPE_COLOR[t])
        d.text((lx + 26, PH - 100), name, font=f, fill=(220, 225, 235))
        lx += 36 + d.textlength(name, font=f) + 18
    im.save(os.path.join(OUT, "post1_galaxy.png"))

    im, x, y = still(0, 20.0)
    d = ImageDraw.Draw(im, "RGBA")
    cl = sorted([c for c in G["clusters"] if c["n"] >= 600], key=lambda c: -c["n"])[:40]
    lab = np.array(G["cl"])
    placed = []
    for c in cl:
        m = np.where(lab == G["clusters"].index(c))[0]
        cx, cy = float(np.median(x[m])), float(np.median(y[m]))
        tw = d.textlength(c["label"], font=font(24))
        if not (tw / 2 + 24 < cx < PW - tw / 2 - 24 and 280 < cy < PH - 80):
            continue
        if any(abs(cx - px) < (tw + pw) / 2 + 8 and abs(cy - py) < 34 for px, py, pw in placed):
            continue
        placed.append((cx, cy, tw))
        d.rounded_rectangle([cx - tw / 2 - 8, cy - 16, cx + tw / 2 + 8, cy + 18], 8, fill=(7, 9, 13, 170))
        d.text((cx - tw / 2, cy - 15), c["label"], font=font(24), fill=(230, 235, 245))
        if len(placed) >= 16:
            break
    for yy_ in range(0, 260):
        d.line([(0, yy_), (PW, yy_)], fill=(7, 9, 13, int(235 * min(1, (260 - yy_) / 60))))
    for txt, yy, f in (("It sorted itself", 60, font(64)), ("into 360 islands.", 136, font(64))):
        d.text(((PW - d.textlength(txt, font=f)) / 2, yy), txt, font=f, fill="white")
    im.save(os.path.join(OUT, "post2_islands.png"))

    # kit card with waveforms
    th = {}
    tsv = os.path.join(GAL, "thumbs_cache.tsv")
    if os.path.exists(local_g(tsv)):
        for line in open(local_g(tsv), encoding="utf-8"):
            p, _, h = line.rstrip("\n").partition("\t")
            th[p] = h
    evf = os.path.join(OUT, "demo_events.json")
    ev = json.load(open(evf)) if os.path.exists(evf) else {"parts": {}}
    if not PARTS or any(k not in PARTS for k in ev["parts"]):
        ev = {"parts": {}}                          # an old beat made from other sounds: skip the kit card
    im = Image.new("RGB", (PW, PH), (9, 11, 16))
    d = ImageDraw.Draw(im, "RGBA")
    d.text((70, 80), "One beat,", font=font(66), fill="white")
    d.text((70, 158), "picked from the map.", font=font(66), fill="white")
    d.text((70, 250), "91 BPM in A minor. Drums from an SP-1200 pack,", font=font(30, False), fill=(160, 170, 185))
    d.text((70, 292), "keys and bass from a soul composition.", font=font(30, False), fill=(160, 170, 185))
    rows = [r for r in [("kick", "Kick", "kick"), ("snare", "Snare", "snare_clap"), ("hat", "Closed hat", "hat"),
            ("rhodes", "Rhodes", "melodic"), ("bass", "Piano bass", "bass"), ("nylon", "Nylon guitar", "melodic")] if r[0] in ev["parts"]]
    yy = 400
    for key, name, t in rows:
        p = ev["parts"][key]
        h = th.get(p, "")
        col = TYPE_COLOR[t]
        d.rounded_rectangle([60, yy, PW - 60, yy + 130], 18, fill=(18, 23, 32))
        d.ellipse([90, yy + 52, 116, yy + 78], fill=col)
        d.text((140, yy + 26), name, font=font(38), fill="white")
        d.text((140, yy + 76), p.rsplit("\\", 1)[-1].strip()[:34], font=font(24, False), fill=(130, 140, 155))
        if h:
            vals = [int(h[i:i + 2], 16) / 255 for i in range(0, 64, 2)]
            bx, bw = 600, 13
            for k_, v in enumerate(vals):
                bh = max(3, v * 90)
                d.rounded_rectangle([bx + k_ * bw, yy + 65 - bh / 2, bx + k_ * bw + bw - 4, yy + 65 + bh / 2], 3, fill=col)
        yy += 150
    if rows:
        im.save(os.path.join(OUT, "post3_kit.png"))

    im = Image.new("RGB", (PW, PH), (9, 11, 16))
    d = ImageDraw.Draw(im, "RGBA")
    stats = [("168,899", "sounds measured"), ("35 min", "to analyze all of them"), ("360", "sound islands"),
             ("23,000", "duplicate files found"), ("129", "packs on one map")]
    d.text((70, 80), "What the map found", font=font(60), fill="white")
    yy = 220
    for big_, small_ in stats:
        d.text((70, yy), big_, font=font(110), fill=(242, 169, 59))
        d.text((70, yy + 128), small_, font=font(34, False), fill=(200, 208, 220))
        yy += 214
    im.save(os.path.join(OUT, "post4_stats.png"))
    print("photos ->", OUT)


def local_g(p):
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["audio", "frames", "video", "photos"])
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    a = ap.parse_args()
    {"audio": make_audio, "video": make_video, "photos": make_photos}.get(a.step, lambda: render_frames(a.start, a.end))()


if __name__ == "__main__":
    main()
