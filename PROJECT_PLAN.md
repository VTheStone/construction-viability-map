# Construction Viability Map — Project Plan

> Master planning document. Describes the current architecture, scope, and roadmap. Reference point for resuming work in future sessions.

**Last updated:** 2026-06-08

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
| Static asset serving | FastAPI sidecar on localhost (daemon thread) | Streamlit's websocket payload limit (200 MB) is too small for inline base64; serving PNGs/GeoJSONs via HTTP lets us scale to the full set of Master Plan maps |
| Dependencies | pip + venv + requirements.txt | Absolute simplicity, no extra tooling |
| Repository | Public GitHub | Portfolio + supports open-source contribution |
| Master Plan (PDFs) | **Automated vectorization via color segmentation** — the in-scope maps become vector polygon layers; raster PNGs are deprecated as an end-user artifact | The PDFs are vector containers with one embedded JPEG per map and a vector legend with exact RGB colors. Segmenting that JPEG by HSV-distance to legend colors yields clean polygons. This replaces the planned manual QGIS work entirely. |
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

Most maps go through the same automated vectorization pipeline. Each in-scope map has a YAML config specifying the legend colors and the corresponding zone codes/names. Two annexes are deliberately excluded: Map 01 (urban perimeter — already covered by the municipal boundary) and Map 06 (AEI Urbanístico + road system — its zoning duplicates Map 05 and its road layer duplicates the OSM basemap).

| Annex | Map | Subject | Vectorized |
|---|---|---|---|
| 5 | Map 01 | Urban perimeter | ➖ out of scope |
| 6 | Map 02 | Macrozoning (MZ-A, MZ-B, MZ-C, MZ-R) | ✅ |
| 7 | Map 03 | Zoning detail (ZA-1..12, ZB-1..3, ZC-1, ZR) | ✅ |
| 8 | Map 04 | Special-Interest Environmental Areas | ✅ |
| 9 | Map 05 | Special-Interest Urban Areas | ✅ |
| 10 | Map 06 | AEI Urbanístico + Road system | ➖ out of scope |
| 11 | Map 07 | Urban Equipment, Green Areas | ✅ |
| 12 | Map 08 | Social-Interest Areas + Risk Areas | ✅ |
| 13 | Map 09 | Population Density (Transport annex) | ✅ |
| 14 | Map 10 | Disturbance categories | ✅ |

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
│   │   ├── map_02.geoparquet
│   │   ├── map_03.geoparquet
│   │   └── ...
│   └── labels/                  # OCR'd zone codes as point layers
│       ├── map_02_labels.geoparquet
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
│   └── master_plan/
│       └── sao_jose_sc/
│           ├── _template.yaml
│           ├── map_02.yaml
│           ├── map_03.yaml
│           └── map_08.yaml
├── src/
│   ├── core/
│   │   ├── init.py
│   │   ├── config.py
│   │   ├── ingest/                       # IBGE, OSM, Topodata
│   │   ├── transform/
│   │   │   ├── slope.py
│   │   │   ├── slope_visualize.py
│   │   │   ├── app_buffer.py
│   │   │   ├── master_plan_vectorize.py  # HSV segmentation → GeoParquet
│   │   │   ├── master_plan_labels.py     # planned: OCR → point layer
│   │   │   └── manifest.py
│   │   └── pipeline.py
│   ├── regions/
│   │   ├── base.py
│   │   └── sao_jose_sc/
│   │       └── adapter.py
│   └── app/
│       ├── streamlit_app.py
│       ├── static_server.py
│       └── map_view.py
├── data/                                 # gitignored
│   ├── raw/<region>/
│   ├── interim/<region>/
│   └── processed/<region>/
├── qgis_projects/                        # versioned (.points + .qgz)
├── scripts/
│   ├── build_manifest.py
│   └── vectorize_master_plan.py
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
    def load_buildings(self) -> GeoDataFrame: ...
    def load_master_plan_overlays(self) -> list[OverlayMetadata]: ...
