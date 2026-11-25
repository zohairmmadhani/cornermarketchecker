"""
Step 4 - Assemble national layers, per-county need ranking, payer talking
points, target-state ranking, and per-state map files.

Outputs:
  web/data/summary.json              national + per-state stats, sources, targets
  web/data/us_states.geojson         state outlines (context + dropdown)
  web/data/states/<ST>.json          pins + hexes + counties + talking points
  data/processed/vulnerable_export.csv   national GTM list
"""
from __future__ import annotations
import json
import os
import numpy as np
import h3
import polars as pl
import config as C
import reference

EARTH_MI = 3958.7561


# --------------------------------------------------------------------------
# loading / positioning
# --------------------------------------------------------------------------
def load_pins() -> pl.DataFrame:
    vuln = pl.read_parquet(os.path.join(C.PROC_DIR, "vulnerable_pcps.parquet"))
    decay = pl.read_parquet(os.path.join(C.PROC_DIR, "volume_decay.parquet")).select(
        "NPI", "benes_base", "benes_latest", "benes_pct_change",
        "srvcs_pct_change", "winddown_status", "is_winddown")
    geo = pl.read_parquet(os.path.join(C.PROC_DIR, "geocodes.parquet")).with_columns(
        pl.col("addr_key").cast(pl.UInt32, strict=False))
    xwalk = reference.zcta_county().select(
        pl.col("zcta").alias("zip5"), "county_fips", "county_name")
    pins = (vuln.join(decay, on="NPI", how="left")
                .join(geo, on="addr_key", how="left")
                .join(xwalk, on="zip5", how="left"))
    pins = pins.with_columns(pl.col("is_winddown").fill_null(False))
    return pins.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())


def supply_and_pop():
    cents = reference.zcta_centroids().select("zcta", "lat", "lon")
    pop = reference.zcta_population()
    xwalk = reference.zcta_county().select("zcta", "county_fips", "county_name", "state")
    pop_pts = (cents.join(pop, on="zcta", how="inner")
                    .join(xwalk, on="zcta", how="left")
                    .filter(pl.col("population") > 0))
    by_zip = pl.read_parquet(os.path.join(C.PROC_DIR, "active_by_zip.parquet"))
    supply_pts = (by_zip.join(cents, left_on="zip5", right_on="zcta", how="inner")
                        .join(xwalk, left_on="zip5", right_on="zcta", how="left"))
    return supply_pts, pop_pts


# --------------------------------------------------------------------------
# per-pin saturation (numpy, bbox-prefiltered haversine)
# --------------------------------------------------------------------------
def _hav(lat1, lon1, lat2, lon2):
    p = np.pi / 180.0
    a = (0.5 - np.cos((lat2 - lat1) * p) / 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * (1 - np.cos((lon2 - lon1) * p)) / 2)
    return 2 * EARTH_MI * np.arcsin(np.sqrt(a))


def saturation(pins: pl.DataFrame, supply_pts: pl.DataFrame, pop_pts: pl.DataFrame):
    R = C.CATCHMENT_RADIUS_MI
    s_lat = supply_pts["lat"].to_numpy(); s_lon = supply_pts["lon"].to_numpy()
    s_cnt = supply_pts["active_pcps"].to_numpy()
    p_lat = pop_pts["lat"].to_numpy(); p_lon = pop_pts["lon"].to_numpy()
    p_pop = pop_pts["population"].to_numpy()
    dlat = R / 69.0 + 0.02
    nearby, catch = [], []
    for la, lo in zip(pins["lat"].to_numpy(), pins["lon"].to_numpy()):
        dlon = R / (69.0 * max(np.cos(la * np.pi / 180), 0.2)) + 0.02
        sm = (np.abs(s_lat - la) < dlat) & (np.abs(s_lon - lo) < dlon)
        if sm.any():
            d = _hav(la, lo, s_lat[sm], s_lon[sm])
            nearby.append(int(s_cnt[sm][d <= R].sum()))
        else:
            nearby.append(0)
        pm = (np.abs(p_lat - la) < dlat) & (np.abs(p_lon - lo) < dlon)
        if pm.any():
            d = _hav(la, lo, p_lat[pm], p_lon[pm])
            catch.append(int(p_pop[pm][d <= R].sum()))
        else:
            catch.append(0)
    nearby = [max(n - 1, 0) for n in nearby]  # exclude self
    per10k = [round(n / p * 10000, 2) if p > 0 else None for n, p in zip(nearby, catch)]
    return pins.with_columns(
        pl.Series("nearby_active_pcps", nearby),
        pl.Series("catchment_population", catch),
        pl.Series("pcps_per_10k", per10k),
    ).with_columns(
        (pl.col("nearby_active_pcps") < C.THIN_SUPPLY_THRESHOLD).alias("thin_local_supply")
    ).with_columns(
        pl.when(pl.col("is_winddown") & pl.col("thin_local_supply")).then(pl.lit("A"))
        .when(pl.col("is_winddown") | pl.col("thin_local_supply")).then(pl.lit("B"))
        .otherwise(pl.lit("C")).alias("priority_tier")
    )


