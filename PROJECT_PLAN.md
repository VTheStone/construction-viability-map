# Construction Viability Map — Project Plan

> Master planning document. This file records every decision made during the planning phase and is the reference point for resuming work in future sessions.

**Last updated:** 2026-05-17
**Status:** Phase 1 complete, ready for Phase 2 (Core Ingest)

---

## 1. Overview

### Goal
Build an interactive map showing construction viability for lots in a city, with filters across physical and legal characteristics (slope, zoning, risk areas, APP, etc.).

### Pilot city
**São José, Santa Catarina, Brazil** (IBGE code: 4216602)

### Value proposition
- Technical POC + portfolio project (open-source on GitHub)
- Modular architecture allowing new municipalities to be added without refactoring the core
- Demonstrates a full pipeline: ingest → transform → feature engineering → interactive visualization

---

## 2. Architecture and product decisions

| Item | Decision | Rationale |
|---|---|---|
| Spatial unit | Individual lots | Highest analytical granularity |
| Lot strategy | **Alternative B**: OSM buildings + synthetic blocks | São José has no public cadastral dataset; OSM is the best available proxy |
| Viability model | Independent filterable attributes | Lets the user weight criteria themselves rather than locking in a fixed score |
| Stack | Python + Streamlit + Folium | Dynamic filters, integrated Leaflet map, free deploy on Streamlit Cloud |
| Architecture | Multi-municipality via Adapter Pattern | New cities plug in without changing the core |
| Dependencies | pip + venv + requirements.txt | Absolute simplicity, no extra tooling |
| Repository | Public GitHub | Portfolio + supports open-source contribution |
| Master Plan (PDFs) | MVP: georeferenced raster overlay. Vectorization: backlog | Faster path to MVP; vectorization is 4–8h of manual work |
| Code language | English | Open-source best practice |
| Documentation language | English | Maximum reach for portfolio and contributors |

---

## 3. Data sources

| Layer | Source | Type | Status |
|---|---|---|---|
| Municipal boundary | IBGE (2022 mesh) | Shapefile | ✅ Open |
| Roads | OpenStreetMap (via osmnx) | GeoJSON | ✅ Open |
| Buildings (lot proxy) | OSM `building=*` | GeoJSON | ✅ Open |
| Blocks | OSM (street-network faces) | Derived | ✅ Open |
| DEM / Slope | Topodata INPE (~30m) | GeoTIFF | ✅ Open |
| Hydrography | IBGE BC250 or ANA | Shapefile | ✅ Open |
| Census tracts | IBGE Census 2022 | Shapefile + CSV | ✅ Open |
| Legal zoning | SJ Master Plan — Map 03 (PDF) | Raster (MVP) → vector (v2) | ⚠️ Manual |
| Risk areas | SJ Master Plan — Map 08 (PDF) | Raster (MVP) → vector (v2) | ⚠️ Manual |

### Notes on São José/SC
- **No public geoportal** (unlike Florianópolis, which has `geo.pmf.sc.gov.br`)
- **No open lot shapefile** — only per-property lookup via municipal IPTU records
- Geotechnical Charts of Urbanization Aptitude Against Natural Disasters exist in Annex 15 of the Master Plan

---

## 4. Data model

Each row in the final dataset = one lot (or proxy). Schema:

```
lot_id           : str           — unique identifier
geometry         : Polygon       — geometry in EPSG:31982 (UTM 22S)
lot_type         : enum          — osm_building | synthetic_block | synthetic_lot
area_m2          : float
centroid_lon     : float
centroid_lat     : float
neighborhood     : str

# Physical characteristics
slope_mean_pct   : float         — average slope inside the lot
slope_max_pct    : float
elevation_m      : float

# Environmental restrictions
inside_app       : bool          — inside a Permanent Preservation Area?
app_distance_m   : float         — distance to nearest watercourse

# Risk
in_risk_area     : bool          — (v2, once Map 08 is vectorized)
risk_type        : str           — (v2)

# Legal zoning
zone_code        : str           — (v2, once Map 03 is vectorized)
zone_name        : str           — (v2)

# Urban accessibility
distance_to_main_road_m   : float
distance_to_school_m      : float
distance_to_health_m      : float
```

### Lot-generation strategy (Alternative B)
1. Import blocks from OSM (faces of the street network)
2. Import `building=*` features from OSM
3. Where buildings exist: use the buffered footprint as a lot proxy → `lot_type=osm_building`
4. Blocks without buildings: use the whole block as a unit → `lot_type=synthetic_block`
5. Block-to-synthetic-lot subdivision: leave as future refinement (backlog)
6. Document in the README that this **does not replace the official cadastre**

