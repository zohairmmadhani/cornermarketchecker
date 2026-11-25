"""
Shared Census reference data:
  - ZCTA centroids + land area (Gazetteer)  -> geocode fallback + hex pop
  - ZCTA population (ACS5 B01003)            -> saturation denominators

Outputs cached in data/raw/ ; returns polars frames filtered to AZ/WA ZIPs.
"""
from __future__ import annotations
import io
import os
import zipfile
import requests
import polars as pl
import config as C


def _in_scope_zip(zip_col: pl.Expr) -> pl.Expr:
    prefixes = tuple(p for ps in C.ZIP_PREFIXES.values() for p in ps)
    cond = pl.lit(False)
    for p in prefixes:
        cond = cond | zip_col.str.starts_with(p)
    return cond


def zcta_centroids() -> pl.DataFrame:
    """ZCTA -> lat/lon + land area (sq meters), AZ/WA scope."""
    cache = os.path.join(C.RAW_DIR, "zcta_centroids.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    print("[ref] downloading ZCTA gazetteer ...")
    r = requests.get(C.CENSUS_GAZETTEER_ZCTA_URL, timeout=180)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    name = zf.namelist()[0]
    raw = zf.read(name).decode("latin-1")
    # Tab-separated, trailing spaces in column names.
    df = pl.read_csv(io.StringIO(raw), separator="\t", infer_schema_length=0)
    df = df.rename({c: c.strip() for c in df.columns})
    df = df.select(
        pl.col("GEOID").str.strip_chars().str.zfill(5).alias("zcta"),
        pl.col("INTPTLAT").str.strip_chars().cast(pl.Float64).alias("lat"),
        pl.col("INTPTLONG").str.strip_chars().cast(pl.Float64).alias("lon"),
        pl.col("ALAND").str.strip_chars().cast(pl.Float64).alias("aland_m2"),
    ).filter(_in_scope_zip(pl.col("zcta")))
    df.write_parquet(cache)
    print(f"[ref] ZCTA centroids (AZ/WA): {df.height:,}")
    return df


def zcta_population() -> pl.DataFrame:
    """
    ZCTA -> total population (ACS5 2022, table B01003), AZ/WA scope.

    Uses the keyless table-based Summary File. ZCTA rows carry a
    GEO_ID of the form '860Z200US<zip5>'; E001 is the population estimate.
    """
    cache = os.path.join(C.RAW_DIR, "zcta_population.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    print("[ref] downloading ACS5 ZCTA population (summary file) ...")
    r = requests.get(C.ACS_POP_URL, timeout=300)
    r.raise_for_status()
    df = pl.read_csv(io.BytesIO(r.content), separator="|", infer_schema_length=0)
    df = (
        df.filter(pl.col("GEO_ID").str.starts_with("860Z200US"))
        .select(
            pl.col("GEO_ID").str.slice(9, 5).alias("zcta"),
            pl.col("B01003_E001").cast(pl.Int64, strict=False).alias("population"),
        )
        .filter(_in_scope_zip(pl.col("zcta")))
    )
    df.write_parquet(cache)
    print(f"[ref] ZCTA population (AZ/WA): {df.height:,}  "
          f"total pop: {df['population'].sum():,}")
    return df


if __name__ == "__main__":
    c = zcta_centroids()
    p = zcta_population()
    print(c.head())
    print(p.head())
