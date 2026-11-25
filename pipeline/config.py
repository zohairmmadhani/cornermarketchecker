"""
Central configuration for the Primary Care Supply Cliff pipeline (national).

Source URLs/ids are resolved at run time from the CMS catalog (see
catalog.py) so the pipeline always pulls the latest data and can stamp it
with a vintage; this file holds scope, thresholds and paths.
"""
from __future__ import annotations
import json
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
PROC_DIR = os.path.join(ROOT, "data", "processed")
GEO_DIR = os.path.join(ROOT, "data", "geo")
WEB_DIR = os.path.join(ROOT, "web")
WEB_DATA = os.path.join(WEB_DIR, "data")
WEB_STATES = os.path.join(WEB_DATA, "states")
for _d in (RAW_DIR, PROC_DIR, GEO_DIR, WEB_DIR, WEB_DATA, WEB_STATES):
    os.makedirs(_d, exist_ok=True)

VINTAGE_PATH = os.path.join(PROC_DIR, "vintage.json")
DAC_CSV_PATH = os.path.join(RAW_DIR, "DAC_NationalDownloadableFile.csv")
# Human-facing landing pages for each dataset (shown in the UI next to the
# raw-file download link).
CARE_COMPARE_LANDING = "https://data.cms.gov/provider-data/dataset/mj5m-pzi6"
PARTB_LANDING = ("https://data.cms.gov/provider-summary-by-type-of-service/"
                 "medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider")
ACS_LANDING = "https://www.census.gov/programs-surveys/acs/data/summary-file.html"

# ---------------------------------------------------------------------------
# Geographic scope — all US states + DC + territories (comprehensive)
# ---------------------------------------------------------------------------
STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
STATES = sorted(STATE_NAMES)  # all states in scope
# Full Practice Authority states where NPs can independently own a clinic
# (per AANP State Practice Environment, ~2025; OK added Nov 2025). This is
# the gating filter for where Corner can operate today — verify before acting.
FPA_STATES = {
    "AK", "AZ", "CO", "CT", "DE", "HI", "ID", "IA", "KS", "ME", "MD", "MA",
    "MN", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "ND", "OK", "OR", "RI",
    "SD", "UT", "VT", "WA", "WY", "DC",
}
# States Corner operates in or has named for expansion (brief).
CORNER_ACTIVE = {"AZ", "WA"}
CORNER_EXPANSION = {"NM"}

# ---------------------------------------------------------------------------
# Vulnerability logic (Layer 1)
# ---------------------------------------------------------------------------
PRIMARY_CARE_SPECIALTIES = [
    "FAMILY PRACTICE", "FAMILY MEDICINE", "INTERNAL MEDICINE",
    "GENERAL PRACTICE", "GERIATRIC MEDICINE",
]
PHYSICIAN_CREDENTIALS = ["MD", "DO"]
GRAD_YEAR_CUTOFF = 1986       # proxy for age ~66+
GRAD_YEAR_FLOOR = 1945
SOLO_MAX_GROUP = 2            # Care Compare group size never == 1; solo => null org or <=2

# ---------------------------------------------------------------------------
# Volume decay logic (Layer 2) — years resolved from the catalog at run time.
# ---------------------------------------------------------------------------
DECAY_SPAN_YEARS = 2          # latest vs (latest - 2)
DECAY_FLAG_THRESHOLD = -0.20  # >=20% drop in unique Medicare beneficiaries
CMS_SUPPRESSION_FLOOR = 11

# ---------------------------------------------------------------------------
# Saturation logic (Layer 3)
# ---------------------------------------------------------------------------
CATCHMENT_RADIUS_MI = 4.0     # ~10-minute urban drive-time proxy
H3_RESOLUTION = 7
THIN_SUPPLY_THRESHOLD = 20    # < this many active PCPs within catchment = thin
# HRSA-style primary-care shortage reference: ~1 PCP per 3,500 people = 2.9/10k.
SHORTAGE_PCP_PER_10K = 2.9
# Rough panel size for an orphaned solo practice (brief: 1,000-1,500).
EST_PANEL_SIZE = 1200
# Facility-fee multiplier when an orphaned patient drifts to a hospital clinic.
FACILITY_FEE_MULTIPLIER = "2-3x"
NP_RATE_RANGE = "85-95%"

# ---------------------------------------------------------------------------
# Census reference data (keyless)
# ---------------------------------------------------------------------------
CENSUS_GAZETTEER_ZCTA_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
    "2023_Gaz_zcta_national.zip"
)
ACS_YEAR = 2022
ACS_POP_URL = (
    f"https://www2.census.gov/programs-surveys/acs/summary_file/{ACS_YEAR}/"
    "table-based-SF/data/5YRData/acsdt5y2022-b01003.dat"
)
ZCTA_COUNTY_XWALK_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_county20_natl.txt"
)
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_GEOCODER_BENCHMARK = "Public_AR_Current"

# TIGERweb generalized boundaries (fetched at build time, bundled as geojson)
TIGER_STATE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
               "State_County/MapServer/0/query")
TIGER_COUNTY = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                "State_County/MapServer/1/query")

CURRENT_YEAR = 2026


# ---------------------------------------------------------------------------
# Vintage tracking — each step records the source + date it used.
# ---------------------------------------------------------------------------
def read_vintage() -> dict:
    try:
        with open(VINTAGE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def update_vintage(**kv) -> None:
    v = read_vintage()
    v.update(kv)
    with open(VINTAGE_PATH, "w") as f:
        json.dump(v, f, indent=2)
