"""
Step 1 - Care Compare National Downloadable File  (Layers 1 & 3 backbone).

Inputs : data/raw/DAC_NationalDownloadableFile.csv  (national, ~3.6M rows)
Outputs:
  data/processed/vulnerable_pcps.parquet   one row per vulnerable solo PCP
  data/processed/active_pcps.parquet       all primary-care physician locations
  data/processed/geocode_input.csv         addresses to geocode (Census batch fmt)

Run:  python pipeline/step1_carecompare.py
"""
from __future__ import annotations
import os
import polars as pl
import config as C


def _clean(col: str) -> pl.Expr:
    return pl.col(col).str.strip_chars()


def load_state_slice() -> pl.DataFrame:
    """Load only the AZ/WA rows from the national file (strings first)."""
    lf = pl.scan_csv(C.DAC_CSV_PATH, infer_schema_length=0)
    df = lf.filter(pl.col("State").is_in(C.STATES)).collect()
    print(f"[step1] AZ/WA rows loaded: {df.height:,}  distinct NPIs: {df['NPI'].n_unique():,}")
    return df


def enrich_rows(df: pl.DataFrame) -> pl.DataFrame:
    """Add typed / derived columns at the practice-row level."""
    df = df.with_columns(
        pl.col("num_org_mem").cast(pl.Int64, strict=False).alias("group_size"),
        pl.col("Grd_yr").cast(pl.Int64, strict=False).alias("grad_year"),
        (
            _clean("org_pac_id").is_null() | (_clean("org_pac_id") == "")
        ).alias("no_org"),
        _clean("adr_ln_1").alias("street1"),
        _clean("adr_ln_2").alias("street2"),
        _clean("City/Town").alias("city"),
        pl.col("State").alias("state"),
        _clean("ZIP Code").str.slice(0, 5).alias("zip5"),
    )
    # Primary-care physician flag at the row level.
    df = df.with_columns(
        (
            pl.col("Cred").is_in(C.PHYSICIAN_CREDENTIALS)
            & pl.col("pri_spec").is_in(C.PRIMARY_CARE_SPECIALTIES)
        ).alias("is_pcp_physician")
    )
    return df


def classify_npis(pcp_rows: pl.DataFrame) -> pl.DataFrame:
    """Collapse primary-care-physician rows to one record per NPI."""
    agg = pcp_rows.group_by("NPI").agg(
        pl.col("group_size").min().alias("min_group_size"),
        pl.col("group_size").max().alias("max_group_size"),
        pl.col("no_org").all().alias("all_no_org"),
        pl.col("no_org").any().alias("any_no_org"),
        pl.col("grad_year").min().alias("grad_year"),
        pl.col("pri_spec").first().alias("pri_spec"),
        pl.col("Provider Last Name").first().alias("last_name"),
        pl.col("Provider First Name").first().alias("first_name"),
        pl.col("Cred").first().alias("credential"),
        pl.col("gndr").first().alias("gender"),
        pl.col("Med_sch").first().alias("med_school"),
        pl.col("Telehlth").first().alias("telehealth"),
        pl.len().alias("n_practice_rows"),
        pl.col("org_pac_id").n_unique().alias("n_distinct_orgs"),
    )
    # Practice-type classification.
    #   sole_proprietor: never affiliated to an org (true solo billing)
    #   solo_duo       : only ever in a practice of <= SOLO_MAX_GROUP
    #   group          : everything else
    agg = agg.with_columns(
        pl.when(pl.col("all_no_org"))
        .then(pl.lit("sole_proprietor"))
        .when(pl.col("max_group_size") <= C.SOLO_MAX_GROUP)
        .then(pl.lit("solo_duo"))
        .otherwise(pl.lit("group"))
        .alias("practice_type")
    )
    agg = agg.with_columns(
        pl.col("practice_type").is_in(["sole_proprietor", "solo_duo"]).alias("is_solo")
    )
    # Age proxy: typical US med-school graduation age ~27.
    agg = agg.with_columns(
        (C.CURRENT_YEAR - pl.col("grad_year") + 27).alias("est_age"),
    )
    agg = agg.with_columns(
        (
            pl.col("is_solo")
            & pl.col("grad_year").is_not_null()
            & (pl.col("grad_year") <= C.GRAD_YEAR_CUTOFF)
            & (pl.col("grad_year") >= C.GRAD_YEAR_FLOOR)
        ).alias("is_vulnerable")
    )
    return agg


