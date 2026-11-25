"""
Step 3 - Geocoding (practice street addresses -> lat/lon).

Primary  : US Census Bureau batch geocoder (free, no key, <=10k/batch).
Fallback : ZCTA population-weighted centroid for the address ZIP.

Input : data/processed/geocode_input.csv   (id, street, city, state, zip)
Output: data/processed/geocodes.parquet     (addr_key, lat, lon, geo_source)

Run: python pipeline/step3_geocode.py
"""
from __future__ import annotations
import io
import os
import time
import requests
import polars as pl
import config as C
import reference

BATCH = 5000  # keep each POST comfortably under the 10k limit


def census_batch(df: pl.DataFrame) -> pl.DataFrame:
    """Geocode a chunk via the Census addressbatch endpoint."""
    buf = io.StringIO()
    # No header, exactly: id, street, city, state, zip
    df.select("id", "street", "city", "state", "zip").write_csv(buf, include_header=False)
    payload = buf.getvalue().encode("utf-8")

    for attempt in range(5):
        try:
            resp = requests.post(
                C.CENSUS_GEOCODER_URL,
                files={"addressFile": ("addresses.csv", payload, "text/csv")},
                data={"benchmark": C.CENSUS_GEOCODER_BENCHMARK},
                timeout=300,
            )
            resp.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001
            wait = 3 * (attempt + 1)
            print(f"   census batch retry {attempt+1}: {e}; sleep {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError("census batch failed")

    # Returned CSV has no header:
    # id, input, match, matchtype, matched_addr, "lon,lat", tigerline, side
    out_cols = ["id", "input_addr", "match", "matchtype",
                "matched_addr", "coords", "tigerline", "side"]
    res = pl.read_csv(
        io.BytesIO(resp.content),
        has_header=False,
        new_columns=out_cols,
        schema_overrides={c: pl.Utf8 for c in out_cols},
        infer_schema_length=0,
    )
    res = res.with_columns(
        pl.when(pl.col("coords").str.contains(","))
        .then(pl.col("coords").str.split(",").list.get(0).cast(pl.Float64, strict=False))
        .otherwise(None)
        .alias("lon"),
        pl.when(pl.col("coords").str.contains(","))
        .then(pl.col("coords").str.split(",").list.get(1).cast(pl.Float64, strict=False))
        .otherwise(None)
        .alias("lat"),
    )
    return res.select("id", "match", "matchtype", "lat", "lon")


def main() -> None:
    src = pl.read_csv(os.path.join(C.PROC_DIR, "geocode_input.csv"),
                      schema_overrides={"id": pl.Utf8, "zip": pl.Utf8})
    src = src.with_columns(pl.col("zip").str.zfill(5))
    print(f"[step3] geocoding {src.height:,} unique addresses ...")

    results = []
    for i in range(0, src.height, BATCH):
        chunk = src.slice(i, BATCH)
        print(f"[step3]   batch {i//BATCH + 1}: rows {i}-{i+chunk.height}")
        results.append(census_batch(chunk))
    geo = pl.concat(results)

    matched = geo.filter(pl.col("lat").is_not_null())
    print(f"[step3] census matched: {matched.height:,}/{geo.height:,} "
          f"({matched.height/geo.height:.1%})")

    # Fallback: ZCTA centroid for unmatched addresses.
    cents = reference.zcta_centroids().select(
        pl.col("zcta"),
        pl.col("lat").alias("z_lat"),
        pl.col("lon").alias("z_lon"),
    )
    geo = geo.join(src.select(pl.col("id"), pl.col("zip").alias("zcta")),
                   on="id", how="left")
    geo = geo.join(cents, on="zcta", how="left")
    geo = geo.with_columns(
        pl.when(pl.col("lat").is_not_null())
        .then(pl.col("lat"))
        .otherwise(pl.col("z_lat"))
        .alias("lat_final"),
        pl.when(pl.col("lon").is_not_null())
        .then(pl.col("lon"))
        .otherwise(pl.col("z_lon"))
        .alias("lon_final"),
        pl.when(pl.col("lat").is_not_null())
        .then(
            pl.when(pl.col("matchtype") == "Exact")
            .then(pl.lit("census_exact"))
            .otherwise(pl.lit("census_nonexact"))
        )
        .when(pl.col("z_lat").is_not_null())
        .then(pl.lit("zcta_centroid"))
        .otherwise(pl.lit("none"))
        .alias("geo_source"),
    )
    out = geo.select(
        pl.col("id").alias("addr_key"),
        pl.col("lat_final").alias("lat"),
        pl.col("lon_final").alias("lon"),
        "geo_source",
    )
    out.write_parquet(os.path.join(C.PROC_DIR, "geocodes.parquet"))
    print(f"[step3] wrote geocodes.parquet ({out.height:,} rows)")
    print("[step3] geo_source breakdown:")
    print(out["geo_source"].value_counts(sort=True))


if __name__ == "__main__":
    main()
