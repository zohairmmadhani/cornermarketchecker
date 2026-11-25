# Supply Cliff Radar — Retiring Solo PCP Intelligence

Finds **aging, solo primary-care physicians who are actively winding down their
practices** across Arizona and Washington, and shows where their patients are
about to be orphaned. Built for two Corner Health jobs:

1. **Payer / insurer negotiations** — address-level proof of coming primary-care
   access deserts, to pitch insurers on NP clinics as a cost-containment shield.
2. **Clinic site selection** — micro-markets with imminent orphaned panels where
   Corner can place an NP clinic across the street and broker a warm handoff.

**▶ Interactive map:** published as a Claude Artifact — open `web/index.html`
(served over HTTP) or the shared artifact link. Click any pin for a full
drill-down card; filter and copy the GTM list straight to a spreadsheet.

---

## What it found (v1, AZ + WA)

| Metric | Count |
|---|---|
| Vulnerable solo PCPs (solo, grad yr ≤ 1986) | **188** (105 AZ · 83 WA) |
| Actively winding down (≥20% fewer Medicare patients 2022→2024, or stopped) | **63** |
| **Tier A — act now** (winding down *and* thin local supply) | **9** |
| In thin-supply zones (<20 active PCPs within 4 mi) | 22 |
| Active PCP supply mapped for context | 9,770 |

The 9 **Tier A** providers are the hottest targets: a retiring solo doctor whose
panel is already shrinking, in a spot with almost no nearby capacity to absorb
the patients. Example from the data: a 70-year-old family doctor in Yuma, AZ
whose Medicare panel fell from **871 → 234 patients (−73%)** in two years, with
only **7** active PCPs within four miles.

---

## The three layers (plain-English)

Each layer is a transparent, self-explanatory signal — no black-box score.

**Layer 1 — Vulnerable solo PCP pins (the supply cliff).**
Every pin is one doctor. We keep only *primary-care physicians* (Family /
Internal / General practice, MD or DO) who practice *solo* and graduated med
school in **1986 or earlier** (a proxy for age ~66+). When a doctor inside a big
health system retires, the system keeps the patients. When a *solo* doctor
retires, 1,000–1,500 patients are orphaned overnight.

**Layer 2 — 2-year patient decay (active wind-down).**
We pull each doctor's Medicare billing for 2022 and 2024 and compute the change
in unique patients. A **≥20% drop** (or stopping Medicare billing entirely)
flags someone genuinely cutting back — not a healthy 68-year-old still working
full-time. This is the **pin color**: red = winding down, green = stable.

**Layer 3 — Local PCP saturation (net-deficit proof).**
For each pin we count how many *active* PCPs sit within a 4-mile drive-time proxy
and divide by the local population. Few nearby PCPs = no buffer to catch the
displaced patients = a real desert. This is the **amber ring** on thin-supply
pins, plus the hex "supply density" wash on the map.

**Priority tier** combines Layers 2 and 3 with two visible booleans (no hidden
weighting):

- **Tier A** — winding down **and** thin local supply → imminent desert.
- **Tier B** — one of the two.
- **Tier C** — neither (monitor).

---

## Data sources (all public, all free)

| Source | Used for | Access |
|---|---|---|
| CMS **Doctors & Clinicians National Downloadable File** (Care Compare) | Layer 1 + 3: who's solo, specialty, grad year, practice address | Full CSV download (~900 MB), filtered to AZ/WA |
| CMS **Medicare Physician & Other Practitioners — by Provider** (2022, 2024) | Layer 2: unique Medicare beneficiaries per NPI per year | `data.cms.gov` data-API, paged + state-filtered |
| U.S. Census **ACS 5-yr (2022) B01003** | Layer 3: population denominators by ZIP | Keyless table-based Summary File |
| U.S. Census **Gazetteer + TIGERweb** | ZIP centroids (geocode fallback) + county/state outlines | Keyless download / ArcGIS GeoJSON |
| U.S. Census **batch geocoder** | Practice street address → lat/lon | Free, no key, batch endpoint |

---