def pick_practice_location(df_rows: pl.DataFrame, npis: pl.Series) -> pl.DataFrame:
    """
    For each target NPI, choose the single best practice location:
    prefer a solo/no-org row with a real street address.
    """
    rows = df_rows.filter(pl.col("NPI").is_in(npis.implode()))
    rows = rows.with_columns(
        # score: lower = better representative of the *solo* practice
        (
            pl.when(pl.col("no_org")).then(0).otherwise(1)
            + pl.when(pl.col("street1").str.len_chars() > 0).then(0).otherwise(10)
            + pl.col("group_size").fill_null(3)  # smaller practice preferred
        ).alias("_score")
    )
    rows = rows.sort(["NPI", "_score"])
    best = rows.group_by("NPI", maintain_order=True).first()
    return best.select(
        "NPI", "street1", "street2", "city", "state", "zip5",
        pl.col("Facility Name").alias("facility_name"),
        pl.col("Telephone Number").alias("phone"),
        pl.col("group_size").alias("location_group_size"),
        pl.col("adrs_id").alias("address_id"),
    )


def main() -> None:
    df = load_state_slice()
    df = enrich_rows(df)

    pcp_rows = df.filter(pl.col("is_pcp_physician"))
    print(f"[step1] primary-care-physician rows: {pcp_rows.height:,}  "
          f"NPIs: {pcp_rows['NPI'].n_unique():,}")

    npis = classify_npis(pcp_rows)
    print(f"[step1] PCP physicians (NPI level): {npis.height:,}")
    print(f"[step1]   solo: {npis.filter(pl.col('is_solo')).height:,}")
    print(f"[step1]   VULNERABLE (solo + grad<={C.GRAD_YEAR_CUTOFF}): "
          f"{npis.filter(pl.col('is_vulnerable')).height:,}")

    # ---- Active PCP universe (Layer 3): all PCP physicians w/ location ----
    active_loc = pick_practice_location(pcp_rows, npis["NPI"])
    active = npis.join(active_loc, on="NPI", how="left")

    # ---- Stable address key shared by providers + geocode input ----
    # Deterministic integer id per unique (street, city, state, zip).
    addr = (
        active.filter(pl.col("street1").str.len_chars() > 0)
        .select("street1", "city", "state", "zip5")
        .unique()
        .sort(["state", "zip5", "city", "street1"])
        .with_row_index("addr_key")
    )
    active = active.join(addr, on=["street1", "city", "state", "zip5"], how="left")
    active.write_parquet(os.path.join(C.PROC_DIR, "active_pcps.parquet"))
    print(f"[step1] wrote active_pcps.parquet ({active.height:,} rows)")

    # ---- Vulnerable subset (Layer 1) ----
    vuln = active.filter(pl.col("is_vulnerable"))
    vuln.write_parquet(os.path.join(C.PROC_DIR, "vulnerable_pcps.parquet"))
    print(f"[step1] wrote vulnerable_pcps.parquet ({vuln.height:,} rows)")

    # ---- Geocode input: unique addresses (Census batch format) ----
    geo_out = addr.select(
        pl.col("addr_key").alias("id"),
        pl.col("street1").alias("street"),
        pl.col("city"),
        pl.col("state"),
        pl.col("zip5").alias("zip"),
    )
    geo_out.write_csv(os.path.join(C.PROC_DIR, "geocode_input.csv"))
    print(f"[step1] wrote geocode_input.csv ({geo_out.height:,} unique addresses)")

    # practice-type breakdown for docs
    print("[step1] vulnerable practice-type breakdown:")
    print(vuln["practice_type"].value_counts(sort=True))
    print("[step1] vulnerable specialty breakdown:")
    print(vuln["pri_spec"].value_counts(sort=True))
    print("[step1] vulnerable by state:")
    print(vuln["state"].value_counts(sort=True))


if __name__ == "__main__":
    main()
