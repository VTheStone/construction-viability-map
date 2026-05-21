# Construction Viability Map — Project Plan

> Master planning document. Describes the current architecture, scope, and roadmap. Reference point for resuming work in future sessions.

**Last updated:** 2026-05-21

---

## 1. Overview

### Goal
Build an interactive map that helps users visually understand construction viability across a municipality. Users explore the city through multiple toggleable layers (slope, APPs, zoning, risk areas, etc.) and interact with the map directly — there is no precomputed list of viable parcels.

### Pilot city
**São José, Santa Catarina, Brazil** (IBGE code: 4216602)

### Value proposition
- Technical POC + portfolio project (open-source on GitHub)
- Modular architecture allowing new municipalities to be added without refactoring the core
- Demonstrates a full geospatial pipeline: ingest → transform → publish as interactive layers

---

## 2. Architecture and product decisions

| Item | Decision | Rationale |
|---|---|---|
| Spatial model | **Continuous overlay layers** (raster + vector) | Layers communicate restrictions directly; no need to simulate a cadastre we don't have |
| Viability model | Independent toggleable layers + lightweight thresholds | User adjusts opacity, toggles layers on/off, applies simple filters (e.g. "highlight slope > 30%") |
| Stack | Python + Streamlit + Folium | Dynamic UI, integrated Leaflet, free deploy on Streamlit Cloud |
| Architecture | Multi-municipality via Adapter Pattern | New cities plug in without changing the core |
| Dependencies | pip + venv + requirements.txt | Absolute simplicity, no extra tooling |
| Repository | Public GitHub | Portfolio + supports open-source contribution |
| Master Plan (PDFs) | Georeferenced raster overlay for all 10 maps. Vectorization of Maps 02 (macrozoning) and 08 (risk) for filterable layers | Faster path to a useful MVP; vectorization is manual labor in QGIS |
| Code language | English | Open-source best practice |
| Documentation language | English | Maximum reach for portfolio and contributors |

---

## 3. Data sources

| Layer | Source | Type | Final form in app |
|---|---|---|---|
| Municipal boundary | IBGE (2022 mesh) | Shapefile | Clipping mask + map extent |
| Roads | OpenStreetMap (via osmnx) | GeoJSON | Optional vector overlay |
| Buildings | OSM `building=*` | GeoJSON | Optional vector overlay (reference) |
| Hydrography | OSM `waterway=*` | GeoJSON | Vector overlay (blue lines) |
| DEM (elevation) | Topodata INPE (~30m) | GeoTIFF | Source for slope; optional hillshade |
| Slope | Computed from DEM | Colored raster (PNG + bounds) | Main analytical layer (green→red gradient) |
| APP buffer | Computed from hydrography | Polygon overlay | Semi-transparent blue zone |
| Legal zoning | SJ Master Plan — LC 173/2024 (PDFs) | Raster overlay; vector for Maps 02 and 08 | Toggleable raster overlay; macrozone filter for vectorized maps |
| Risk areas | SJ Master Plan — Map 08 (PDF) | Raster overlay; vector | Toggleable raster overlay; risk filter once vectorized |

### São José Master Plan — current legislation

| Law | Date | Subject |
|---|---|---|
| **LC 172/2024** | 2024-12-18 | Master Plan (Plano Diretor) |
| **LC 173/2024** | 2024-12-18 | Land Use and Occupation |
| **LC 188/2025** | 2025 | Amendments to LC 172/2024 |

### Map annexes (LC 173/2024)

| Annex | Map | Subject | MVP status |
|---|---|---|---|
| 5 | Map 01 | Urban perimeter | Raster overlay |
| 6 | Map 02 | Macrozoning (A, B, C, MZR) | Raster overlay + vectorized (filter) |
| 7 | Map 03 | Zoning detail | Raster overlay |
| 8 | Map 04 | Special-Interest Environmental Areas | Raster overlay |
| 9 | Map 05 | Special-Interest Urban Areas | Raster overlay |
| 10 | Map 06 | AEI Urbanístico + Road system | Raster overlay |
| 11 | Map 07 | Urban Equipment, Green Areas | Raster overlay |
| **12** | **Map 08** | **Social-Interest Areas + Risk Areas** | Raster overlay + vectorized (filter) |
| 13 | Map 09 | Transport Strategy | Raster overlay |
| 14 | Map 10 | Disturbance categories | Raster overlay |

