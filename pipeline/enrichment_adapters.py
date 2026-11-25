"""
Optional enrichment adapters (modular, not required by the core pipeline).

The core 3-layer pipeline (CMS Care Compare + Part B + Census) is fully
self-contained. These adapters are leading-indicator signals that sharpen
the wind-down call but whose formats vary by state/vendor, so they live
behind a stable contract and are OFF by default.

Contract for every adapter:
  * input : a polars DataFrame of target providers with an "NPI" column
            (and, where useful, name/address/zip for fuzzy matching).
  * output: a polars DataFrame keyed on "NPI" with adapter-specific columns,
            left-joined onto the vulnerable table in step4.
  * never raise on missing data; return an empty frame with the right schema
    so the core pipeline is unaffected when the adapter is unavailable.

Wire an adapter in by importing it in step4 and left-joining on NPI, then
surfacing its columns in the pin properties + detail card.
"""
from __future__ import annotations
import polars as pl

EMPTY = pl.DataFrame({"NPI": []}, schema={"NPI": pl.Utf8})


def state_license_expirations(npis: pl.DataFrame) -> pl.DataFrame:
    """
    AZ (azbomqa / AZ Medical Board) and WA (DOH Provider Credential Search)
    bulk license files -> license status + expiration date per provider.

    High signal: a solo PCP whose license lapses or is on
    "retired/inactive" status inside the next renewal cycle is almost
    certainly winding down. Match on NPI where present, else fuzzy
    name+city. Returns columns:
        NPI, license_status, license_expires, license_source

    Stub: returns EMPTY until a state file is dropped in data/raw/ and
    parsed here.
    """
    return EMPTY.clone()


def google_places_signals(providers: pl.DataFrame) -> pl.DataFrame:
    """
    Google Places / Business Profile -> operating hours, 'permanently
    closed' / 'temporarily closed' flags, review recency.

    Reduced hours (e.g. open 2 days/week) and stale reviews are strong
    wind-down tells. Requires a Places API key; match on name+address.
    Returns columns:
        NPI, business_status, days_open_per_week, last_review_days,
        places_source

    Stub: returns EMPTY until a Places key + fetch are configured.
    """
    return EMPTY.clone()


def nppes_urgent_care(states: list[str]) -> pl.DataFrame:
    """
    NPPES organizations with taxonomy 261QU0200X (Urgent Care) ->
    facility locations to overlay as Layer-3 spillover capacity.

    Returns columns: org_npi, name, lat, lon, zip, state.
    Stub: returns EMPTY; see README for the NPPES API pattern.
    """
    return pl.DataFrame(schema={
        "org_npi": pl.Utf8, "name": pl.Utf8, "lat": pl.Float64,
        "lon": pl.Float64, "zip": pl.Utf8, "state": pl.Utf8})
