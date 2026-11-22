"""
Shared settings for every soundlab tool.

Settings live in soundlab.json next to this file (copy soundlab.example.json to
start). Point SOUNDLAB_CONFIG at another file to use that instead.

  sample_folders  folders of samples to index. Each entry: {"name", "path"} and
                  optionally "only": a regex of subfolder names to include
  library         where the catalog, measurements and browse folders go
  projects        where new REAPER projects, kits and remixes are saved
  reaper          path to the REAPER program (only for the launcher scripts)
  downloads       where the Sound Galaxy saves kits and carts (your browser's download folder)
  exclude         optional list of regexes; any sample path matching one is left out everywhere
  project_folders optional list of folders holding your REAPER projects (default: projects).
                  The Sound Galaxy reads them to show which sounds you actually use
  exclude_projects optional list of regexes; project file names matching one are skipped
  reaper_paths    optional extra folders handed to REAPER scripts, e.g. {"stoner_doom": "..."}
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DEFAULTS = {
    "sample_folders": [{"name": "Samples", "path": os.path.join(HOME, "Music", "Samples")}],
    "library": os.path.join(HOME, "Music", "Samples", "_library"),
    "projects": os.path.join(HOME, "Music", "soundlab_projects"),
    "reaper": r"C:\Program Files\REAPER (x64)\reaper.exe" if os.name == "nt" else "/Applications/REAPER.app",
    "downloads": os.path.join(HOME, "Downloads"),
}


def _expand(p):
    return os.path.normpath(os.path.expandvars(os.path.expanduser(p))) if isinstance(p, str) else p


def config_path():
    return os.environ.get("SOUNDLAB_CONFIG") or os.path.join(HERE, "soundlab.json")


def load(required=True):
    path = config_path()
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            cfg.update(json.load(fh))
    elif required:
        raise SystemExit(f"No settings file yet. Copy soundlab.example.json to {path} and set your sample folders "
                         f"(or run Setup.ps1 / setup.sh).")
    for k in ("library", "projects", "reaper", "downloads"):
        cfg[k] = _expand(cfg[k])
    cfg["sample_folders"] = [dict(s, path=_expand(s["path"])) for s in cfg["sample_folders"]]
    return cfg


def excluded(path, cfg):
    return any(re.search(rx, path, re.I) for rx in cfg.get("exclude", []))


def lib_file(name, cfg=None):
    cfg = cfg or load()
    return os.path.join(cfg["library"], name)


def basename(p):
    """File name from a path written on Windows or Mac/Linux."""
    return p.replace("\\", "/").rsplit("/", 1)[-1]


def dirname(p):
    return p.replace("\\", "/").rsplit("/", 1)[0] if "/" in p.replace("\\", "/") else ""


def write_reaper_paths(cfg=None):
    """REAPER scripts read their folders from reaper/soundlab_paths.lua (made from soundlab.json)."""
    cfg = cfg or load()
    sep = "\\" if os.name == "nt" else "/"
    out = os.path.join(HERE, "reaper", "soundlab_paths.lua")
    lua = lambda s: "[[" + s.rstrip("\\/") + sep + "]]"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("-- made by soundlab_config.py from soundlab.json; do not edit by hand\n")
        fh.write("SOUNDLAB = {\n")
        fh.write(f"  projects = {lua(cfg['projects'])},\n")
        fh.write(f"  kits = {lua(os.path.join(cfg['projects'], 'kits'))},\n")
        fh.write(f"  downloads = {lua(cfg['downloads'])},\n")
        fh.write(f"  library = {lua(cfg['library'])},\n")
        for k, v in cfg.get("reaper_paths", {}).items():       # extra folders for example scripts
            fh.write(f"  {k} = {lua(_expand(v))},\n")
        fh.write("}\n")
    return out


if __name__ == "__main__":
    c = load()
    print(json.dumps(c, indent=2))
    print("REAPER paths written to", write_reaper_paths(c))
