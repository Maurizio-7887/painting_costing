"""
nesting_ordine_routes.py — ENOROSSI Paint Optimizer v26
========================================================
Route Flask per il nesting degli ordini SAP/NetPro (OrdineOrdine).

Funzionalità:
  GET  /ordine/<id>/nesting               → Hub: scegli Auto o Manuale
  GET  /ordine/<id>/nesting/auto          → Risultato nesting automatico
  GET  /ordine/<id>/nesting/manuale       → Canvas drag-drop manuale
  POST /api/ordine/<id>/nesting/save      → Salva layout (auto o manuale)
  GET  /api/ordine/<id>/nesting/suggest   → Suggerimento CBR dal learner
  GET  /api/nesting/learning/stats        → Statistiche dataset ML

Logica di lookup articoli:
  OrdineOrdine.codice_prodotto → cerca in ItemMasterCAD.codice_art (exact)
                               → poi cerca in ItemMasterCAD.nome (contains)
                               → fallback: placeholder rettangolare configurabile

Registrazione:
  Aggiungere in app.py:
      from nesting_ordine_routes import register_ordine_nesting_routes
      register_ordine_nesting_routes(app, db, OrdineOrdine, ItemMasterCAD, Configurazione)
"""

import json
from datetime import datetime, timezone
from flask import render_template, request, jsonify, abort

from nesting_learning import (
    NestingLearner,
    make_learner_for_flask,
    session_key_from_ordine_ordine,
    session_key_from_ordine_cad,
)


# ─── Costanti placeholder ───────────────────────────────────────────────────────
PLACEHOLDER_DIMS = {
    'L_mm':    300.0,
    'H_mm':    400.0,
    'W_mm':     80.0,
    'peso_kg':   5.0,
}


# ─── Lookup articolo ────────────────────────────────────────────────────────────
def _lookup_item(codice: str, ItemMasterCAD):
    """
    Cerca un ItemMasterCAD dal codice prodotto.
    Strategia: exact → ilike → None (placeholder).
    """
    # 1. Exact match su codice_art
    item = ItemMasterCAD.query.filter_by(codice_art=codice).first()
    if item:
        return item, 'exact'

    # 2. Case-insensitive su codice_art
    item = ItemMasterCAD.query.filter(
        ItemMasterCAD.codice_art.ilike(f'%{codice}%')
    ).first()
    if item:
        return item, 'ilike'

    # 3. Cerca nel nome
    item = ItemMasterCAD.query.filter(
        ItemMasterCAD.nome.ilike(f'%{codice}%')
    ).first()
    if item:
        return item, 'nome'

    return None, 'placeholder'


def _item_to_palette_entry(codice: str, qty: int, item, match_type: str) -> dict:
    """Converte un ItemMasterCAD (o None) in voce palette per il canvas."""
    if item:
        return {
            'codice':        item.codice_art,
            'nome':          item.nome,
            'L_mm':          round(item.lunghezza_mm or PLACEHOLDER_DIMS['L_mm'], 1),
            'H_mm':          round(item.altezza_mm   or PLACEHOLDER_DIMS['H_mm'], 1),
            'W_mm':          round(item.larghezza_mm or PLACEHOLDER_DIMS['W_mm'], 1),
            'peso_kg':       round(item.peso_kg      or PLACEHOLDER_DIMS['peso_kg'], 2),
            'mesh_presente': bool(item.mesh_presente),
            'match_type':    match_type,
            'qty_totale':    qty,
            'qty_palette':   qty,
        }
    else:
        return {
            'codice':        codice,
            'nome':          f'{codice} (non in DB)',
            'L_mm':          PLACEHOLDER_DIMS['L_mm'],
            'H_mm':          PLACEHOLDER_DIMS['H_mm'],
            'W_mm':          PLACEHOLDER_DIMS['W_mm'],
            'peso_kg':       PLACEHOLDER_DIMS['peso_kg'],
            'mesh_presente': False,
            'match_type':    'placeholder',
            'qty_totale':    qty,
            'qty_palette':   qty,
        }


