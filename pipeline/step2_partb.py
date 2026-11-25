"""
Step 2 - Medicare Part B utilization (national; Layer 2: 2-year volume decay).

Resolves the latest 'by Provider' annual files from the CMS catalog, pulls
the bulk national CSVs (cached), keeps only the columns we need, and
computes each provider's change in unique Medicare beneficiaries over the
most recent 2-year span. Records data vintage + source URLs.

Output: data/processed/volume_decay.parquet
"""
from __future__ import annotations
import os
import urllib.request
import polars as pl
import config as C
import catalog

KEEP = {
    "Rndrng_NPI": pl.Utf8, "Rndrng_Prvdr_State_Abrvtn": pl.Utf8,
    "Rndrng_Prvdr_Type": pl.Utf8, "Rndrng_Prvdr_Ent_Cd": pl.Utf8,
    "Tot_Benes": pl.Utf8, "Tot_Srvcs": pl.Utf8,
}


def ensure_year(year: int, url: str) -> pl.DataFrame:
    cache = os.path.join(C.RAW_DIR, f"partb_{year}.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    csv = os.path.join(C.RAW_DIR, f"partb_full_{year}.csv")
    if not os.path.exists(csv):
        print(f"[step2] downloading Part B {year} bulk CSV (~500 MB) ...")
        urllib.request.urlretrieve(url, csv)
    df = (pl.scan_csv(csv, infer_schema_length=0)
            .select(list(KEEP))
            .filter(pl.col("Rndrng_Prvdr_State_Abrvtn").is_in(C.STATES))
            .collect())
    df = df.select(
        pl.col("Rndrng_NPI").alias("NPI"),
        pl.col("Rndrng_Prvdr_State_Abrvtn").alias("state"),
        pl.col("Rndrng_Prvdr_Type").alias("partb_type"),
        pl.col("Tot_Benes").cast(pl.Int64, strict=False).alias("benes"),
        pl.col("Tot_Srvcs").cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).alias("srvcs"),
    )
    df.write_parquet(cache)
    print(f"[step2] {year}: {df.height:,} in-scope provider rows")
    return df


def main() -> None:
    cat = catalog.partb_by_provider()
    latest = max(cat)
    base = latest - C.DECAY_SPAN_YEARS
    if base not in cat:
        base = sorted(cat)[max(0, sorted(cat).index(latest) - C.DECAY_SPAN_YEARS)]
    print(f"[step2] decay span: {base} -> {latest}")
    C.update_vintage(
        partb_latest={"name": f"CMS Medicare Physician & Other Practitioners — by Provider ({latest})",
                      "year": latest, "modified": cat[latest]["modified"],
                      "url": cat[latest]["csv"], "landing": C.PARTB_LANDING},
        partb_base={"name": f"CMS Medicare Physician & Other Practitioners — by Provider ({base})",
                    "year": base, "modified": cat[base]["modified"],
                    "url": cat[base]["csv"], "landing": C.PARTB_LANDING},
    )

    bf = ensure_year(base, cat[base]["csv"]).select(
        "NPI", pl.col("benes").alias("benes_base"), pl.col("srvcs").alias("srvcs_base"))
    lf = ensure_year(latest, cat[latest]["csv"]).select(
        "NPI", pl.col("benes").alias("benes_latest"),
        pl.col("srvcs").alias("srvcs_latest"), pl.col("partb_type"))

    d = bf.join(lf, on="NPI", how="full", coalesce=True)
    d = d.with_columns(
        (pl.col("benes_base").is_not_null() & pl.col("benes_latest").is_not_null()).alias("both"),
        (pl.col("benes_base").is_not_null() & pl.col("benes_latest").is_null()).alias("dropout"),
    ).with_columns(
        pl.when(pl.col("both") & (pl.col("benes_base") > 0))
        .then((pl.col("benes_latest") - pl.col("benes_base")) / pl.col("benes_base"))
        .otherwise(None).alias("benes_pct_change"),
        pl.when(pl.col("both") & (pl.col("srvcs_base") > 0))
        .then((pl.col("srvcs_latest") - pl.col("srvcs_base")) / pl.col("srvcs_base"))
        .otherwise(None).alias("srvcs_pct_change"),
    ).with_columns(
        pl.when(pl.col("dropout")).then(pl.lit("dropout_no_medicare"))
        .when(pl.col("benes_pct_change") <= C.DECAY_FLAG_THRESHOLD).then(pl.lit("active_winddown"))
        .when(pl.col("benes_pct_change").is_not_null()).then(pl.lit("stable_or_growing"))
        .otherwise(pl.lit("insufficient_medicare_data")).alias("winddown_status")
    ).with_columns(
        pl.col("winddown_status").is_in(["active_winddown", "dropout_no_medicare"]).alias("is_winddown")
    )
    out = os.path.join(C.PROC_DIR, "volume_decay.parquet")
    d.write_parquet(out)
    print(f"[step2] wrote {out} ({d.height:,} providers)")
    print(d["winddown_status"].value_counts(sort=True))


if __name__ == "__main__":
    main()