---

## 4. Data model

The pipeline produces a **collection of named layers**, each in its native format:

```
data/processed/<region_slug>/
├── slope.tif                    # 2-band raster (degrees, percent) in projected CRS
├── slope.png                    # Colored visualization (green→red), web-ready
├── slope.json                   # Bounds + legend metadata for the app
├── app.geoparquet               # APP polygons (Brazilian Forest Code buffer)
├── waterways.geoparquet         # Source watercourses
├── boundary.geoparquet          # Municipal boundary
├── master_plan/
│   ├── overlays/                # 10 georeferenced GeoTIFFs (raster overlays)
│   └── vectors/                 # GeoJSON for Maps 02 and 08
└── manifest.json                # Index of all available layers + metadata
```

The `manifest.json` is what the Streamlit app reads to populate the layer toggle panel.

---

## 5. Repository layout

```
construction-viability-map/
├── README.md
├── CONTRIBUTING.md
├── PROJECT_PLAN.md
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
├── Makefile
├── config/
│   ├── global.yaml
│   └── regions/
│       ├── _template.yaml
│       └── sao_jose_sc.yaml
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── ingest/                       # IBGE, OSM, Topodata
│   │   ├── transform/
│   │   │   ├── slope.py                  # DEM → slope (2-band GeoTIFF)
│   │   │   ├── slope_visualize.py        # slope.tif → colored PNG
│   │   │   ├── app_buffer.py             # waterways → APP polygons
│   │   │   ├── master_plan_overlays.py   # raster overlay loader
│   │   │   └── manifest.py               # writes processed/manifest.json
│   │   └── pipeline.py                   # orchestrator
│   ├── regions/
│   │   ├── base.py
│   │   └── sao_jose_sc/
│   │       └── adapter.py
│   └── app/
│       ├── streamlit_app.py
│       ├── layer_panel.py                # toggles and opacity per layer
│       └── map_view.py                   # Folium with layer control
├── data/                                 # gitignored
│   ├── raw/<region>/
│   ├── interim/<region>/
│   └── processed/<region>/
├── qgis_projects/                        # versioned (.points + .qgz)
├── scripts/                              # one-off utilities
└── tests/
```

---

## 6. RegionAdapter interface

```python
class RegionAdapter(Protocol):
    slug: str
    ibge_code: str
    crs_local: str
    bbox: tuple[float, float, float, float]

    def load_boundary(self) -> GeoDataFrame: ...
    def load_buildings(self) -> GeoDataFrame: ...       # optional overlay
    def load_master_plan_overlays(self) -> list[OverlayMetadata]: ...
    def load_zoning_vectors(self) -> GeoDataFrame: ...  # vectorized maps only
    def load_risk_vectors(self) -> GeoDataFrame: ...    # vectorized maps only
```

---

## 7. Streamlit UI

### Layout
- **Left sidebar**: layer panel (toggles + opacity + lightweight thresholds)
- **Center**: full-width Folium map
- **Bottom or right**: click-to-inspect panel showing values at the clicked point

### Layer panel
Grouped by category:

**Base**
- ☑ OpenStreetMap (always on)

**Terrain**
- ☐ Slope (colored gradient) — slider for "highlight slope > X%"
- ☐ Hillshade (optional)

**Environmental**
- ☐ Hydrography (lines)
- ☐ APP — Permanent Preservation Areas

**Master Plan (LC 173/2024)**
- ☐ Map 01 — Urban perimeter
- ☐ Map 02 — Macrozoning
- ☐ Map 03 — Zoning
- ☐ ... (all 10)

**Reference**
- ☐ OSM buildings

Each toggle has an opacity slider (default 60%).