```

`load_master_plan_overlays` still exists (it tells the manifest builder which maps to look for vectors of). Once labels land, an additional `load_master_plan_labels` will return one point layer per map.

---

## 7. Streamlit UI

### Layout
- **Left sidebar**: layer panel (toggles + opacity per layer)
- **Center**: full-width Folium map. **OSM basemap is permanent** (always rendered, not a toggleable overlay).
- **Right (in-map)**: Leaflet LayerControl with checkboxes per zone, grouped by Master Plan layer. Implemented via `folium.plugins.GroupedLayerControl`.

### Layer panel (sidebar)
Grouped by category:

**Terrain**
- ☐ Declividade

**Environmental**
- ☐ APP — Áreas de Preservação Permanente

**Master Plan (LC 173/2024)** — one toggle per map
- ☐ PD: Macrozoneamento
- ☐ PD: Zoneamento
- ☐ PD: Interesse Social e Áreas de Risco
- ☐ ... (more as YAMLs are produced)

Each layer has an opacity slider. Master Plan layers additionally expose per-zone checkboxes in the in-map LayerControl (right-side panel) so individual zones can be hidden.

### Click-to-inspect (implemented)
When the user clicks a point on the map, a panel shows:
- Slope (degrees / %)
- Elevation (sampled from band 3 of the slope raster)
- Inside APP? (point-in-polygon against the 30 m riparian buffer)
- For each enabled Master Plan layer: the zone code and name at that point

### Filters (implemented)
- Slope-threshold highlight: paints pixels where `slope > X%` in red (lives in the Terreno group)

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
| 5a | ✅ done | `master_plan_vectorize.py` — automated polygon extraction (HSV segmentation + overlay detection/label propagation, validated on Maps 02/03/04/05/08) |
| 5b | ✅ done | App consumes vector Master Plan layers; OSM permanent; per-zone toggles via GroupedLayerControl |
| 5c | ✅ done | YAML configs for all in-scope Master Plan maps (02, 03, 04, 05, 07, 08, 09, 10). Out of scope: 01, 06. |
| 6 | ✅ done | `master_plan_labels.py` — OCR-extracted zone codes (EasyOCR) as an optional per-layer label layer |
| 7 | ✅ done | Pipeline orchestrator — `python -m src.core.pipeline --stage all` (`make process`) |
| 8 | ✅ done | Click-to-inspect panel + slope-threshold filter |
| 9 | 🟡 in progress | Polish & deploy — **live at [construction-viability-map.streamlit.app](https://construction-viability-map.streamlit.app)**; README/disclaimer done; CI pending |
| 10 | 🔵 planned | **Construction Potential** — urbanistic parameters per zone (floors/gabarito, CA, TO, allowed uses) from LC 173/2024, surfaced in click-inspect for real-estate investors. See §12. |

> **Live deploy.** The app runs on Streamlit Community Cloud. To stay self-contained, a curated set of processed artifacts (manifest, slope raster, zone/label GeoParquets) is versioned in git; the heavy raw inputs and the OCR step stay out of the deploy. The localhost static-PNG server was replaced by base64-embedded overlays so the map needs no sidecar.

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
| Folium performance with 10+ vector overlays | Vector polygons are much lighter than rasters; split by zone via GroupedLayerControl lets users hide what they don't need; Douglas-Peucker simplification keeps geometries thin |
| Slope raster too coarse (30m Topodata) | Acceptable for MVP; could swap for 12.5m ALOS later |
| Color segmentation fails on a specific map (low contrast, unusual palette) | Per-map YAML lets us tune saturation/value gates, hue tolerance, morphology kernels; grayscale zones (ZC-1) use a separate `match_by: low_saturation` path |
| OCR misreads zone codes ("ZA-l" instead of "ZA-1") | Validate against a regex `^Z[ABCR]-?\d+$`; flag mismatches; allow YAML override per label |
| Co-colored subzones merged into a single polygon | Documented limitation; per-subzone codes preserved in the future label layer; backlog item to split using label positions as seeds |
| Hillshade outside the municipality bleeds into segmentation | Boundary clip via `boundary.geoparquet` runs after polygonization, before output |
| User expects lot-level precision the data can't deliver | Explicit disclaimer in README (to be added) + click-inspect shows underlying values |
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

---

## 12. Phase 10 — Construction Potential (investor view)

The maps answer *"which zone is here?"*. This phase answers *"what can be built here?"* — the urbanistic parameters per zone from LC 173/2024 — turning the viewer into a development-potential tool for real-estate investors (the target audience).

### Data model
A new `config/master_plan/sao_jose_sc/parameters.yaml`, keyed by **subzone code** (ZA-1 … ZA-12, ZB-1…3, ZC-1, ZR), with: max floors / height (gabarito), basic & maximum floor-area ratio (CA — the maximum reached via *outorga onerosa*), occupancy rate (TO), permeability rate, minimum lot area/frontage, setbacks, and allowed uses. The reserved `adapter.zoning_schema()` becomes its loader.

Co-colored subzones (e.g. ZA-9/ZA-10/ZB-2) collapse to one polygon, so the per-point parameter lookup uses the **OCR label layer** (Phase 6) to resolve the exact subzone — this is where that work pays off.

### Milestones
- **M1 — Sourcing & data model:** extract the parameter quadro from the LC 173 PDF into `parameters.yaml`; loader + validation.
- **M2 — Wire into the model:** implement `zoning_schema()`; join zone polygon + OCR label → parameters.
- **M3 — Click-inspect "Construction potential":** show floors/CA/TO/uses at the clicked point.
- **M4 — Investor synthesis:** estimated buildable area (lot × CA) + a readable verdict combining potential × constraints (slope/APP/risk).
- **M5 — Criteria search:** filter/highlight zones meeting investor criteria (e.g. ≥ N floors + residential + outside APP/risk).
- **M6 — Docs, disclaimer, tests.**

### Investor-relevant data already in the app
Constraints are already covered (slope, APP, risk Map 08, disturbance Map 10, density Map 09, amenities Map 07). Phase 10 adds the missing half — the **buildable potential** — and synthesizes the two.