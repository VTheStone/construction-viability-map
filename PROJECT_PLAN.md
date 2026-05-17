# Construction Viability Map — Plano do Projeto

> Documento mestre de planejamento. Este arquivo registra todas as decisões tomadas na fase de planejamento e serve como referência para retomar o projeto em conversas futuras.

**Última atualização:** 2026-05-17
**Status:** Planejamento concluído, pronto para Fase 1 (Setup)

---

## 1. Visão geral

### Objetivo
Construir um mapa interativo que mostra a viabilidade de construção em lotes de uma cidade, permitindo filtrar por características físicas e legais (declividade, zoneamento, áreas de risco, APP, etc.).

### Cidade piloto
**São José, Santa Catarina** (código IBGE: 4216602)

### Proposta de valor
- POC técnica + projeto de portfólio (open-source no GitHub)
- Arquitetura modular permitindo adicionar novos municípios sem refatorar o core
- Demonstra pipeline completo: ingest → transform → feature engineering → visualização interativa

---

## 2. Decisões de arquitetura e produto

| Item | Decisão | Justificativa |
|---|---|---|
| Unidade espacial | Lotes individuais | Maior granularidade analítica |
| Estratégia de lotes | **Alternativa B**: OSM buildings + quadras sintéticas | São José não tem cadastro público de lotes; OSM é a melhor proxy disponível |
| Modelo de viabilidade | Atributos independentes filtráveis | Permite ao usuário ponderar critérios próprios, em vez de score fixo |
| Stack | Python + Streamlit + Folium | Filtros dinâmicos, mapa Leaflet integrado, deploy gratuito no Streamlit Cloud |
| Arquitetura | Multi-município via Adapter Pattern | Permite plugar novas cidades sem alterar o core |
| Dependências | pip + venv + requirements.txt | Simplicidade absoluta, sem ferramentas extras |
| Repositório | GitHub público | Portfólio + facilita contribuição open-source |
| Plano Diretor (PDFs) | MVP: imagem georreferenciada como camada visual. Vetorização: backlog | Reduz tempo até MVP; vetorização é trabalho manual de 4–8h |
| Idioma do código | Inglês | Boas práticas open-source |
| Idioma da documentação | Português | Público inicial é brasileiro |

---

## 3. Fontes de dados

| Camada | Fonte | Tipo | Status |
|---|---|---|---|
| Limite municipal | IBGE (malha 2022) | Shapefile | ✅ Aberto |
| Vias | OpenStreetMap (via osmnx) | GeoJSON | ✅ Aberto |
| Edificações (proxy lote) | OSM `building=*` | GeoJSON | ✅ Aberto |
| Quadras | OSM (faces da rede viária) | Derivado | ✅ Aberto |
| MDE / Declividade | Topodata INPE (~30m) | GeoTIFF | ✅ Aberto |
| Hidrografia | IBGE BC250 ou ANA | Shapefile | ✅ Aberto |
| Setores censitários | IBGE Censo 2022 | Shapefile + CSV | ✅ Aberto |
| Zoneamento legal | Plano Diretor SJ — Mapa 03 (PDF) | Imagem (MVP) → vetor (v2) | ⚠️ Manual |
| Áreas de risco | Plano Diretor SJ — Mapa 08 (PDF) | Imagem (MVP) → vetor (v2) | ⚠️ Manual |

### Notas sobre São José/SC
- **Não há geoportal público** (diferente de Florianópolis, que tem `geo.pmf.sc.gov.br`)
- **Não há shapefile aberto de lotes** — só consulta individual por inscrição no IPTU
- Cartas Geotécnicas de Aptidão à Urbanização Frente aos Desastres Naturais existem no Anexo 15 do Plano Diretor

---

## 4. Modelo de dados

Cada linha do dataset final = um lote (ou proxy). Esquema da tabela:

```
lot_id           : str           — identificador único
geometry         : Polygon       — geometria em EPSG:31982 (UTM 22S)
lot_type         : enum          — osm_building | synthetic_block | synthetic_lot
area_m2          : float
centroid_lon     : float
centroid_lat     : float
neighborhood     : str           — bairro

# Características físicas
slope_mean_pct   : float         — declividade média no lote
slope_max_pct    : float
elevation_m      : float

# Restrições ambientais
inside_app       : bool          — dentro de Área de Preservação Permanente?
app_distance_m   : float         — distância ao corpo d'água mais próximo

# Risco
in_risk_area     : bool          — (v2 com Mapa 08 vetorizado)
risk_type        : str           — (v2)

# Zoneamento legal
zone_code        : str           — (v2 com Mapa 03 vetorizado)
zone_name        : str           — (v2)

# Acessibilidade urbana
distance_to_main_road_m   : float
distance_to_school_m      : float
distance_to_health_m      : float
```

