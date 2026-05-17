"""
app_cad_routes.py — ENOROSSI Paint Optimizer v5
NUOVE ROUTE: CAD Parsing · BOM · Production Order · Overhead 3D Nesting

Questo file contiene ESCLUSIVAMENTE il codice da AGGIUNGERE a app.py.
Le route qui definite si collegano ai moduli:
  - cad_parser.py     → PHASE 1: STEP → BOM
  - overhead_nesting.py → PHASE 3: 3D Nesting overhead

COME INTEGRARE:
  1. Aggiungere i modelli DB (ItemMasterCAD, BOMAssembly, BOMRecordCAD,
     OrdineCAD) dentro app.py INSIEME agli altri modelli.
  2. Aggiungere le route al fondo di app.py (prima di if __name__=='__main__').
  3. Creare migration: flask db migrate -m "add_cad_bom_models"
"""

# ════════════════════════════════════════════════════════════════
# NUOVI MODELLI DB
# (Inserire in app.py insieme a Prodotto, Lotto, etc.)
# ════════════════════════════════════════════════════════════════

NUOVI_MODELLI_CODE = '''
# ══════════════════════════════════════════════════════════════════
# MODELLI CAD / BOM / ORDINE-CAD (aggiunti con cad_parser)
# ══════════════════════════════════════════════════════════════════

class ItemMasterCAD(db.Model):
    """Item Master generato da parsing STEP — codice deterministico ART-AGRI-XXXXXX."""
    __tablename__ = 'item_master_cad'
    id               = db.Column(db.Integer, primary_key=True)
    codice_art       = db.Column(db.String(20), unique=True, nullable=False)  # ART-AGRI-XXXXXX
    nome             = db.Column(db.String(200), nullable=False)
    assembly_file    = db.Column(db.String(200))                              # file STEP di origine
    # Dati geometrici estratti
    superficie_m2    = db.Column(db.Float, default=0.0)
    volume_m3        = db.Column(db.Float, default=0.0)
    peso_kg          = db.Column(db.Float, default=0.0)
    lunghezza_mm     = db.Column(db.Float, default=0.0)
    larghezza_mm     = db.Column(db.Float, default=0.0)
    altezza_mm       = db.Column(db.Float, default=0.0)
    cog_x_mm         = db.Column(db.Float, default=0.0)
    cog_y_mm         = db.Column(db.Float, default=0.0)
    cog_z_mm         = db.Column(db.Float, default=0.0)
    passo_gancio_m   = db.Column(db.Float, default=0.4)
    complessita      = db.Column(db.Integer, default=1)
    hash_geom        = db.Column(db.String(20))
    mesh_presente    = db.Column(db.Boolean, default=False)
    creato_il        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class BOMAssembly(db.Model):
    """Intestazione assembly STEP (l'entità prodotto completo)."""
    __tablename__ = 'bom_assembly'
    id              = db.Column(db.Integer, primary_key=True)
    nome            = db.Column(db.String(200), nullable=False)    # nome assembly
    file_step       = db.Column(db.String(300))                    # nome file originale
    n_parti_uniche  = db.Column(db.Integer, default=0)
    n_parti_totali  = db.Column(db.Integer, default=0)
    peso_totale_kg  = db.Column(db.Float, default=0.0)
    sup_totale_m2   = db.Column(db.Float, default=0.0)
    parse_json      = db.Column(db.Text, default='{}')             # JSON completo del ParseResult
    creato_il       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    records         = db.relationship('BOMRecordCAD', backref='assembly', lazy=True, cascade='all,delete')


class BOMRecordCAD(db.Model):
    """Singola voce BOM: parte X appartiene all'assembly Y con qty Z."""
    __tablename__ = 'bom_record_cad'
    id              = db.Column(db.Integer, primary_key=True)
    assembly_id     = db.Column(db.Integer, db.ForeignKey('bom_assembly.id'), nullable=False)
    codice_art      = db.Column(db.String(20))
    nome_part       = db.Column(db.String(200))
    livello         = db.Column(db.Integer, default=1)
    nome_parent     = db.Column(db.String(200), default='')
    qty             = db.Column(db.Integer, default=1)


class OrdineCAD(db.Model):
    """Ordine di produzione generato da BOM CAD con quantità esplosa."""
    __tablename__ = 'ordine_cad'
    id              = db.Column(db.Integer, primary_key=True)
    assembly_id     = db.Column(db.Integer, db.ForeignKey('bom_assembly.id'))
    n_unita         = db.Column(db.Integer, default=1)             # N macchine da produrre
    stato           = db.Column(db.String(30), default='aperto')   # aperto, in_nesting, chiuso
    nesting_json    = db.Column(db.Text, default='{}')             # risultato overhead nesting
    note            = db.Column(db.Text)
    creato_il       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    assembly_ref    = db.relationship('BOMAssembly', backref='ordini', lazy=True)
'''

