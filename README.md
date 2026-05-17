# Construction Viability Map

> Mapa interativo de viabilidade de construção em lotes urbanos, com filtros independentes por características físicas e legais.

**Status:** 🚧 Em desenvolvimento — Fase 1 (Setup)

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-WIP-orange.svg)

## Sobre

Este projeto constrói um mapa interativo que mostra a viabilidade de construção em lotes de uma cidade, permitindo ao usuário filtrar por:

- **Características físicas**: declividade, áreas de preservação permanente (APP), áreas de risco
- **Características legais**: zoneamento do Plano Diretor
- **Acessibilidade urbana**: distância a vias principais, escolas, equipamentos de saúde

A arquitetura é **modular por município**: novas cidades podem ser adicionadas sem modificar o código central.

### Cidade piloto

**São José, Santa Catarina** (código IBGE: 4216602)

Próxima cidade planejada: Florianópolis/SC.

## Stack

- **Python 3.11+**
- **GeoPandas / Rasterio / OSMnx** — pipeline de dados geoespaciais
- **Streamlit + Folium** — interface web interativa
- **GeoParquet** — dataset final tabular

## Como rodar

```bash
# 1. Clonar o repositório
git clone https://github.com/VTheStone/construction-viability-map.git
cd construction-viability-map

# 2. Criar e ativar ambiente virtual
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows PowerShell:
venv\Scripts\Activate.ps1

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Executar pipeline de dados (uma vez)
make process REGION=sao_jose_sc

# 5. Subir o app
make app
```

## Estrutura do projeto

```

construction-viability-map/
├── config/regions/             # 1 YAML por município
├── src/
│   ├── core/                   # código compartilhado entre cidades
│   └── regions/                # adapter específico de cada cidade
├── data/                       # gitignored (gerado pelo pipeline)
└── tests/

```

Detalhes completos em [`PROJECT_PLAN.md`](./PROJECT_PLAN.md).

## Adicionar uma nova cidade

1. Criar `config/regions/minha_cidade.yaml` (use `_template.yaml` como base)
2. Criar `src/regions/minha_cidade/adapter.py` implementando a interface `RegionAdapter`
3. Rodar `make process REGION=minha_cidade`

Veja [`CONTRIBUTING.md`](./CONTRIBUTING.md) (em breve).

## Limitações conhecidas

- **Lotes**: São José/SC não tem cadastro público de lotes. Este projeto usa edificações do OpenStreetMap + quadras sintéticas como proxy. **Não substitui consulta oficial à Prefeitura.**
- **Zoneamento**: na versão atual, os mapas do Plano Diretor (Mapa 03 e Mapa 08) são exibidos como camada de imagem georreferenciada. A vetorização completa está no backlog.
- **MDE**: usa Topodata INPE (~30m de resolução), adequado para análise municipal mas grosseiro para análise intra-quadra.

## Licença

MIT — veja [`LICENSE`](./LICENSE) (a adicionar).

## Documentação adicional

- [`PROJECT_PLAN.md`](./PROJECT_PLAN.md) — plano completo do projeto, arquitetura e roadmap