### Estratégia de lotes (Alternativa B)
1. Importar quadras do OSM (faces da rede viária)
2. Importar edificações `building=*` do OSM
3. Onde existe prédio: usar footprint expandido por buffer como lote proxy → `lot_type=osm_building`
4. Quadras sem edificações: usar a quadra inteira como unidade → `lot_type=synthetic_block`
5. Subdivisão de quadras em lotes sintéticos: deixar como refinamento futuro (backlog)
6. Documentar no README que **não substitui cadastro oficial**

---

## 5. Estrutura do repositório

```
construction-viability-map/
├── README.md
├── CONTRIBUTING.md
├── PROJECT_PLAN.md                       # este documento
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
├── Makefile
├── config/
│   ├── global.yaml                       # CRS, paths, defaults
│   └── regions/
│       ├── _template.yaml                # template para nova cidade
│       └── sao_jose_sc.yaml
├── src/
│   ├── core/                             # COMPARTILHADO entre cidades
│   │   ├── __init__.py
│   │   ├── config.py                     # loader YAML
│   │   ├── ingest/
│   │   │   ├── ibge.py
│   │   │   ├── osm.py
│   │   │   └── topodata.py
│   │   ├── transform/
│   │   │   ├── slope.py
│   │   │   ├── app_buffer.py
│   │   │   ├── lots_from_blocks.py
│   │   │   └── attribute_join.py
│   │   ├── features/                     # 1 módulo por atributo
│   │   │   ├── slope_feature.py
│   │   │   ├── zoning_feature.py
│   │   │   ├── app_feature.py
│   │   │   ├── risk_feature.py
│   │   │   └── distance_features.py
│   │   └── pipeline.py                   # orquestra tudo
│   ├── regions/                          # ESPECÍFICO por cidade
│   │   ├── __init__.py
│   │   ├── base.py                       # interface RegionAdapter
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

## 6. Interface RegionAdapter (modularidade multi-município)

Cada município implementa essa interface. O core não conhece detalhes de nenhuma cidade.

```python
# src/regions/base.py
from typing import Protocol
from geopandas import GeoDataFrame

class RegionAdapter(Protocol):
    slug: str                              # ex: "sao_jose_sc"
    ibge_code: str                         # ex: "4216602"
    crs_local: str                         # ex: "EPSG:31982"
    bbox: tuple[float, float, float, float]

    def load_boundary(self) -> GeoDataFrame: ...
    def load_zoning(self) -> GeoDataFrame: ...        # específico
    def load_risk_areas(self) -> GeoDataFrame: ...    # específico
    def load_lots(self) -> GeoDataFrame: ...          # estratégia varia
    def zoning_schema(self) -> dict: ...              # mapeia códigos locais → atributos padrão
```

Adicionar Florianópolis = criar `src/regions/florianopolis_sc/adapter.py` + YAML. Não mexer no core.

---

## 7. Configuração YAML por região

Exemplo: `config/regions/sao_jose_sc.yaml`

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
    strategy: image_overlay              # MVP — v2 muda para "vector"
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
    thresholds: {low: 15, medium: 30}    # % declividade
  app:
    enabled: true
    river_buffer_m: 30                   # Código Florestal
  risk:
    enabled: false                       # v2
  zoning:
    enabled: false                       # v2
  distance_to_main_road:
    enabled: true
```

**Trocar de cidade = trocar o YAML + criar adapter. Pronto.**

---

## 8. UI do Streamlit (filtros independentes)

### Layout
- **Sidebar esquerda**: filtros e controles
- **Área central**: mapa Folium em tela cheia
- **Sidebar direita (opcional)**: estatísticas dos lotes filtrados

### Widgets de controle
- **Seleção de município** (dropdown — preparado para multi-cidade)
- **Atributo de coloração** (radio): qual variável colore o mapa
  - declividade média, área, distância a via, zona (quando vetorizado), bairro
