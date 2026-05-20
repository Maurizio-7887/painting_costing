# nesting_manual_routes.py
# ──────────────────────────────────────────────────────────────────────────────
# Nesting Manuale Interattivo — ADDIZIONE PURA (non modifica app.py)
#
# Aggiunge a app.py (2 righe):
#   from nesting_manual_routes import register_manual_routes
#   register_manual_routes(app, db, OrdineCAD, BOMAssembly, BOMRecordCAD, ItemMasterCAD, Configurazione)
# ──────────────────────────────────────────────────────────────────────────────

import json
from datetime import datetime, timezone
from collections import defaultdict

from flask import render_template, request, jsonify


def register_manual_routes(app, db, OrdineCAD, BOMAssembly, BOMRecordCAD, ItemMasterCAD, Configurazione):
    """Registra le route del nesting manuale + tabella history."""

    # ── Tabella history (crea automaticamente se non esiste) ──────────────
    from sqlalchemy import text
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS nesting_manual_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ordine_id   INTEGER NOT NULL,
                    layout_json TEXT,
                    kpi_json    TEXT,
                    salvato_il  TEXT,
                    operatore   TEXT DEFAULT ''
                )
            """))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # ─────────────────────────────────────────────────────────────────────
    # PAGINA PRINCIPALE — Canvas manuale
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/cad/ordine/<int:ordine_id>/manuale')
    def cad_nesting_manuale(ordine_id):
        """Canvas drag-drop per nesting manuale su catena di verniciatura."""
        ordine = OrdineCAD.query.get_or_404(ordine_id)
        asm    = ordine.assembly_ref

        # ── Configurazione impianto ──
        cfg           = Configurazione.query.first()
        n_ganci_tot   = int(cfg.n_ganci_totali  if cfg else 425)
        passo_mm      = float(cfg.passo_gancio_mm if cfg else 400)
        max_kg_hook   = 60.0   # limite di default per gancio

        # ── Parti della BOM (raggruppate per codice) ──
        records = BOMRecordCAD.query.filter_by(assembly_id=asm.id).all()
        palette = {}
        for rec in records:
            item = ItemMasterCAD.query.filter_by(codice_art=rec.codice_art).first()
            if not item:
                continue
            qty_totale = rec.qty * ordine.n_unita
            cod = item.codice_art
            if cod not in palette:
                palette[cod] = {
                    'codice':        cod,
                    'nome':          item.nome,
                    'L_mm':          round(item.lunghezza_mm or 200, 1),
                    'H_mm':          round(item.altezza_mm   or 300, 1),
                    'W_mm':          round(item.larghezza_mm or 50,  1),
                    'peso_kg':       round(item.peso_kg      or 1.0, 2),
                    'mesh_presente': bool(item.mesh_presente),
                    'qty_totale':    0,
                    'qty_palette':   0,   # aggiornata dal JS dopo carico layout
                }
            palette[cod]['qty_totale']  += qty_totale
            palette[cod]['qty_palette'] += qty_totale

        items_list = sorted(palette.values(), key=lambda x: -x['peso_kg'])

        # ── Layout esistente (se mode == 'manual') ──
        existing_layout = {}
        if ordine.nesting_json and ordine.nesting_json not in ('{}', ''):
            try:
                raw = json.loads(ordine.nesting_json)
                if raw.get('mode') == 'manual':
                    existing_layout = raw
            except Exception:
                pass

        # ── Numero di hook da mostrare inizialmente ──
        # Mostra max(hook_usati + 5, 12), non tutti i 425
        n_hooks_display = 12
        if existing_layout.get('hooks'):
            n_hooks_display = max(len(existing_layout['hooks']) + 5, 12)
        n_hooks_display = min(n_hooks_display, n_ganci_tot)

        return render_template(
            'nesting_manuale.html',
            ordine          = ordine,
            asm             = asm,
            items_json      = json.dumps(items_list),
            n_ganci_tot     = n_ganci_tot,
            n_hooks_display = n_hooks_display,
            passo_mm        = passo_mm,
            max_kg_hook     = max_kg_hook,
            layout_json     = json.dumps(existing_layout),
        )

    # ─────────────────────────────────────────────────────────────────────
    # API — Salva layout manuale
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/nesting/save_manual', methods=['POST'])
    def api_nesting_save_manual():
        """
        Salva il layout manuale su ordine.nesting_json e nella tabella history.
        Body JSON:
          {
            "ordine_id": 5,
            "operatore": "Mario",      # opzionale
            "layout": {
              "hooks": [
                { "idx": 0, "parts": [ { "codice":..., "L_mm":..., ... } ] }
              ],
              "kpi": { "hooks_usati": 4, "peso_totale": 88.5, ... }
            }
          }
        """
        try:
            d         = request.get_json(force=True) or {}
            ordine_id = d.get('ordine_id')
            operatore = d.get('operatore', '')
            layout    = d.get('layout', {})

            if not ordine_id:
                return jsonify({'ok': False, 'message': 'ordine_id mancante'}), 400

            ordine = OrdineCAD.query.get_or_404(ordine_id)

            # Arricchisce con metadati
            layout['mode']       = 'manual'
            layout['salvato_il'] = datetime.now(timezone.utc).isoformat()
            layout['operatore']  = operatore

            layout_str = json.dumps(layout, ensure_ascii=False)
            kpi_str    = json.dumps(layout.get('kpi', {}))

            # Salva sull'ordine
            ordine.nesting_json = layout_str
            db.session.commit()

            # Salva in history
            try:
                db.session.execute(
                    __import__('sqlalchemy').text("""
                        INSERT INTO nesting_manual_history
                            (ordine_id, layout_json, kpi_json, salvato_il, operatore)
                        VALUES
                            (:oid, :lj, :kj, :sl, :op)
                    """),
                    {
                        'oid': ordine_id,
                        'lj':  layout_str,
                        'kj':  kpi_str,
                        'sl':  layout['salvato_il'],
                        'op':  operatore,
                    }
                )
                db.session.commit()
            except Exception:
                db.session.rollback()   # history non critica

            return jsonify({
                'ok':      True,
                'message': f'Layout manuale salvato — ordine #{ordine_id}',
                'kpi':     layout.get('kpi', {}),
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({'ok': False, 'message': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────
    # API — Storia layout (per futuro CBR / apprendimento)
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/nesting/history')
    def api_nesting_history():
        """Ultimi 50 layout manuali salvati (per CBR futuro)."""
        try:
            rows = db.session.execute(
                __import__('sqlalchemy').text("""
                    SELECT id, ordine_id, kpi_json, salvato_il, operatore
                    FROM   nesting_manual_history
                    ORDER  BY id DESC
                    LIMIT  50
                """)
            ).fetchall()
            result = []
            for row in rows:
                result.append({
                    'id':         row[0],
                    'ordine_id':  row[1],
                    'kpi':        json.loads(row[2] or '{}'),
                    'salvato_il': row[3],
                    'operatore':  row[4],
                })
            return jsonify({'ok': True, 'history': result})
        except Exception as e:
            return jsonify({'ok': False, 'message': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────
    # API — Suggerimento CBR (layout simile da storico)
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/nesting/suggest/<int:ordine_id>')
    def api_nesting_suggest(ordine_id):
        """
        Cerca nella history un layout con parti simili all'ordine corrente.
        Risponde con il layout trovato + score di similarità.
        Futuro: sostituire con ML/embedding. Per ora: Jaccard su set di codici.
        """
        try:
            ordine = OrdineCAD.query.get_or_404(ordine_id)
            asm    = ordine.assembly_ref
            records = BOMRecordCAD.query.filter_by(assembly_id=asm.id).all()
            current_codes = {r.codice_art for r in records}

            rows = db.session.execute(
                __import__('sqlalchemy').text("""
                    SELECT id, ordine_id, layout_json, kpi_json, salvato_il
                    FROM   nesting_manual_history
                    ORDER  BY id DESC
                    LIMIT  200
                """)
            ).fetchall()

            best_score  = -1
            best_layout = None
            best_meta   = {}

            for row in rows:
                try:
                    lay = json.loads(row[2] or '{}')
                    # Estrae codici da layout
                    hist_codes = set()
                    for hk in lay.get('hooks', []):
                        for pt in hk.get('parts', []):
                            hist_codes.add(pt.get('codice', ''))
                    # Jaccard
                    if not hist_codes:
                        continue
                    intersection = len(current_codes & hist_codes)
                    union        = len(current_codes | hist_codes)
                    score        = intersection / union if union else 0
                    if score > best_score:
                        best_score  = score
                        best_layout = lay
                        best_meta   = {
                            'history_id': row[0],
                            'ordine_ref': row[1],
                            'salvato_il': row[4],
                            'score_pct':  round(score * 100, 1),
                        }
                except Exception:
                    continue

            if best_layout and best_score > 0.3:
                return jsonify({
                    'ok':     True,
                    'found':  True,
                    'layout': best_layout,
                    'meta':   best_meta,
                })
            else:
                return jsonify({'ok': True, 'found': False,
                                'message': 'Nessun layout simile trovato nello storico'})

        except Exception as e:
            return jsonify({'ok': False, 'message': str(e)}), 500
