"""
Step 5 - Assemble the self-contained map page.

Inlines the vendored Leaflet stylesheet into web/app.html (the Artifact
CSP only allows external stylesheets from Google Fonts), producing the
final web/index.html that is published as the artifact.

Run: python pipeline/build_map.py
"""
import os
import config as C

LEAFLET_CSS = os.path.join(C.WEB_DIR, "vendor", "leaflet-1.9.4.min.css")
TEMPLATE = os.path.join(C.WEB_DIR, "app.html")
OUT = os.path.join(C.WEB_DIR, "index.html")


def main():
    css = open(LEAFLET_CSS, encoding="utf-8").read()
    html = open(TEMPLATE, encoding="utf-8").read()
    if "/*__LEAFLET_CSS__*/" not in html:
        raise SystemExit("marker /*__LEAFLET_CSS__*/ not found in app.html")
    html = html.replace("/*__LEAFLET_CSS__*/", "/* Leaflet 1.9.4 */\n" + css, 1)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"[build_map] wrote {OUT} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