## How to run

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# full pipeline (downloads the ~900 MB Care Compare file on first run)
python pipeline/run_all.py --download

# subsequent runs reuse cached raw pulls
python pipeline/run_all.py
```

Then serve the `web/` folder and open `index.html` (it `fetch()`es the data
files, so `file://` won't work):

```bash
python -m http.server --directory web 8000   # http://localhost:8000
```

### Pipeline steps (each is independently runnable from `pipeline/`)

| Script | Output |
|---|---|
| `step1_carecompare.py` | `vulnerable_pcps.parquet`, `active_pcps.parquet`, `geocode_input.csv` |
| `step2_partb.py` | `volume_decay.parquet` (+ per-year raw caches) |
| `step3_geocode.py` | `geocodes.parquet` |
| `step4_build.py` | `web/data/*.geojson`, `summary.json`, `vulnerable_pcps_export.csv` |
| `build_map.py` | `web/index.html` (inlines Leaflet CSS) |

All tunables (states, grad-year cutoff, thresholds, dataset IDs) live in
`pipeline/config.py`.

---

## Data roadblocks & how they're handled

Healthcare data is messy. Each known issue is handled explicitly rather than
silently dropped:

- **CMS suppression (<11 beneficiaries).** Rows with fewer than 11 Medicare
  patients are suppressed. We treat them as *missing*, not zero, and label such
  providers `insufficient_medicare_data` — they're flagged as low-Medicare /
  commercial-heavy, not crashed joins.
- **Stopped billing Medicare.** A provider who billed in 2022 but not 2024 is
  `dropout_no_medicare` — the strongest wind-down signal, kept separate from a
  simple decline.
- **Dirty / PO-box addresses.** We geocode the Care Compare *practice* street
  address, deduplicate co-located providers, and fall back to the ZIP centroid
  when the street can't be matched. Each pin records its `geo_source`
  (`census_exact` / `census_nonexact` / `zcta_centroid`). Match rate: **92%
  exact street-level**, 8% ZIP-centroid fallback, 1 unplaceable.
- **Missing graduation years.** Only 77 of ~151k AZ/WA rows lack a grad year;
  providers without one simply don't qualify for the age filter (surfaced, not
  guessed).
- **Solo detection.** Care Compare's group-size field never equals 1, so a true
  sole proprietor is identified by a *null organization affiliation*; we also
  admit practices that never exceed 2 members (`solo_duo`).

### Known MVP limitations (verify before outreach)

- **Per-capita ratios are approximate in sparse rural areas.** Population is
  summed from ZIP (ZCTA) *centroids* within the catchment radius; where a rural
  ZIP's centroid falls just outside the radius, the population denominator can be
  understated. **The nearby-PCP count is exact**; the `pcps_per_10k` ratio is a
  secondary signal and is shown as `—` where it can't be computed.
- **The hex "supply density" layer** shows active-PCP *counts* (robust), not the
  noisy per-capita ratio.
- **Active PCP supply = physicians only.** NP/PA primary-care supply isn't
  separable in Care Compare's specialty field and is a planned enrichment.
- Grad year is an age *proxy*; a handful of providers may be semi-retired
  already or practicing well past 66.

---

## Extending it

- **More states:** add to `STATES` and `ZIP_PREFIXES` in `config.py`. The whole
  pipeline is national-ready; AZ/WA is just the v1 filter (FPA states Corner
  operates in). CMS/Census pulls already scale.
- **Leading-indicator enrichment:** `pipeline/enrichment_adapters.py` has stable,
  off-by-default hooks for **state medical-board license expirations** (AZ/WA
  bulk files), **Google Places** hours/closure/review signals, and **NPPES urgent
  care** locations. Each returns an NPI-keyed frame that left-joins in `step4`
  without touching the core pipeline.

---

## Repo layout

```
pipeline/            ETL + build scripts (config, step1-4, build_map, run_all, adapters)
data/raw/            cached source pulls (gitignored; reproducible)
data/processed/      intermediate parquet + the GTM CSV export
web/                 the map: app.html (source) -> index.html (built), data/, vendor/
web/data/            geojson + summary + CSV the map loads
```
