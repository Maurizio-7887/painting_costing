# ENOROSSI Paint Optimizer v5 — Industrial Coating System

Sistema industriale di verniciatura/costing con gestione 3D di pezzi appesi a ganci.

## Stack
- **Backend**: Flask + SQLAlchemy + PostgreSQL/SQLite
- **CAD parsing**: trimesh (STEP/STL → BOM automatica)
- **3D Viewer**: Three.js + GLB pipeline
- **Fisica**: motore custom per equilibrio pendolare e bilanciamento ganci
- **Nesting**: algoritmo first-fit con vincoli peso/spazio/drenaggio

## Moduli principali

| File | Descrizione |
|------|-------------|
| `app.py` | Flask app — tutte le route (produzione, CAD, nesting, API REST) |
| `cad_parser.py` | Phase 1 — parsing STEP → BOM + geometrie + fori di aggancio |
| `physics_hanging.py` | Motore fisico — equilibrio pendolare, multi-hook, drenaggio |
| `overhead_nesting.py` | Phase 3 — nesting overhead 3D, SVG render, allocazione su ganci |
| `cad_parser_patch.py` | Patch helper per fix mapping nomi→mesh STEP |
| `nesting_catena.py` | Nesting catena trasporto (modulo legacy) |
| `templates/cad_viewer3d.html` | Three.js viewer — drag&drop, BVH collision, AJAX validate |

## Directory runtime (create automaticamente)
- `static/glb/<asm_id>/` — GLB files generati da `export_glb_for_viewer()`
- `uploads/` — STEP/STL files caricati dall'utente

## Setup

```bash
pip install -r requirements.txt
flask db upgrade
flask run
```

## Route principali

```
GET  /cad/upload                        → upload STEP
GET  /cad/bom/<asm_id>                  → BOM assembly
GET  /cad/ordine/<id>/viewer3d          → viewer Three.js
POST /cad/ordine/<id>/nesting           → calcolo nesting
GET  /api/cad/glb/<asm_id>/<codice>     → serve file GLB
GET  /api/cad/glb_list/<asm_id>         → lista GLB disponibili
POST /api/nesting/validate_drop         → validazione drop D&D
```

## Debiti tecnici aperti

- ⚠️ **Mapping nomi→mesh STEP**: per assembly complessi l'ordine di parsing
  non è garantito — il matching per posizione può assegnare dimensioni errate.
  Soluzione: matching per similarity geometrica (extents + volume).
  Non blocca il funzionamento base, da correggere prima del go-live.

## Versioning fasi
- **Fase 1**: STEP parser + BOM + fisica hanging
- **Fase 2**: Three.js viewer 3D + GLB pipeline  
- **Fase 3**: Drag&drop + BVH collision client-side + AJAX validate_drop
- **Fase 4**: KPI header, hatch DANGER, tilt wire 3D, polish gunmetal