---

## 5. Repository layout

```
construction-viability-map/
├── README.md
├── CONTRIBUTING.md
├── PROJECT_PLAN.md                       # this document
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
├── Makefile
├── config/
│   ├── global.yaml                       # CRS, paths, defaults
│   └── regions/
│       ├── _template.yaml                # template for new cities
│       └── sao_jose_sc.yaml
├── src/
│   ├── core/                             # SHARED across cities
│   │   ├── __init__.py
│   │   ├── config.py                     # YAML loader
│   │   ├── ingest/
│   │   │   ├── ibge.py
│   │   │   ├── osm.py
│   │   │   └── topodata.py
│   │   ├── transform/
│   │   │   ├── slope.py
│   │   │   ├── app_buffer.py
│   │   │   ├── lots_from_blocks.py
│   │   │   └── attribute_join.py
│   │   ├── features/                     # 1 module per attribute
│   │   │   ├── slope_feature.py
│   │   │   ├── zoning_feature.py
│   │   │   ├── app_feature.py
│   │   │   ├── risk_feature.py
│   │   │   └── distance_features.py
│   │   └── pipeline.py                   # orchestrator
│   ├── regions/                          # CITY-SPECIFIC
│   │   ├── __init__.py
│   │   ├── base.py                       # RegionAdapter interface
│   │   └── sao_jose_sc/
│   │       ├── __init__.py
│   │       ├── adapter.py
│   │       ├── zoning_loader.py
│   │       └── risk_loader.py
│   └── app/
│       ├── __init__.py
│       ├── streamlit_app.py
│       ├── region_selector.py
│       ├── filters.py
│       └── map_view.py
├── data/                                 # gitignored
│   ├── raw/<region_slug>/
│   ├── interim/<region_slug>/
│   └── processed/<region_slug>/
│       └── lots.geoparquet
├── notebooks/
│   └── exploration.ipynb
└── tests/
    ├── test_core/
    └── test_regions/
```

---

## 6. RegionAdapter interface (multi-municipality modularity)

Each municipality implements this interface. The core knows nothing about any specific city.

```python
# src/regions/base.py
from typing import Protocol
from geopandas import GeoDataFrame

class RegionAdapter(Protocol):
    slug: str                              # e.g. "sao_jose_sc"
    ibge_code: str                         # e.g. "4216602"
    crs_local: str                         # e.g. "EPSG:31982"
    bbox: tuple[float, float, float, float]

    def load_boundary(self) -> GeoDataFrame: ...
    def load_zoning(self) -> GeoDataFrame: ...        # city-specific
    def load_risk_areas(self) -> GeoDataFrame: ...    # city-specific
    def load_lots(self) -> GeoDataFrame: ...          # strategy varies
    def zoning_schema(self) -> dict: ...              # maps local codes → standard attributes
```

Adding Florianópolis = create `src/regions/florianopolis_sc/adapter.py` + YAML. Never touch the core.

---

## 7. Per-region YAML configuration

Example: `config/regions/sao_jose_sc.yaml`

```yaml
region:
  slug: sao_jose_sc
  name: "São José"
  state: SC
  ibge_code: "4216602"
  crs_local: "EPSG:31982"               # UTM 22S
  bbox: [-48.72, -27.70, -48.51, -27.49]

data_sources:
  boundary:
    provider: ibge
  zoning:
    provider: local
    strategy: image_overlay              # MVP — v2 switches to "vector"
    source_files:
      - "data/raw/sao_jose_sc/zoneamento_mapa03.png"
      - "data/raw/sao_jose_sc/zoneamento_mapa03.wld"
  risk:
    provider: local
    strategy: image_overlay
    source_files:
      - "data/raw/sao_jose_sc/risco_mapa08.png"
      - "data/raw/sao_jose_sc/risco_mapa08.wld"
  lots:
    strategy: osm_with_synthetic
    osm_building_filters: ["yes", "residential", "commercial", "industrial"]
    synthetic_params:
      target_lot_area_m2: 360
      min_lot_frontage_m: 10

features:
  slope:
    enabled: true
    thresholds: {low: 15, medium: 30}    # % slope
  app:
    enabled: true
    river_buffer_m: 30                   # Brazilian Forest Code
  risk:
    enabled: false                       # v2
  zoning:
    enabled: false                       # v2
  distance_to_main_road:
    enabled: true
```

**Switching cities = swap the YAML + create an adapter. That is the whole job.**

