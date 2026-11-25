"""
Resolve the *latest* CMS source files + their publication dates at run time,
so the pipeline always pulls current data and can stamp it with a vintage.

Two catalogs:
  * data.cms.gov/data.json             — open data (Part B utilization)
  * data.cms.gov/provider-data/...     — Care Compare (National Downloadable File)
"""
from __future__ import annotations
import functools
import requests

DATA_JSON = "https://data.cms.gov/data.json"
PROVIDER_META = ("https://data.cms.gov/provider-data/api/1/metastore/"
                 "schemas/dataset/items/mj5m-pzi6?show-reference-ids=true")
PARTB_PREFIX = "Medicare Physician & Other Practitioners - by Provider :"


@functools.lru_cache(maxsize=1)
def _data_json() -> dict:
    r = requests.get(DATA_JSON, timeout=120)
    r.raise_for_status()
    return r.json()


def partb_by_provider() -> dict[int, dict]:
    """
    {year: {'csv': url, 'modified': 'YYYY-MM-DD', 'period_end': 'YYYY-MM-DD'}}
    for every available Part B 'by Provider' annual file.
    """
    out: dict[int, dict] = {}
    ds = _data_json().get("dataset", [])
    for x in ds:
        t = x.get("title", "")
        if not t.startswith(PARTB_PREFIX):
            continue
        temporal = x.get("temporal", [])
        try:
            period = temporal[0] if isinstance(temporal, list) else temporal
            start = period.get("startDate", "")
            year = int(start[:4])
            period_end = period.get("endDate", "")
        except Exception:
            continue
        csv = None
        for dist in x.get("distribution", []):
            u = dist.get("downloadURL") or dist.get("accessURL", "")
            if u and u.endswith(".csv"):
                csv = u
        if csv:
            out[year] = {"csv": csv, "modified": x.get("modified", ""),
                         "period_end": period_end}
    return out


def care_compare() -> dict:
    """{'csv': url, 'modified': 'YYYY-MM-DD'} for the National Downloadable File."""
    r = requests.get(PROVIDER_META, timeout=120)
    r.raise_for_status()
    d = r.json()
    csv = None
    for dist in d.get("distribution", []):
        data = dist.get("data", dist)
        u = data.get("downloadURL")
        if u and u.endswith(".csv"):
            csv = u
    return {"csv": csv, "modified": d.get("modified", "")}


if __name__ == "__main__":
    pb = partb_by_provider()
    for y in sorted(pb)[-4:]:
        print(y, pb[y]["modified"], pb[y]["csv"][:80])
    print("care compare:", care_compare()["modified"])
