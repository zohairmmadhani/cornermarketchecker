# Open Territory — Retiring Solo PCP Intelligence

Finds **aging, solo primary-care physicians who are actively winding down their
practices** in **every U.S. state**, shows where their patients are about to be
left without a doctor, and hands Corner Health both a site-selection map and a
ready-made payer-negotiation argument. Pick a state and you get a one-line
read-out, three headline numbers, a county map shaded by local primary-care
supply, a payer playbook, and where Corner should expand next.

**▶ Interactive map ("Open Territory"):** published as a Claude Artifact, and
also as a single self-contained `open_territory.html` (built by
`pipeline/build_standalone.py`) you can double-click to open in any browser with
no server. Serve `web/` and open `index.html` locally, or use the artifact link.

Two commercial jobs it serves:
1. **Payer / insurer negotiations** — address-level proof of coming access
   deserts, pre-written into three concrete payer arguments.
2. **Clinic site selection** — the exact counties and ZIPs with imminent
   orphaned panels where Corner can place an NP clinic.

---

## What it found (national)

| Metric | Count |
|---|---|
| States / territories covered | **52** |
| Vulnerable solo PCPs (solo, grad yr ≤ 1986) | **7,338** |
| Actively winding down (≥20% fewer Medicare patients, or stopped) | **2,302** |
| Tier A — "act now" (winding down *and* thin local supply) | **488** |
| Medicare patients attached to winding-down practices | **~273,000** |
| Active PCPs mapped for supply context | 203,844 |
| Vulnerable PCPs in Full Practice Authority states | 2,271 |

Each state view breaks these down locally. Example (Arizona, a Corner market):
105 vulnerable solo PCPs, 27 winding down, 7 Tier A, with hotspots in Lake
Havasu City, Surprise and Peoria and a shortage in Pinal County.

---

## The three map layers (plain-English)

Every layer is a transparent signal — no black-box score.

- **Layer 1 — Vulnerable solo PCP pins.** Each pin is one solo primary-care
  physician (Family / Internal / General practice, MD/DO) who graduated med
  school in **1986 or earlier** (proxy for age ~66+). Solo retirements orphan
  1,000–1,500 patients each; system-employed retirements don't.
- **Layer 2 — 2-year patient decay (pin color).** Red = lost ≥20% of unique
  Medicare patients (or stopped billing) between the two most recent CMS years;
  green = stable.
- **Layer 3 — local PCP saturation (amber ring + hex shading).** Active PCPs
  within a 4-mile drive-time proxy, per capita. Few nearby = no buffer to catch
  displaced patients.

**Priority tier** = two visible booleans: **A** (winding down *and* thin supply),
**B** (one), **C** (neither).

## The payer-negotiation playbook (per state)

Auto-generated from each state's real numbers, built around three levers:

1. **Network-adequacy risk** — counts the winding-down solo PCPs and the ZIP
   codes where 2+ are winding down at once (each a ~12-month drive-time
   compliance gap under MA / state standards), naming the specific ZIPs.
2. **Facility-fee shield** — the Medicare patients attached to those practices
   who, if they drift to hospital-owned clinics, cost the plan 2–3× more per
   visit via facility fees; Corner NPs hold them at ~85–95% of physician rates.
3. **Auto-assignment targets** — the thinnest-supply counties where unattributed
   members and HEDIS gaps will concentrate, ripe for auto-assignment or
   co-funded outreach.

Plus an **immediate ask** and a **counties-most-in-need** ranking.

## Where to expand next

Among **Full Practice Authority** states (where NPs can independently own a
clinic — AANP, ~2025; the gating filter for Corner today), the tool ranks
expansion targets by the number of winding-down solo PCPs. Top of the list:
New York, New Jersey, Maryland, Massachusetts, Oklahoma, Connecticut, Colorado,
Nevada.

---

## Data — latest available, timestamped in the UI

The pipeline resolves the **newest** files from the CMS catalog on every run and
stamps each with its publication date (shown in the app's "Data sources" panel,
each with a direct download link to the authoritative file).

