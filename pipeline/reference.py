"""
Shared Census reference data (national, keyless):
  - ZCTA centroids + land area (Gazetteer)   geocode fallback + hex positions
  - ZCTA population (ACS5 B01003)             saturation denominators
  - ZCTA -> county crosswalk (2020 rel file)  county roll-ups / need ranking

All cached in data/raw/ and returned as polars frames.
"""
from __future__ import annotations
import io
import os
import zipfile
import requests
import polars as pl
import config as C


def zcta_centroids() -> pl.DataFrame:
    cache = os.path.join(C.RAW_DIR, "zcta_centroids.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    print("[ref] downloading ZCTA gazetteer ...")
    r = requests.get(C.CENSUS_GAZETTEER_ZCTA_URL, timeout=300)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    raw = zf.read(zf.namelist()[0]).decode("latin-1")
    df = pl.read_csv(io.StringIO(raw), separator="\t", infer_schema_length=0)
    df = df.rename({c: c.strip() for c in df.columns})
    df = df.select(
        pl.col("GEOID").str.strip_chars().str.zfill(5).alias("zcta"),
        pl.col("INTPTLAT").str.strip_chars().cast(pl.Float64).alias("lat"),
        pl.col("INTPTLONG").str.strip_chars().cast(pl.Float64).alias("lon"),
        pl.col("ALAND").str.strip_chars().cast(pl.Float64).alias("aland_m2"),
    )
    df.write_parquet(cache)
    print(f"[ref] ZCTA centroids (national): {df.height:,}")
    return df


def zcta_population() -> pl.DataFrame:
    cache = os.path.join(C.RAW_DIR, "zcta_population.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    print("[ref] downloading ACS5 ZCTA population (summary file) ...")
    r = requests.get(C.ACS_POP_URL, timeout=300)
    r.raise_for_status()
    df = pl.read_csv(io.BytesIO(r.content), separator="|", infer_schema_length=0)
    df = (df.filter(pl.col("GEO_ID").str.starts_with("860Z200US"))
            .select(pl.col("GEO_ID").str.slice(9, 5).alias("zcta"),
                    pl.col("B01003_E001").cast(pl.Int64, strict=False).alias("population")))
    df.write_parquet(cache)
    print(f"[ref] ZCTA population (national): {df.height:,}  pop {df['population'].sum():,}")
    return df


def zcta_county() -> pl.DataFrame:
    """ZCTA -> county FIPS + county name + state abbr (largest-overlap county)."""
    cache = os.path.join(C.RAW_DIR, "zcta_county.parquet")
    if os.path.exists(cache):
        return pl.read_parquet(cache)
    print("[ref] downloading ZCTA->county crosswalk ...")
    r = requests.get(C.ZCTA_COUNTY_XWALK_URL, timeout=300)
    r.raise_for_status()
    df = pl.read_csv(io.BytesIO(r.content), separator="|", infer_schema_length=0,
                     encoding="utf8-lossy")
    df = df.select(
        pl.col("GEOID_ZCTA5_20").str.zfill(5).alias("zcta"),
        pl.col("GEOID_COUNTY_20").str.zfill(5).alias("county_fips"),
        pl.col("NAMELSAD_COUNTY_20").alias("county_name"),
        pl.col("AREALAND_PART").cast(pl.Float64, strict=False).alias("land_part"),
    ).filter(pl.col("zcta").is_not_null() & (pl.col("zcta") != "00000"))
    # a ZCTA can straddle counties; keep the county holding the most land.
    df = (df.sort("land_part", descending=True)
            .group_by("zcta", maintain_order=True)
            .first())
    df = df.with_columns(
        pl.col("county_fips").str.slice(0, 2)
          .replace_strict(C.STATE_FIPS, default=None).alias("state")
    )
    df.write_parquet(cache)
    print(f"[ref] ZCTA->county rows: {df.height:,}")
    return df


if __name__ == "__main__":
    print(zcta_centroids().head(3))
    print(zcta_population().head(3))
    print(zcta_county().head(3))
