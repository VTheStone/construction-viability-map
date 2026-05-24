# Construction Viability Map — Project Plan

> Master planning document. Describes the current architecture, scope, and roadmap. Reference point for resuming work in future sessions.

**Last updated:** 2026-05-24

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
| Static asset serving | FastAPI sidecar on localhost (daemon thread) | Streamlit's websocket payload limit (200 MB) is too small for inline base64; serving PNGs/GeoJSONs via HTTP lets us scale to all 10 maps |
| Dependencies | pip + venv + requirements.txt | Absolute simplicity, no extra tooling |
| Repository | Public GitHub | Portfolio + supports open-source contribution |
| Master Plan (PDFs) | **Automated vectorization via color segmentation** — all 10 maps become vector polygon layers; raster PNGs are deprecated as an end-user artifact | The PDFs are vector containers with one embedded JPEG per map and a vector legend with exact RGB colors. Segmenting that JPEG by HSV-distance to legend colors yields clean polygons. This replaces the planned manual QGIS work entirely. |
| Code language | English | Open-source best practice |
| Documentation language | English | Maximum reach for portfolio and contributors |

---

## 3. Data sources

| Layer | Source | Type | Final form in app |
|---|---|---|---|
| Municipal boundary | IBGE (2022 mesh) | Shapefile | Clipping mask + map extent |
| Roads | OpenStreetMap (via osmnx) | GeoJSON | Implicit in OSM basemap (always visible) |
| Buildings | OSM `building=*` | GeoJSON | Optional vector overlay (reference) |
| Hydrography | OSM `waterway=*` | GeoJSON | Vector overlay (blue lines) |
| DEM (elevation) | Topodata INPE (~30m) | GeoTIFF | Source for slope; optional hillshade |
| Slope | Computed from DEM | Colored raster (PNG + bounds) | Main analytical layer (green→red gradient) |
| APP buffer | Computed from hydrography | Polygon overlay | Semi-transparent blue zone |
| Master Plan zones | SJ Master Plan (LC 173/2024 PDFs) → automated color segmentation of the embedded JPEG | **GeoParquet polygon layers** (one per map) | Toggleable vector overlay with semantic attributes; supports filtering by zone code |
| Zone labels | OCR of map labels ("ZA-9", "ZA-10", ...) on the embedded JPEG | Point layer with `zone_code` attribute | Toggleable label layer for disambiguating co-colored subzones |

### São José Master Plan — current legislation

| Law | Date | Subject |
|---|---|---|
| **LC 172/2024** | 2024-12-18 | Master Plan (Plano Diretor) |
| **LC 173/2024** | 2024-12-18 | Land Use and Occupation |
| **LC 188/2025** | 2025 | Amendments to LC 172/2024 |

### Map annexes (LC 173/2024)

All 10 maps go through the same automated vectorization pipeline. Each map has a YAML config specifying the legend colors and the corresponding zone codes/names.

| Annex | Map | Subject |
|---|---|---|
| 5 | Map 01 | Urban perimeter |
| 6 | Map 02 | Macrozoning (MZ-A, MZ-B, MZ-C, MZ-R) |
| 7 | Map 03 | Zoning detail (ZA-1..12, ZB-1..3, ZC-1, ZR) |
| 8 | Map 04 | Special-Interest Environmental Areas |
| 9 | Map 05 | Special-Interest Urban Areas |
| 10 | Map 06 | AEI Urbanístico + Road system |
| 11 | Map 07 | Urban Equipment, Green Areas |
| 12 | Map 08 | Social-Interest Areas + Risk Areas |
| 13 | Map 09 | Transport Strategy |
| 14 | Map 10 | Disturbance categories |

> **Important note on zone granularity.** The PDF legends group subzones that share a color (e.g. "Zona de Estruturação e Qualificação Urbana (ZA-9 e ZA-10 e ZB-2)" is one legend entry, one color). The automated vectorizer respects this grouping: one polygon per color. The OCR-based label layer (point geometry) preserves the per-subzone code so filtering for "ZA-9" alone remains possible at the UI layer.

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
│   ├── vectors/                 # GeoParquet per map (the canonical form)
│   │   ├── map_01.geoparquet
│   │   ├── map_02.geoparquet
│   │   └── ...
│   └── labels/                  # OCR'd zone codes as point layers
│       ├── map_01_labels.geoparquet
│       └── ...
└── manifest.json                # Index of all available layers + metadata
```

The `manifest.json` is what the Streamlit app reads to populate the layer toggle panel. Each Master Plan entry references the vector (and optional label) layer, not a PNG.

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
│   ├── regions/
│   │   ├── _template.yaml
│   │   └── sao_jose_sc.yaml
│   └── master_plan/                      # NEW: per-map vectorization configs
│       └── sao_jose_sc/
│           ├── map_01.yaml
│           ├── map_02.yaml
│           └── ...
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── ingest/                       # IBGE, OSM, Topodata
│   │   ├── transform/
│   │   │   ├── slope.py                  # DEM → slope (2-band GeoTIFF)
│   │   │   ├── slope_visualize.py        # slope.tif → colored PNG
│   │   │   ├── app_buffer.py             # waterways → APP polygons
│   │   │   ├── master_plan_vectorize.py  # NEW: PDF JPEG → polygons (HSV segmentation)
│   │   │   ├── master_plan_labels.py     # NEW: PDF JPEG → labeled points (OCR)
│   │   │   └── manifest.py               # writes processed/manifest.json
│   │   └── pipeline.py                   # orchestrator
│   ├── regions/
│   │   ├── base.py
│   │   └── sao_jose_sc/
│   │       └── adapter.py
│   └── app/
│       ├── streamlit_app.py
│       ├── static_server.py              # FastAPI sidecar (daemon thread)
│       ├── layer_panel.py                # toggles and opacity per layer
│       └── map_view.py                   # Folium with layer control; OSM always visible
├── data/                                 # gitignored
│   ├── raw/<region>/
│   ├── interim/<region>/
│   └── processed/<region>/
├── qgis_projects/                        # versioned (.points + .qgz)
├── scripts/                              # one-off utilities
│   ├── build_manifest.py
│   ├── build_master_plan_pngs.py         # legacy — to be removed after vectorize works
│   └── vectorize_master_plan.py          # NEW: CLI wrapper
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
    def load_buildings(self) -> GeoDataFrame: ...           # optional overlay
    def load_master_plan_vectors(self) -> dict[str, GeoDataFrame]: ...
    def load_master_plan_labels(self) -> dict[str, GeoDataFrame]: ...
```

