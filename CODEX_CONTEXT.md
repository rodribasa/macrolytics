# Macrolytics development context

## Objective

Macrolytics is a macroeconomic dashboard for Argentina.

Architecture:

- ETLs fetch official/public economic data.
- Data is stored in Dolt/DoltHub.
- React frontend queries DoltHub directly.
- GitHub Actions updates datasets and deploys GitHub Pages.

## Frontend

Located in:

frontend/

Stack:

- React
- Vite
- react-router-dom
- Plotly
- GitHub Pages

Production:

https://rbasa.github.io/macrolytics/#/

Vite uses `/macrolytics/` as production base and `/` locally.

GitHub Pages is deployed through:

.github/workflows/publish_pages.yml

The old static `docs/` frontend is being retired.

### Reusable frontend structure

Components:

- Navbar.jsx
- Footer.jsx
- Layout.jsx
- StatCard.jsx
- ChartCard.jsx
- PlotlyChart.jsx
- VariationChart.jsx

Utilities:

- utils/series.js: calculations / numeric-series transformations
- utils/formatters.js: display formatting
- utils/charts.js: Plotly trace builders

Pages:

- HomePage.jsx
- Inflation.jsx
- economicActivity/EconomicActivity.jsx
- economicActivity/PbiAnalysis.jsx
- economicActivity/EmaeAnalysis.jsx
- TradeBalance.jsx
- FiscalBalance.jsx
- UvaAnalysis.jsx
- Expectations.jsx

Avoid putting generic formatting, series math, or Plotly trace construction
inside individual pages when it can be reused.

## Existing datasets

### IPC Argentina

Table:

ipc_argentina

ETL:

etl/populate_ipc_arg.py

Source:

Datos Argentina API / INDEC

The API is queried with multiple series IDs simultaneously and `last=5`.
Available values are retained even when other components are unpublished.
The ETL logs missing series and periods, and fails if no usable observations
remain. Before writing, it makes existing IPC value columns nullable (preserving
their types and the date primary key). New missing values are stored as NULL;
upserts preserve existing values when the source returns NULL. This schema
change is committed with the IPC data and is a no-op on subsequent runs.
Source availability must be checked at runtime; a successful run does not
guarantee that Datos Argentina has republished the latest INDEC release.

Do not assume the single-series endpoint response has the same freshness:
we observed inconsistent behavior when querying the IPC general index alone.

### EMAE

ETL:

etl/populate_emae_arg.py

### Quarterly GDP

Tables:

- pbi_argentina_precios_2004
- pbi_argentina_precios_corrientes
- pbi_demanda_argentina_precios_2004
- pbi_demanda_argentina_precios_corrientes

ETL:

etl/populate_pbi_arg.py

Source:

Datos Argentina / INDEC quarterly national accounts, using fixed series IDs.

The tables keep constant 2004 and current prices separate. Seasonally adjusted
volume aggregates live in the constant-price tables. Analytical growth rates
should be derived outside the ETL.

### Trade

Table:

trade_argentina

ETL:

etl/populate_trade_arg.py

### Consumer confidence

Table:

consumer_confidence_argentina

ETL:

etl/populate_consumer_confidence_arg.py

Series:

- ICC nacional
- Capital
- GBA
- Interior
- Situación personal
- Situación macroeconómica
- Bienes durables e inmuebles

Historical regional/component coverage begins later than Capital.
Missing observations should remain NULL, never be replaced with zero.

### Fiscal data

Table:

fiscal_argentina

Goal:

Replicate every line of the official monthly AIF / Sector Público Nacional
cash-basis statement, plus detailed tax revenue by tax.

Important design decision:

DO NOT dynamically resolve fiscal series using fuzzy text search.

An earlier implementation used Datos Argentina search metadata and selected
the most recent matching series. This produced wrong mappings such as:

- ingresos tributarios -> ingresos no tributarios
- ingresos de operación -> remuneraciones
- superávit empresas públicas -> déficit empresas públicas

The final ETL should therefore use deterministic sources.

Current intended design:

1. Official monthly ONP AIF Excel:
   read every published balance-sheet row directly from the `TOTAL` column.

2. Datos Argentina / IMIG:
   use fixed IDs only for detailed tax breakdown.

No accounting identity should be inferred in the ETL if the official row
exists directly in the source.

The AIF rows required include:

- Ingresos corrientes
- Ingresos impositivos
- Aportes y contribuciones seguridad social
- Ingresos no impositivos
- Ventas bienes/servicios
- Ingresos de operación
- Rentas propiedad netas
- Transferencias corrientes
- Otros ingresos
- Superávit operativo empresas públicas

- Gastos corrientes
- Gastos consumo y operación
  - Remuneraciones
  - Bienes y servicios
  - Otros gastos
- Intereses y otras rentas
  - Intereses netos
  - Otras rentas
- Prestaciones seguridad social
- Otros gastos corrientes
- Transferencias corrientes
  - Sector privado
  - Sector público
    - Provincias y CABA
    - Universidades
    - Otras
  - Sector externo
- Otros gastos
- Déficit operativo empresas públicas

- Resultado económico
- Recursos de capital
- Gastos de capital
  - Inversión real directa
  - Transferencias de capital
    - Provincias y CABA
    - Otras
  - Inversión financiera
    - Provincias y CABA
    - Resto

- Ingresos antes de figurativos
- Gastos antes de figurativos
- Resultado financiero antes de figurativos

- Contribuciones figurativas
  - Tesoro Nacional
  - Recursos afectados
  - Organismos descentralizados
  - Seguridad social
  - PAMI/fondos/otros

- Gastos figurativos
- Ingresos después de figurativos
- Gastos primarios después de figurativos
- Gastos después de figurativos
- Resultado primario
- Resultado financiero

Memo:
- Rentas percibidas BCRA
- Rentas públicas percibidas por FGS y otros
- Intereses pagados intra-sector público

Detailed tax breakdown from IMIG:

- IVA
- Ganancias
- Débitos y créditos
- Bienes personales
- Combustibles
- Derechos de exportación
- Derechos de importación
- Impuestos internos
- Resto tributarios

All fiscal values are millions of current ARS.

Pending backlog:
build constant-price equivalents for fiscal analysis.

## UVA

Page:

frontend/src/pages/UvaAnalysis.jsx

Data:

fx_rate

Pairs:

- UVA_ARS
- USD_ARS
- USDB_ARS

UVA/USD official = UVA ARS / official USD mid
UVA/USD blue = UVA ARS / blue USD mid

DoltHub query results are limited, so the frontend needs date chunking
(approximately six-month chunks) to retrieve the full history from 2017 onward.

## Expectations section

Page:

frontend/src/pages/Expectations.jsx

Current dataset:

consumer_confidence_argentina

Analysis should emphasize:

- current ICC level
- monthly change in index points
- 12-month change in index points
- temporal trajectory
- component decomposition
- regional decomposition

Do not treat a change in an index as a conventional percentage return unless
there is a methodological reason to do so.

Future additions:

- government confidence index
- business confidence
- inflation expectations

## GitHub Actions

Daily ETL workflow updates datasets and pushes Dolt commits.

React/GitHub Pages deployment is separate and only runs when:

- frontend/** changes
- publish_pages.yml changes
- manually triggered

A data refresh does not require redeploying the frontend because the frontend
queries DoltHub at runtime.

## Coding preferences

- Prefer straightforward code over unnecessary abstractions.
- Abstract genuinely reusable calculations / formatting / charts.
- Avoid duplicate utility logic inside pages.
- Fixed economic-series IDs are preferable to heuristic search.
- Missing economic values -> NULL, never zero unless zero is actually published.
- Keep workflow and ETL output concise.
