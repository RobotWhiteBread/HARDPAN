#!/usr/bin/env bash
# setup.sh: one-time setup on Mac or Linux
#   ./setup.sh ~/Music/Samples
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SAMPLES="${1:-$HOME/Music/Samples}"
PROJECTS="${2:-$HOME/Music/soundlab_projects}"
python3 -m pip install --quiet -r "$HERE/requirements.txt"
if [ ! -f "$HERE/soundlab.json" ]; then
  python3 - "$SAMPLES" "$PROJECTS" "$HERE/soundlab.json" <<'PY'
import json, os, sys
s, p, out = sys.argv[1:4]
json.dump({"sample_folders": [{"name": os.path.basename(s.rstrip("/")) or "Samples", "path": s}],
           "library": os.path.join(s, "_library"), "projects": p,
           "reaper": "/Applications/REAPER.app", "downloads": os.path.expanduser("~/Downloads")},
          open(out, "w"), indent=2)
print("Wrote", out, "- open it to add more sample folders.")
PY
else
  echo "Keeping your existing soundlab.json"
fi
python3 "$HERE/soundlab.py" paths
echo
echo "Ready. Next:  python3 \"$HERE/soundlab.py\" all"