### Click-to-inspect
When the user clicks a point on the map, a panel shows:
- Slope (degrees / %)
- Elevation
- Inside APP? Distance to nearest watercourse
- Macrozone (when vectorized layer is enabled)
- Risk area (when vectorized layer is enabled)
- Nearest road / neighborhood

### Filters (lightweight)
- Highlight pixels where `slope > X%` — overlay a single-color mask
- Exclude APPs from view
- Filter by macrozone (when vectorized)

---

## 8. Execution roadmap

| Phase | Status | Deliverable |
|---|---|---|
| 1 | ✅ done | Setup: repo, structure, configuration, interfaces |
| 2 | ✅ done | Core ingest: IBGE, OSM, Topodata |
| 3 | ✅ done | São José adapter + 10 Master Plan PDFs georeferenced |
| 4a | ✅ done | `slope.py` — DEM to slope GeoTIFF |
| 4b | ✅ done | `app_buffer.py` — APP polygons from waterways |
| 4c | 🟡 next | `slope_visualize.py` — colorize slope into PNG for the app |
| 4d | next | `manifest.py` — write `processed/manifest.json` |
| 5 | future | Vectorize Maps 02 and 08 (manual in QGIS) |
| 6 | future | Pipeline orchestrator (`make process REGION=sao_jose_sc`) |
| 7 | future | Streamlit MVP — layer panel + map + click inspect |
| 8 | future | Polish, deploy, README with screenshots |

---

## 9. Backlog (future GitHub issues)

### Data and quality
- [ ] Request lot shapefile from the São José municipality via Brazilian Freedom-of-Information Law (LAI)
- [ ] Full vectorization of remaining Master Plan annexes (Maps 01, 03, 04, 05, 06, 07, 09, 10 + Table 01)
- [ ] Cache Overpass queries (avoid rate limiting)
- [ ] Monitor amendments to LC 172/2024 and LC 173/2024 (LC 188/2025 already exists)

### Features
- [ ] Hillshade layer from DEM
- [ ] Click-to-inspect popup with neighborhood lookup
- [ ] Custom slope threshold filter (highlight pixels above N%)
- [ ] Export current view as PNG/PDF

### Multi-municipality
- [ ] Florianópolis adapter (use `geo.pmf.sc.gov.br`)
- [ ] Documented `_template.yaml` for new cities
- [ ] `CONTRIBUTING.md` with a step-by-step guide

### Infrastructure
- [ ] CI on GitHub Actions (lint + tests)
- [ ] Automated deploy to Streamlit Cloud

---

## 10. Known risks

| Risk | Mitigation |
|---|---|
| Folium performance with 10+ raster overlays | Use Leaflet WMS or simple PNG-with-bounds; pre-downsample if needed |
| Slope raster too coarse (30m Topodata) | Acceptable for MVP; could swap for 12.5m ALOS later |
| Master Plan rasters look ugly (low-contrast PDFs) | Adjust opacity per layer in the app |
| User expects lot-level precision the data can't deliver | Explicit disclaimer in README + click-inspect shows raster values |
| Brazilian Forest Code APP requires variable buffer per river width | Use 30m default; document the simplification |

---

## 11. Glossary

- **APP** — Área de Preservação Permanente (Permanent Preservation Area, Brazilian Forest Code, Law 12.651/2012). Along rivers: minimum 30m marginal buffer.
- **DEM** — Digital Elevation Model
- **Topodata** — DEM refined for Brazil by INPE from SRTM data
- **Master Plan (Plano Diretor)** — Municipal law defining zoning and land use. São José current Master Plan: LC 172/2024.
- **LC** — Lei Complementar (Complementary Law)
- **Macrozone** — Top-level territorial division (A, B, C, Rural in São José)
- **Zone** — Subdivision of a macrozone (e.g. ZA1, ZB2)
- **CRS** — Coordinate Reference System. EPSG:31982 = SIRGAS 2000 / UTM zone 22S (official for SC)
- **EPSG:4326** — WGS84, lat/lon (web/GPS format)
- **GeoParquet** — Efficient columnar format for geospatial data
- **Adapter Pattern** — Design pattern where each concrete implementation honors a common interface
- **Manifest** — JSON file listing all processed layers available to the app