# ════════════════════════════════════════════════════════════════
# NUOVE ROUTE
# (Inserire in app.py PRIMA di if __name__=='__main__')
# ════════════════════════════════════════════════════════════════

NUOVE_ROUTE_CODE = '''
# ══════════════════════════════════════════════════════════════════
# ROUTE: CAD PARSER — Upload STEP → BOM automatica
# ══════════════════════════════════════════════════════════════════

@app.route('/cad/upload', methods=['GET', 'POST'])
def cad_upload():
    """Upload file STEP/STP → parsing → BOM nel DB."""
    assemblies = BOMAssembly.query.order_by(BOMAssembly.id.desc()).limit(20).all()
    result = None
    errore = None

    if request.method == 'POST':
        f = request.files.get('file_step')
        if not f or not f.filename:
            errore = "Nessun file selezionato."
        elif not f.filename.lower().endswith(('.stp', '.step')):
            errore = "Il file deve essere in formato STEP (.stp o .step)."
        else:
            import tempfile
            try:
                from cad_parser import analizza_step, scrivi_bom_nel_db, parse_result_to_dict

                with tempfile.NamedTemporaryFile(
                    suffix=os.path.splitext(f.filename)[1], delete=False
                ) as tmp:
                    f.save(tmp.name)
                    parse_result = analizza_step(tmp.name)
                os.unlink(tmp.name)

                if parse_result.errori:
                    errore = " | ".join(parse_result.errori[:3])
                else:
                    # Salva BOMAssembly
                    asm = BOMAssembly(
                        nome           = parse_result.assembly_nome,
                        file_step      = f.filename,
                        n_parti_uniche = parse_result.n_parti_uniche,
                        n_parti_totali = parse_result.n_parti_totali,
                        peso_totale_kg = parse_result.peso_totale_kg,
                        sup_totale_m2  = parse_result.superficie_totale_m2,
                        parse_json     = json.dumps(parse_result_to_dict(parse_result)),
                    )
                    db.session.add(asm)
                    db.session.flush()

                    stats = scrivi_bom_nel_db(
                        parse_result, db, ItemMasterCAD, BOMRecordCAD,
                        assembly_id=asm.id
                    )
                    db.session.commit()

                    flash(
                        f"✅ Assembly '{parse_result.assembly_nome}' analizzato: "
                        f"{stats['n_parti_uniche']} parti uniche · "
                        f"{parse_result.peso_totale_kg:.1f} kg · "
                        f"{parse_result.superficie_totale_m2:.2f} m²",
                        'success'
                    )
                    if parse_result.warning:
                        flash(f"⚠️ Note: {parse_result.warning[0]}", 'warning')
                    return redirect(url_for('cad_bom_detail', asm_id=asm.id))

            except Exception as e:
                db.session.rollback()
                errore = f"Errore parsing: {e}"

    return render_template('cad_upload.html',
                           assemblies=assemblies,
                           result=result,
                           errore=errore)


@app.route('/cad/bom/<int:asm_id>')
def cad_bom_detail(asm_id):
    """Vista dettaglio BOM di un assembly STEP."""
    asm = BOMAssembly.query.get_or_404(asm_id)
    records = BOMRecordCAD.query.filter_by(assembly_id=asm_id).all()

    # Arricchisce records con dati ItemMasterCAD
    items_data = []
    for rec in records:
        item = ItemMasterCAD.query.filter_by(codice_art=rec.codice_art).first()
        items_data.append({
            'codice_art':    rec.codice_art,
            'nome':          rec.nome_part,
            'livello':       rec.livello,
            'qty':           rec.qty,
            'superficie_m2': item.superficie_m2 if item else 0,
            'peso_kg':       item.peso_kg if item else 0,
            'lunghezza_mm':  item.lunghezza_mm if item else 0,
            'larghezza_mm':  item.larghezza_mm if item else 0,
            'altezza_mm':    item.altezza_mm if item else 0,
            'cog_x_mm':      item.cog_x_mm if item else 0,
            'cog_y_mm':      item.cog_y_mm if item else 0,
            'cog_z_mm':      item.cog_z_mm if item else 0,
            'passo_m':       item.passo_gancio_m if item else 0.4,
            'mesh_presente': item.mesh_presente if item else False,
        })

    ordini = OrdineCAD.query.filter_by(assembly_id=asm_id).order_by(OrdineCAD.id.desc()).all()

    return render_template('cad_bom_detail.html',
                           asm=asm, items=items_data, ordini=ordini)


@app.route('/cad/ordine/nuovo', methods=['POST'])
def cad_ordine_nuovo():
    """Crea ordine di produzione CAD con N unità → esplode BOM."""
    asm_id  = int(request.form.get('assembly_id', 0))
    n_unita = int(request.form.get('n_unita', 1))
    note    = request.form.get('note', '')

    asm = BOMAssembly.query.get_or_404(asm_id)
    ordine = OrdineCAD(
        assembly_id = asm_id,
        n_unita     = n_unita,
        stato       = 'aperto',
        note        = note,
    )
    db.session.add(ordine)
    db.session.commit()
    flash(
        f"✅ Ordine CAD #{ordine.id} creato: {n_unita} × '{asm.nome}' "
        f"→ {asm.n_parti_totali * n_unita} pezzi totali in coda",
        'success'
    )
    return redirect(url_for('cad_ordine_nesting', ordine_id=ordine.id))


@app.route('/cad/ordine/<int:ordine_id>/nesting', methods=['GET', 'POST'])
def cad_ordine_nesting(ordine_id):
    """
    PHASE 3: Overhead 3D Nesting per un ordine CAD.
    GET  → mostra form configurazione barra + risultato se già calcolato.
    POST → esegue ottimizzazione e salva risultato.
    """
    ordine = OrdineCAD.query.get_or_404(ordine_id)
    asm = ordine.assembly_ref
    cfg_imp = get_config()

    nesting_data = {}
    if ordine.nesting_json and ordine.nesting_json != '{}':
        try:
            nesting_data = json.loads(ordine.nesting_json)
        except Exception:
            nesting_data = {}

    if request.method == 'POST':
        try:
            from overhead_nesting import (
                LoadBarConfig, esplodi_ordine_produzione,
                ottimizza_nesting_overhead, analizza_load_balance
            )

            # Leggi parametri barra dal form
            bar_cfg = LoadBarConfig(
                L_max_mm        = float(request.form.get('L_max_mm', 3000)),
                Z_max_mm        = float(request.form.get('Z_max_mm', 2000)),
                passo_gancio_mm = float(cfg_imp.passo_gancio_mm or 400),
                peso_max_bar_kg = float(request.form.get('peso_max_kg', 420)),
                peso_max_gancio_kg = float(request.form.get('peso_max_gancio', 60)),
            )

            # Recupera BOM dal ParseResult salvato
            parse_dict = {}
            if asm.parse_json:
                try:
                    parse_dict = json.loads(asm.parse_json)
                except Exception:
                    pass

            bom_items = parse_dict.get('bom', [])
            # Arricchisci con dati geometrici da BOMRecord
            records = BOMRecordCAD.query.filter_by(assembly_id=asm.id).all()
            rec_map = {r.codice_art: r for r in records}
            item_map = {
                i.codice_art: i
                for i in ItemMasterCAD.query.filter(
                    ItemMasterCAD.codice_art.in_([r.codice_art for r in records])
                ).all()
            }

            bom_per_nesting = []
            for rec in records:
                item = item_map.get(rec.codice_art)
                if not item:
                    continue
                bom_per_nesting.append({
                    'codice_art':    rec.codice_art,
                    'nome':          rec.nome_part,
                    'lunghezza_mm':  item.lunghezza_mm,
                    'larghezza_mm':  item.larghezza_mm,
                    'altezza_mm':    item.altezza_mm,
                    'peso_kg':       item.peso_kg,
                    'superficie_m2': item.superficie_m2,
                    'passo_gancio_m':item.passo_gancio_m,
                    'qty':           rec.qty,
                })

            # Esplodi per N unità
            parti = esplodi_ordine_produzione(bom_per_nesting, ordine.n_unita)
            strategia = request.form.get('strategia', 'ffd_peso')

            # Ottimizza
            result = ottimizza_nesting_overhead(parti, bar_cfg, strategia=strategia)
            lb = analizza_load_balance(result.ganci, bar_cfg)

            nesting_data = result.to_dict()
            nesting_data['load_balance'] = lb
            nesting_data['strategia'] = strategia
            nesting_data['n_unita'] = ordine.n_unita

            ordine.nesting_json = json.dumps(nesting_data)
            ordine.stato = 'in_nesting'
            db.session.commit()

            flash(
                f"✅ Nesting overhead calcolato: "
                f"{nesting_data['kpi']['allocate']} pezzi allocati · "
                f"Saturazione {nesting_data['kpi']['saturazione_pct']:.0f}% · "
                f"Peso {nesting_data['kpi']['peso_totale_kg']:.1f} kg",
                'success'
            )
            if nesting_data.get('avvisi'):
                for av in nesting_data['avvisi'][:2]:
                    flash(av, 'warning')

        except Exception as e:
            flash(f"Errore nesting: {e}", 'danger')
            import traceback; traceback.print_exc()

    return render_template(
        'cad_nesting.html',
        ordine=ordine,
        asm=asm,
        nesting=nesting_data,
        cfg=cfg_imp,
    )


@app.route('/cad/ordine/<int:ordine_id>/nesting.png')
def cad_nesting_png(ordine_id):
    """Genera PNG del piano nesting overhead."""
    ordine = OrdineCAD.query.get_or_404(ordine_id)
    if not ordine.nesting_json or ordine.nesting_json == '{}':
        return "Nesting non ancora calcolato", 400

    try:
        from overhead_nesting import (
            NestingResult, LoadBarConfig, GancioState, PartSuspension, render_overhead_png
        )
        import tempfile

        nd = json.loads(ordine.nesting_json)
        bc_data = nd.get('bar_config', {})
        bar_cfg = LoadBarConfig(
            L_max_mm        = bc_data.get('L_max_mm', 3000),
            Z_max_mm        = bc_data.get('Z_max_mm', 2000),
            passo_gancio_mm = bc_data.get('passo_mm', 400),
        )

        # Ricostruisce ganci + parti per rendering
        ganci = []
        for g_data in nd.get('ganci', []):
            parti_g = []
            for p_data in g_data.get('parti', []):
                p = PartSuspension(
                    codice=p_data.get('codice', ''),
                    nome=p_data.get('nome', ''),
                    L_mm=p_data.get('L_mm', 400),
                    H_mm=p_data.get('H_mm', 300),
                    peso_kg=p_data.get('peso_kg', 1.0),
                    colore=p_data.get('colore', '#455A64'),
                    z_offset_mm=p_data.get('z_offset', 0.0),
                    rot_deg=p_data.get('rot_deg', 0.0),
                    superficie_m2=p_data.get('sup_m2', 0.0),
                    n_ganci_req=1,
                    allocato=True,
                )
                parti_g.append(p)
            g = GancioState(
                idx=g_data.get('idx', 0),
                x_mm=g_data.get('x_mm', 0.0),
                peso_tot_kg=g_data.get('peso_kg', 0.0),
                z_occupata_mm=g_data.get('z_mm', 0.0),
                parti=parti_g,
                libero=g_data.get('libero', True),
            )
            ganci.append(g)

        kpi = nd.get('kpi', {})
        result = NestingResult(
            bar_config=bar_cfg,
            ganci=ganci,
            peso_totale_kg=kpi.get('peso_totale_kg', 0),
            saturazione_pct=kpi.get('saturazione_pct', 0),
            ganci_usati=kpi.get('ganci_usati', 0),
            momento_max_Nm=kpi.get('momento_max_Nm', 0),
            avvisi=nd.get('avvisi', []),
        )

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            asm_nome = ordine.assembly_ref.nome if ordine.assembly_ref else f"Ordine #{ordine_id}"
            render_overhead_png(
                result, tmp.name,
                titolo=f"NESTING OVERHEAD — {asm_nome} × {ordine.n_unita} unità"
            )
            return send_file(tmp.name, mimetype='image/png',
                             download_name=f'nesting_overhead_{ordine_id}.png')

    except Exception as e:
        return f"Errore rendering: {e}", 500


@app.route('/api/cad/assemblies')
def api_cad_assemblies():
    """API REST: lista assembly caricati."""
    assemblies = BOMAssembly.query.order_by(BOMAssembly.id.desc()).all()
    return jsonify([{
        'id':             a.id,
        'nome':           a.nome,
        'file_step':      a.file_step,
        'n_parti_uniche': a.n_parti_uniche,
        'n_parti_totali': a.n_parti_totali,
        'peso_totale_kg': a.peso_totale_kg,
        'sup_totale_m2':  a.sup_totale_m2,
        'creato_il':      a.creato_il.isoformat() if a.creato_il else None,
    } for a in assemblies])


@app.route('/api/cad/bom/<int:asm_id>')
def api_cad_bom(asm_id):
    """API REST: BOM completa di un assembly."""
    asm = BOMAssembly.query.get_or_404(asm_id)
    records = BOMRecordCAD.query.filter_by(assembly_id=asm_id).all()
    item_map = {
        i.codice_art: i
        for i in ItemMasterCAD.query.filter(
            ItemMasterCAD.codice_art.in_([r.codice_art for r in records])
        ).all()
    }
    return jsonify({
        'assembly': {
            'id': asm.id, 'nome': asm.nome,
            'peso_totale_kg': asm.peso_totale_kg,
            'sup_totale_m2': asm.sup_totale_m2,
        },
        'bom': [
            {
                'codice_art':    r.codice_art,
                'nome':          r.nome_part,
                'qty':           r.qty,
                'sup_m2':        item_map[r.codice_art].superficie_m2 if r.codice_art in item_map else 0,
                'peso_kg':       item_map[r.codice_art].peso_kg if r.codice_art in item_map else 0,
                'passo_m':       item_map[r.codice_art].passo_gancio_m if r.codice_art in item_map else 0.4,
                'lunghezza_mm':  item_map[r.codice_art].lunghezza_mm if r.codice_art in item_map else 0,
                'altezza_mm':    item_map[r.codice_art].altezza_mm if r.codice_art in item_map else 0,
                'cog_z_mm':      item_map[r.codice_art].cog_z_mm if r.codice_art in item_map else 0,
            }
            for r in records
        ],
    })


@app.route('/api/cad/ordine/<int:ordine_id>/esplodi')
def api_cad_esplodi(ordine_id):
    """API REST: BOM esplosa (quantità × N unità) per un ordine CAD."""
    ordine = OrdineCAD.query.get_or_404(ordine_id)
    asm = ordine.assembly_ref
    records = BOMRecordCAD.query.filter_by(assembly_id=asm.id).all()
    item_map = {
        i.codice_art: i
        for i in ItemMasterCAD.query.filter(
            ItemMasterCAD.codice_art.in_([r.codice_art for r in records])
        ).all()
    }
    esplosa = []
    for rec in records:
        item = item_map.get(rec.codice_art)
        esplosa.append({
            'codice_art':    rec.codice_art,
            'nome':          rec.nome_part,
            'qty_per_unita': rec.qty,
            'qty_totale':    rec.qty * ordine.n_unita,
            'sup_m2_unit':   item.superficie_m2 if item else 0,
            'sup_m2_tot':    round((item.superficie_m2 if item else 0) * rec.qty * ordine.n_unita, 4),
            'peso_kg_unit':  item.peso_kg if item else 0,
            'peso_kg_tot':   round((item.peso_kg if item else 0) * rec.qty * ordine.n_unita, 2),
        })
    totali = {
        'n_righe_bom':        len(esplosa),
        'n_unita':            ordine.n_unita,
        'pezzi_totali':       sum(e['qty_totale'] for e in esplosa),
        'superficie_totale':  round(sum(e['sup_m2_tot'] for e in esplosa), 3),
        'peso_totale_kg':     round(sum(e['peso_kg_tot'] for e in esplosa), 2),
    }
    return jsonify({'ordine_id': ordine_id, 'totali': totali, 'righe': esplosa})

from flask import send_from_directory, safe_join

@app.route('/api/cad/mesh/<path:filename>')
def serve_cad_mesh(filename):
    """
    Ritorna il vero file STL o GLB della parte Enorossi 
    memorizzato nella cartella dei caricamenti del server.
    """
    upload_dir = app.config.get('UPLOAD_FOLDER', 'uploads/meshes')
    # Protegge da directory traversal e invia il file reale
    return send_from_directory(upload_dir, filename, as_attachment=False)

if __name__ == "__main__":
    print("app_cad_routes.py — Contiene codice da integrare in app.py")
    print("Variabili esposte:")
    print("  NUOVI_MODELLI_CODE — incolla nei modelli DB di app.py")
    print("  NUOVE_ROUTE_CODE   — incolla nelle route di app.py")