# --------------------------------------------------------------------------
# county roll-up + need ranking
# --------------------------------------------------------------------------
def county_rollup(pins: pl.DataFrame, supply_pts, pop_pts) -> pl.DataFrame:
    xwalk = reference.zcta_county()
    by_zip = pl.read_parquet(os.path.join(C.PROC_DIR, "active_by_zip.parquet"))
    active_cty = (by_zip.join(xwalk.select(pl.col("zcta"), "county_fips"),
                              left_on="zip5", right_on="zcta", how="left")
                        .group_by("county_fips").agg(pl.col("active_pcps").sum().alias("active_pcps")))
    pop_cty = (reference.zcta_population().join(xwalk.select("zcta", "county_fips"), on="zcta", how="left")
               .group_by("county_fips").agg(pl.col("population").sum().alias("population")))
    pins_cty = pins.group_by(["state", "county_fips", "county_name"]).agg(
        pl.len().alias("vulnerable"),
        pl.col("is_winddown").sum().alias("winddown"),
        (pl.col("priority_tier") == "A").sum().alias("tier_a"),
        pl.when(pl.col("is_winddown")).then(pl.col("benes_latest")).otherwise(0).sum().alias("medicare_at_risk"),
    )
    c = (pins_cty.join(active_cty, on="county_fips", how="left")
                 .join(pop_cty, on="county_fips", how="left")
                 .with_columns(pl.col("active_pcps").fill_null(0), pl.col("population").fill_null(0)))
    c = c.with_columns(
        pl.when(pl.col("population") > 0)
        .then((pl.col("active_pcps") / pl.col("population") * 10000).round(2))
        .otherwise(None).alias("pcp_per_10k")
    )
    return c


# --------------------------------------------------------------------------
# geojson helpers
# --------------------------------------------------------------------------
def _title(s): return (s or "").title()


def _round_coords(x, nd):
    if isinstance(x, (int, float)):
        return round(x, nd)
    if isinstance(x, list):
        return [_round_coords(v, nd) for v in x]
    return x


def _round_features(feats, nd=4):
    for f in feats:
        g = f.get("geometry")
        if g and "coordinates" in g:
            g["coordinates"] = _round_coords(g["coordinates"], nd)
    return feats


def pin_feature(r):
    name = f"{_title(r['first_name'])} {_title(r['last_name'])}".strip()
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "npi": r["NPI"], "name": name, "credential": r["credential"],
                "specialty": _title(r["pri_spec"]), "grad_year": r["grad_year"],
                "est_age": r["est_age"], "practice_type": r["practice_type"],
                "facility": r["facility_name"],
                "address": f"{_title(r['street1'])}, {_title(r['city'])}, {r['state']} {r['zip5']}",
                "city": _title(r["city"]), "state": r["state"], "zip": r["zip5"],
                "county": r["county_name"], "phone": r["phone"], "geo_source": r["geo_source"],
                "benes_base": r["benes_base"], "benes_latest": r["benes_latest"],
                "benes_pct_change": (round(r["benes_pct_change"] * 100, 1)
                                     if r["benes_pct_change"] is not None else None),
                "winddown_status": r["winddown_status"], "is_winddown": bool(r["is_winddown"]),
                "nearby_active_pcps": r["nearby_active_pcps"],
                "catchment_population": r["catchment_population"],
                "pcps_per_10k": r["pcps_per_10k"],
                "thin_local_supply": bool(r["thin_local_supply"]),
                "priority_tier": r["priority_tier"]}}


