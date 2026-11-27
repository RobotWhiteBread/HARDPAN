"""Keeps galaxy/data/mixes.js: the list of Mix Stage songs and Mix Check reports that the
tool bar's Mixes menu shows on every soundlab page."""
import datetime, json, os, re

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "galaxy", "data")
INDEX = os.path.join(DATA, "mixes.js")
PREFIX = "window.MIXES = "


def slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s[:60] or "song"


def load():
    try:
        txt = open(INDEX, encoding="utf-8").read().strip()
        return json.loads(txt[len(PREFIX):].rstrip(";")) if txt.startswith(PREFIX) else []
    except (OSError, ValueError):
        return []


def register(kind, name, href, info=""):
    """Add or refresh one entry, newest first."""
    os.makedirs(DATA, exist_ok=True)
    items = [m for m in load() if not (m.get("kind") == kind and m.get("href") == href)]
    items.insert(0, {"kind": kind, "name": name, "href": href, "info": info,
                     "date": datetime.datetime.now().isoformat(timespec="seconds")})
    with open(INDEX, "w", encoding="utf-8") as fh:
        fh.write(PREFIX + json.dumps(items[:200], separators=(",", ":")) + ";\n")


def backfill(projects_dir):
    """List Mix Check reports and the last Mix Stage made before the Mixes menu existed."""
    have = {m.get("href") for m in load()}
    checks = os.path.join(projects_dir or "", "mix_checks")
    if projects_dir and os.path.isdir(checks):
        for f in sorted(os.listdir(checks)):
            if not f.endswith("_mixcheck.html"):
                continue
            sl = slug(f[:-len("_mixcheck.html")]); href = "data/checks/" + sl + ".html"
            if href in have:
                continue
            html = open(os.path.join(checks, f), encoding="utf-8").read()
            os.makedirs(os.path.join(DATA, "checks"), exist_ok=True)
            with open(os.path.join(DATA, "checks", sl + ".html"), "w", encoding="utf-8") as fh:
                fh.write(html.replace("</body>", '<script src="../../toolbar.js"></script>\n</body>', 1))
            register("check", f[:-len("_mixcheck.html")], href)
    latest = os.path.join(DATA, "stage_data.js")
    if os.path.isfile(latest):
        txt = open(latest, encoding="utf-8").read()
        try:
            song = json.loads(txt[len("window.STAGE = "):].rstrip().rstrip(";")).get("song", "song")
        except ValueError:
            return
        sl = slug(song); href = "stage.html?s=" + sl
        if href not in have:
            os.makedirs(os.path.join(DATA, "stages"), exist_ok=True)
            with open(os.path.join(DATA, "stages", sl + ".js"), "w", encoding="utf-8") as fh:
                fh.write(txt)
            register("stage", song, href)