| Source | Used for | Vintage (current) |
|---|---|---|
| CMS **Doctors & Clinicians National Downloadable File** (Care Compare) | Layers 1 & 3: solo status, specialty, grad year, address | updated 2026-10-30 |
| CMS **Medicare Physician & Other Practitioners — by Provider** | Layer 2: unique Medicare beneficiaries per NPI (latest 2 yrs) | CY2024 (rel. 2026-05-21) vs CY2022 |
| U.S. Census **ACS 5-yr B01003** | population denominators by ZIP | 2022 |
| U.S. Census **Gazetteer / ZCTA↔County / TIGERweb** | centroids, county roll-ups, boundaries | 2023 / 2020 |

All sources are public and keyless (the Census data API now requires a key, so
the pipeline uses the keyless Summary File and relationship files instead).

---

## How to run

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python pipeline/run_all.py          # downloads sources on first run, then caches
python -m http.server --directory web 8000   # open http://localhost:8000
```

Steps (each runnable on its own from `pipeline/`):

| Script | Output |
|---|---|
| `catalog.py` | resolves latest CMS file URLs + publication dates |
| `step1_carecompare.py` | `vulnerable_pcps.parquet`, `active_by_zip.parquet`, geocode input |
| `step2_partb.py` | `volume_decay.parquet` from the bulk national Part B CSVs |
| `step3_geocode.py` | `geocodes.parquet` (Census batch geocoder, ~90% exact) |
| `fetch_boundaries.py` | national state + county GeoJSON |
| `step4_build.py` | per-state files, county need, talking points, targets, `summary.json` |
| `build_map.py` | `web/index.html` (inlines Leaflet CSS) |

Scope, thresholds and the FPA state list live in `pipeline/config.py`.

---

## Data roadblocks & how they're handled

- **CMS suppression (<11 beneficiaries)** → treated as missing, labelled
  `insufficient_medicare_data`, never zero.
- **Stopped billing Medicare** → `dropout_no_medicare`, the strongest wind-down
  signal, kept separate from a decline.
- **Dirty / PO-box addresses** → geocode the practice street address (Census
  batch), fall back to ZIP centroid; each pin records its `geo_source`.
- **Solo detection** → Care Compare's group-size never equals 1, so a sole
  proprietor is a *null org affiliation*; practices never exceeding 2 members
  count as `solo_duo`.
- **Census API key requirement** → bypassed with keyless Summary / relationship
  files.

### Known MVP limitations (verify before outreach)

- **Per-capita ratios are approximate in sparse rural areas** (ZIP-centroid
  population within a tight radius). The nearby-PCP *count* is exact; the
  `pcps_per_10k` ratio is secondary and shown as `—` where it can't be computed.
- **Active PCP supply = physicians only** (NP/PA primary care isn't separable in
  Care Compare's specialty field — a planned enrichment).
- **Grad year is an age proxy.** The **FPA state list** is from AANP ~2025 and
  changes with legislation (OK added Nov 2025) — verify before acting.
- County shortage uses a HRSA-style <2.9 PCP/10k threshold; dense urban counties
  rarely trip it even when specific ZIPs are deserts (the ZIP hotspots catch
  those).

---

## Extending it

- **Scope** is already national; `config.STATES` / `FPA_STATES` are the only
  knobs. The per-state file structure scales without UI changes.
- **Leading-indicator enrichment:** `pipeline/enrichment_adapters.py` has stable,
  off-by-default hooks for **state medical-board license expirations**, **Google
  Places** hours/closure signals, and **NPPES urgent care** locations — each
  returns an NPI-keyed frame that left-joins in `step4`.

---

## Repo layout

```
pipeline/     catalog, config, step1-4, fetch_boundaries, build_map, run_all, adapters
data/raw/     cached source pulls (gitignored; reproducible)
data/geo/     intermediate national boundaries (gitignored)
web/          app.html (source) -> index.html (built), vendor/leaflet, data/
web/data/     summary.json, us_states.geojson, states/<ST>.json, export CSV
```
