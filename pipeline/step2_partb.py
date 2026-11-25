"""
Step 2 - Medicare Part B utilization  (Layer 2: 2-year volume decay).

Pulls the CMS "Medicare Physician & Other Practitioners - by Provider"
annual files for AZ + WA via the data-api (paged), then computes each
provider's 2-year change in unique Medicare beneficiaries and total
services.  Handles CMS suppression (< 11 benes) and full drop-out
(stopped billing Medicare) as distinct wind-down signals.

Outputs:
  data/raw/partb_<year>.parquet        per-year AZ/WA extract (cache)
  data/processed/volume_decay.parquet  NPI-level decay metrics

Run:  python pipeline/step2_partb.py
"""
from __future__ import annotations
import os
import time
import requests
import polars as pl
import config as C

KEEP_COLS = [
    "Rndrng_NPI", "Rndrng_Prvdr_State_Abrvtn", "Rndrng_Prvdr_Type",
    "Rndrng_Prvdr_Ent_Cd", "Tot_Benes", "Tot_Srvcs", "Tot_HCPCS_Cds",
    "Tot_Mdcr_Alowd_Amt",
]


def fetch_year_state(dataset_id: str, state: str, year: int) -> list[dict]:
    """Page through all rows for one state/year."""
    base = f"{C.API_BASE}/{dataset_id}/data"
    out: list[dict] = []
    offset = 0
    while True:
        params = {
            "filter[Rndrng_Prvdr_State_Abrvtn]": state,
            "size": C.API_PAGE_SIZE,
            "offset": offset,
        }
        for attempt in range(5):
            try:
                r = requests.get(base, params=params, timeout=120)
                r.raise_for_status()
                batch = r.json()
                break
            except Exception as e:  # noqa: BLE001
                wait = 2 * (attempt + 1)
                print(f"      retry {attempt+1} ({state} {year} off={offset}): {e}; sleep {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"failed {state} {year} offset {offset}")
        if not batch:
            break
        # keep only needed columns to save memory
        out.extend({k: row.get(k) for k in KEEP_COLS} for row in batch)
        offset += len(batch)
        if len(batch) < C.API_PAGE_SIZE:
            break
    return out


def pull_year(year: int) -> pl.DataFrame:
    cache = os.path.join(C.RAW_DIR, f"partb_{year}.parquet")
    if os.path.exists(cache):
        print(f"[step2] {year}: using cache {cache}")
        return pl.read_parquet(cache)
    dataset_id = C.PARTB_BY_PROVIDER[year]
    rows: list[dict] = []
    for st in C.STATES:
        print(f"[step2] fetching {st} {year} ...")
        part = fetch_year_state(dataset_id, st, year)
        print(f"[step2]   {st} {year}: {len(part):,} rows")
        rows.extend(part)
    df = pl.DataFrame(rows).with_columns(
        pl.col("Rndrng_NPI").cast(pl.Utf8).alias("NPI"),
        pl.col("Tot_Benes").cast(pl.Int64, strict=False).alias("benes"),
        pl.col("Tot_Srvcs").cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).alias("srvcs"),
        pl.lit(year).alias("year"),
    )
    df.write_parquet(cache)
    print(f"[step2] {year}: wrote {cache} ({df.height:,} rows)")
    return df


def main() -> None:
    base_y, latest_y = C.DECAY_BASE_YEAR, C.DECAY_LATEST_YEAR
    frames = {y: pull_year(y) for y in sorted({base_y, latest_y} |
                                              set(C.PARTB_BY_PROVIDER) & {2023})}

    base = frames[base_y].select(
        "NPI",
        pl.col("benes").alias("benes_base"),
        pl.col("srvcs").alias("srvcs_base"),
    )
    latest = frames[latest_y].select(
        "NPI",
        pl.col("benes").alias("benes_latest"),
        pl.col("srvcs").alias("srvcs_latest"),
        pl.col("Rndrng_Prvdr_Type").alias("partb_provider_type"),
    )
    mid = None
    if 2023 in frames:
        mid = frames[2023].select("NPI", pl.col("benes").alias("benes_mid"))

    decay = base.join(latest, on="NPI", how="full", coalesce=True)
    if mid is not None:
        decay = decay.join(mid, on="NPI", how="left")

    # Suppression / dropout flags.
    #  - present both years with benes -> compute pct change
    #  - present base, absent/suppressed latest -> dropout (strong wind-down)
    #  - absent base, present latest -> ramp-up / new
    #  - benes null but row exists -> CMS suppressed (<11 benes)
    decay = decay.with_columns(
        (pl.col("benes_base").is_not_null() & pl.col("benes_latest").is_not_null()).alias("both_years"),
        (pl.col("benes_base").is_not_null() & pl.col("benes_latest").is_null()).alias("dropout"),
    )
    decay = decay.with_columns(
        pl.when(pl.col("both_years") & (pl.col("benes_base") > 0))
        .then((pl.col("benes_latest") - pl.col("benes_base")) / pl.col("benes_base"))
        .otherwise(None)
        .alias("benes_pct_change"),
        pl.when(pl.col("both_years") & (pl.col("srvcs_base") > 0))
        .then((pl.col("srvcs_latest") - pl.col("srvcs_base")) / pl.col("srvcs_base"))
        .otherwise(None)
        .alias("srvcs_pct_change"),
    )
    # Wind-down classification.
    decay = decay.with_columns(
        pl.when(pl.col("dropout"))
        .then(pl.lit("dropout_no_medicare"))
        .when(pl.col("benes_pct_change") <= C.DECAY_FLAG_THRESHOLD)
        .then(pl.lit("active_winddown"))
        .when(pl.col("benes_pct_change").is_not_null())
        .then(pl.lit("stable_or_growing"))
        .otherwise(pl.lit("insufficient_medicare_data"))
        .alias("winddown_status")
    )
    decay = decay.with_columns(
        pl.col("winddown_status").is_in(["active_winddown", "dropout_no_medicare"])
        .alias("is_winddown")
    )

    out = os.path.join(C.PROC_DIR, "volume_decay.parquet")
    decay.write_parquet(out)
    print(f"[step2] wrote {out} ({decay.height:,} rows)")
    print("[step2] winddown_status distribution (all AZ/WA providers):")
    print(decay["winddown_status"].value_counts(sort=True))


if __name__ == "__main__":
    main()
