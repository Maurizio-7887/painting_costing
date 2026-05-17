# ENOROSSI Paint Optimizer v5 — Industrial Coating System

Sistema industriale di verniciatura con nesting 3D, viewer interattivo e calcolo costi.

## Funzionalità
- Upload STEP/STL → parsing geometria → BOM automatica
- Calcolo fisico pendolare (bilanciamento, peso, baricentro)
- Nesting 3D su ganci con safety UX (DANGER/WARNING)
- Viewer Three.js con drag & drop, BVH collision, validazione AJAX
- KPI header: saturazione, peso totale, momento, m² superficie

## Setup locale
```bash
pip install -r requirements.txt
flask db upgrade
flask run
```

## Deploy Railway
1. Crea un Volume Railway montato su `/data`
2. Aggiungi variabile: `GLB_DIR=/data/glb`
3. `git push` → Railway fa il deploy automatico

## Struttura
- `app.py` — Flask backend (route API + UI)
- `cad_parser.py` — Parser STEP/STL → BOM
- `physics_hanging.py` — Motore fisico pendolare
- `overhead_nesting.py` — Nesting 3D + SVG
- `templates/cad_viewer3d.html` — Viewer Three.js interattivo

## Note tecniche
- Three.js 0.165.0 + three-mesh-bvh 0.7.4
- GLB generati runtime → richiedono storage persistente (Volume Railway o S3)
- Fallback box placeholder automatico se GLB non disponibili (timeout 8s)