- **Filtros ativos** (acordeão expansível):
  - Declividade: slider min/max
  - Zona: multi-select (quando disponível)
  - APP: checkbox "excluir lotes em APP"
  - Risco: multi-select de tipos a excluir
  - Área mínima do lote: slider
  - Distância máx a via principal: slider
  - Tipo de lote (`osm_building` vs `synthetic_block`)

### Interações
- Tooltip ao passar mouse no lote: atributos resumidos
- Popup ao clicar: tabela completa + link Street View
- Camadas opcionais (toggle): hidrografia, vias, imagens georref. do Plano Diretor
- Legenda dinâmica baseada no atributo de coloração

### Performance
- Limite recomendado para Folium: ~10–20k features
- Se necessário, migrar mapa para `pydeck` (deck.gl, WebGL)
- Considerar tile servidor para versão futura

---

## 9. Roadmap de execução

| Fase | Entregável | Commits estimados |
|---|---|---|
| 1 | Setup: repo, venv, requirements, estrutura, README inicial, config loader | 3–5 |
| 2 | Core ingest: IBGE, OSM, Topodata (genéricos) | 5–8 |
| 3 | Adapter São José/SC + georreferenciamento PDF→PNG+world file | 4–6 |
| 4 | Core transform: slope, APP buffer, lotes alt. B | 5–7 |
| 5 | Features: 1 commit por atributo | 5 |
| 6 | Pipeline orquestrador + dataset final em GeoParquet | 2–3 |
| 7 | App Streamlit MVP (mapa estático) | 4–6 |
| 8 | Filtros, tooltips, legenda | 3–4 |
| 9 | README com screenshots, CONTRIBUTING, deploy no Streamlit Cloud | 3–4 |

**Total estimado:** 35–50 commits

---

## 10. Backlog (issues futuras no GitHub)

### Dados e qualidade
- [ ] Solicitar shapefile de lotes à Prefeitura de São José via LAI
- [ ] Vetorização completa dos Mapas 03 e 08 do Plano Diretor
- [ ] Validação manual de quadras OSM em bairros menos mapeados
- [ ] Cache de queries Overpass (evitar rate limit)

### Funcionalidade
- [ ] Algoritmo de subdivisão de quadras em lotes sintéticos
- [ ] Cálculo de score combinado opcional (ponderação configurável)
- [ ] Exportação de lotes filtrados como CSV/GeoJSON

### Multi-município
- [ ] Adapter para Florianópolis (usar `geo.pmf.sc.gov.br`)
- [ ] Template `_template.yaml` documentado para nova cidade
- [ ] CONTRIBUTING.md com guia passo-a-passo

### Infraestrutura
- [ ] CI no GitHub Actions (lint + testes)
- [ ] Deploy automatizado no Streamlit Cloud
- [ ] DVC ou Git LFS para versionar dados processados

---

## 11. Riscos conhecidos

| Risco | Mitigação |
|---|---|
| Qualidade desigual do OSM em São José | Validação visual antes de processar; documentar limitação |
| Georreferenciamento manual introduz erro | Documentar pontos de controle e RMS error |
| Performance do Folium com >10k lotes | Plano B: migrar para pydeck (WebGL) |
| Rate limit Overpass API | Cache local + download em batches |
| Topodata 30m é grosseiro para análise intra-quadra | Aceitável para MVP; documentar limitação |

---

## 12. Glossário

- **APP** — Área de Preservação Permanente (Código Florestal, Lei 12.651/2012). Em rios, faixa marginal mínima de 30m.
- **MDE** — Modelo Digital de Elevação
- **Topodata** — MDE refinado para o Brasil pelo INPE a partir do SRTM
- **Plano Diretor** — Lei municipal que define zoneamento, uso e ocupação do solo
- **CRS** — Coordinate Reference System. EPSG:31982 = SIRGAS 2000 / UTM zona 22S (oficial para SC)
- **EPSG:4326** — WGS84, lat/lon (formato web/GPS)
- **GeoParquet** — formato colunar eficiente para dados geográficos
- **Adapter Pattern** — padrão de design onde cada implementação específica respeita uma interface comum

---

## 13. Estado atual

✅ **Concluído:**
- Levantamento de requisitos
- Identificação de fontes de dados
- Arquitetura definida
- Decisões técnicas tomadas
- Roadmap delineado

🟡 **Próximo passo:**
- Fase 1: criação da estrutura inicial do repositório