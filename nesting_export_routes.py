"""
nesting_export_routes.py — ENOROSSI Paint Optimizer v5
PATCH ROUTE: Export CAD 2D Nesting (SVG / DXF) + Viewer 3D singolo pezzo (GLB)

═══════════════════════════════════════════════════════════════════
COME INTEGRARE IN app.py (SENZA TOCCARE NULLA DELL'ESISTENTE):
  1. Copia questo file nella root del progetto.
  2. In app.py, DOPO l'ultima route e PRIMA di if __name__=='__main__',
     aggiungi le 3 righe:

       # ── Nuove route: export CAD 2D + viewer GLB singolo pezzo ──
       from nesting_export_routes import register_export_routes
       register_export_routes(app, db, OrdineCAD, BOMAssembly,
                              BOMRecordCAD, ItemMasterCAD)

  3. In requirements.txt aggiungi:
       ezdxf>=1.2.0

  4. Nei template, aggiungere i pulsanti (vedi istruzioni in fondo a questo file).
═══════════════════════════════════════════════════════════════════

Nuove route aggiunte:
  GET /cad/ordine/<id>/nesting.svg   → SVG download (o inline con ?inline=1)
  GET /cad/ordine/<id>/nesting.dxf   → DXF download
  GET /cad/part/<codice>/viewer      → Pagina HTML viewer 3D pezzo singolo
  GET /api/cad/part/<codice>/geometry.glb  → Serve GLB del singolo pezzo
"""

from __future__ import annotations
import io
import os
import json
import tempfile

from flask import (
    request, send_file, render_template,
    abort, current_app, Response
)


