"""
Run the full Primary Care Supply Cliff pipeline end to end.

    python pipeline/run_all.py            # full run (skips the DAC download if present)
    python pipeline/run_all.py --download # force re-download of the Care Compare file

Steps:
  0. (optional) download Care Compare National Downloadable File
  1. step1_carecompare   -> vulnerable + active PCP tables
  2. step2_partb         -> Medicare volume decay (uses cached per-year extracts)
  3. step3_geocode       -> practice-address lat/lon
  4. step4_build         -> layers, saturation, GeoJSON/CSV/summary for the map
  5. build_map           -> web/index.html

All raw pulls are cached under data/raw/, so re-runs are cheap.
"""
from __future__ import annotations
import os
import sys
import time
import urllib.request
import config as C

import step1_carecompare
import step2_partb
import step3_geocode
import step4_build
import build_map


def download_dac(force: bool = False) -> None:
    if os.path.exists(C.DAC_CSV_PATH) and not force:
        size = os.path.getsize(C.DAC_CSV_PATH)
        print(f"[run] DAC file present ({size/1e6:.0f} MB) — skipping download")
        return
    print("[run] downloading Care Compare National Downloadable File (~900 MB)…")
    urllib.request.urlretrieve(C.DAC_CSV_URL, C.DAC_CSV_PATH)
    print("[run] download complete")


def main() -> None:
    force = "--download" in sys.argv
    t0 = time.time()
    download_dac(force=force)
    for name, mod in [
        ("step1 carecompare", step1_carecompare),
        ("step2 partb", step2_partb),
        ("step3 geocode", step3_geocode),
        ("step4 build", step4_build),
        ("build map", build_map),
    ]:
        print(f"\n{'='*60}\n[run] {name}\n{'='*60}")
        mod.main()
    print(f"\n[run] done in {time.time()-t0:.0f}s. Open web/index.html (served) "
          f"or publish it as an artifact.")


if __name__ == "__main__":
    main()
