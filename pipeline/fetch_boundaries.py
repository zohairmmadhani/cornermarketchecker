"""
Fetch generalized US state + county boundaries (TIGERweb) at build time and
bundle as GeoJSON. Counties are paginated (national set exceeds the service
page limit). Saved to data/geo/.
"""
from __future__ import annotations
import json
import os
import time
import requests
import config as C


def _query(url: str, offset: int, count: int, fields: str, offset_param: bool) -> dict:
    params = {
        "where": "1=1", "outFields": fields, "returnGeometry": "true",
        "maxAllowableOffset": "0.01", "outSR": "4326", "f": "geojson",
        "resultRecordCount": count,
    }
    if offset_param:
        params["resultOffset"] = offset
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"   retry {attempt+1} offset {offset}: {e}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"query failed offset {offset}")


def fetch_all(url: str, fields: str) -> list[dict]:
    feats, offset, page = [], 0, 1000
    while True:
        gj = _query(url, offset, page, fields, offset_param=True)
        batch = gj.get("features", [])
        feats.extend(batch)
        if len(batch) < page:
            break
        offset += len(batch)
    return feats


def main() -> None:
    print("[bound] fetching states ...")
    states = fetch_all(C.TIGER_STATE, "NAME,STATE,STUSAB")
    for f in states:
        p = f["properties"]
        p["st"] = p.get("STUSAB") or C.STATE_FIPS.get(p.get("STATE", ""), "")
    sg = {"type": "FeatureCollection",
          "features": [f for f in states if f["properties"].get("st") in C.STATE_NAMES]}
    json.dump(sg, open(os.path.join(C.GEO_DIR, "us_states.geojson"), "w"))
    print(f"[bound] states: {len(sg['features'])}")

    print("[bound] fetching counties ...")
    counties = fetch_all(C.TIGER_COUNTY, "NAME,STATE,GEOID")
    for f in counties:
        p = f["properties"]
        p["st"] = C.STATE_FIPS.get(p.get("STATE", ""), "")
    cg = [f for f in counties if f["properties"].get("st") in C.STATE_NAMES]
    json.dump({"type": "FeatureCollection", "features": cg},
              open(os.path.join(C.GEO_DIR, "us_counties.geojson"), "w"))
    print(f"[bound] counties: {len(cg)}")


if __name__ == "__main__":
    main()