# ─── Register routes ────────────────────────────────────────────────────────────
def register_ordine_nesting_routes(app, db, OrdineOrdine, ItemMasterCAD, Configurazione):
    """Registra tutte le route di nesting per OrdineOrdine."""

    learner = make_learner_for_flask(db)

    # Garantisce che la tabella history esista
    with app.app_context():
        try:
            learner.ensure_table()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # HUB: scelta Auto / Manuale + info CBR
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/ordine/<int:ordine_id>/nesting')
    def ordine_nesting_hub(ordine_id):
        """Landing page: mostra info ordine, pulsanti Auto/Manuale, suggerimento CBR."""
        ordine = OrdineOrdine.query.get_or_404(ordine_id)
        cfg    = Configurazione.query.first()

        # Lookup articolo
        item, match_type = _lookup_item(ordine.codice_prodotto, ItemMasterCAD)
        palette_entry    = _item_to_palette_entry(
            ordine.codice_prodotto, ordine.quantita, item, match_type
        )
        codici_qty = {palette_entry['codice']: ordine.quantita}

        # Suggerimento CBR
        sk          = session_key_from_ordine_ordine(ordine_id)
        suggestions = learner.suggest_layout(codici_qty, source_filter='manual', top_k=1)
        suggestion  = suggestions[0] if suggestions else None

        # Ultimo layout salvato
        latest = learner.get_latest_layout(sk)

        # Stats ML
        stats = learner.get_stats()

        return render_template(
            'nesting_ordine_hub.html',
            ordine        = ordine,
            palette_entry = palette_entry,
            match_type    = match_type,
            suggestion    = suggestion,
            latest        = latest,
            stats         = stats,
        )

    # ─────────────────────────────────────────────────────────────────────
    # AUTO NESTING
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/ordine/<int:ordine_id>/nesting/auto')
    def ordine_nesting_auto(ordine_id):
        """Esegue il nesting automatico e mostra il risultato."""
        ordine = OrdineOrdine.query.get_or_404(ordine_id)
        cfg    = Configurazione.query.first()

        item, match_type = _lookup_item(ordine.codice_prodotto, ItemMasterCAD)
        palette_entry    = _item_to_palette_entry(
            ordine.codice_prodotto, ordine.quantita, item, match_type
        )

        # Configura parametri impianto
        n_ganci_tot = int(getattr(cfg, 'n_ganci_totali', 425) if cfg else 425)
        passo_mm    = float(getattr(cfg, 'passo_gancio_mm', 400) if cfg else 400)
        max_kg_hook = 60.0

        error_msg   = None
        result_json = None
        svg_url     = None

        try:
            from overhead_nesting import (
                ottimizza_nesting_overhead,
                LoadBarConfig,
                PartSuspension,
                prepara_fisica_parte,
                render_overhead_svg,
            )

            bar_cfg = LoadBarConfig(
                n_ganci_totali = n_ganci_tot,
                passo_gancio_mm= passo_mm,
                max_kg_gancio  = max_kg_hook,
            )

            # Costruisce lista pezzi
            pezzi = []
            for _ in range(ordine.quantita):
                ps = PartSuspension(
                    codice_art = palette_entry['codice'],
                    nome       = palette_entry['nome'],
                    L_mm       = palette_entry['L_mm'],
                    H_mm       = palette_entry['H_mm'],
                    W_mm       = palette_entry['W_mm'],
                    peso_kg    = palette_entry['peso_kg'],
                )
                pezzi.append(ps)

            nesting_result = ottimizza_nesting_overhead(pezzi, bar_cfg)

            # Salva in history come 'auto'
            sk         = session_key_from_ordine_ordine(ordine_id)
            codici_qty = {palette_entry['codice']: ordine.quantita}
            layout_dict = {
                'mode':     'auto',
                'ordine_id': ordine_id,
                'hooks':    [
                    {
                        'hook_idx': i,
                        'parts':    [p.codice_art for p in g.pezzi],
                        'peso_kg':  g.peso_totale_kg,
                    }
                    for i, g in enumerate(nesting_result.ganci)
                    if g.pezzi
                ],
                'kpi': {
                    'saturazione_pct': nesting_result.saturazione_pct,
                    'bilanciamento':   nesting_result.bilanciamento_pct,
                    'n_ganci_usati':   nesting_result.n_ganci_usati,
                    'n_pezzi':         sum(len(g.pezzi) for g in nesting_result.ganci),
                    'peso_totale_kg':  nesting_result.peso_totale_kg,
                },
            }
            learner.record_layout(
                sk, codici_qty, layout_dict,
                source    = 'auto',
                score_sat = nesting_result.saturazione_pct,
                score_bal = nesting_result.bilanciamento_pct,
            )
            result_json = json.dumps(layout_dict)

        except ImportError:
            error_msg = "Modulo overhead_nesting non disponibile."
        except Exception as e:
            error_msg = f"Errore ottimizzazione: {str(e)}"

        return render_template(
            'nesting_ordine_auto.html',
            ordine        = ordine,
            palette_entry = palette_entry,
            match_type    = match_type,
            result_json   = result_json,
            error_msg     = error_msg,
            n_ganci_tot   = n_ganci_tot,
            passo_mm      = passo_mm,
            max_kg_hook   = max_kg_hook,
        )

    # ─────────────────────────────────────────────────────────────────────
    # MANUALE CANVAS
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/ordine/<int:ordine_id>/nesting/manuale')
    def ordine_nesting_manuale(ordine_id):
        """Canvas drag-drop manuale per OrdineOrdine."""
        ordine = OrdineOrdine.query.get_or_404(ordine_id)
        cfg    = Configurazione.query.first()

        item, match_type = _lookup_item(ordine.codice_prodotto, ItemMasterCAD)
        palette_entry    = _item_to_palette_entry(
            ordine.codice_prodotto, ordine.quantita, item, match_type
        )

        n_ganci_tot = int(getattr(cfg, 'n_ganci_totali', 425) if cfg else 425)
        passo_mm    = float(getattr(cfg, 'passo_gancio_mm', 400) if cfg else 400)
        max_kg_hook = 60.0

        # Carica ultimo layout manuale se esiste
        sk          = session_key_from_ordine_ordine(ordine_id)
        latest      = learner.get_latest_layout(sk)
        existing_layout = {}
        if latest and latest.get('layout', {}).get('mode') == 'manual':
            existing_layout = latest['layout']

        n_hooks_display = 12
        if existing_layout.get('hooks'):
            n_hooks_display = max(len(existing_layout['hooks']) + 5, 12)
        n_hooks_display = min(n_hooks_display, n_ganci_tot)

        # Suggerimento CBR per pre-populate
        codici_qty  = {palette_entry['codice']: ordine.quantita}
        suggestions = learner.suggest_layout(codici_qty, source_filter='manual', top_k=1)
        cbr_suggestion = suggestions[0] if suggestions else None

        return render_template(
            'nesting_ordine_manuale.html',
            ordine          = ordine,
            items_json      = json.dumps([palette_entry]),
            n_ganci_tot     = n_ganci_tot,
            n_hooks_display = n_hooks_display,
            passo_mm        = passo_mm,
            max_kg_hook     = max_kg_hook,
            layout_json     = json.dumps(existing_layout),
            session_key     = sk,
            cbr_suggestion  = json.dumps(cbr_suggestion) if cbr_suggestion else 'null',
            match_type      = match_type,
        )

    # ─────────────────────────────────────────────────────────────────────
    # API — Salva layout (auto o manuale)
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/ordine/<int:ordine_id>/nesting/save', methods=['POST'])
    def api_ordine_nesting_save(ordine_id):
        """
        Body JSON atteso:
        {
            "layout":    { ... oggetto layout completo ... },
            "codici_qty": { "codice": quantita, ... },
            "source":    "manual" | "auto",
            "score_sat": 72.5,
            "score_bal": 91.0,
            "operatore": "Mario"   (opzionale)
        }
        """
        ordine = OrdineOrdine.query.get_or_404(ordine_id)
        data   = request.get_json(silent=True) or {}

        layout     = data.get('layout', {})
        codici_qty = data.get('codici_qty', {ordine.codice_prodotto: ordine.quantita})
        source     = data.get('source', 'manual')
        score_sat  = float(data.get('score_sat', 0))
        score_bal  = float(data.get('score_bal', 0))
        operatore  = data.get('operatore')

        if not layout:
            return jsonify({'ok': False, 'error': 'layout vuoto'}), 400

        sk  = session_key_from_ordine_ordine(ordine_id)
        rid = learner.record_layout(
            sk, codici_qty, layout,
            source    = source,
            score_sat = score_sat,
            score_bal = score_bal,
            operatore = operatore,
        )

        return jsonify({
            'ok':         True,
            'history_id': rid,
            'session_key': sk,
            'message':    f'Layout {source} salvato (ID {rid})',
        })

    # ─────────────────────────────────────────────────────────────────────
    # API — Suggerimento CBR
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/ordine/<int:ordine_id>/nesting/suggest')
    def api_ordine_nesting_suggest(ordine_id):
        """Restituisce il layout più simile dal learner CBR."""
        ordine = OrdineOrdine.query.get_or_404(ordine_id)
        item, _ = _lookup_item(ordine.codice_prodotto, ItemMasterCAD)
        codice  = item.codice_art if item else ordine.codice_prodotto
        codici_qty = {codice: ordine.quantita}

        suggestions = learner.suggest_layout(codici_qty, top_k=3)
        return jsonify({
            'ok':          True,
            'suggestions': suggestions,
            'query_codici': codici_qty,
        })

    # ─────────────────────────────────────────────────────────────────────
    # API — Stats ML
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/nesting/learning/stats')
    def api_nesting_learning_stats():
        """Statistiche dataset apprendimento."""
        try:
            stats = learner.get_stats()
            return jsonify({'ok': True, **stats})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────────────
    # API — History per sessione
    # ─────────────────────────────────────────────────────────────────────
    @app.route('/api/ordine/<int:ordine_id>/nesting/history')
    def api_ordine_nesting_history(ordine_id):
        """Storico layout per questo ordine."""
        sk      = session_key_from_ordine_ordine(ordine_id)
        history = learner.get_history_for_session(sk, limit=20)
        return jsonify({'ok': True, 'session_key': sk, 'history': history})
