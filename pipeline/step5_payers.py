"""
Step 5 - Medicare Advantage enrollment by county & insurer (Method 2).

Pulls CMS "Monthly Enrollment by Contract/Plan/State/County" (CPSC), maps
each contract to its parent insurer, filters to Medicare Advantage medical
plans, and aggregates to county x insurer member counts. Feeds the
payer-specific playbook: "which insurer's members are most exposed where
solo PCPs are retiring."

Output: data/processed/county_payers.parquet  (county_fips, payer, members)
Records source + vintage to vintage.json.
"""
from __future__ import annotations
import io
import os
import zipfile
import requests
import polars as pl
import config as C

CPSC_BASE = "https://www.cms.gov/files/zip/monthly-enrollment-cpsc-{month}-{year}.zip"
CPSC_LANDING = ("https://www.cms.gov/data-research/statistics-trends-and-reports/"
                "medicare-advantagepart-d-contract-and-enrollment-data/"
                "monthly-enrollment-contract/plan/state/county")
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

# Normalize messy parent-org names to recognizable payer brands (substring match).
PAYER_MAP = [
    ("unitedhealth", "UnitedHealthcare"),
    ("humana", "Humana"),
    ("cvs health", "Aetna (CVS Health)"),
    ("aetna", "Aetna (CVS Health)"),
    ("kaiser", "Kaiser Permanente"),
    ("elevance", "Anthem / Elevance (BCBS)"),
    ("anthem", "Anthem / Elevance (BCBS)"),
    ("health care service corporation", "BCBS (HCSC)"),
    ("centene", "Centene / WellCare"),
    ("wellcare", "Centene / WellCare"),
    ("cigna", "Cigna"),
    ("molina", "Molina Healthcare"),
    ("scan ", "SCAN Health Plan"),
    ("highmark", "Highmark (BCBS)"),
    ("devoted", "Devoted Health"),
    ("blue cross blue shield of michigan", "BCBS of Michigan"),
    ("healthfirst", "Healthfirst"),
    ("aware integrated", "BCBS of Minnesota"),
    ("lifetime healthcare", "Excellus BCBS"),
    ("alignment", "Alignment Health"),
    ("guidewell", "Florida Blue (BCBS)"),
    ("upmc", "UPMC Health Plan"),
]


def normalize_payer(name: str) -> str:
    low = (name or "").lower()
    for key, brand in PAYER_MAP:
        if key in low:
            return brand
    # trim common corporate suffixes for the long tail
    out = (name or "Unknown").strip()
    for suf in [", Inc.", " Inc.", ", LLC", " LLC", ", L.P.", " Corporation",
                " Mutual Ins. Co.", ", Incorporated", " Group", " Health System"]:
        if out.endswith(suf):
            out = out[: -len(suf)]
    return out.strip() or "Unknown"


def resolve_latest() -> tuple[bytes, str]:
    """Download the newest available CPSC zip; return (bytes, 'YYYY-MM')."""
    import datetime
    today = datetime.date.today()
    y, m = today.year, today.month
    for _ in range(6):  # walk back up to 6 months
        url = CPSC_BASE.format(month=MONTHS[m - 1], year=y)
        try:
            r = requests.get(url, timeout=180)
            if r.ok and r.content[:2] == b"PK" and len(r.content) > 1_000_000:
                return r.content, f"{y}-{m:02d}"
        except Exception:  # noqa: BLE001
            pass
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    raise RuntimeError("could not resolve a CPSC enrollment file")


def main() -> None:
    cache = os.path.join(C.PROC_DIR, "county_payers.parquet")
    raw_zip = os.path.join(C.RAW_DIR, "cpsc.zip")
    vintage_path = os.path.join(C.RAW_DIR, "cpsc_vintage.txt")

    if os.path.exists(cache) and os.path.exists(vintage_path):
        vintage = open(vintage_path).read().strip()
        url = CPSC_BASE.format(month=MONTHS[int(vintage[5:7]) - 1], year=int(vintage[:4]))
        print(f"[step5] using cached county_payers.parquet (vintage {vintage})")
    else:
        if os.path.exists(raw_zip):
            content = open(raw_zip, "rb").read()
            # vintage from a sibling file if present, else parse later
            vintage = open(vintage_path).read().strip() if os.path.exists(vintage_path) else "unknown"
        else:
            print("[step5] downloading CMS CPSC MA enrollment ...")
            content, vintage = resolve_latest()
            open(raw_zip, "wb").write(content)
            open(vintage_path, "w").write(vintage)
        url = CPSC_BASE.format(month=MONTHS[int(vintage[5:7]) - 1], year=int(vintage[:4])) if vintage != "unknown" else CPSC_LANDING

        zf = zipfile.ZipFile(io.BytesIO(content))
        enr_name = next(n for n in zf.namelist() if "Enrollment_Info" in n)
        con_name = next(n for n in zf.namelist() if "Contract_Info" in n)

        def rc(name):
            return pl.read_csv(io.BytesIO(zf.read(name)), infer_schema_length=0, encoding="utf8-lossy")

        enr = rc(enr_name).select(
            pl.col("Contract Number").alias("contract"), pl.col("Plan ID").alias("plan"),
            pl.col("FIPS State County Code").alias("fips"), pl.col("Enrollment").alias("enr"),
        ).filter(pl.col("enr") != "*").with_columns(
            pl.col("enr").cast(pl.Int64, strict=False)
        ).filter(pl.col("enr").is_not_null() & (pl.col("fips").str.len_chars() == 5))

        ci = rc(con_name).select(
            pl.col("Contract ID").alias("contract"), pl.col("Plan ID").alias("plan"),
            pl.col("Plan Type").alias("pt"), pl.col("Organization Type").alias("ot"),
            pl.col("Parent Organization").alias("parent"),
        )
        ma = enr.join(ci, on=["contract", "plan"], how="left").filter(
            ~(pl.col("pt").fill_null("").str.contains("PDP")
              | pl.col("ot").fill_null("").str.contains("Prescription Drug"))
        )
        ma = ma.with_columns(
            pl.col("parent").map_elements(normalize_payer, return_dtype=pl.Utf8).alias("payer"))
        cp = (ma.group_by(["fips", "payer"]).agg(pl.col("enr").sum().alias("members"))
                .rename({"fips": "county_fips"}))
        cp.write_parquet(cache)
        print(f"[step5] wrote county_payers.parquet: {cp.height:,} county-payer rows, "
              f"{cp['members'].sum():,} members, vintage {vintage}")

    C.update_vintage(ma_enrollment={
        "name": f"CMS Medicare Advantage enrollment by county (CPSC, {vintage})",
        "modified": vintage, "url": url, "landing": CPSC_LANDING})


if __name__ == "__main__":
    main()