def state_hexes(state, supply_pts, pop_pts) -> list[dict]:
    res = C.H3_RESOLUTION
    sp = supply_pts.filter(pl.col("state") == state)
    pp = pop_pts.filter(pl.col("state") == state)
    if pp.height == 0:
        return []
    def cell(df):
        return df.with_columns(pl.struct(["lat", "lon"]).map_elements(
            lambda s: h3.latlng_to_cell(s["lat"], s["lon"], res), return_dtype=pl.Utf8).alias("hex"))
    hp = cell(pp).group_by("hex").agg(pl.col("population").sum().alias("population"))
    hs = cell(sp).group_by("hex").agg(pl.col("active_pcps").sum().alias("pcp_count")) if sp.height else \
        pl.DataFrame(schema={"hex": pl.Utf8, "pcp_count": pl.Int64})
    hexes = hp.join(hs, on="hex", how="left").with_columns(pl.col("pcp_count").fill_null(0))
    feats = []
    for row in hexes.iter_rows(named=True):
        ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(row["hex"])]
        ring.append(ring[0])
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"pcp_count": row["pcp_count"], "population": row["population"]}})
    return feats


# --------------------------------------------------------------------------
# talking points
# --------------------------------------------------------------------------
def talking_points(state, sp, counties) -> list[str]:
    name = C.STATE_NAMES[state]
    wind = sp.filter(pl.col("is_winddown"))
    n_vuln, n_wind = sp.height, wind.height
    tier_a = sp.filter(pl.col("priority_tier") == "A").height
    med_risk = int(wind["benes_latest"].fill_null(0).sum())
    # compliance hotspots: ZIPs with >=2 winding-down solo PCPs
    zc = (wind.group_by(["zip5", "city"]).agg(pl.len().alias("n"))
              .filter(pl.col("n") >= 2).sort("n", descending=True))
    comp_zip_n = zc.height
    top_zips = ", ".join(f"{_title(r['city'])} {r['zip5']}" for r in zc.head(3).iter_rows(named=True))
    short = counties.filter((pl.col("pcp_per_10k") < C.SHORTAGE_PCP_PER_10K)
                            & (pl.col("winddown") > 0)).sort("winddown", descending=True)
    top_short = ", ".join(r["county_name"] for r in short.head(3).iter_rows(named=True))
    tp = []
    tp.append(
        f"**Network adequacy:** {n_wind} solo PCPs winding down in {name}" + (
            f"; {comp_zip_n} ZIP(s) have 2+ at once"
            + (f" ({top_zips})" if top_zips else "")
            + " — each a ~12-month drive-time compliance gap."
            if comp_zip_n else ", several with no local backup — drive-time gap risk."))
    tp.append(
        f"**Facility-fee shield:** ~{med_risk:,} Medicare patients at risk. In hospital clinics they cost "
        f"{C.FACILITY_FEE_MULTIPLIER} more (facility fees); Corner NPs hold them at {C.NP_RATE_RANGE} of physician rates.")
    if top_short:
        tp.append(
            f"**Auto-assignment:** thinnest-supply counties ({top_short}) — where unattributed members and "
            f"HEDIS gaps concentrate. Prime for auto-assignment or co-funded outreach.")
    tp.append(
        f"**Ask:** fast-track Corner credentialing. {tier_a} Tier-A practice(s) are top priority; "
        f"~{n_vuln*C.EST_PANEL_SIZE:,} patients across {n_vuln} at-risk practices statewide.")
    return tp


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    pins = saturation(load_pins(), *supply_and_pop())
    supply_pts, pop_pts = supply_and_pop()
    counties_all = county_rollup(pins, supply_pts, pop_pts)
    active_state = pl.read_parquet(os.path.join(C.PROC_DIR, "active_by_state.parquet"))
    pop_state = (reference.zcta_population().join(
        reference.zcta_county().select("zcta", "state"), on="zcta", how="left")
        .group_by("state").agg(pl.col("population").sum().alias("population")))

    print(f"[step4] pins positioned: {pins.height:,}")
    us_counties = json.load(open(os.path.join(C.GEO_DIR, "us_counties.geojson")))
    counties_by_state: dict[str, list] = {}
    for f in us_counties["features"]:
        counties_by_state.setdefault(f["properties"]["st"], []).append(f)

    vint = C.read_vintage()
    states_out = {}
    present_states = sorted(set(pins["state"].to_list()))

    for st in present_states:
        sp = pins.filter(pl.col("state") == st)
        cty = counties_all.filter(pl.col("state") == st)
        # per-county need list (worst first)
        need = (cty.sort(["winddown", "vulnerable"], descending=True)
                   .select("county_name", "vulnerable", "winddown", "tier_a",
                           "active_pcps", "population", "pcp_per_10k", "medicare_at_risk")
                   .head(12).to_dicts())
        tps = talking_points(st, sp, cty)
        hexes = state_hexes(st, supply_pts, pop_pts)
        # bbox
        la = sp["lat"].to_list(); lo = sp["lon"].to_list()
        bbox = [min(lo), min(la), max(lo), max(la)]
        state_file = {
            "state": st, "name": C.STATE_NAMES[st],
            "pins": _round_features([pin_feature(r) for r in sp.iter_rows(named=True)], 5),
            "hexes": _round_features(hexes, 4),
            "counties": _round_features(counties_by_state.get(st, []), 3),
            "talking_points": tps, "counties_need": need, "bbox": bbox,
        }
        json.dump(state_file, open(os.path.join(C.WEB_STATES, f"{st}.json"), "w"))

        apcp = int(active_state.filter(pl.col("state") == st)["active_pcps"].sum())
        spop = int(pop_state.filter(pl.col("state") == st)["population"].sum() or 0)
        states_out[st] = {
            "name": C.STATE_NAMES[st], "fpa": st in C.FPA_STATES,
            "corner_status": ("active" if st in C.CORNER_ACTIVE else
                              "expansion" if st in C.CORNER_EXPANSION else
                              "fpa" if st in C.FPA_STATES else "non_fpa"),
            "vulnerable": sp.height,
            "winddown": int(sp["is_winddown"].sum()),
            "tier_a": sp.filter(pl.col("priority_tier") == "A").height,
            "tier_b": sp.filter(pl.col("priority_tier") == "B").height,
            "tier_c": sp.filter(pl.col("priority_tier") == "C").height,
            "thin": int(sp["thin_local_supply"].sum()),
            "active_pcps": apcp,
            "population": spop,
            "pcp_per_10k": round(apcp / spop * 10000, 2) if spop else None,
            "medicare_at_risk": int(sp.filter(pl.col("is_winddown"))["benes_latest"].fill_null(0).sum()),
            "compliance_zips": int(sp.filter(pl.col("is_winddown"))
                                   .group_by("zip5").len().filter(pl.col("len") >= 2).height),
            "shortage_counties": int(cty.filter((pl.col("pcp_per_10k") < C.SHORTAGE_PCP_PER_10K)
                                                & (pl.col("winddown") > 0)).height),
        }

    # ---- target-state ranking (FPA expansion priorities) ----
    rank = []
    for st, s in states_out.items():
        if s["corner_status"] in ("active",):
            continue
        if not s["fpa"]:
            continue
        rank.append((st, s["winddown"], s["tier_a"], s["shortage_counties"]))
    rank.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
    targets = [{"state": st, "name": C.STATE_NAMES[st], "winddown": w,
                "tier_a": a, "shortage_counties": sc,
                "status": states_out[st]["corner_status"]} for st, w, a, sc in rank]

    national = {
        "states_covered": len(present_states),
        "vulnerable_total": pins.height,
        "winddown_total": int(pins["is_winddown"].sum()),
        "tier_a_total": pins.filter(pl.col("priority_tier") == "A").height,
        "active_pcps_total": int(active_state["active_pcps"].sum()),
        "medicare_at_risk_total": int(pins.filter(pl.col("is_winddown"))["benes_latest"].fill_null(0).sum()),
        "fpa_vulnerable": int(sum(s["vulnerable"] for s in states_out.values() if s["fpa"])),
    }
    sources = [vint.get(k) for k in ("care_compare", "partb_latest", "partb_base") if vint.get(k)]
    sources += [
        {"name": f"U.S. Census ACS 5-year {C.ACS_YEAR} — population (table B01003)",
         "modified": f"{C.ACS_YEAR}", "url": C.ACS_POP_URL, "landing": C.ACS_LANDING},
        {"name": "U.S. Census ZCTA↔County Relationship File (2020)",
         "modified": "2020", "url": C.ZCTA_COUNTY_XWALK_URL, "landing": C.ACS_LANDING},
    ]
    summary = {
        "generated": "2026-11-25",
        "methodology": {
            "vulnerable": f"solo MD/DO in family/internal/general practice, grad yr ≤ {C.GRAD_YEAR_CUTOFF}",
            "winddown": f"≥{int(abs(C.DECAY_FLAG_THRESHOLD)*100)}% fewer Medicare patients "
                        f"{vint.get('partb_base',{}).get('year','')}→{vint.get('partb_latest',{}).get('year','')}, "
                        f"or stopped billing",
            "thin_supply": f"<{C.THIN_SUPPLY_THRESHOLD} active PCPs within {C.CATCHMENT_RADIUS_MI} mi",
            "shortage_county": f"<{C.SHORTAGE_PCP_PER_10K} PCPs/10k (HRSA-style)",
            "targets": "FPA states ranked by winding-down solo PCPs",
        },
        "sources": sources,
        "national": national,
        "states": states_out,
        "state_list": sorted(present_states, key=lambda s: C.STATE_NAMES[s]),
        "targets": targets,
        "fpa_states": sorted(C.FPA_STATES),
        "corner_active": sorted(C.CORNER_ACTIVE), "corner_expansion": sorted(C.CORNER_EXPANSION),
    }
    json.dump(summary, open(os.path.join(C.WEB_DATA, "summary.json"), "w"), indent=1)
    # state outlines for the UI
    import shutil
    shutil.copy(os.path.join(C.GEO_DIR, "us_states.geojson"),
                os.path.join(C.WEB_DATA, "us_states.geojson"))

    # national GTM CSV
    export = pins.select(
        "state", pl.col("priority_tier").alias("tier"), pl.col("NPI").alias("npi"),
        "first_name", "last_name", "credential", pl.col("pri_spec").alias("specialty"),
        "grad_year", "est_age", "practice_type", "facility_name",
        pl.col("street1").alias("address"), "city", pl.col("zip5").alias("zip"),
        "county_name", "phone", "benes_base", "benes_latest",
        (pl.col("benes_pct_change") * 100).round(1).alias("benes_pct_change"),
        "winddown_status", "is_winddown", "nearby_active_pcps", "thin_local_supply",
        "pcps_per_10k", "lat", "lon", "geo_source",
    ).sort(["state", "tier", "benes_pct_change"])
    export.write_csv(os.path.join(C.PROC_DIR, "vulnerable_export.csv"))
    export.write_csv(os.path.join(C.WEB_DATA, "vulnerable_export.csv"))

    print(f"[step4] national: {json.dumps(national)}")
    print(f"[step4] states covered: {len(present_states)}; wrote {len(states_out)} state files")
    print(f"[step4] top targets: {[t['state'] for t in targets[:8]]}")


if __name__ == "__main__":
    main()
