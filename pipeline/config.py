"""
Central configuration for the Primary Care Supply Cliff pipeline.

All dataset IDs, thresholds, and paths live here so the rest of the
pipeline stays declarative and easy to audit.
"""
from __future__ import annotations
import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
PROC_DIR = os.path.join(ROOT, "data", "processed")
GEO_DIR = os.path.join(ROOT, "data", "geo")
WEB_DIR = os.path.join(ROOT, "web")
for _d in (RAW_DIR, PROC_DIR, GEO_DIR, WEB_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------------------
# Geographic scope (v1 = Full Practice Authority states Corner operates in)
# ---------------------------------------------------------------------------
STATES = ["AZ", "WA"]
# ZIP prefixes used to subset national ZCTA reference files cheaply.
ZIP_PREFIXES = {
    "AZ": ("85", "86"),
    "WA": ("980", "981", "982", "983", "984", "985", "986", "988", "989",
            "990", "991", "992", "993", "994"),
}

# ---------------------------------------------------------------------------
# Vulnerability logic (Layer 1)
# ---------------------------------------------------------------------------
# Primary-care taxonomies as they appear in the Care Compare `pri_spec` field.
PRIMARY_CARE_SPECIALTIES = [
    "FAMILY PRACTICE",
    "FAMILY MEDICINE",
    "INTERNAL MEDICINE",
    "GENERAL PRACTICE",
    "GERIATRIC MEDICINE",
]
PHYSICIAN_CREDENTIALS = ["MD", "DO"]

# Graduation-year cutoff = proxy for age >= ~66 (retirement window).
GRAD_YEAR_CUTOFF = 1986
# Sanity floor to drop obviously bad grad years.
GRAD_YEAR_FLOOR = 1945

# Solo practice: Care Compare `num_org_mem` never equals 1 in the file;
# true sole proprietors carry a NULL org_pac_id, and small independents
# never appear in a group larger than SOLO_MAX_GROUP.
SOLO_MAX_GROUP = 2

# ---------------------------------------------------------------------------
# Volume decay logic (Layer 2)
# ---------------------------------------------------------------------------
# CMS "Medicare Physician & Other Practitioners - by Provider" annual files.
# One row per rendering NPI per year (Tot_Benes, Tot_Srvcs, etc.).
PARTB_BY_PROVIDER = {
    2024: "8889d81e-2ee7-448f-8713-f071038289b5",
    2023: "8ba584c6-a43a-4b0b-a35a-eb9a59e3a571",
    2022: "21555c17-ec1b-4e74-b2c6-925c6cbb3147",
    2021: "44e0a489-666c-4ea4-a1a2-360b6cdc19db",
}
# Years used for the 2-year decay calc (base -> latest).
DECAY_BASE_YEAR = 2022
DECAY_LATEST_YEAR = 2024
# A pin is flagged "active wind-down" at or beyond this decline.
DECAY_FLAG_THRESHOLD = -0.20  # -20% in unique Medicare beneficiaries
# CMS suppresses rows with < 11 unique beneficiaries.
CMS_SUPPRESSION_FLOOR = 11

# ---------------------------------------------------------------------------
# Care Compare National Downloadable File (Layer 1 + Layer 3 source)
# ---------------------------------------------------------------------------
DAC_CSV_URL = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "52c3f098d7e56028a298fd297cb0b38d_1793398546/DAC_NationalDownloadableFile.csv"
)
DAC_CSV_PATH = os.path.join(RAW_DIR, "DAC_NationalDownloadableFile.csv")

# ---------------------------------------------------------------------------
# Saturation logic (Layer 3)
# ---------------------------------------------------------------------------
# Straight-line radius (miles) used as a ~10-minute urban drive-time proxy
# for the local catchment around each vulnerable pin.
CATCHMENT_RADIUS_MI = 4.0
# H3 resolution for the saturation choropleth (res 7 ~ 5 km hex edge).
H3_RESOLUTION = 7
# A vulnerable pin has "thin local supply" when fewer than this many active
# PCPs sit within the catchment radius (little buffer to absorb patients).
THIN_SUPPLY_THRESHOLD = 20

# Census reference data
CENSUS_GAZETTEER_ZCTA_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
    "2023_Gaz_zcta_national.zip"
)
# ACS 5-year population (B01003) by ZCTA.
# The public data API now requires a registered key, so we pull the
# keyless table-based Summary File instead (pipe-delimited .dat).
ACS_YEAR = 2022
ACS_POP_URL = (
    f"https://www2.census.gov/programs-surveys/acs/summary_file/{ACS_YEAR}/"
    "table-based-SF/data/5YRData/acsdt5y2022-b01003.dat"
)

# Census batch geocoder
CENSUS_GEOCODER_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
)
CENSUS_GEOCODER_BENCHMARK = "Public_AR_Current"

# CMS data-api paging
API_PAGE_SIZE = 5000
API_BASE = "https://data.cms.gov/data-api/v1/dataset"

CURRENT_YEAR = 2026
