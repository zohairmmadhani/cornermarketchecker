"""
Step 1 - Care Compare National Downloadable File (national; Layers 1 & 3).

Outputs:
  data/processed/vulnerable_pcps.parquet   one row per vulnerable solo PCP
  data/processed/active_by_zip.parquet      active PCP-physician supply per ZIP
  data/processed/geocode_input.csv          vulnerable addresses (Census batch fmt)

Downloads the Care Compare file on demand (via catalog) and records its
publication date to the vintage file.
"""
from __future__ import annotations
import os
import urllib.request
import polars as pl
import config as C
import catalog


def _clean(c: str) -> pl.Expr:
    return pl.col(c).str.strip_chars()


def ensure_dac() -> None:
    meta = catalog.care_compare()
    C.update_vintage(care_compare={
        "name": "CMS Care Compare — Doctors & Clinicians National Downloadable File",
        "modified": meta["modified"], "url": meta["csv"],
        "landing": C.CARE_COMPARE_LANDING})
    if os.path.exists(C.DAC_CSV_PATH):
        print(f"[step1] DAC present; Care Compare vintage {meta['modified']}")
        return
    print(f"[step1] downloading Care Compare file (~900 MB), vintage {meta['modified']} ...")
    urllib.request.urlretrieve(meta["csv"], C.DAC_CSV_PATH)


def load_slice() -> pl.DataFrame:
    df = (pl.scan_csv(C.DAC_CSV_PATH, infer_schema_length=0)
            .filter(pl.col("State").is_in(C.STATES))
            .collect())
    print(f"[step1] in-scope rows: {df.height:,}  NPIs: {df['NPI'].n_unique():,}  "
          f"states: {df['State'].n_unique()}")
    return df


def enrich(df: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns(
        pl.col("num_org_mem").cast(pl.Int64, strict=False).alias("group_size"),
        pl.col("Grd_yr").cast(pl.Int64, strict=False).alias("grad_year"),
        (_clean("org_pac_id").is_null() | (_clean("org_pac_id") == "")).alias("no_org"),
        _clean("adr_ln_1").alias("street1"),
        _clean("City/Town").alias("city"),
        pl.col("State").alias("state"),
        _clean("ZIP Code").str.slice(0, 5).alias("zip5"),
    )
    return df.with_columns(
        (pl.col("Cred").is_in(C.PHYSICIAN_CREDENTIALS)
         & pl.col("pri_spec").is_in(C.PRIMARY_CARE_SPECIALTIES)).alias("is_pcp_physician")
    )


def classify(pcp_rows: pl.DataFrame) -> pl.DataFrame:
    agg = pcp_rows.group_by("NPI").agg(
        pl.col("group_size").min().alias("min_group_size"),
        pl.col("group_size").max().alias("max_group_size"),
        pl.col("no_org").all().alias("all_no_org"),
        pl.col("grad_year").min().alias("grad_year"),
        pl.col("pri_spec").first().alias("pri_spec"),
        pl.col("Provider Last Name").first().alias("last_name"),
        pl.col("Provider First Name").first().alias("first_name"),
        pl.col("Cred").first().alias("credential"),
        pl.col("gndr").first().alias("gender"),
        pl.col("Med_sch").first().alias("med_school"),
        pl.col("Telehlth").first().alias("telehealth"),
        pl.len().alias("n_practice_rows"),
    )
    agg = agg.with_columns(
        pl.when(pl.col("all_no_org")).then(pl.lit("sole_proprietor"))
        .when(pl.col("max_group_size") <= C.SOLO_MAX_GROUP).then(pl.lit("solo_duo"))
        .otherwise(pl.lit("group")).alias("practice_type")
    ).with_columns(
        pl.col("practice_type").is_in(["sole_proprietor", "solo_duo"]).alias("is_solo"),
        (C.CURRENT_YEAR - pl.col("grad_year") + 27).alias("est_age"),
    )
    return agg.with_columns(
        (pl.col("is_solo") & pl.col("grad_year").is_not_null()
         & (pl.col("grad_year") <= C.GRAD_YEAR_CUTOFF)
         & (pl.col("grad_year") >= C.GRAD_YEAR_FLOOR)).alias("is_vulnerable")
    )


def pick_location(pcp_rows: pl.DataFrame) -> pl.DataFrame:
    rows = pcp_rows.with_columns(
        (pl.when(pl.col("no_org")).then(0).otherwise(1)
         + pl.when(pl.col("street1").str.len_chars() > 0).then(0).otherwise(10)
         + pl.col("group_size").fill_null(3)).alias("_score")
    ).sort(["NPI", "_score"])
    best = rows.group_by("NPI", maintain_order=True).first()
    return best.select(
        "NPI", "street1", "city", "state", "zip5",
        pl.col("Facility Name").alias("facility_name"),
        pl.col("Telephone Number").alias("phone"),
        pl.col("group_size").alias("location_group_size"),
    )


def main() -> None:
    ensure_dac()
    df = enrich(load_slice())
    pcp_rows = df.filter(pl.col("is_pcp_physician"))
    print(f"[step1] PCP-physician rows: {pcp_rows.height:,}  NPIs: {pcp_rows['NPI'].n_unique():,}")

    npis = classify(pcp_rows)
    loc = pick_location(pcp_rows)
    active = npis.join(loc, on="NPI", how="left")
    print(f"[step1] active PCP physicians: {active.height:,}  "
          f"vulnerable: {active.filter(pl.col('is_vulnerable')).height:,}")

    # Active PCP supply per ZIP (each active PCP counted once at its main site).
    by_zip = (active.filter(pl.col("zip5").str.len_chars() == 5)
                    .group_by(["state", "zip5"]).agg(pl.len().alias("active_pcps")))
    by_zip.write_parquet(os.path.join(C.PROC_DIR, "active_by_zip.parquet"))
    print(f"[step1] active_by_zip rows: {by_zip.height:,}")

    vuln = active.filter(pl.col("is_vulnerable"))
    vuln.write_parquet(os.path.join(C.PROC_DIR, "vulnerable_pcps.parquet"))
    print(f"[step1] wrote vulnerable_pcps.parquet ({vuln.height:,})  "
          f"states with vuln: {vuln['state'].n_unique()}")

    addr = (vuln.filter(pl.col("street1").str.len_chars() > 0)
                .select("street1", "city", "state", "zip5").unique()
                .sort(["state", "zip5", "city", "street1"]).with_row_index("addr_key"))
    # attach addr_key to vulnerable for the geocode join in step4
    vuln2 = vuln.join(addr, on=["street1", "city", "state", "zip5"], how="left")
    vuln2.write_parquet(os.path.join(C.PROC_DIR, "vulnerable_pcps.parquet"))
    addr.select(pl.col("addr_key").alias("id"), pl.col("street1").alias("street"),
                "city", "state", pl.col("zip5").alias("zip")
                ).write_csv(os.path.join(C.PROC_DIR, "geocode_input.csv"))
    print(f"[step1] geocode_input.csv: {addr.height:,} unique vulnerable addresses")

    # store active totals per state for the summary
    (active.filter(pl.col("zip5").str.len_chars() == 5)
           .group_by("state").agg(pl.len().alias("active_pcps"))
           .write_parquet(os.path.join(C.PROC_DIR, "active_by_state.parquet")))

    # PCP-physician NPI -> location, for the county Medicare-market trend
    (active.select("NPI", "zip5", "state")
           .write_parquet(os.path.join(C.PROC_DIR, "active_pcps.parquet")))


if __name__ == "__main__":
    main()
