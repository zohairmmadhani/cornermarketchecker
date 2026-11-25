"""
Run the full national Primary Care Supply Cliff pipeline end to end.

    python pipeline/run_all.py

Source files (Care Compare ~900 MB, Part B ~500 MB x2) download on first
run via the CMS catalog (latest vintage) and are cached under data/raw/,
so re-runs are cheap. Steps:

  1. step1_carecompare  -> vulnerable pins + active-PCP supply per ZIP
  2. step2_partb        -> 2-year Medicare volume decay (latest 2 years)
  3. step3_geocode      -> vulnerable practice-address lat/lon
  4. fetch_boundaries   -> national state + county outlines
  5. step4_build        -> per-state files, county need, talking points, targets
  6. build_map          -> web/index.html
"""
from __future__ import annotations
import time
import step1_carecompare
import step2_partb
import step3_geocode
import fetch_boundaries
import step4_build
import build_map


def main() -> None:
    t0 = time.time()
    for name, mod in [
        ("step1 carecompare", step1_carecompare),
        ("step2 partb", step2_partb),
        ("step3 geocode", step3_geocode),
        ("fetch boundaries", fetch_boundaries),
        ("step4 build", step4_build),
        ("build map", build_map),
    ]:
        print(f"\n{'='*60}\n[run] {name}\n{'='*60}")
        mod.main()
    print(f"\n[run] done in {time.time()-t0:.0f}s. Serve web/ and open index.html.")


if __name__ == "__main__":
    main()