def register_export_routes(app, db, OrdineCAD, BOMAssembly,
                            BOMRecordCAD, ItemMasterCAD):
    """
    Registra le nuove route nell'istanza Flask esistente.
    Chiamare da app.py DOPO aver definito i modelli.
    """

    # ══════════════════════════════════════════════════════════════
    # ROUTE 1 — SVG Nesting
    # ══════════════════════════════════════════════════════════════
    @app.route('/cad/ordine/<int:ordine_id>/nesting.svg')
    def cad_nesting_svg(ordine_id):
        """
        Genera SVG del piano nesting con silhouette reali e quote.
        ?inline=1  → ritorna SVG inline (per anteprima browser)
        default    → download
        """
        ordine = OrdineCAD.query.get_or_404(ordine_id)

        if not ordine.nesting_json or ordine.nesting_json == '{}':
            return "Nesting non ancora calcolato", 400

        try:
            from nesting_cad_export import render_nesting_svg

            nd = json.loads(ordine.nesting_json)
            asm_nome = ordine.assembly_ref.nome if ordine.assembly_ref else f"Ordine #{ordine_id}"
            titolo = f"NESTING OVERHEAD — {asm_nome} × {ordine.n_unita} unità"

            svg_bytes = render_nesting_svg(nd, titolo, dark_mode=True)

            inline = request.args.get('inline', '0') == '1'
            if inline:
                return Response(svg_bytes,
                                mimetype='image/svg+xml',
                                headers={'Cache-Control': 'no-cache'})
            else:
                return send_file(
                    io.BytesIO(svg_bytes),
                    mimetype='image/svg+xml',
                    as_attachment=True,
                    download_name=f'nesting_{ordine_id}.svg'
                )

        except Exception as e:
            import traceback; traceback.print_exc()
            return f"Errore generazione SVG: {e}", 500


    # ══════════════════════════════════════════════════════════════
    # ROUTE 2 — DXF Nesting
    # ══════════════════════════════════════════════════════════════
    @app.route('/cad/ordine/<int:ordine_id>/nesting.dxf')
    def cad_nesting_dxf(ordine_id):
        """
        Genera DXF ISO del piano nesting.
        Download diretto (.dxf) apribile in AutoCAD / DraftSight / FreeCAD.
        """
        ordine = OrdineCAD.query.get_or_404(ordine_id)

        if not ordine.nesting_json or ordine.nesting_json == '{}':
            return "Nesting non ancora calcolato. Esegui prima il calcolo overhead.", 400

        try:
            from nesting_cad_export import render_nesting_dxf

            nd = json.loads(ordine.nesting_json)
            asm_nome = ordine.assembly_ref.nome if ordine.assembly_ref else f"Ordine #{ordine_id}"
            titolo = f"{asm_nome} — ×{ordine.n_unita} unità"

            # Genera in temp file
            dxf_path = render_nesting_dxf(nd, titolo)

            return send_file(
                dxf_path,
                mimetype='application/dxf',
                as_attachment=True,
                download_name=f'nesting_overhead_{ordine_id}.dxf'
            )

        except ImportError:
            return (
                "Libreria ezdxf non installata. "
                "Aggiungere 'ezdxf>=1.2.0' a requirements.txt e riavviare.",
                500
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            return f"Errore generazione DXF: {e}", 500


    # ══════════════════════════════════════════════════════════════
    # ROUTE 3 — Viewer 3D pezzo singolo (pagina HTML)
    # ══════════════════════════════════════════════════════════════
    @app.route('/cad/part/<codice>/viewer')
    def cad_part_viewer(codice):
        """
        Pagina HTML con viewer Three.js per singolo pezzo.
        Carica GLB dal route /api/cad/part/<codice>/geometry.glb
        """
        item = ItemMasterCAD.query.filter_by(codice_art=codice).first()
        if not item:
            abort(404)

        return render_template(
            'cad_part_viewer.html',
            item=item,
            glb_url=f'/api/cad/part/{codice}/geometry.glb',
        )


    # ══════════════════════════════════════════════════════════════
    # ROUTE 4 — Serve GLB singolo pezzo
    # ══════════════════════════════════════════════════════════════
    @app.route('/api/cad/part/<codice>/geometry.glb')
    def api_cad_part_glb(codice):
        """
        Serve il file GLB di un singolo pezzo.
        Pipeline (in ordine di preferenza):
          1. stl_path nel parse_json → carica STL → esporta GLB
          2. mesh in parse_json (vertices/faces) → ricostruisce → GLB
          3. Fallback → bounding-box trimesh.creation.box() con L×W×H reali + CoG marker
        """
        item = ItemMasterCAD.query.filter_by(codice_art=codice).first()
        if not item:
            abort(404)

        try:
            import trimesh
            import numpy as np

            mesh = None

            # ── Tentativo 1: recupera STL path dal parse_json dell'assembly ──
            asm_record = BOMAssembly.query.filter_by(
                file_step=item.assembly_file
            ).first()

            if asm_record and asm_record.parse_json:
                try:
                    parse_dict = json.loads(asm_record.parse_json)
                    bom = parse_dict.get('bom', [])
                    for p in bom:
                        if p.get('codice') == codice and p.get('stl_path'):
                            stl_p = p['stl_path']
                            if os.path.exists(stl_p):
                                mesh = trimesh.load(stl_p, force='mesh', process=True)
                                break
                except Exception:
                    pass

            # ── Tentativo 2: bounding box con dimensioni reali ────────────────
            if mesh is None or (hasattr(mesh, 'is_empty') and mesh.is_empty):
                L = float(item.lunghezza_mm) or 200.0
                W = float(item.larghezza_mm) or 150.0
                H = float(item.altezza_mm)   or 100.0

                mesh = trimesh.creation.box(extents=[L, W, H])

                # Colora in grigio chiaro (aspetto metallo grezzo)
                mesh.visual.face_colors = [100, 120, 140, 200]

                # Aggiunge marker sferico per il CoG
                cog_x = float(item.cog_x_mm) - L/2
                cog_y = float(item.cog_y_mm) - W/2
                cog_z = float(item.cog_z_mm) - H/2
                cog_sphere = trimesh.creation.icosphere(radius=min(L,W,H)*0.04)
                cog_sphere.apply_translation([cog_x, cog_y, cog_z])
                cog_sphere.visual.face_colors = [0, 200, 80, 255]  # verde CoG

                mesh = trimesh.util.concatenate([mesh, cog_sphere])

            # ── Esporta GLB ───────────────────────────────────────────────────
            glb_bytes = mesh.export(file_type='glb')

            return send_file(
                io.BytesIO(glb_bytes),
                mimetype='model/gltf-binary',
                as_attachment=False,
                download_name=f'{codice}.glb'
            )

        except Exception as e:
            import traceback; traceback.print_exc()
            return f"Errore GLB: {e}", 500


# ══════════════════════════════════════════════════════════════════
# ═══  ISTRUZIONI PATCH TEMPLATE  ══════════════════════════════════
# ══════════════════════════════════════════════════════════════════
"""
────────────────────────────────────────────────────────────────────
PATCH 1 — cad_nesting.html
Trovare questa riga (circa riga 171-172):

  <a href="{{ url_for('cad_nesting_png', ordine_id=ordine.id) }}" class="btn-out" style="padding:.2rem .6rem;font-size:.7rem" target="_blank">
    <i class="bi bi-download me-1"></i>PNG
  </a>

AGGIUNGERE SUBITO DOPO (non toccare la riga PNG):

  {% if nesting %}
  <a href="{{ url_for('cad_nesting_svg', ordine_id=ordine.id) }}"
     class="btn-out" style="padding:.2rem .6rem;font-size:.7rem" target="_blank"
     title="Scarica SVG con quote e sagome reali">
    <i class="bi bi-filetype-svg me-1"></i>SVG
  </a>
  <a href="{{ url_for('cad_nesting_dxf', ordine_id=ordine.id) }}"
     class="btn-out" style="padding:.2rem .6rem;font-size:.7rem;background:rgba(0,200,80,.08);border-color:rgba(0,200,80,.3);color:#00C851"
     title="Scarica DXF — apri in AutoCAD / DraftSight / FreeCAD">
    <i class="bi bi-download me-1"></i>DXF ↗
  </a>
  {% endif %}

────────────────────────────────────────────────────────────────────
PATCH 2 — cad_bom_detail.html
Trovare questo blocco (circa riga 96):

  <td style="text-align:center">
    {% if item.mesh_presente %}
      <span class="bdg bdg-grn">3D</span>
    {% else %}
      <span class="bdg bdg-gray">est.</span>
    {% endif %}
  </td>

SOSTITUIRE CON:

  <td style="text-align:center">
    {% if item.mesh_presente %}
      <span class="bdg bdg-grn">3D</span>
      <a href="{{ url_for('cad_part_viewer', codice=item.codice_art) }}"
         class="btn-out" style="padding:.15rem .45rem;font-size:.65rem;margin-left:4px"
         target="_blank" title="Apri viewer 3D pezzo">
        <i class="bi bi-box"></i>
      </a>
    {% else %}
      <span class="bdg bdg-gray">est.</span>
      <a href="{{ url_for('cad_part_viewer', codice=item.codice_art) }}"
         class="btn-out" style="padding:.15rem .45rem;font-size:.65rem;margin-left:4px;opacity:.6"
         target="_blank" title="Viewer 3D (bounding box)">
        <i class="bi bi-box-seam"></i>
      </a>
    {% endif %}
  </td>
"""

if __name__ == '__main__':
    print("nesting_export_routes.py — Patch route per app.py")
    print("Vedi ISTRUZIONI PATCH TEMPLATE nel docstring finale.")
    print("Route aggiunte:")
    print("  GET /cad/ordine/<id>/nesting.svg")
    print("  GET /cad/ordine/<id>/nesting.dxf")
    print("  GET /cad/part/<codice>/viewer")
    print("  GET /api/cad/part/<codice>/geometry.glb")
