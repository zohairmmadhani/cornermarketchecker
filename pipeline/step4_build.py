"""
Step 4 - Assemble layers + Layer 3 saturation, emit web/GTM outputs.

Joins:
  active_pcps  + geocodes           -> positioned active PCP supply
  vulnerable   + volume_decay + geo -> Layer 1 pins w/ Layer 2 signal
Computes Layer 3:
  * active PCPs within CATCHMENT_RADIUS_MI of each vulnerable pin
  * catchment population (sum of ZCTA pops whose centroid is in range)
  * active PCPs per 10k residents (local saturation)
  * H3 hex choropleth of active-PCP density (for the map's area layer)

Outputs (data/processed + web/data):
  vulnerable_pins.geojson   Layer 1 + 2 + local saturation
  active_pcps.geojson       Layer 3 point supply
  saturation_hex.geojson    Layer 3 choropleth
  vulnerable_pcps_export.csv GTM / payer outreach list
  summary.json              headline stats for the map + README

Run: python pipeline/step4_build.py
"""
from __future__ import annotations
import json
import math
import os
import h3
import polars as pl
import config as C
import reference

EARTH_MI = 3958.7561


def haversine_mi(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 2 * EARTH_MI * math.asin(math.sqrt(a))


def load_positioned_active() -> pl.DataFrame:
    active = pl.read_parquet(os.path.join(C.PROC_DIR, "active_pcps.parquet"))
    geo = pl.read_parquet(os.path.join(C.PROC_DIR, "geocodes.parquet"))
    geo = geo.with_columns(pl.col("addr_key").cast(pl.UInt32, strict=False))
    active = active.join(geo, on="addr_key", how="left")
    active = active.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
    return active


def build_saturation(vuln: pl.DataFrame, active: pl.DataFrame) -> pl.DataFrame:
    """For each vulnerable pin, count nearby active PCPs + catchment pop."""
    pop = reference.zcta_population()
    cent = reference.zcta_centroids()
    zp = cent.join(pop, on="zcta", how="inner").select("zcta", "lat", "lon", "population")

    a_lat = active["lat"].to_list()
    a_lon = active["lon"].to_list()
    z_lat = zp["lat"].to_list()
    z_lon = zp["lon"].to_list()
    z_pop = zp["population"].to_list()

    R = C.CATCHMENT_RADIUS_MI
    nearby_counts, catchment_pops, sat = [], [], []
    for lat, lon in zip(vuln["lat"].to_list(), vuln["lon"].to_list()):
        if lat is None or lon is None:
            nearby_counts.append(None); catchment_pops.append(None); sat.append(None)
            continue
        n = sum(1 for la, lo in zip(a_lat, a_lon) if haversine_mi(lat, lon, la, lo) <= R)
        # minus self
        n = max(n - 1, 0)
        p = sum(pp for la, lo, pp in zip(z_lat, z_lon, z_pop)
                if pp and haversine_mi(lat, lon, la, lo) <= R)
        nearby_counts.append(n)
        catchment_pops.append(p)
        sat.append(round(n / p * 10000, 2) if p and p > 0 else None)

    return vuln.with_columns(
        pl.Series("nearby_active_pcps", nearby_counts),
        pl.Series("catchment_population", catchment_pops),
        pl.Series("pcps_per_10k", sat),
    )


def hex_choropleth(active: pl.DataFrame) -> dict:
    """Aggregate active PCPs to H3 hexes + attach population for density."""
    pop = reference.zcta_population()
    cent = reference.zcta_centroids()
    zp = cent.join(pop, on="zcta", how="inner")

    res = C.H3_RESOLUTION
    # active PCP count per hex
    a = active.with_columns(
        pl.struct(["lat", "lon"]).map_elements(
            lambda s: h3.latlng_to_cell(s["lat"], s["lon"], res), return_dtype=pl.Utf8
        ).alias("hex")
    )
    hex_pcp = a.group_by("hex").agg(pl.len().alias("pcp_count"))

    # population per hex (assign each ZCTA population-centroid to a hex)
    z = zp.with_columns(
        pl.struct(["lat", "lon"]).map_elements(
            lambda s: h3.latlng_to_cell(s["lat"], s["lon"], res), return_dtype=pl.Utf8
        ).alias("hex")
    )
    hex_pop = z.group_by("hex").agg(pl.col("population").sum().alias("population"))

    # Base the grid on POPULATED hexes so that people-but-no-PCP areas
    # (true access deserts) appear as 0-PCP shortage cells, not gaps.
    hexes = hex_pop.join(hex_pcp, on="hex", how="full", coalesce=True)
    hexes = hexes.with_columns(
        pl.col("pcp_count").fill_null(0),
        pl.col("population").fill_null(0),
    ).filter(pl.col("population") > 0)
    hexes = hexes.with_columns(
        pl.when(pl.col("population") > 0)
        .then(pl.col("pcp_count") / pl.col("population") * 10000)
        .otherwise(None)
        .alias("pcp_per_10k")
    )

    feats = []
    for row in hexes.iter_rows(named=True):
        boundary = h3.cell_to_boundary(row["hex"])  # [(lat,lon),...]
        ring = [[lon, lat] for lat, lon in boundary]
        ring.append(ring[0])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "hex": row["hex"],
                "pcp_count": row["pcp_count"],
                "population": row["population"],
                "pcp_per_10k": round(row["pcp_per_10k"], 2) if row["pcp_per_10k"] is not None else None,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def vuln_to_geojson(vuln: pl.DataFrame) -> dict:
    feats = []
    for r in vuln.iter_rows(named=True):
        if r["lat"] is None or r["lon"] is None:
            continue
        name = f"{(r['first_name'] or '').title()} {(r['last_name'] or '').title()}".strip()
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "npi": r["NPI"],
                "name": name,
                "credential": r["credential"],
                "specialty": (r["pri_spec"] or "").title(),
                "grad_year": r["grad_year"],
                "est_age": r["est_age"],
                "practice_type": r["practice_type"],
                "facility": r["facility_name"],
                "address": f"{r['street1']}, {r['city']}, {r['state']} {r['zip5']}",
                "city": r["city"], "state": r["state"], "zip": r["zip5"],
                "phone": r["phone"],
                "geo_source": r["geo_source"],
                # Layer 2
                "benes_base": r["benes_base"],
                "benes_latest": r["benes_latest"],
                "benes_pct_change": (round(r["benes_pct_change"] * 100, 1)
                                     if r["benes_pct_change"] is not None else None),
                "srvcs_pct_change": (round(r["srvcs_pct_change"] * 100, 1)
                                     if r["srvcs_pct_change"] is not None else None),
                "winddown_status": r["winddown_status"],
                "is_winddown": bool(r["is_winddown"]) if r["is_winddown"] is not None else False,
                # Layer 3
                "nearby_active_pcps": r["nearby_active_pcps"],
                "catchment_population": r["catchment_population"],
                "pcps_per_10k": r["pcps_per_10k"],
                "thin_local_supply": bool(r["thin_local_supply"]) if r["thin_local_supply"] is not None else None,
                # Transparent priority tier
                "priority_tier": r["priority_tier"],
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def active_to_geojson(active: pl.DataFrame) -> dict:
    feats = []
    for r in active.iter_rows(named=True):
        name = f"{(r['first_name'] or '').title()} {(r['last_name'] or '').title()}".strip()
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "npi": r["NPI"], "name": name,
                "specialty": (r["pri_spec"] or "").title(),
                "practice_type": r["practice_type"],
                "is_solo": bool(r["is_solo"]),
                "est_age": r["est_age"],
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def main() -> None:
    web_data = os.path.join(C.WEB_DIR, "data")
    os.makedirs(web_data, exist_ok=True)

    active = load_positioned_active()
    print(f"[step4] positioned active PCPs: {active.height:,}")

    vuln = pl.read_parquet(os.path.join(C.PROC_DIR, "vulnerable_pcps.parquet"))
    decay = pl.read_parquet(os.path.join(C.PROC_DIR, "volume_decay.parquet"))
    geo = pl.read_parquet(os.path.join(C.PROC_DIR, "geocodes.parquet")).with_columns(
        pl.col("addr_key").cast(pl.UInt32, strict=False))

    vuln = vuln.join(decay, on="NPI", how="left").join(geo, on="addr_key", how="left")
    vuln = build_saturation(vuln, active)
    print(f"[step4] vulnerable pins positioned: "
          f"{vuln.filter(pl.col('lat').is_not_null()).height}/{vuln.height}")

    # ---- Transparent priority tier (two visible booleans, no black box) ----
    vuln = vuln.with_columns(
        (pl.col("nearby_active_pcps") < C.THIN_SUPPLY_THRESHOLD).alias("thin_local_supply"),
        pl.col("is_winddown").fill_null(False).alias("is_winddown"),
    )
    vuln = vuln.with_columns(
        pl.when(pl.col("is_winddown") & pl.col("thin_local_supply"))
        .then(pl.lit("A"))
        .when(pl.col("is_winddown") | pl.col("thin_local_supply"))
        .then(pl.lit("B"))
        .otherwise(pl.lit("C"))
        .alias("priority_tier")
    )
    print("[step4] priority tiers:")
    print(vuln["priority_tier"].value_counts(sort=True))

    # ---- GeoJSON outputs ----
    vgj = vuln_to_geojson(vuln)
    with open(os.path.join(web_data, "vulnerable_pins.geojson"), "w") as f:
        json.dump(vgj, f)
    agj = active_to_geojson(active)
    with open(os.path.join(web_data, "active_pcps.geojson"), "w") as f:
        json.dump(agj, f)
    hgj = hex_choropleth(active)
    with open(os.path.join(web_data, "saturation_hex.geojson"), "w") as f:
        json.dump(hgj, f)
    print(f"[step4] geojson: {len(vgj['features'])} pins, "
          f"{len(agj['features'])} active, {len(hgj['features'])} hexes")

    # ---- CSV export for GTM / payer outreach ----
    export = vuln.select(
        pl.col("priority_tier"),
        pl.col("NPI").alias("npi"),
        pl.col("first_name"), pl.col("last_name"),
        pl.col("credential"), pl.col("pri_spec").alias("specialty"),
        pl.col("grad_year"), pl.col("est_age"), pl.col("practice_type"),
        pl.col("facility_name"), pl.col("street1").alias("address"),
        pl.col("city"), pl.col("state"), pl.col("zip5").alias("zip"),
        pl.col("phone"),
        pl.col("benes_base"), pl.col("benes_latest"),
        (pl.col("benes_pct_change") * 100).round(1).alias("benes_pct_change"),
        pl.col("winddown_status"), pl.col("is_winddown"),
        pl.col("nearby_active_pcps"), pl.col("thin_local_supply"),
        pl.col("catchment_population"), pl.col("pcps_per_10k"),
        pl.col("lat"), pl.col("lon"), pl.col("geo_source"),
    ).sort(["priority_tier", "benes_pct_change"], descending=[False, False])
    export.write_csv(os.path.join(C.PROC_DIR, "vulnerable_pcps_export.csv"))
    export.write_csv(os.path.join(web_data, "vulnerable_pcps_export.csv"))
    print(f"[step4] wrote vulnerable_pcps_export.csv ({export.height} rows)")

    # ---- Summary stats ----
    winddown = vuln.filter(pl.col("is_winddown") == True)
    summary = {
        "generated": "2026-11-25",
        "scope_states": C.STATES,
        "grad_year_cutoff": C.GRAD_YEAR_CUTOFF,
        "catchment_radius_mi": C.CATCHMENT_RADIUS_MI,
        "decay_years": [C.DECAY_BASE_YEAR, C.DECAY_LATEST_YEAR],
        "vulnerable_total": int(vuln.height),
        "vulnerable_by_state": {k: int(v) for k, v in
                                vuln["state"].value_counts().iter_rows()},
        "active_winddown": int(winddown.height),
        "dropout_no_medicare": int(vuln.filter(
            pl.col("winddown_status") == "dropout_no_medicare").height),
        "active_pcps_positioned": int(active.height),
        "median_nearby_active_pcps": float(
            vuln["nearby_active_pcps"].median() or 0),
        "thin_local_supply": int(vuln.filter(pl.col("thin_local_supply")).height),
        "thin_supply_threshold": C.THIN_SUPPLY_THRESHOLD,
        "tier_a": int(vuln.filter(pl.col("priority_tier") == "A").height),
        "tier_b": int(vuln.filter(pl.col("priority_tier") == "B").height),
        "tier_c": int(vuln.filter(pl.col("priority_tier") == "C").height),
        "specialty_breakdown": {k: int(v) for k, v in
                                vuln["pri_spec"].value_counts().iter_rows()},
    }
    with open(os.path.join(web_data, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(C.PROC_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("[step4] summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