The old `load_master_plan_overlays()` (raster) and the stub `load_zoning_vectors()` / `load_risk_vectors()` are replaced by `load_master_plan_vectors()`, which returns a dict keyed by `map_id`.

---

## 7. Streamlit UI

### Layout
- **Left sidebar**: layer panel (toggles + opacity + lightweight thresholds)
- **Center**: full-width Folium map. **OSM basemap is permanent** (always rendered, not a toggleable overlay).
- **Bottom or right**: click-to-inspect panel showing values at the clicked point.

### Layer panel
Grouped by category:

**Terrain**
- ☐ Slope (colored gradient) — slider for "highlight slope > X%"
- ☐ Hillshade (optional)

**Environmental**
- ☐ Hydrography (lines)
- ☐ APP — Permanent Preservation Areas

**Master Plan (LC 173/2024)** — vector polygons, one toggle per map
- ☐ Map 01 — Urban perimeter
- ☐ Map 02 — Macrozoning
- ☐ Map 03 — Zoning
- ☐ ... (all 10)
- ☐ Show zone labels (separate toggle; renders OCR'd "ZA-9" / "ZA-10" point labels over enabled Master Plan layers)

**Reference**
- ☐ OSM buildings

Each Master Plan toggle exposes a **zone filter** (multi-select of zone codes for that map) and an opacity slider (default 60%).

### Click-to-inspect
When the user clicks a point on the map, a panel shows:
- Slope (degrees / %)
- Elevation
- Inside APP? Distance to nearest watercourse
- For each enabled Master Plan layer: the zone code and name at that point
- Nearest road / neighborhood

### Filters (lightweight)
- Highlight pixels where `slope > X%` — overlay a single-color mask
- Exclude APPs from view
- Filter by zone code in any enabled Master Plan layer

---

## 8. Execution roadmap

| Phase | Status | Deliverable |
|---|---|---|
| 1 | ✅ done | Setup: repo, structure, configuration, interfaces |
| 2 | ✅ done | Core ingest: IBGE, OSM, Topodata |
| 3 | ✅ done | São José adapter + 10 Master Plan PDFs georeferenced |
| 4a | ✅ done | `slope.py` — DEM to slope GeoTIFF |
| 4b | ✅ done | `app_buffer.py` — APP polygons from waterways |
| 4c | ✅ done | `slope_visualize.py` — colorize slope into PNG |
| 4d | ✅ done | `manifest.py` — write `processed/manifest.json` |
| 4e | ✅ done (deprecated) | `master_plan_visualize.py` — raster PNGs; will be removed once vectorize replaces it |
| **5a** | 🟡 **next** | **`master_plan_vectorize.py` — automated polygon extraction from PDF JPEGs (validate on Maps 02 + 03 + 08, then run on all 10)** |
| **5b** | next | `master_plan_labels.py` — OCR-extracted zone labels as point layer |
| **5c** | next | Update `RegionAdapter` and `manifest.py` to expose vector layers; drop raster overlays from the app |
| 5d | next | App fix: OSM basemap always visible, polygons replace raster overlays |
| 6 | future | Pipeline orchestrator (`make process REGION=sao_jose_sc`) |
| 7 | future | Click-to-inspect panel + zone filter UI |
| 8 | future | Polish, deploy, README with screenshots |

---

## 9. Backlog (future GitHub issues)

### Data and quality
- [ ] Request lot shapefile from the São José municipality via Brazilian Freedom-of-Information Law (LAI)
- [ ] Per-subzone polygon split (currently subzones sharing a color are merged; could be split using OCR'd label positions as seed for a watershed segmentation)
- [ ] Cache Overpass queries (avoid rate limiting)
- [ ] Monitor amendments to LC 172/2024 and LC 173/2024 (LC 188/2025 already exists)

### Features
- [ ] Hillshade layer from DEM
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
| Folium performance with 10+ vector overlays | Vector polygons are much lighter than rasters; serve via FastAPI as GeoJSON, simplify with Douglas-Peucker before delivery |
| Slope raster too coarse (30m Topodata) | Acceptable for MVP; could swap for 12.5m ALOS later |
| Color segmentation fails on a specific map (low contrast, unusual palette) | Per-map YAML lets us tune tolerances; fallback is manual vectorization in QGIS (the old plan) |
| OCR misreads zone codes ("ZA-l" instead of "ZA-1") | Validate against a regex `^Z[ABCR]-?\d+$`; flag mismatches; allow YAML override per label |
| Co-colored subzones merged into a single polygon | Documented limitation; per-subzone codes preserved in the label layer; backlog item to split using label positions as seeds |
| Hillshade outside the municipality bleeds into "gray zone" segmentation | Clip the JPEG by the municipal boundary before segmentation |
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
- **HSV** — Hue/Saturation/Value color space. Used for color segmentation because it isolates hue from lighting variations (hillshade) better than RGB.