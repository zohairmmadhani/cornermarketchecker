"""
Build a single self-contained HTML file with ALL data embedded, so it opens
directly in a browser by double-click (no server / no claude.ai needed).

Reads the built web/index.html (Leaflet CSS already inlined) and injects
window.__EMBED__ = {summary, states:{...}} before the app script. The page's
data loader (j()) reads from __EMBED__ when present, else fetch().

Output: web/supply_cliff_radar.html
"""
from __future__ import annotations
import json
import os
import config as C

INDEX = os.path.join(C.WEB_DIR, "index.html")
OUT = os.path.join(C.WEB_DIR, "supply_cliff_radar.html")


def main() -> None:
    html = open(INDEX, encoding="utf-8").read()
    summary = json.load(open(os.path.join(C.WEB_DATA, "summary.json")))
    states = {}
    sd = C.WEB_STATES
    for f in sorted(os.listdir(sd)):
        if f.endswith(".json"):
            states[f[:-5]] = json.load(open(os.path.join(sd, f)))
    embed = {"summary": summary, "states": states}
    payload = json.dumps(embed, separators=(",", ":")).replace("</", "<\\/")
    tag = f"<script>window.__EMBED__={payload};</script>\n"

    # insert right before the Leaflet script so __EMBED__ exists first
    marker = '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>'
    assert marker in html, "leaflet script marker not found"
    html = html.replace(marker, tag + marker, 1)
    open(OUT, "w", encoding="utf-8").write(html)
    mb = os.path.getsize(OUT) / 1e6
    print(f"[standalone] wrote {OUT} ({mb:.1f} MB, {len(states)} states embedded)")


if __name__ == "__main__":
    main()