---

## 8. Streamlit UI (independent filters)

### Layout
- **Left sidebar**: filters and controls
- **Center**: full-width Folium map
- **Right sidebar (optional)**: statistics for the filtered lot set

### Controls
- **City selector** (dropdown — wired for multi-city)
- **Coloring attribute** (radio): which variable colors the map
  - mean slope, area, distance to road, zone (once vectorized), neighborhood
- **Active filters** (expandable accordion):
  - Slope: min/max slider
  - Zone: multi-select (when available)
  - APP: checkbox "exclude lots inside APP"
  - Risk: multi-select of types to exclude
  - Minimum lot area: slider
  - Max distance to main road: slider
  - Lot type (`osm_building` vs `synthetic_block`)

### Interactions
- Hover tooltip: summary attributes
- Click popup: full attribute table + Street View link
- Toggleable overlays: hydrography, roads, Master Plan rasters
- Legend updates dynamically with the chosen coloring attribute

### Performance
- Folium handles roughly 10–20k features comfortably
- If needed, switch the map layer to `pydeck` (deck.gl, WebGL)
- A tile server is an option for a future revision

---

## 9. Execution roadmap

| Phase | Deliverable | Approx. commits |
|---|---|---|
| 1 | Setup: repo, venv, requirements, structure, initial README, config loader | 3–5 |
| 2 | Core ingest: IBGE, OSM, Topodata (generic) | 5–8 |
| 3 | São José/SC adapter + PDF→PNG+world-file georeferencing | 4–6 |
| 4 | Core transform: slope, APP buffer, alt. B lots | 5–7 |
| 5 | Features: 1 commit per attribute | 5 |
| 6 | Pipeline orchestrator + final GeoParquet dataset | 2–3 |
| 7 | Streamlit MVP (static map) | 4–6 |
| 8 | Filters, tooltips, legend | 3–4 |
| 9 | README with screenshots, CONTRIBUTING, Streamlit Cloud deploy | 3–4 |

**Total estimate:** 35–50 commits

---

## 10. Backlog (future GitHub issues)

### Data and quality
- [ ] Request lot shapefile from the São José municipality via Brazilian Freedom-of-Information Law (LAI)
- [ ] Full vectorization of Master Plan Maps 03 and 08
- [ ] Manual validation of OSM blocks in less-mapped neighborhoods
- [ ] Cache Overpass queries (avoid rate limiting)

### Features
- [ ] Algorithmic subdivision of blocks into synthetic lots
- [ ] Optional combined-score calculation (configurable weights)
- [ ] Export of filtered lots as CSV/GeoJSON

### Multi-municipality
- [ ] Florianópolis adapter (use `geo.pmf.sc.gov.br`)
- [ ] Documented `_template.yaml` for new cities
- [ ] `CONTRIBUTING.md` with a step-by-step guide

### Infrastructure
- [ ] CI on GitHub Actions (lint + tests)
- [ ] Automated deploy to Streamlit Cloud
- [ ] DVC or Git LFS to version processed data

---

## 11. Known risks

| Risk | Mitigation |
|---|---|
| Uneven OSM quality across São José | Visual validation before processing; document the limitation |
| Manual georeferencing introduces error | Record control points and RMS error |
| Folium performance with >10k lots | Plan B: migrate to pydeck (WebGL) |
| Overpass API rate limiting | Local cache + batched downloads |
| Topodata 30m is coarse for intra-block analysis | Acceptable for MVP; document the limitation |

---

## 12. Glossary

- **APP** — Área de Preservação Permanente (Permanent Preservation Area, Brazilian Forest Code, Law 12.651/2012). Along rivers: minimum 30m marginal buffer.
- **DEM** — Digital Elevation Model
- **Topodata** — DEM refined for Brazil by INPE from SRTM data
- **Master Plan** — Municipal law defining zoning and land use
- **CRS** — Coordinate Reference System. EPSG:31982 = SIRGAS 2000 / UTM zone 22S (official for SC)
- **EPSG:4326** — WGS84, lat/lon (web/GPS format)
- **GeoParquet** — Efficient columnar format for geospatial data
- **Adapter Pattern** — Design pattern where each concrete implementation honors a common interface

---

## 13. Current state

✅ **Complete:**
- Requirements elicitation
- Data-source identification
- Architecture defined
- Technical decisions made
- Roadmap drafted
- **Phase 1: repo structure, configuration system, base interfaces**

🟡 **Next step:**
- Phase 2: Core ingest (IBGE, OSM, Topodata)