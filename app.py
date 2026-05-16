"""
ENOROSSI Paint Optimizer v4.0
app.py — Flask application completa
Collega tutti i 18 template HTML con le route Flask.

Route map:
  /                   → dashboard
  /ottimizzatore      → carico giornaliero (Elia)
  /operaio            → vista operaio mobile
  /operaio/<id>       → dettaglio lotto per operaio
  /operaio/<id>/avvia → avvia lotto
  /lotti              → storico lotti
  /lotto/<id>         → dettaglio lotto
  /lotto/<id>/conferma, /completa, /delete
  /ordini             → ordini di produzione
  /prodotti           → catalogo prodotti
  /prodotto/nuovo     → form nuovo prodotto
  /prodotto/<id>/edit → modifica prodotto
  /storico            → storico costi ABC
  /storico/export     → CSV export
  /storico/<codice>   → dettaglio codice
  /abc                → analisi ABC
  /tempi              → standard tempi
  /knowledge_base     → KB operativa
  /import_sap         → import CSV/NetPro
  /configurazione     → parametri impianto
  /piano_carico       → smart loading plan
  /api/...            → API REST
"""

import os, json, csv, io, math
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, Response, send_file)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

app = Flask(__name__)

# ── CONFIG ──────────────────────────────────────────────────────
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///enorossi.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif _db_url.startswith('postgresql://') and '+psycopg2' not in _db_url:
    _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'enorossi-dev-key-2026')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB per file 3D

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ── MODELLI ─────────────────────────────────────────────────────

class Configurazione(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lunghezza_tunnel_m          = db.Column(db.Float, default=170.0)
    velocita_default            = db.Column(db.Float, default=1.5)
    velocita_min                = db.Column(db.Float, default=0.5)
    velocita_max                = db.Column(db.Float, default=2.5)
    capacita_termica_max_kg     = db.Column(db.Float, default=400.0)
    costo_orario_esercizio      = db.Column(db.Float, default=150.0)
    costo_manodopera_ora        = db.Column(db.Float, default=28.0)
    costo_centro_orario         = db.Column(db.Float, default=350.0)
    costo_kwh                   = db.Column(db.Float, default=0.22)
    peso_specifico_vernice      = db.Column(db.Float, default=1.2)
    efficienza_applicazione     = db.Column(db.Float, default=0.65)
    spessore_default_micron     = db.Column(db.Integer, default=120)
    tempo_aggancio_min          = db.Column(db.Float, default=2.0)
    tempo_aggancio_medio_min    = db.Column(db.Float, default=4.0)
    tempo_aggancio_complesso_min= db.Column(db.Float, default=7.0)
    netpro_api_url              = db.Column(db.String(200), default='')
    netpro_api_key              = db.Column(db.String(100), default='')
    # v4: passo gancio fisso 400mm
    passo_gancio_mm             = db.Column(db.Float, default=400.0)
    n_ganci_totali              = db.Column(db.Integer, default=425)


class Prodotto(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    codice          = db.Column(db.String(50), unique=True, nullable=False)
    nome            = db.Column(db.String(200), nullable=False)
    descrizione     = db.Column(db.String(200))
    famiglia        = db.Column(db.String(100))
    lunghezza_mm    = db.Column(db.Float, default=0)
    larghezza_mm    = db.Column(db.Float, default=0)
    altezza_mm      = db.Column(db.Float, default=0)
    peso_kg         = db.Column(db.Float, default=0)
    superficie_m2   = db.Column(db.Float, default=0)
    passo_gancio_m  = db.Column(db.Float, default=0.4)
    densita_gcc     = db.Column(db.Float, default=7.85)
    densita_materiale = db.Column(db.Float, default=7.85)
    complessita_aggancio = db.Column(db.Integer, default=1)
    file_3d         = db.Column(db.String(300))
    note            = db.Column(db.Text)
    # Standard aggiornato dalla produzione reale
    costo_standard      = db.Column(db.Float, default=0.0)
    tempo_standard_min  = db.Column(db.Float, default=0.0)
    n_campioni_standard = db.Column(db.Integer, default=0)

    @property
    def dim_x(self): return self.lunghezza_mm or 0
    @property
    def dim_y(self): return self.altezza_mm or 0
    @property
    def dim_z(self): return self.larghezza_mm or 0
    @property
    def superficie_m2_m2(self): return round(self.superficie_m2 or 0, 4)
    @property
    def passo_gancio_mm(self): return (self.passo_gancio_m or 0.4) * 1000


class OrdineOrdine(db.Model):
    __tablename__ = 'ordine_ordine'
    id              = db.Column(db.Integer, primary_key=True)
    numero_ordine   = db.Column(db.String(50))
    codice_prodotto = db.Column(db.String(50))
    quantita        = db.Column(db.Integer, default=1)
    colore          = db.Column(db.String(50), default='VERDE RAL 6005')
    priorita        = db.Column(db.Integer, default=5)
    data_richiesta  = db.Column(db.String(20))
    stato           = db.Column(db.String(20), default='ATTESA')
    origine         = db.Column(db.String(20), default='MANUALE')
    centro_lavoro   = db.Column(db.String(50), default='VERN-01')
    note            = db.Column(db.Text)
    creato_il       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Lotto(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    codice_lotto    = db.Column(db.String(50))
    nome            = db.Column(db.String(100))
    data_produzione = db.Column(db.String(20))
    stato           = db.Column(db.String(20), default='pianificato')
    velocita_catena = db.Column(db.Float, default=1.5)
    operatore       = db.Column(db.String(100))
    note            = db.Column(db.Text)
    saturazione_pct = db.Column(db.Float, default=0)
    peso_totale_kg  = db.Column(db.Float, default=0)
    lunghezza_usata_m = db.Column(db.Float, default=0)
    lunghezza_occupata_m = db.Column(db.Float, default=0)
    tempo_ciclo_min = db.Column(db.Float, default=0)
    tempo_totale_min = db.Column(db.Float, default=0)
    n_pezzi_totali  = db.Column(db.Integer, default=0)
    n_ganci_totali  = db.Column(db.Integer, default=0)
    costo_totale    = db.Column(db.Float, default=0)
    costo_totale_eur = db.Column(db.Float, default=0)
    costo_orario    = db.Column(db.Float, default=150)
    costo_gancio_eur = db.Column(db.Float, default=0)
    sequenza_json   = db.Column(db.Text, default='[]')
    # Timbratura operaio
    inizio          = db.Column(db.DateTime, nullable=True)
    fine            = db.Column(db.DateTime, nullable=True)
    # Termico
    Q_R_kJ = db.Column(db.Float, default=0)
    Q_a_kJ = db.Column(db.Float, default=0)
    Q_f_kJ = db.Column(db.Float, default=0)
    Q_d_kJ = db.Column(db.Float, default=0)
    P_R_kW = db.Column(db.Float, default=0)
    energia_kWh = db.Column(db.Float, default=0)
    costo_energia_eur = db.Column(db.Float, default=0)
    creato_il = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completato_at = db.Column(db.DateTime, nullable=True)
    items = db.relationship('LottoItem', backref='lotto', lazy=True, cascade='all,delete')

    @property
    def durata_min(self):
        if self.inizio and self.fine:
            return round((self.fine - self.inizio).total_seconds() / 60, 1)
        return None


class LottoItem(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    lotto_id        = db.Column(db.Integer, db.ForeignKey('lotto.id'), nullable=False)
    prodotto_id     = db.Column(db.Integer, db.ForeignKey('prodotto.id'))
    quantita        = db.Column(db.Integer, default=1)
    zona_assegnata  = db.Column(db.Integer, default=1)
    posizione_inizio_m = db.Column(db.Float, default=0)
    n_ganci_occupati = db.Column(db.Integer, default=1)
    # Costi ABC unitari
    costo_materiale_unitario  = db.Column(db.Float, default=0)
    costo_processo_unitario   = db.Column(db.Float, default=0)
    costo_manodopera_unitario = db.Column(db.Float, default=0)
    costo_termico_unitario    = db.Column(db.Float, default=0)
    costo_unitario_totale     = db.Column(db.Float, default=0)
    costo_riga = db.Column(db.Float, default=0)
    tempo_unitario_min        = db.Column(db.Float, default=0)
    prodotto = db.relationship('Prodotto', backref='lotto_items', lazy=True)


class StoricoCosto(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    prodotto_id     = db.Column(db.Integer, db.ForeignKey('prodotto.id'))
    lotto_id        = db.Column(db.Integer, db.ForeignKey('lotto.id'))
    data            = db.Column(db.Date, default=date.today)
    quantita        = db.Column(db.Integer, default=1)
    costo_materiale = db.Column(db.Float, default=0)
    costo_processo  = db.Column(db.Float, default=0)
    costo_manodopera = db.Column(db.Float, default=0)
    costo_totale    = db.Column(db.Float, default=0)
    tempo_min       = db.Column(db.Float, default=0)
    velocita_catena = db.Column(db.Float, default=1.5)
    costo_orario    = db.Column(db.Float, default=150)
    n_ganci         = db.Column(db.Integer, default=1)
    costo_termico   = db.Column(db.Float, default=0)
    prodotto_ref    = db.relationship('Prodotto', backref='storico', lazy=True)


class KBRegola(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    prodotto_id     = db.Column(db.Integer, db.ForeignKey('prodotto.id'), nullable=True)
    tipo            = db.Column(db.String(30), default='nota')
    descrizione     = db.Column(db.Text)
    zona_min        = db.Column(db.Integer, default=1)
    zona_max        = db.Column(db.Integer, default=17)
    spessore_override_micron = db.Column(db.Float, nullable=True)
    velocita_delta_pct = db.Column(db.Float, default=0)
    priorita        = db.Column(db.Integer, default=5)
    attiva          = db.Column(db.Boolean, default=True)
    creata_da       = db.Column(db.String(50))
    creata_il       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    prodotto_ref    = db.relationship('Prodotto', backref='kb_regole', lazy=True)


# ── INIT DB ─────────────────────────────────────────────────────

def get_config():
    cfg = Configurazione.query.first()
    if not cfg:
        cfg = Configurazione()
        db.session.add(cfg)
        db.session.commit()
    return cfg


with app.app_context():
    # Schema is managed exclusively by Flask-Migrate (`flask db upgrade`).
    # db.create_all() has been removed to prevent conflicts with migrations.

    # Seed prodotti demo se vuoto (runs only after migrations have created the table)
    try:
        if Prodotto.query.count() == 0:
            _seed_prodotti = [
                ('FALC-TF280','Trincia TF280','Trinciatrici',2850,980,700,185,3.20,0.80,6),
                ('FALC-TF320','Trincia TF320','Trinciatrici',3250,1000,720,220,3.80,0.80,6),
                ('FALC-FN300','Falciatrice FN300','Falciatrici',3050,460,420,98,2.10,0.40,4),
                ('PRES-RB100','Rotopresse RB100','Rotopresse',2000,1800,1600,310,4.50,2.40,6),
                ('PRES-RB120','Rotopresse RB120','Rotopresse',2200,1900,1700,380,5.20,2.40,6),
                ('VEND-VN500','Vendemmiatrice VN500','Vendemmiatrici',4800,1600,2100,580,9.20,2.40,6),
                ('ENOD-780','ENODUO 780','ENODUO',3200,1200,1400,280,4.80,2.00,6),
                ('BTRN-150','Barra Traino 150','Traino',1550,250,160,38,0.48,0.40,2),
                ('CART-LAT-M','Carter Laterale M','Carter',680,520,280,12,0.58,0.40,2),
                ('COPR-VAL-S','Coperchio Valvola S','Coperchi',280,220,60,1.1,0.12,0.40,1),
                ('STAF-UNI-S','Staffa Universale S','Staffaggi',250,200,80,0.8,0.06,0.40,1),
                ('BRACC-TL-M','Braccio Telescopico M','Bracci',980,150,140,5.8,0.32,0.40,2),
                ('ROTA-STD','Ruota Standard','Carrello',220,220,180,8.5,0.25,0.40,1),
                ('PIAS-AGG-M','Piastra Aggancio M','Staffaggi',280,200,25,1.1,0.08,0.40,1),
            ]
            for r in _seed_prodotti:
                p = Prodotto(
                    codice=r[0], nome=r[1], descrizione=r[1], famiglia=r[2],
                    lunghezza_mm=r[3], larghezza_mm=r[4], altezza_mm=r[5],
                    peso_kg=r[6], superficie_m2=r[7], passo_gancio_m=r[8],
                    complessita_aggancio=3 if r[9]>=4 else (2 if r[9]>=2 else 1)
                )
                db.session.add(p)
            db.session.commit()
    except Exception:
        # Table may not exist yet (before first migration); seed will run on next startup.
        db.session.rollback()


# ── ABC COSTING ─────────────────────────────────────────────────

def calcola_abc(prodotto: Prodotto, cfg: Configurazione,
                velocita_override: float = None,
                saturazione_pct: float = 100.0,
                n_pezzi_blocco: int = 1) -> dict:
    """
    Formula fisica corretta per il costo unitario di verniciatura.

    TEMPO IN CATENA (fisso per il pezzo, indipendente dal lotto):
        t_occ_min = passo_gancio_m / (velocita m/min)
        → il pezzo occupa fisicamente quello spazio sulla catena

    COSTO ENERGIA (proporzionale al tempo):
        c_energia = kW_impianto × (t_occ_min/60) × €/kWh

    COSTO VERNICE (proporzionale alla superficie):
        c_vernice = sup_m2 × spessore_m × densita_kg_m3 × €/kg / efficienza

    COSTO MANODOPERA:
        c_mdo = (€/h / 60) × (t_aggancio + t_sgancio)

    COSTO TERMICO (centro orario: manodopera + ammortamento + manutenzione):
        c_termico = (costo_centro_orario / 60) × tempo_ciclo_min / n_pezzi_blocco
        → ripartisce il costo del centro di lavoro (€350/h) sul numero
          effettivo di pezzi nel blocco/lotto, tenendo conto della saturazione.

    EFFETTO SATURAZIONE:
        I costi fissi impianto (ammortamento, riscaldamento a vuoto) si
        ripartiscono su meno pezzi quando la catena è sotto-utilizzata.
        Fattore = 100 / saturazione_pct  (es. 50% sat → costo ×2)
        Applicato solo alla quota costi fissi (energia strutturale),
        NON alla vernice (quella è variabile pura).
    """
    spessore_m    = cfg.spessore_default_micron / 1_000_000
    densita_kg_m3 = cfg.peso_specifico_vernice * 1000
    c_vernice_kg  = 8.50
    sup           = prodotto.superficie_m2 or 0
    passo_m       = prodotto.passo_gancio_m or 0.4
    vel           = velocita_override or cfg.velocita_default or 1.5
    sat           = max(saturazione_pct, 5.0)  # minimo 5% per evitare /0
    n_pezzi       = max(n_pezzi_blocco, 1)     # minimo 1 per evitare /0

    # ── Tempo occupazione catena ──
    t_occ_min = passo_m / vel          # minuti che il pezzo occupa la catena
    t_occ_ore = t_occ_min / 60.0

    # ── Costo energia (kW × ore × €/kWh) ──
    kw_impianto  = cfg.costo_orario_esercizio / max(cfg.costo_kwh or 0.22, 0.01)
    c_energia    = kw_impianto * t_occ_ore * (cfg.costo_kwh or 0.22)

    # ── Costo quota fissa impianto (ammortamento, riscaldamento struttura) ──
    # Questa quota varia con la saturazione: meno pezzi = più costo/pezzo
    costo_fisso_ora = max(cfg.costo_orario_esercizio - c_energia/t_occ_ore, 0) if t_occ_ore > 0 else 0
    c_fisso = costo_fisso_ora * t_occ_ore * (100.0 / sat)

    # ── Costo vernice (puro variabile, non dipende da saturazione) ──
    c_vernice = sup * spessore_m * densita_kg_m3 * c_vernice_kg / cfg.efficienza_applicazione

    # ── Costo manodopera ──
    comp  = prodotto.complessita_aggancio or 1
    t_agg = [cfg.tempo_aggancio_min,
             cfg.tempo_aggancio_medio_min,
             cfg.tempo_aggancio_complesso_min][min(comp-1, 2)]
    c_mdo = (cfg.costo_manodopera_ora / 60.0) * t_agg * 2

    # ── Costo termico centro orario (€/h ÷ 60 × t_ciclo_min ÷ n_pezzi) ──
    # Ripartisce il costo del centro di lavoro (manodopera + ammortamento +
    # manutenzione) sul numero effettivo di pezzi nel blocco/lotto.
    c_termico = (cfg.costo_centro_orario / 60.0) * t_occ_min / n_pezzi

    # ── Totale ──
    c_processo = round(c_energia + c_fisso, 4)
    totale     = c_processo + c_vernice + c_mdo + c_termico

    return {
        'costo_energia':     round(c_energia, 4),
        'costo_fisso':       round(c_fisso, 4),
        'costo_processo':    c_processo,
        'costo_vernice':     round(c_vernice, 4),
        'costo_manodopera':  round(c_mdo, 4),
        'costo_termico':     round(c_termico, 4),
        'costo_totale':      round(totale, 4),
        'costo_m2':          round(totale / sup, 4) if sup > 0 else 0,
        't_occ_min':         round(t_occ_min, 3),
        'saturazione_pct':   sat,
        'vel_usata':         vel,
    }


# ── OTTIMIZZATORE ───────────────────────────────────────────────

IMPIANTO = {
    'n_zone': 17, 'ganci_zona': 25,
    'peso_max_zona_kg': 60,
    'zone_curva': [1, 5, 9, 13, 17],
}

def ottimizza_lotto_db(items_con_prodotti: list, cfg: Configurazione) -> dict:
    """Ottimizza un lotto di LottoItem con prodotti associati."""
    IMP = IMPIANTO
    zone = [{'zona': z+1, 'ganci': 0, 'peso': 0, 'items': [],
              'e_curva': (z+1) in IMP['zone_curva']} for z in range(IMP['n_zone'])]
    vel = cfg.velocita_default
    avvisi_kb = []

    for item in sorted(items_con_prodotti, key=lambda x: -(x.prodotto.peso_kg or 0)):
        p = item.prodotto
        if not p: continue
        g = max(1, round((p.passo_gancio_m or 0.4) / 0.4))

        # Controlla KB
        for regola in KBRegola.query.filter_by(attiva=True, prodotto_id=p.id).all():
            if regola.tipo == 'velocita' and regola.velocita_delta_pct:
                vel = vel * (1 + regola.velocita_delta_pct/100)
                avvisi_kb.append(f'KB: {p.codice} → velocità modificata del {regola.velocita_delta_pct:+.0f}%')
            if regola.tipo == 'zona':
                avvisi_kb.append(f'KB: {p.codice} → zone {regola.zona_min}–{regola.zona_max}')

        # Assegna zona
        best = None; best_g = Infinity = float('inf')
        for z in zone:
            if g >= 3 and z['e_curva']: continue
            peso_lim = max(IMP['peso_max_zona_kg'], (p.peso_kg or 0)*1.1)
            if z['ganci'] + g > IMP['ganci_zona']: continue
            if z['peso'] + (p.peso_kg or 0) > peso_lim: continue
            if z['ganci'] < best_g: best_g = z['ganci']; best = z

        if best:
            best['ganci'] += g
            best['peso']  += p.peso_kg or 0
            best['items'].append(item)
            item.zona_assegnata = best['zona']
            item.n_ganci_occupati = g
        else:
            avvisi_kb.append(f'⚠️ {p.codice}: impossibile allocare')

    ganci_tot = sum(z['ganci'] for z in zone)
    peso_tot  = sum(z['peso']  for z in zone)
    sat_pct   = round(ganci_tot / (IMP['n_zone'] * IMP['ganci_zona']) * 100, 1)
    t_ciclo   = round(cfg.lunghezza_tunnel_m / max(vel, 0.1))

    # Calcola saturazione per zona
    for z in zone:
        z['saturazione_pct'] = round(z['ganci'] / IMP['ganci_zona'] * 100)

    return {
        'zone': zone, 'ganci_tot': ganci_tot, 'peso_tot': peso_tot,
        'sat_pct': sat_pct, 't_ciclo': t_ciclo, 'vel': vel,
        'avvisi_kb': avvisi_kb,
    }


# ══════════════════════════════════════════════════════════════════
# ROUTE: DASHBOARD
# ══════════════════════════════════════════════════════════════════

@app.route('/')
def dashboard():
    oggi = date.today().isoformat()
    n_prodotti = Prodotto.query.count()
    ordini_attesa = OrdineOrdine.query.filter_by(stato='ATTESA').all()
    lotti_recenti = ordini_attesa[:8]
    lotti = Lotto.query.order_by(Lotto.id.desc()).limit(6).all()
    kpi = {
        'n_prodotti': n_prodotti,
        'n_lotti': len(ordini_attesa),
        'pezzi_attesa': sum(o.quantita for o in ordini_attesa),
    }
    return render_template('dashboard.html', oggi=oggi, kpi=kpi,
                           lotti_recenti=lotti_recenti, lotti=lotti)


# ══════════════════════════════════════════════════════════════════
# ROUTE: OTTIMIZZATORE
# ══════════════════════════════════════════════════════════════════

@app.route('/ottimizzatore', methods=['GET','POST'])
def ottimizzatore():
    cfg = get_config()
    prodotti = Prodotto.query.order_by(Prodotto.famiglia, Prodotto.codice).all()
    result = None

    if request.method == 'POST':
        codici  = request.form.getlist('codice[]')
        qtys    = request.form.getlist('quantita[]')
        vel_req = float(request.form.get('velocita', cfg.velocita_default))

        # Crea lotto temporaneo per visualizzazione
        items_tmp = []
        for cod, qty in zip(codici, qtys):
            p = Prodotto.query.filter_by(codice=cod).first()
            if not p: continue
            item = LottoItem(prodotto_id=p.id, quantita=int(qty or 1))
            item.prodotto = p
            items_tmp.append(item)

        if items_tmp:
            cfg_tmp = get_config()
            cfg_tmp.velocita_default = vel_req
            ris = ottimizza_lotto_db(items_tmp, cfg_tmp)

            # Calcola costi ABC
            costo_tot = 0
            costo_tot_materiale = 0
            costo_tot_processo = 0
            costo_tot_manodopera = 0
            costo_tot_termico = 0
            n_pezzi_blocco = sum(i.quantita for i in items_tmp)
            for item in items_tmp:
                abc = calcola_abc(item.prodotto, cfg,
                                  n_pezzi_blocco=n_pezzi_blocco)
                item.costo_materiale_unitario  = abc['costo_vernice']
                item.costo_processo_unitario   = abc['costo_processo']
                item.costo_manodopera_unitario = abc['costo_manodopera']
                item.costo_termico_unitario    = abc['costo_termico']
                item.costo_unitario_totale     = abc['costo_totale']
                item.costo_riga = abc['costo_totale'] * item.quantita
                costo_tot += item.costo_riga
                costo_tot_materiale  += abc['costo_vernice']    * item.quantita
                costo_tot_processo   += abc['costo_processo']   * item.quantita
                costo_tot_manodopera += abc['costo_manodopera'] * item.quantita
                costo_tot_termico    += abc['costo_termico']    * item.quantita

            # ── SALVA LOTTO NEL DB ──
            operatore = request.form.get('operatore','')
            lotto_db = Lotto(
                codice_lotto    = f"L{datetime.now().strftime('%Y%m%d%H%M%S')}",
                nome            = f"Lotto {datetime.now().strftime('%d/%m %H:%M')}",
                data_produzione = date.today().isoformat(),
                stato           = 'pianificato',
                velocita_catena = vel_req,
                operatore       = operatore,
                costo_orario    = cfg.costo_orario_esercizio,
                n_pezzi_totali  = sum(i.quantita for i in items_tmp),
                saturazione_pct = ris['sat_pct'],
                peso_totale_kg  = round(ris['peso_tot'], 1),
                tempo_ciclo_min = ris['t_ciclo'],
                costo_totale    = round(costo_tot, 2),
                costo_totale_eur= round(costo_tot, 2),
            )
            db.session.add(lotto_db)
            try:
                db.session.flush()  # ottieni lotto_db.id
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Flush lotto fallito: {e}")
                raise

            for item in items_tmp:
                li = LottoItem(
                    lotto_id              = lotto_db.id,
                    prodotto_id           = item.prodotto_id,
                    quantita              = item.quantita,
                    zona_assegnata        = item.zona_assegnata,
                    n_ganci_occupati      = item.n_ganci_occupati,
                    costo_materiale_unitario  = item.costo_materiale_unitario,
                    costo_processo_unitario   = item.costo_processo_unitario,
                    costo_manodopera_unitario = item.costo_manodopera_unitario,
                    costo_termico_unitario    = item.costo_termico_unitario,
                    costo_unitario_totale     = item.costo_unitario_totale,
                    costo_riga                = item.costo_riga,
                )
                db.session.add(li)
            try:
                db.session.commit()
                app.logger.info(f"Lotto {lotto_db.id} salvato OK")
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Commit lotto fallito: {e}")
                raise

            result = {
                'lotto_id': lotto_db.id,
                'zone': ris['zone'],
                'n_pezzi_totali': sum(i.quantita for i in items_tmp),
                'saturazione_pct': ris['sat_pct'],
                'peso_totale_kg': round(ris['peso_tot'], 1),
                'tempo_totale_min': ris['t_ciclo'],
                'costo_totale': round(costo_tot, 2),
                'costo_totale_materiale': round(costo_tot_materiale, 2),
                'costo_totale_processo': round(costo_tot_processo, 2),
                'costo_totale_manodopera': round(costo_tot_manodopera, 2),
                'costo_totale_termico': round(costo_tot_termico, 2),
                'velocita_usata': round(ris['vel'], 2),
                'avvisi_kb': ris['avvisi_kb'],
                'items': items_tmp,
            }

    # Ri-popola form con i dati inviati (mantiene selezione dopo POST)
    selezione = list(zip(
        request.form.getlist('codice[]'),
        request.form.getlist('quantita[]')
    )) if request.method == 'POST' else []

    return render_template('ottimizzatore.html', config=cfg, prodotti=prodotti,
                           result=result, selezione=selezione)


# ══════════════════════════════════════════════════════════════════
# ROUTE: OPERAIO
# ══════════════════════════════════════════════════════════════════

@app.route('/operaio')
def operaio():
    oggi = date.today().isoformat()
    lotto_attivo = Lotto.query.filter_by(stato='in_corso').first()
    lotti = Lotto.query.filter(
        Lotto.stato.in_(['confermato','pianificato']),
        Lotto.data_produzione == oggi
    ).order_by(Lotto.id.desc()).all()
    return render_template('operaio.html', lotto_attivo=lotto_attivo,
                           lotti=lotti, today=oggi)


@app.route('/operaio/<int:lotto_id>')
def operaio_lotto(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    items = LottoItem.query.filter_by(lotto_id=lotto_id).all()
    # Raggruppa per zona
    zone_items = defaultdict(list)
    for item in items:
        zone_items[item.zona_assegnata].append(item)
    return render_template('operaio_lotto.html', lotto=lotto,
                           items=items, zone_items=zone_items)


@app.route('/operaio/<int:lotto_id>/avvia', methods=['POST'])
def operaio_avvia(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    lotto.stato  = 'in_corso'
    lotto.inizio = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for('operaio_lotto', lotto_id=lotto_id))


@app.route('/operaio/<int:lotto_id>/completa_operaio', methods=['POST'])
def operaio_completa(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    lotto.stato = 'completato'
    lotto.fine  = datetime.now(timezone.utc)
    cfg = get_config()

    # Durata reale START→STOP
    durata_min = lotto.durata_min or lotto.tempo_ciclo_min or 0

    # Superficie totale per ripartire i costi
    items = LottoItem.query.filter_by(lotto_id=lotto_id).all()
    sup_tot = sum(
        (it.prodotto.superficie_m2 or 0) * it.quantita
        for it in items if it.prodotto
    )

    costo_tot = 0.0
    for it in items:
        p = it.prodotto
        if not p:
            continue

        # Ricalcola costi ABC con durata reale e saturazione reale
        sat = lotto.saturazione_pct or 100.0
        abc = calcola_abc(p, cfg,
                          velocita_override=lotto.velocita_catena,
                          saturazione_pct=sat)

        it.costo_materiale_unitario  = abc['costo_vernice']
        it.costo_processo_unitario   = abc['costo_processo']
        it.costo_manodopera_unitario = abc['costo_manodopera']
        it.costo_termico_unitario    = abc['costo_termico']
        it.costo_unitario_totale     = abc['costo_totale']
        it.costo_riga                = round(abc['costo_totale'] * it.quantita, 2)
        it.tempo_unitario_min        = abc['t_occ_min']
        costo_tot += it.costo_riga

        # Aggiorna standard prodotto (media mobile)
        n = p.n_campioni_standard or 0
        p.costo_standard     = round((p.costo_standard * n + abc['costo_totale']) / (n + 1), 4)
        p.tempo_standard_min = round(
            ((p.tempo_standard_min or 0) * n + abc['t_occ_min']) / (n + 1), 3)
        p.n_campioni_standard = n + 1

        # Salva StoricoCosto
        db.session.add(StoricoCosto(
            prodotto_id      = p.id,
            lotto_id         = lotto.id,
            data             = date.today(),
            quantita         = it.quantita,
            costo_materiale  = abc['costo_vernice'],
            costo_processo   = abc['costo_processo'],
            costo_manodopera = abc['costo_manodopera'],
            costo_termico    = abc['costo_termico'],
            costo_totale     = abc['costo_totale'],
            tempo_min        = abc['t_occ_min'],
            velocita_catena  = lotto.velocita_catena or cfg.velocita_default,
            costo_orario     = cfg.costo_orario_esercizio,
            n_ganci          = it.n_ganci_occupati or 1,
        ))

    lotto.costo_totale     = round(costo_tot, 2)
    lotto.costo_totale_eur = round(costo_tot, 2)
    db.session.commit()

    flash(
        f'✅ Completato! Durata: {int(durata_min)} min · '
        f'Costo totale: €{costo_tot:.2f} · '
        f'Storico aggiornato per {len(items)} codici',
        'success'
    )
    return redirect(url_for('operaio'))


# ══════════════════════════════════════════════════════════════════
# ROUTE: LOTTI
# ══════════════════════════════════════════════════════════════════

@app.route('/lotti')
def lotti():
    tutti = Lotto.query.order_by(Lotto.id.desc()).all()
    return render_template('lotti.html', lotti=tutti)


@app.route('/lotto/<int:lotto_id>')
def lotto_detail(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    items = LottoItem.query.filter_by(lotto_id=lotto_id).all()
    return render_template('lotto_detail.html', lotto=lotto, items=items)


@app.route('/lotto/<int:lotto_id>/conferma', methods=['POST'])
def lotto_conferma(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    lotto.stato = 'confermato'
    db.session.commit()
    flash('Lotto confermato.', 'success')
    return redirect(url_for('lotto_detail', lotto_id=lotto_id))


@app.route('/lotto/<int:lotto_id>/completa', methods=['POST'])
def lotto_completa(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    cfg   = get_config()
    lotto.stato = 'completato'
    lotto.completato_at = datetime.now(timezone.utc)
    if not lotto.fine:
        lotto.fine = datetime.now(timezone.utc)

    # ── Energia termica inserita manualmente al completamento ──
    energia_kwh_raw = request.form.get('energia_kWh', '').strip()
    costo_energia_unitario = 0.0
    if energia_kwh_raw:
        try:
            energia_kwh = float(energia_kwh_raw)
            if energia_kwh > 0:
                lotto.energia_kWh = energia_kwh
                lotto.costo_energia_eur = round(energia_kwh * cfg.costo_kwh, 4)
                n_pezzi = max(lotto.n_pezzi_totali, 1)
                costo_energia_unitario = round(
                    (energia_kwh * cfg.costo_kwh) / n_pezzi, 4
                )
        except ValueError:
            pass

    # Aggiorna standard prodotti e salva storico
    for item in lotto.items:
        # Aggiunge la quota energia termica al costo termico unitario già
        # calcolato al momento della pianificazione (centro orario + vernice).
        if costo_energia_unitario > 0:
            item.costo_termico_unitario = round(
                (item.costo_termico_unitario or 0.0) + costo_energia_unitario, 4
            )
            item.costo_unitario_totale = round(
                (item.costo_unitario_totale or 0.0) + costo_energia_unitario, 4
            )
            item.costo_riga = round(item.costo_unitario_totale * item.quantita, 4)

        if item.prodotto and item.costo_unitario_totale > 0:
            p = item.prodotto
            n = p.n_campioni_standard
            p.costo_standard = (p.costo_standard * n + item.costo_unitario_totale) / (n+1)
            p.n_campioni_standard = n + 1
        # Salva storico
        if item.prodotto:
            sc = StoricoCosto(
                prodotto_id=item.prodotto_id,
                lotto_id=lotto.id,
                data=date.today(),
                quantita=item.quantita,
                costo_materiale=item.costo_materiale_unitario,
                costo_processo=item.costo_processo_unitario,
                costo_manodopera=item.costo_manodopera_unitario,
                costo_termico=item.costo_termico_unitario,
                costo_totale=item.costo_unitario_totale,
                tempo_min=item.tempo_unitario_min,
                velocita_catena=lotto.velocita_catena,
                costo_orario=lotto.costo_orario,
            )
            db.session.add(sc)

    # Aggiorna il costo totale del lotto
    lotto.costo_totale = round(
        sum(item.costo_riga for item in lotto.items), 2
    )
    lotto.costo_totale_eur = lotto.costo_totale

    db.session.commit()
    flash('Lotto completato e storico aggiornato.', 'success')
    return redirect(url_for('lotto_detail', lotto_id=lotto_id))


@app.route('/lotto/<int:lotto_id>/delete', methods=['POST'])
def lotto_delete(lotto_id):
    lotto = Lotto.query.get_or_404(lotto_id)
    db.session.delete(lotto)
    db.session.commit()
    flash('Lotto eliminato.', 'warning')
    return redirect(url_for('lotti'))


# ══════════════════════════════════════════════════════════════════
# ROUTE: ORDINI
# ══════════════════════════════════════════════════════════════════

COLORI_STD = ['VERDE RAL 6005','VERDE RAL 6018','GIALLO RAL 1021',
               'GRIGIO RAL 7035','NERO RAL 9005','BIANCO RAL 9010']

@app.route('/ordini')
def ordini():
    stato_sel = request.args.get('stato','')
    q = OrdineOrdine.query
    if stato_sel:
        q = q.filter_by(stato=stato_sel)
    tutti = q.order_by(OrdineOrdine.priorita, OrdineOrdine.data_richiesta).all()
    stati = ['','ATTESA','PIANIFICATO','IN CORSO','EVASO']
    return render_template('ordini.html', ordini=tutti, stati=stati,
                           stato_sel=stato_sel, colori=COLORI_STD,
                           oggi=date.today().isoformat())


@app.route('/ordine/nuovo', methods=['POST'])
def ordine_nuovo():
    o = OrdineOrdine(
        numero_ordine   = request.form.get('numero_ordine',''),
        codice_prodotto = request.form.get('codice_prodotto','').upper(),
        quantita        = int(request.form.get('quantita',1)),
        colore          = request.form.get('colore', COLORI_STD[0]),
        priorita        = int(request.form.get('priorita',5)),
        data_richiesta  = request.form.get('data_richiesta', date.today().isoformat()),
        centro_lavoro   = request.form.get('centro_lavoro','VERN-01'),
        note            = request.form.get('note',''),
        origine         = 'MANUALE',
    )
    db.session.add(o)
    db.session.commit()
    flash(f'Ordine {o.numero_ordine} creato.', 'success')
    return redirect(url_for('ordini'))


@app.route('/ordine/<int:ordine_id>/stato', methods=['POST'])
def ordine_stato(ordine_id):
    o = OrdineOrdine.query.get_or_404(ordine_id)
    o.stato = request.form.get('stato', o.stato)
    db.session.commit()
    return redirect(url_for('ordini'))


@app.route('/ordine/<int:ordine_id>/delete', methods=['POST'])
def ordine_delete(ordine_id):
    o = OrdineOrdine.query.get_or_404(ordine_id)
    db.session.delete(o)
    db.session.commit()
    return redirect(url_for('ordini'))


# ══════════════════════════════════════════════════════════════════
# ROUTE: PRODOTTI
# ══════════════════════════════════════════════════════════════════

@app.route('/prodotti')
def prodotti():
    fam_sel = request.args.get('famiglia','')
    q = Prodotto.query
    if fam_sel:
        q = q.filter_by(famiglia=fam_sel)
    lista = q.order_by(Prodotto.famiglia, Prodotto.codice).all()
    famiglie = [r[0] for r in db.session.query(Prodotto.famiglia).distinct().all() if r[0]]
    return render_template('prodotti.html', prodotti=lista,
                           famiglie=famiglie, fam_sel=fam_sel)


@app.route('/prodotto/nuovo', methods=['GET','POST'])
@app.route('/prodotto/<int:pid>/edit', methods=['GET','POST'])
def prodotto_form(pid=None):
    prodotto = Prodotto.query.get(pid) if pid else None
    famiglie = [r[0] for r in db.session.query(Prodotto.famiglia).distinct().all() if r[0]]

    if request.method == 'POST':
        if not prodotto:
            prodotto = Prodotto()
            db.session.add(prodotto)

        prodotto.codice     = request.form.get('codice','').upper()
        prodotto.nome       = request.form.get('nome','')
        prodotto.descrizione = request.form.get('nome','')
        prodotto.famiglia   = request.form.get('famiglia','')
        prodotto.lunghezza_mm = float(request.form.get('lunghezza_mm',0) or 0)
        prodotto.larghezza_mm = float(request.form.get('larghezza_mm',0) or 0)
        prodotto.altezza_mm   = float(request.form.get('altezza_mm',0) or 0)
        prodotto.peso_kg      = float(request.form.get('peso_kg',0) or 0)
        prodotto.densita_gcc  = float(request.form.get('densita_gcc',7.85) or 7.85)
        prodotto.densita_materiale = prodotto.densita_gcc
        sup_raw = float(request.form.get('superficie_m2',0) or 0)
        if sup_raw <= 0 and prodotto.lunghezza_mm > 0:
            # Stima automatica da bounding box
            l,w,h = prodotto.lunghezza_mm, prodotto.larghezza_mm or 500, prodotto.altezza_mm or 500
            s_bb = 2*(l*w + l*h + w*h) / 1e6
            sup_raw = round(s_bb * 2.4 / 2, 4)
        prodotto.superficie_m2  = sup_raw
        passo_raw = float(request.form.get('passo_gancio_m', 0.4) or 0.4)
        if passo_raw <= 0:
            dim_max = max(prodotto.lunghezza_mm, prodotto.larghezza_mm or 0) / 1000
            passo_raw = math.ceil(dim_max * 1.15 * 20) / 20
        prodotto.passo_gancio_m = passo_raw
        prodotto.note = request.form.get('note','')
        g = round(passo_raw / 0.4)
        prodotto.complessita_aggancio = 3 if g >= 4 else (2 if g >= 2 else 1)
        db.session.commit()
        flash(f'Prodotto {prodotto.codice} salvato.', 'success')
        return redirect(url_for('prodotti'))

    return render_template('prodotto_form.html', prodotto=prodotto, famiglie=famiglie)


# ══════════════════════════════════════════════════════════════════
# ROUTE: STORICO COSTI
# ══════════════════════════════════════════════════════════════════

@app.route('/storico')
def storico():
    cfg = get_config()
    oggi = date.today()

    # Filtri
    giorni = request.args.get('giorni', type=int)
    mese   = request.args.get('mese', type=int)
    fam    = request.args.get('famiglia','')
    if giorni:
        d_inizio = oggi - timedelta(days=giorni)
        d_fine   = oggi
    elif mese:
        d_inizio = date(oggi.year, oggi.month, 1)
        d_fine   = oggi
    else:
        d_inizio = date.fromisoformat(request.args.get('inizio', (oggi - timedelta(days=30)).isoformat()))
        d_fine   = date.fromisoformat(request.args.get('fine',   oggi.isoformat()))

    filtro = {'inizio': d_inizio.isoformat(), 'fine': d_fine.isoformat(),
              'famiglia': fam, 'giorni': giorni}

    # KPI giornalieri
    lotti_oggi = Lotto.query.filter(
        Lotto.data_produzione == oggi.isoformat(),
        Lotto.stato.in_(['completato','in_corso','confermato'])
    ).all()
    kpi = {
        'n_lotti': len(lotti_oggi),
        'n_pezzi': sum(l.n_pezzi_totali for l in lotti_oggi),
        'saturazione_media': round(sum(l.saturazione_pct or 0 for l in lotti_oggi) / max(len(lotti_oggi),1), 1),
        'costo_totale_giornata': round(sum(l.costo_totale or 0 for l in lotti_oggi), 2),
    }

    # Report per codice
    from sqlalchemy import func, extract
    q = db.session.query(
        StoricoCosto.prodotto_id,
        func.count(StoricoCosto.id).label('n_passaggi'),
        func.sum(StoricoCosto.quantita).label('n_pezzi'),
        func.avg(StoricoCosto.costo_totale).label('costo_medio'),
        func.avg(StoricoCosto.costo_materiale).label('c_mat'),
        func.avg(StoricoCosto.costo_processo).label('c_proc'),
        func.avg(StoricoCosto.costo_manodopera).label('c_man'),
        func.avg(StoricoCosto.tempo_min).label('t_medio'),
    ).filter(
        StoricoCosto.data >= d_inizio,
        StoricoCosto.data <= d_fine,
    ).group_by(StoricoCosto.prodotto_id).order_by(func.count(StoricoCosto.id).desc())

    report = []
    for row in q.all():
        p = Prodotto.query.get(row.prodotto_id)
        if not p: continue
        if fam and p.famiglia != fam: continue
        scost = None
        if p.costo_standard > 0:
            scost = round(((float(row.costo_medio or 0) - p.costo_standard) / p.costo_standard) * 100, 1)
        report.append({
            'codice': p.codice, 'descrizione': p.nome, 'famiglia': p.famiglia or '',
            'n_passaggi': row.n_passaggi, 'n_pezzi': int(row.n_pezzi or 0),
            'costo_medio': round(float(row.costo_medio or 0), 4),
            'c_materiale': round(float(row.c_mat or 0), 4),
            'c_processo':  round(float(row.c_proc or 0), 4),
            'c_manodopera':round(float(row.c_man or 0), 4),
            'tempo_medio_min': round(float(row.t_medio or 0), 3),
            'standard_attuale': round(p.costo_standard, 4),
            'scostamento_pct': scost,
        })

    # Trend top 5 per grafici sparkline
    trend_top5 = {}
    for r in report[:5]:
        serie = StoricoCosto.query.join(Prodotto).filter(
            Prodotto.codice == r['codice'],
            StoricoCosto.data >= d_inizio, StoricoCosto.data <= d_fine
        ).order_by(StoricoCosto.data).all()
        by_day = defaultdict(list)
        for s in serie: by_day[s.data.isoformat()].append(s.costo_totale)
        trend_top5[r['codice']] = [{'data': d, 'costo_medio': round(sum(v)/len(v),4)} for d,v in sorted(by_day.items())]

    famiglie = [r[0] for r in db.session.query(Prodotto.famiglia).distinct().all() if r[0]]

    # Lista prodotti per autocomplete ricerca
    tutti_prodotti = Prodotto.query.order_by(Prodotto.codice).all()
    prodotti_json = json.dumps([{
        'codice': p.codice,
        'nome': p.nome,
        'famiglia': p.famiglia or '',
        'costo_std': round(p.costo_standard, 4),
    } for p in tutti_prodotti])

    return render_template('storico.html', report=report, kpi=kpi, filtro=filtro,
                           famiglie=famiglie, trend_top5=json.dumps(trend_top5),
                           prodotti_json=prodotti_json)


@app.route('/storico/export')
def storico_export():
    inizio = request.args.get('inizio', (date.today()-timedelta(days=30)).isoformat())
    fine   = request.args.get('fine',   date.today().isoformat())
    records = StoricoCosto.query.filter(
        StoricoCosto.data >= date.fromisoformat(inizio),
        StoricoCosto.data <= date.fromisoformat(fine)
    ).order_by(StoricoCosto.data.desc()).all()
    output = io.StringIO()
    w = csv.writer(output, delimiter=';')
    w.writerow(['Data','Codice','Descrizione','Famiglia','Qty','C.Materiale','C.Processo','C.Manodopera','C.Totale','Tempo min'])
    for r in records:
        p = r.prodotto_ref
        w.writerow([r.data, p.codice if p else '','',p.famiglia if p else '',
                    r.quantita, r.costo_materiale, r.costo_processo,
                    r.costo_manodopera, r.costo_totale, r.tempo_min])
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename=storico_{inizio}_{fine}.csv'})


@app.route('/storico/<codice>')
def storico_codice(codice):
    prodotto = Prodotto.query.filter_by(codice=codice).first_or_404()
    cfg      = get_config()
    ultimi   = (StoricoCosto.query
                .filter_by(prodotto_id=prodotto.id)
                .order_by(StoricoCosto.data.desc())
                .limit(30).all())

    # Ricalcola costi ai prezzi ATTUALI per ogni passaggio storico
    # I dati fisici (velocita, saturazione) sono quelli reali del lotto
    righe_ricalcolate = []
    for r in ultimi:
        lotto_ref = Lotto.query.get(r.lotto_id) if r.lotto_id else None
        vel_lotto = (lotto_ref.velocita_catena if lotto_ref else None)
        sat_lotto = (lotto_ref.saturazione_pct if lotto_ref else 100.0)

        abc_oggi = calcola_abc(prodotto, cfg,
                               velocita_override=vel_lotto,
                               saturazione_pct=sat_lotto or 100.0)
        righe_ricalcolate.append({
            'data':             r.data,
            'lotto_id':         r.lotto_id,
            'quantita':         r.quantita,
            'vel_catena':       vel_lotto or cfg.velocita_default,
            'saturazione_pct':  sat_lotto or 100.0,
            # costi originali (come erano al momento della lavorazione)
            'c_orig_totale':    r.costo_totale,
            # costi ricalcolati ai prezzi odierni
            'c_energia':        abc_oggi['costo_energia'],
            'c_fisso':          abc_oggi['costo_fisso'],
            'c_vernice':        abc_oggi['costo_vernice'],
            'c_manodopera':     abc_oggi['costo_manodopera'],
            'c_oggi_totale':    abc_oggi['costo_totale'],
            't_occ_min':        abc_oggi['t_occ_min'],
            'delta_pct': round(
                (abc_oggi['costo_totale'] - r.costo_totale) / max(r.costo_totale, 0.0001) * 100, 1
            ) if r.costo_totale else 0,
        })

    # Costo attuale standard (100% saturazione, velocità default)
    abc_std = calcola_abc(prodotto, cfg)

    # Serie temporale per grafico — costo ricalcolato oggi per ogni data
    by_day = defaultdict(list)
    for rr in righe_ricalcolate:
        by_day[rr['data'].isoformat()].append(rr['c_oggi_totale'])
    serie = [{'data': d, 'costo_medio': round(sum(v)/len(v), 4)}
             for d, v in sorted(by_day.items())]

    # Serie saturazione per grafico secondario
    by_day_sat = defaultdict(list)
    for rr in righe_ricalcolate:
        by_day_sat[rr['data'].isoformat()].append(rr['saturazione_pct'])
    serie_sat = [{'data': d, 'sat': round(sum(v)/len(v), 1)}
                 for d, v in sorted(by_day_sat.items())]

    return render_template('storico_codice.html',
                           prodotto=prodotto,
                           cfg=cfg,
                           abc_std=abc_std,
                           righe=righe_ricalcolate,
                           serie=json.dumps(serie),
                           serie_sat=json.dumps(serie_sat))


# ══════════════════════════════════════════════════════════════════
# ROUTE: ABC
# ══════════════════════════════════════════════════════════════════

@app.route('/abc')
def abc():
    cfg = get_config()
    fam_sel = request.args.get('famiglia','')
    q = Prodotto.query
    if fam_sel: q = q.filter_by(famiglia=fam_sel)
    prodotti_list = q.order_by(Prodotto.famiglia, Prodotto.codice).all()

    lista_abc = []
    for p in prodotti_list:
        c = calcola_abc(p, cfg)
        lista_abc.append({**c, 'codice': p.codice, 'nome': p.nome,
                          'famiglia': p.famiglia or '', 'superficie_m2': p.superficie_m2,
                          'peso_kg': p.peso_kg, 'passo_gancio_m': p.passo_gancio_m})

    lista_abc.sort(key=lambda x: -x['costo_totale'])
    top = lista_abc[:15]
    chart_data = json.dumps({
        'labels':     [x['codice'] for x in top],
        'processo':   [x['costo_processo']   for x in top],
        'vernice':    [x['costo_vernice']     for x in top],
        'manodopera': [x['costo_manodopera']  for x in top],
    })
    famiglie = [r[0] for r in db.session.query(Prodotto.famiglia).distinct().all() if r[0]]
    return render_template('abc.html', lista_abc=lista_abc, chart_data=chart_data,
                           famiglie=famiglie, fam_sel=fam_sel)


# ══════════════════════════════════════════════════════════════════
# ROUTE: TEMPI
# ══════════════════════════════════════════════════════════════════

@app.route('/tempi')
def tempi():
    cfg = get_config()
    fam_sel = request.args.get('famiglia','')
    q = Prodotto.query
    if fam_sel: q = q.filter_by(famiglia=fam_sel)
    prodotti_list = q.order_by(Prodotto.famiglia, Prodotto.codice).all()
    vel = cfg.velocita_default

    lista = []
    for p in prodotti_list:
        passo_m = p.passo_gancio_m or 0.4
        t_ciclo = round(cfg.lunghezza_tunnel_m / vel, 1)
        t_occ   = round(passo_m / (vel/60) / 60, 3)
        comp    = p.complessita_aggancio or 1
        t_agg   = [cfg.tempo_aggancio_min, cfg.tempo_aggancio_medio_min, cfg.tempo_aggancio_complesso_min][min(comp-1,2)]
        t_tot   = round(t_ciclo + t_agg*2, 2)
        pezzi_h = round(60 / t_tot, 2) if t_tot > 0 else 0
        c_linea = round((cfg.costo_orario_esercizio / 60) * t_occ, 4)
        lista.append({
            'codice': p.codice, 'nome': p.nome, 'famiglia': p.famiglia or '',
            'tempo_ciclo_tunnel_min': t_ciclo, 'tempo_occupazione_min': t_occ,
            'tempo_aggancio_min': round(t_agg,2), 'tempo_sgancio_min': round(t_agg,2),
            'tempo_totale_min': t_tot, 'pezzi_ora': pezzi_h,
            'costo_linea_per_pezzo': c_linea,
        })

    famiglie = [r[0] for r in db.session.query(Prodotto.famiglia).distinct().all() if r[0]]
    config_ctx = {
        'lunghezza_tunnel_m': cfg.lunghezza_tunnel_m,
        'velocita_catena_mmin': vel,
        'costo_orario_eur': cfg.costo_orario_esercizio,
        'tempo_aggancio_sec': round(cfg.tempo_aggancio_min*60),
        'tempo_sgancio_sec': round(cfg.tempo_aggancio_min*60),
        'operatori_linea': 2,
    }
    return render_template('tempi.html', lista=lista, config=config_ctx,
                           famiglie=famiglie, fam_sel=fam_sel)


# ══════════════════════════════════════════════════════════════════
# ROUTE: KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════

@app.route('/knowledge_base')
def knowledge_base():
    regole   = KBRegola.query.order_by(KBRegola.priorita, KBRegola.id.desc()).all()
    prodotti = Prodotto.query.order_by(Prodotto.codice).all()
    return render_template('knowledge_base.html', regole=regole, prodotti=prodotti)


@app.route('/kb/nuova', methods=['POST'])
def kb_nuova_regola():
    pid = request.form.get('prodotto_id') or None
    r = KBRegola(
        prodotto_id  = int(pid) if pid else None,
        tipo         = request.form.get('tipo','nota'),
        descrizione  = request.form.get('descrizione',''),
        zona_min     = int(request.form.get('zona_min', 1)),
        zona_max     = int(request.form.get('zona_max', 17)),
        spessore_override_micron = float(request.form.get('spessore_override') or 0) or None,
        velocita_delta_pct = float(request.form.get('velocita_delta', 0)),
        priorita     = int(request.form.get('priorita', 5)),
        creata_da    = request.form.get('operatore',''),
        attiva       = True,
    )
    db.session.add(r)
    db.session.commit()
    flash('Regola KB aggiunta.', 'success')
    return redirect(url_for('knowledge_base'))


@app.route('/kb/<int:rid>/toggle', methods=['POST'])
def kb_toggle(rid):
    r = KBRegola.query.get_or_404(rid)
    r.attiva = not r.attiva
    db.session.commit()
    return redirect(url_for('knowledge_base'))


@app.route('/kb/<int:rid>/delete', methods=['POST'])
def kb_delete(rid):
    r = KBRegola.query.get_or_404(rid)
    db.session.delete(r)
    db.session.commit()
    return redirect(url_for('knowledge_base'))


# ══════════════════════════════════════════════════════════════════
# ROUTE: IMPORT SAP
# ══════════════════════════════════════════════════════════════════

@app.route('/import_sap', methods=['GET','POST'])
def import_sap():
    template_csv = 'AUFNR;MATNR;MAKTX;GAMNG;GSTRP;PRIOK;FARBE;ARBPL\n100001;FALC-TF280;Trincia TF280;5;15.05.2026;3;VERDE RAL 6005;VERN-01\n'

    if request.method == 'POST':
        f = request.files.get('file_csv')
        if f:
            content = f.read().decode('utf-8', errors='replace')
            # Parse CSV semplice
            reader = csv.DictReader(io.StringIO(content), delimiter=';')
            imported = 0
            for row in reader:
                cod = (row.get('MATNR') or row.get('Materiale') or '').strip()
                if not cod: continue
                o = OrdineOrdine(
                    numero_ordine   = (row.get('AUFNR') or row.get('Ordine') or f'SAP{imported}').strip(),
                    codice_prodotto = cod.upper(),
                    quantita        = int(float((row.get('GAMNG') or row.get('Quantità') or 1))) or 1,
                    colore          = (row.get('FARBE') or row.get('Colore') or COLORI_STD[0]).strip(),
                    priorita        = int(float((row.get('PRIOK') or row.get('Priorità') or 5))) or 5,
                    data_richiesta  = (row.get('GSTRP') or row.get('Data Inizio') or date.today().isoformat()),
                    origine         = 'SAP',
                    centro_lavoro   = (row.get('ARBPL') or 'VERN-01').strip(),
                    stato           = 'ATTESA',
                )
                db.session.add(o)
                imported += 1
            db.session.commit()
            flash(f'{imported} ordini importati da SAP.', 'success')
            return redirect(url_for('ordini'))

    return render_template('import_sap.html', template_csv=template_csv)


# ══════════════════════════════════════════════════════════════════
# ROUTE: CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════

@app.route('/configurazione', methods=['GET','POST'])
def configurazione():
    cfg = get_config()
    if request.method == 'POST':
        cfg.lunghezza_tunnel_m       = float(request.form.get('lunghezza', 170))
        cfg.velocita_default         = float(request.form.get('velocita_default', 1.5))
        cfg.velocita_min             = float(request.form.get('velocita_min', 0.5))
        cfg.velocita_max             = float(request.form.get('velocita_max', 2.5))
        cfg.capacita_termica_max_kg  = float(request.form.get('capacita_termica', 400))
        cfg.costo_orario_esercizio   = float(request.form.get('costo_orario', 150))
        cfg.costo_manodopera_ora     = float(request.form.get('costo_manodopera', 28))
        cfg.costo_centro_orario      = float(request.form.get('costo_centro_orario', 350))
        cfg.costo_kwh                = float(request.form.get('costo_kwh', 0.22))
        cfg.peso_specifico_vernice   = float(request.form.get('peso_vernice', 1.2))
        cfg.efficienza_applicazione  = float(request.form.get('efficienza', 0.65))
        cfg.spessore_default_micron  = int(float(request.form.get('spessore', 120)))
        cfg.tempo_aggancio_min       = float(request.form.get('t_agg_1', 2))
        cfg.tempo_aggancio_medio_min = float(request.form.get('t_agg_2', 4))
        cfg.tempo_aggancio_complesso_min = float(request.form.get('t_agg_3', 7))
        cfg.netpro_api_url           = request.form.get('netpro_url','')
        cfg.netpro_api_key           = request.form.get('netpro_key','')
        db.session.commit()
        flash('Configurazione salvata.', 'success')
        return redirect(url_for('configurazione'))
    return render_template('configurazione.html', config=cfg)


# ══════════════════════════════════════════════════════════════════
# ROUTE: PIANO DI CARICO (Smart Loading Plan)
# ══════════════════════════════════════════════════════════════════

@app.route('/piano_carico')
def piano_carico():
    lotto_id  = request.args.get('lotto_id', type=int)
    lotto_sel = lotto_id
    lotto_json = 'null'

    lotti_attivi = Lotto.query.filter(
        Lotto.stato.in_(['attesa','pianificato','confermato','in_corso'])
    ).order_by(Lotto.id.desc()).limit(20).all()

    if lotto_id:
        lotto = Lotto.query.get(lotto_id)
        if lotto:
            items = LottoItem.query.filter_by(lotto_id=lotto_id).all()
            lotto_data = {
                'id':    lotto.id,
                'nome':  lotto.nome or f"Lotto #{lotto.id}",
                'stato': lotto.stato,
                'items': []
            }
            for it in items:
                p = it.prodotto
                if p:
                    lotto_data['items'].append({
                        'cod':      p.codice,
                        'nome':     p.nome,
                        'fam':      p.famiglia or '',
                        'famiglia': p.famiglia or '',
                        'qty':      it.quantita,
                        'peso_kg':  p.peso_kg or 0,
                        'sup_m2':   p.superficie_m2 or 0,
                        'ganci':    it.n_ganci_occupati or 1,
                    })
            import json as _json
            lotto_json = _json.dumps(lotto_data)

    return render_template('piano_carico.html',
        lotti_attivi=lotti_attivi,
        lotto_sel=lotto_sel,
        lotto_json=lotto_json)


# ══════════════════════════════════════════════════════════════════
# API REST
# ══════════════════════════════════════════════════════════════════

@app.route('/api/catalogo')
def api_catalogo():
    prodotti = Prodotto.query.order_by(Prodotto.famiglia, Prodotto.codice).all()
    return jsonify([{
        'cod': p.codice, 'nome': p.nome, 'fam': p.famiglia or '',
        'sup_m2': round(p.superficie_m2 or 0, 3),
        'peso_kg': p.peso_kg or 0,
        'ganci': max(1, round((p.passo_gancio_m or 0.4)/0.4)),
        'vel_default': 0.7 if (p.peso_kg or 0)>200 else (1.0 if (p.peso_kg or 0)>50 else 1.5),
        'dim_mm': f"{int(p.lunghezza_mm or 0)}×{int(p.larghezza_mm or 0)}×{int(p.altezza_mm or 0)}",
    } for p in prodotti])


@app.route('/api/ottimizza', methods=['POST'])
def api_ottimizza():
    data   = request.json or {}
    pezzi  = data.get('pezzi', [])
    if not pezzi:
        return jsonify({'error': 'Nessun pezzo'}), 400
    cfg    = get_config()
    IMP    = IMPIANTO
    zone   = [{'zona': z+1, 'ganci_usati': 0, 'peso_kg': 0.0, 'costo_termico': 0.0,
               'pezzi': [], 'is_curva': (z+1) in IMP['zone_curva'],
               'sat_pct': 0, 'peso_pct': 0} for z in range(IMP['n_zone'])]
    vel    = min(p.get('vel_default', 1.5) for p in pezzi)
    avvisi = []
    pezzi_exp = []
    for p in pezzi:
        for _ in range(p.get('qty',1)):
            pezzi_exp.append(dict(p))
    pezzi_exp.sort(key=lambda x: -x.get('peso_kg',0))

    for pezzo in pezzi_exp:
        g   = max(1, round(pezzo.get('ganci',1)))
        kg  = pezzo.get('peso_kg', 0)
        sup = pezzo.get('sup_m2', 0)
        c_t = round(kg * 0.49 * 180 / 3600 * 0.22 * (1.5/max(vel,0.5)), 4)
        best = None; best_s = -1
        for z in zone:
            if g >= 3 and z['is_curva']: continue
            plim = max(IMP['peso_max_zona_kg'], kg*1.1)
            if z['ganci_usati'] + g > IMP['ganci_zona']: continue
            if z['peso_kg'] + kg > plim: continue
            sc = (1 - z['ganci_usati']/IMP['ganci_zona']) * 0.6 + (1 - z['peso_kg']/IMP['peso_max_zona_kg']) * 0.4
            if sc > best_s: best_s = sc; best = z
        if best:
            best['ganci_usati'] += g; best['peso_kg'] += kg; best['costo_termico'] += c_t
            best['pezzi'].append({'cod': pezzo.get('cod','?'), 'nome': pezzo.get('nome',''),
                                  'ganci': g, 'peso_kg': kg, 'c_termico': c_t})
        else:
            avvisi.append(f'❌ {pezzo.get("cod","?")}: impossibile allocare')

    gc_tot  = sum(z['ganci_usati'] for z in zone)
    kg_tot  = sum(z['peso_kg'] for z in zone)
    ct_tot  = round(sum(z['costo_termico'] for z in zone), 2)
    sat     = round(gc_tot / (IMP['n_zone'] * IMP['ganci_zona']) * 100, 1)
    ciclo   = round(cfg.lunghezza_tunnel_m / max(vel,0.1))
    for z in zone:
        z['sat_pct'] = round(z['ganci_usati']/IMP['ganci_zona']*100)
        z['peso_pct']= round(z['peso_kg']/IMP['peso_max_zona_kg']*100)
    if sat < 30:  avvisi.insert(0, f'💡 Saturazione bassa ({sat}%) — aggiungere pezzi')
    if sat > 85:  avvisi.insert(0, f'⚠️ Saturazione alta ({sat}%) — considera due lotti')
    if vel < 1.0: avvisi.append(f'🐢 Velocità lenta ({vel} m/min) — ciclo {ciclo} min')
    return jsonify({'ok': True, 'zone': zone,
                    'kpi': {'sat_media_pct': sat, 'ganci_tot': gc_tot, 'ganci_max': IMP['n_zone']*IMP['ganci_zona'],
                            'peso_tot_kg': round(kg_tot,1), 'costo_termico_tot': ct_tot,
                            'vel_scelta': vel, 'ciclo_min': ciclo,
                            'n_pezzi': len(pezzi_exp), 'n_falliti': len([a for a in avvisi if '❌' in a])},
                    'avvisi': avvisi})


@app.route('/api/analizza_3d', methods=['POST'])
def api_analizza_3d():
    """Analizza file 3D con Trimesh e aggiorna il prodotto."""
    try:
        from geometry import analyze_3d_file
    except ImportError:
        return jsonify({'error': 'geometry.py non trovato'}), 500
    f = request.files.get('file_3d')
    codice = request.form.get('codice','')
    if not f:
        return jsonify({'error': 'File non allegato'}), 400
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(f.filename)[1], delete=False) as tmp:
        f.save(tmp.name)
        result = analyze_3d_file(tmp.name)
    if 'error' in result:
        return jsonify(result), 400
    # Aggiorna prodotto nel DB
    p = Prodotto.query.filter_by(codice=codice).first()
    if p:
        p.lunghezza_mm  = result.get('lunghezza_mm', p.lunghezza_mm)
        p.larghezza_mm  = result.get('larghezza_mm', p.larghezza_mm)
        p.altezza_mm    = result.get('altezza_mm',   p.altezza_mm)
        p.superficie_m2 = result.get('superficie_m2', p.superficie_m2)
        p.peso_kg       = result.get('peso_kg',       p.peso_kg)
        p.passo_gancio_m = result.get('passo_gancio_m', p.passo_gancio_m)
        p.file_3d       = f.filename
        db.session.commit()
    return jsonify({'ok': True, **result})


@app.route('/api/lotto/crea', methods=['POST'])
def api_lotto_crea():
    d = request.json or {}
    pezzi = d.get('pezzi',[])
    cfg = get_config()
    lotto = Lotto(
        codice_lotto    = f"L{datetime.now().strftime('%Y%m%d%H%M%S')}",
        nome            = f"Lotto {datetime.now().strftime('%d/%m %H:%M')}",
        data_produzione = date.today().isoformat(),
        stato           = 'attesa',
        velocita_catena = cfg.velocita_default,
        costo_orario    = cfg.costo_orario_esercizio,
        n_pezzi_totali  = sum(p.get('qty',1) for p in pezzi),
    )
    db.session.add(lotto)
    db.session.flush()
    for p_data in pezzi:
        prod = Prodotto.query.filter_by(codice=p_data.get('cod','')).first()
        if not prod: continue
        abc = calcola_abc(prod, cfg)
        item = LottoItem(
            lotto_id=lotto.id, prodotto_id=prod.id,
            quantita=p_data.get('qty',1),
            n_ganci_occupati=max(1,round((prod.passo_gancio_m or 0.4)/0.4)),
            costo_materiale_unitario  = abc['costo_vernice'],
            costo_processo_unitario   = abc['costo_processo'],
            costo_manodopera_unitario = abc['costo_manodopera'],
            costo_unitario_totale     = abc['costo_totale'],
            costo_riga = abc['costo_totale'] * p_data.get('qty',1),
        )
        db.session.add(item)
    db.session.commit()
    return jsonify({'ok': True, 'id': lotto.id})


@app.route('/api/lotto/corrente')
def api_lotto_corrente():
    lotto = Lotto.query.filter(Lotto.stato.in_(['attesa','in_corso'])).order_by(Lotto.id.desc()).first()
    if not lotto:
        return jsonify({'error': 'Nessun lotto attivo'})
    items = LottoItem.query.filter_by(lotto_id=lotto.id).all()
    pezzi = []
    for it in items:
        p = it.prodotto
        if p:
            pezzi.append({'cod':p.codice,'nome':p.nome,'sup_m2':p.superficie_m2,'peso_kg':p.peso_kg,'ganci':it.n_ganci_occupati,'qty':it.quantita})
    return jsonify({'id':lotto.id,'stato':lotto.stato,'pezzi':pezzi,
                    'inizio':lotto.inizio.isoformat() if lotto.inizio else None,
                    'fine':lotto.fine.isoformat() if lotto.fine else None,
                    'durata_min':lotto.durata_min})


@app.route('/api/lotto/<int:lid>/start', methods=['POST'])
def api_start(lid):
    lotto = db.session.get(Lotto, lid)
    if not lotto: return jsonify({'error':'Not found'}),404
    lotto.inizio  = datetime.now(timezone.utc)
    lotto.stato   = 'in_corso'
    lotto.operatore = (request.json or {}).get('operaio','Operaio')
    db.session.commit()
    return jsonify({'ok':True,'inizio':lotto.inizio.isoformat()})


@app.route('/api/lotto/<int:lid>/stop', methods=['POST'])
def api_stop(lid):
    lotto = db.session.get(Lotto, lid)
    if not lotto: return jsonify({'error':'Not found'}),404
    lotto.fine  = datetime.now(timezone.utc)
    lotto.stato = 'completato'
    db.session.commit()
    # Calcola costi post-timbratura
    items = LottoItem.query.filter_by(lotto_id=lid).all()
    cfg   = get_config()
    dur   = lotto.durata_min or 0
    sup_tot = sum((it.prodotto.superficie_m2 or 0)*it.quantita for it in items if it.prodotto)
    costi = []
    for it in items:
        p = it.prodotto
        if not p: continue
        sup_p = (p.superficie_m2 or 0)*it.quantita
        quota = (sup_p/sup_tot) if sup_tot>0 else 0
        t_p   = dur * quota
        c_proc = (t_p/60)*(cfg.costo_orario_esercizio+cfg.costo_manodopera_ora)
        c_vern = sup_p*cfg.spessore_default_micron/1e6*cfg.peso_specifico_vernice*1000*8.50/cfg.efficienza_applicazione
        c_tot  = c_proc + c_vern
        c_unit = c_tot / max(it.quantita,1)
        costi.append({'cod':p.codice,'nome':p.nome,'qty':it.quantita,
                      'sup_m2':p.superficie_m2,'quota_pct':round(quota*100,1),
                      'tempo_min':round(t_p,1),'c_verniciatura':round(c_vern,2),
                      'c_processo':round(c_proc,2),'c_totale':round(c_tot,2),'c_unitario':round(c_unit,2)})
    return jsonify({'ok':True,'durata_min':lotto.durata_min,'costi':costi})


@app.route('/api/storico/kpi')
def api_storico_kpi():
    lotti = Lotto.query.filter_by(stato='completato').all()
    if not lotti:
        return jsonify({'n_lotti':0,'n_pezzi':0,'costo_tot':0,'durata_media':0,'top_codici':[]})
    n_pezzi=0; costo_tot=0.0; durate=[]; codici={}
    for l in lotti:
        try:
            items = LottoItem.query.filter_by(lotto_id=l.id).all()
            for it in items:
                n_pezzi+=it.quantita
                ct = it.costo_riga or 0; costo_tot+=ct
                p = it.prodotto
                if p:
                    if p.codice not in codici:
                        codici[p.codice]={'cod':p.codice,'nome':p.nome,'n_pezzi':0,'costo_tot':0}
                    codici[p.codice]['n_pezzi']+=it.quantita
                    codici[p.codice]['costo_tot']+=ct
            if l.durata_min: durate.append(l.durata_min)
        except: pass
    top = sorted(codici.values(), key=lambda x:-x['n_pezzi'])[:10]
    for t in top:
        t['costo_medio']=round(t['costo_tot']/max(t['n_pezzi'],1),3)
        t['costo_tot']=round(t['costo_tot'],2)
    return jsonify({'n_lotti':len(lotti),'n_pezzi':n_pezzi,'costo_tot':round(costo_tot,2),
                    'durata_media':round(sum(durate)/len(durate),1) if durate else 0,'top_codici':top})

@app.route('/initdb')
def initdb():
    db.create_all()
    return 'DB inizializzato OK!'

# ── ENTRY POINT ─────────────────────────────────────────────────

@app.route('/carico_guidato')
def carico_guidato():
    lotto_id = request.args.get('lotto_id', type=int)
    lotto    = None
    sequenza_json = 'null'
    ciclo_min = 113

    lotti_disponibili = Lotto.query.filter(
        Lotto.stato.in_(['attesa','pianificato','confermato'])
    ).order_by(Lotto.id.desc()).limit(20).all()

    if lotto_id:
        lotto = Lotto.query.get(lotto_id)
        if lotto:
            cfg   = get_config()
            vel   = lotto.velocita_catena or cfg.velocita_default
            ciclo_min = round(170 / max(vel, 0.1))
            items = LottoItem.query.filter_by(lotto_id=lotto_id).all()

            # Mappa famiglia → tipo gancio
            HOOK_MAP = {
                'Trinciatrici':1,'Falciatrici':1,'Traino':1,'Strutture':1,
                'ENODUO':2,'Bracci':2,
                'Carter':3,'Coperchi':3,'Staffaggi':3,'Carrello':3,
                'Rotopresse':4,'Vendemmiatrici':4,
            }

            # Costruisce sequenza ottimizzata
            # Ordine: prima Type 4 (pesanti), poi Type 1 (lunghi), poi Type 2, poi Type 3 (bulk)
            # E raggruppa per colore per minimizzare cambi
            seq_items = []
            for it in items:
                p = it.prodotto
                if not p: continue
                fam  = p.famiglia or ''
                tipo = HOOK_MAP.get(fam, 3)
                pxg  = {1:1,2:1,3:6,4:1}.get(tipo,1)
                seq_items.append({
                    'cod':   p.codice,
                    'nome':  p.nome,
                    'fam':   fam,
                    'tipo':  tipo,
                    'pxg':   pxg,
                    'qty':   it.quantita,
                    'kg':    p.peso_kg or 0,
                    'sup':   p.superficie_m2 or 0,
                    'l':     int(p.lunghezza_mm or 0),
                    'w':     int(p.larghezza_mm or 0),
                    'h':     int(p.altezza_mm   or 0),
                    'ganci': it.n_ganci_occupati or 1,
                    'colore': 'VERDE RAL 6005',
                })
            # Ordina: Type4 → Type1 → Type2 → Type3 (stesso colore insieme)
            seq_items.sort(key=lambda x: (x['tipo'], -x['kg']))
            sequenza_json = json.dumps(seq_items)

    return render_template('carico_guidato.html',
        lotto=lotto,
        lotti_disponibili=lotti_disponibili,
        sequenza=seq_items if lotto else [],
        sequenza_json=sequenza_json,
        ciclo_min=ciclo_min if lotto else 113)


@app.route('/fix_storico_lotti')
def fix_storico_lotti():
    """Recupera StoricoCosto per lotti completati che non ce l'hanno ancora."""
    cfg = get_config()
    lotti_completati = Lotto.query.filter_by(stato='completato').all()
    recuperati = 0
    pezzi_tot  = 0

    for lotto in lotti_completati:
        # Salta se ha già storico
        if StoricoCosto.query.filter_by(lotto_id=lotto.id).first():
            continue

        items = LottoItem.query.filter_by(lotto_id=lotto.id).all()
        if not items:
            continue

        sat = lotto.saturazione_pct or 100.0
        for it in items:
            p = it.prodotto
            if not p:
                continue
            abc = calcola_abc(p, cfg,
                              velocita_override=lotto.velocita_catena,
                              saturazione_pct=sat)
            it.costo_materiale_unitario  = abc['costo_vernice']
            it.costo_processo_unitario   = abc['costo_processo']
            it.costo_manodopera_unitario = abc['costo_manodopera']
            it.costo_termico_unitario    = abc['costo_termico']
            it.costo_unitario_totale     = abc['costo_totale']
            it.costo_riga = round(abc['costo_totale'] * it.quantita, 2)
            it.tempo_unitario_min = abc['t_occ_min']

            # Aggiorna standard
            n = p.n_campioni_standard or 0
            p.costo_standard = round(
                (p.costo_standard * n + abc['costo_totale']) / (n + 1), 4)
            p.n_campioni_standard = n + 1

            db.session.add(StoricoCosto(
                prodotto_id      = p.id,
                lotto_id         = lotto.id,
                data             = lotto.completato_at.date() if lotto.completato_at else date.today(),
                quantita         = it.quantita,
                costo_materiale  = abc['costo_vernice'],
                costo_processo   = abc['costo_processo'],
                costo_manodopera = abc['costo_manodopera'],
                costo_termico    = abc.get('costo_termico', 0),
                costo_totale     = abc['costo_totale'],
                tempo_min        = abc['t_occ_min'],
                velocita_catena  = lotto.velocita_catena or cfg.velocita_default,
                costo_orario     = cfg.costo_orario_esercizio,
                n_ganci          = it.n_ganci_occupati or 1,
            ))
            pezzi_tot += it.quantita

        # Aggiorna costo totale lotto
        lotto.costo_totale = round(
            sum(it.costo_riga for it in items), 2)
        lotto.costo_totale_eur = lotto.costo_totale
        recuperati += 1

    db.session.commit()
    return (f'✅ Recuperati {recuperati} lotti · {pezzi_tot} codici · '
            f'Storico popolato. <a href="/storico">Vai allo storico</a>')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)





# ══════════════════════════════════════════════════════════════════
# API: Dashboard stats
# ══════════════════════════════════════════════════════════════════

@app.route('/api/dashboard_stats')
def api_dashboard_stats():
    from sqlalchemy import func
    from datetime import datetime as dt
    oggi = date.today()

    try:
        start_oggi = dt.combine(oggi, dt.min.time()).replace(tzinfo=timezone.utc)
        lotti_oggi = Lotto.query.filter(
            Lotto.completato_at >= start_oggi,
            Lotto.stato == 'completato'
        ).all()
        pezzi_oggi = sum(l.n_pezzi_totali or 0 for l in lotti_oggi)
        costo_oggi = sum(l.costo_totale_eur or 0 for l in lotti_oggi)
    except Exception:
        pezzi_oggi = costo_oggi = 0

    lotti_rec = Lotto.query.filter_by(stato='completato').order_by(Lotto.id.desc()).limit(15).all()
    sat_media = round(sum(l.saturazione_pct or 0 for l in lotti_rec) / max(len(lotti_rec), 1), 1)

    try:
        n_commesse = MacchinaCommessa.query.count()
        cq = MacchinaCommessa.query.filter(
            MacchinaCommessa.stato != 'completato',
            MacchinaCommessa.priorita <= 2
        ).order_by(MacchinaCommessa.priorita).limit(3).all()
        commesse_urgenti = [{'nome': c.nome_macchina, 'cliente': c.cliente or "",
            'data_consegna': c.data_consegna or "", 'priorita': c.priorita or 5,
            'commessa': c.commessa} for c in cq]
    except Exception:
        n_commesse = 0
        commesse_urgenti = []

    trend = []
    for i in range(6, -1, -1):
        d = oggi - timedelta(days=i)
        try:
            s = dt.combine(d, dt.min.time()).replace(tzinfo=timezone.utc)
            e_dt = dt.combine(d, dt.max.time()).replace(tzinfo=timezone.utc)
            ll = Lotto.query.filter(Lotto.completato_at >= s,
                Lotto.completato_at <= e_dt, Lotto.stato == 'completato').all()
            pz = sum(l.n_pezzi_totali or 0 for l in ll)
        except Exception:
            pz = 0
        trend.append({'data': d.strftime('%d/%m'), 'pezzi': pz})

    return jsonify({
        'pezzi_oggi': pezzi_oggi,
        'costo_oggi': round(costo_oggi, 2),
        'sat_media': sat_media,
        'ordini_attesa': OrdineOrdine.query.filter_by(stato='ATTESA').count(),
        'pezzi_attesa': db.session.query(func.sum(OrdineOrdine.quantita)).filter_by(stato='ATTESA').scalar() or 0,
        'n_lotti_completati': Lotto.query.filter_by(stato='completato').count(),
        'n_commesse': n_commesse,
        'commesse_urgenti': commesse_urgenti,
        'trend': trend,
    })


# MODELLI AGGIUNTIVI: Macchina da consegnare + Componenti
# ══════════════════════════════════════════════════════════════════

class MacchinaCommessa(db.Model):
    """Una macchina completa da consegnare al cliente."""
    __tablename__ = 'macchina_commessa'
    id              = db.Column(db.Integer, primary_key=True)
    commessa        = db.Column(db.String(50), unique=True, nullable=False)
    num_serie       = db.Column(db.String(100))
    nome_macchina   = db.Column(db.String(200))
    cliente         = db.Column(db.String(200))
    colore          = db.Column(db.String(80))
    data_consegna   = db.Column(db.String(20))
    priorita        = db.Column(db.Integer, default=5)
    stato           = db.Column(db.String(30), default='da_verniciare')  # da_verniciare, in_corso, completato
    doc_num         = db.Column(db.String(80))
    slot_catena     = db.Column(db.String(20))
    ganci_slot      = db.Column(db.Integer, default=7)
    operatore       = db.Column(db.String(100))
    note            = db.Column(db.Text)
    # Piano ottimizzato salvato come JSON
    piano_json      = db.Column(db.Text, default='{}')
    creata_il       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    componenti      = db.relationship('ComponenteMacchina', backref='macchina', lazy=True, cascade='all,delete')


class ComponenteMacchina(db.Model):
    """Un singolo componente da verniciare per una macchina."""
    __tablename__ = 'componente_macchina'
    id              = db.Column(db.Integer, primary_key=True)
    macchina_id     = db.Column(db.Integer, db.ForeignKey('macchina_commessa.id'), nullable=False)
    codice          = db.Column(db.String(50))
    descrizione     = db.Column(db.String(200))
    L_mm            = db.Column(db.Float, default=0)  # lunghezza
    A_mm            = db.Column(db.Float, default=0)  # altezza
    P_mm            = db.Column(db.Float, default=0)  # profondità/larghezza
    peso_unitario   = db.Column(db.Float, default=0)
    ganci_pdf       = db.Column(db.Integer, default=1)  # come indicato nel PDF
    qty             = db.Column(db.Integer, default=1)
    note            = db.Column(db.String(200))
    # Risultato ottimizzazione
    ganci_assegnati = db.Column(db.Integer, default=0)
    posizione_gancio= db.Column(db.Integer, default=0)



# ══════════════════════════════════════════════════════════════════
# LOGICA OTTIMIZZATORE SLOT 3 METRI
# ══════════════════════════════════════════════════════════════════

PASSO_GANCIO_MM = 400
GANCI_SLOT_DEFAULT = 7   # 3 metri / 400mm ≈ 7 ganci
PESO_MAX_PER_GANCIO_KG = 60.0

class SlotMacchina:
    """Rappresenta uno slot di N ganci sulla catena per una macchina."""
    def __init__(self, n_ganci=GANCI_SLOT_DEFAULT):
        self.n_ganci = n_ganci
        self.ganci = [{'pezzi': [], 'peso': 0.0, 'libero': True} for _ in range(n_ganci)]

    def _trova_blocco_libero(self, n):
        """Trova n ganci consecutivi completamente liberi."""
        for i in range(self.n_ganci - n + 1):
            if all(self.ganci[j]['libero'] for j in range(i, i + n)):
                return i
        return -1

    def posiziona_principale(self, codice, descrizione, peso, n_ganci, qty=1, note=''):
        """Posiziona un pezzo che occupa N ganci fisici consecutivi."""
        risultati = []
        for q in range(qty):
            pos = self._trova_blocco_libero(n_ganci)
            if pos == -1:
                return False, risultati
            peso_per_gancio = peso / n_ganci
            for i in range(pos, pos + n_ganci):
                self.ganci[i]['libero'] = False
                self.ganci[i]['pezzi'].append({
                    'codice': codice, 'desc': descrizione,
                    'peso': peso_per_gancio, 'principale': True,
                    'n_ganci': n_ganci, 'note': note
                })
                self.ganci[i]['peso'] += peso_per_gancio
            risultati.append({'gancio_start': pos + 1, 'n_ganci': n_ganci})
        return True, risultati

    def aggiungi_su_gancio(self, codice, descrizione, peso, qty=1, note=''):
        """Aggiunge piccoli pezzi su ganci già occupati (appendesi sotto)."""
        allocati = 0
        for q in range(qty):
            # Candidati: ganci occupati con spazio peso residuo
            candidati = [
                (i, g) for i, g in enumerate(self.ganci)
                if not g['libero'] and g['peso'] + peso <= PESO_MAX_PER_GANCIO_KG
            ]
            if not candidati:
                # Prova su ganci liberi
                liberi = [(i, g) for i, g in enumerate(self.ganci) if g['libero']]
                if not liberi:
                    break
                i, g = liberi[0]
                g['libero'] = False
                g['pezzi'].append({'codice': codice, 'desc': descrizione,
                                   'peso': peso, 'principale': False, 'n_ganci': 0.5, 'note': note})
                g['peso'] += peso
                allocati += 1
            else:
                # Il gancio con meno peso (più spazio)
                i, g = min(candidati, key=lambda x: x[1]['peso'])
                g['pezzi'].append({'codice': codice, 'desc': descrizione,
                                   'peso': peso, 'principale': False, 'n_ganci': 0, 'note': note})
                g['peso'] += peso
                allocati += 1
        return allocati

    def piano_completo(self):
        ganci_usati = sum(1 for g in self.ganci if g['pezzi'])
        pezzi_tot = sum(len(g['pezzi']) for g in self.ganci)
        peso_tot = sum(g['peso'] for g in self.ganci)
        sat = round(ganci_usati / self.n_ganci * 100, 1)
        return {
            'ganci': [{
                'numero': i + 1,
                'posizione_m': round(i * PASSO_GANCIO_MM / 1000, 2),
                'pezzi': g['pezzi'],
                'peso': round(g['peso'], 2),
                'libero': g['libero'],
            } for i, g in enumerate(self.ganci)],
            'ganci_usati': ganci_usati,
            'ganci_tot': self.n_ganci,
            'pezzi_tot': pezzi_tot,
            'peso_tot': round(peso_tot, 1),
            'sat_pct': sat,
            'lunghezza_m': round(ganci_usati * PASSO_GANCIO_MM / 1000, 2),
        }


def ottimizza_slot_commessa(componenti_list, ganci_slot=GANCI_SLOT_DEFAULT):
    """
    Ottimizza il carico di tutti i componenti di una macchina in N ganci (slot da 3m).
    componenti_list: lista di dict {codice, descrizione, L_mm, A_mm, P_mm, peso, ganci_pdf, qty, note}
    Ritorna: piano_completo dict + non_allocati list
    """
    slot = SlotMacchina(ganci_slot)
    non_allocati = []

    # Ordina: prima i pezzi con più ganci (i grandi strutturali)
    pezzi_ord = sorted(componenti_list, key=lambda x: (-x.get('ganci_pdf', 1), -x.get('peso', 0)))

    for comp in pezzi_ord:
        n_g = comp.get('ganci_pdf', 1)
        qty = comp.get('qty', 1)
        peso = comp.get('peso', 0)
        codice = comp.get('codice', '')
        desc = comp.get('descrizione', codice)
        note = comp.get('note', '')

        if n_g >= 1:
            # Pezzo principale: occupa N ganci fisici
            ok, _ = slot.posiziona_principale(codice, desc, peso, n_g, qty, note)
            if not ok:
                # Non entra → aggiunge su ganci esistenti o non allocato
                aggiunto = slot.aggiungi_su_gancio(codice, desc, peso, qty, note)
                if aggiunto < qty:
                    for i in range(qty - aggiunto):
                        non_allocati.append({**comp, 'qty': 1, 'motivo': 'slot pieno'})
        else:
            # Piccolo (ganci_pdf=0): appendesi sempre su gancio esistente
            aggiunto = slot.aggiungi_su_gancio(codice, desc, peso, qty, note)
            if aggiunto < qty:
                for i in range(qty - aggiunto):
                    non_allocati.append({**comp, 'qty': 1, 'motivo': 'nessun gancio disponibile'})

    piano = slot.piano_completo()
    piano['non_allocati'] = non_allocati
    return piano


def parse_commessa_pdf(file_bytes):
    """Legge un PDF NetPro commessa macchina. Ritorna dict con dati macchina + componenti."""
    import pdfplumber, io, re
    result = {
        'commessa': '', 'nome_macchina': '', 'num_serie': '', 'cliente': '',
        'colore': '', 'data_consegna': '', 'priorita': '', 'slot_catena': '',
        'ganci_slot': GANCI_SLOT_DEFAULT, 'doc_num': '', 'data': '',
        'operatore': '', 'turno': '', 'componenti': [], 'errori': []
    }
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                # Header
                for pat, key in [
                    (r'Documento:\s*(NP-[\w-]+)', 'doc_num'),
                    (r'Data emissione:\s*([\d/]+)', 'data'),
                    (r'Operatore SAP:\s*([^\|]+)', 'operatore'),
                    (r'MACCHINA DA CONSEGNARE:\s*(.+)', 'nome_macchina'),
                    (r'Numero Commessa:\s*([\w]+)', 'commessa'),
                    (r'Numero Serie:\s*([\w-]+)', 'num_serie'),
                    (r'Cliente:\s*([^\|]+)', 'cliente'),
                    (r'Colore verniciatura:\s*([^\|]+)', 'colore'),
                    (r'Data consegna:\s*([\d/]+)', 'data_consegna'),
                    (r'Slot catena assegnato:\s*([\w-]+)', 'slot_catena'),
                    (r'Ganci disponibili:\s*(\d+)', 'ganci_slot'),
                    (r'Turno:\s*([^\n]+)', 'turno'),
                ]:
                    m = re.search(pat, text)
                    if m:
                        val = m.group(1).strip()
                        if key == 'ganci_slot':
                            try: val = int(val)
                            except: val = GANCI_SLOT_DEFAULT
                        result[key] = val

                # Priorità numerica
                m = re.search(r'Priorit[àa]:\s*(\d+)', text)
                if m: result['priorita'] = int(m.group(1))

                # Tabella componenti
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or not row[0]: continue
                        if str(row[0]).strip() in ('Pos.', 'Pos', '', None): continue
                        if row[2] and 'TOTALE' in str(row[2]): continue
                        try:
                            pos = str(row[0]).strip()
                            if not pos.isdigit(): continue
                            codice = str(row[1]).strip()
                            desc = str(row[2]).strip()
                            # LxAxP
                            dims = str(row[3]).replace(' ', '').split('×') if row[3] else ['0','0','0']
                            L = float(dims[0]) if len(dims) > 0 and dims[0] else 0
                            A = float(dims[1]) if len(dims) > 1 and dims[1] else 0
                            P = float(dims[2]) if len(dims) > 2 and dims[2] else 0
                            peso_s = str(row[4]).replace('kg','').strip() if row[4] else '0'
                            peso = float(peso_s) if peso_s else 0.0
                            ganci = int(str(row[5]).strip()) if row[5] and str(row[5]).strip().isdigit() else 1
                            qty = int(str(row[6]).strip()) if row[6] and str(row[6]).strip().isdigit() else 1
                            note = str(row[7]).strip() if len(row) > 7 and row[7] else ''
                            if codice:
                                result['componenti'].append({
                                    'codice': codice, 'descrizione': desc,
                                    'L_mm': L, 'A_mm': A, 'P_mm': P,
                                    'peso': peso, 'ganci_pdf': ganci,
                                    'qty': qty, 'note': note
                                })
                        except Exception as e:
                            result['errori'].append(f'Riga {pos}: {e}')
    except Exception as e:
        result['errori'].append(f'Errore PDF: {e}')
    return result


# ══════════════════════════════════════════════════════════════════
# ROUTE: COMMESSE MACCHINE (lista + nuovo da PDF)
# ══════════════════════════════════════════════════════════════════

@app.route('/commesse')
def commesse():
    macs = MacchinaCommessa.query.order_by(MacchinaCommessa.priorita, MacchinaCommessa.id).all()
    return render_template('commesse.html', macchine=macs)


@app.route('/commessa/nuova_pdf', methods=['GET', 'POST'])
def commessa_nuova_pdf():
    parsed = None
    piano = None
    if request.method == 'POST':
        action = request.form.get('action', 'parse')
        if action == 'parse':
            f = request.files.get('file_pdf')
            if f and f.filename.lower().endswith('.pdf'):
                parsed = parse_commessa_pdf(f.read())
                if parsed['componenti']:
                    piano = ottimizza_slot_commessa(
                        parsed['componenti'],
                        ganci_slot=parsed.get('ganci_slot', GANCI_SLOT_DEFAULT)
                    )
                else:
                    flash('Nessun componente trovato nel PDF.', 'warning')
            else:
                flash('Carica un file PDF valido.', 'danger')

        elif action == 'salva':
            import json as _json
            commessa_num = request.form.get('commessa', '')
            ganci_slot = int(request.form.get('ganci_slot', GANCI_SLOT_DEFAULT))
            comp_json = request.form.get('componenti_json', '[]')
            piano_json_str = request.form.get('piano_json', '{}')
            try:
                comp_list = _json.loads(comp_json)
                piano_data = _json.loads(piano_json_str)
            except Exception:
                comp_list = []; piano_data = {}

            # Ricalcola ottimizzazione al momento del salvataggio
            piano_final = ottimizza_slot_commessa(comp_list, ganci_slot)

            mac = MacchinaCommessa(
                commessa      = commessa_num,
                num_serie     = request.form.get('num_serie', ''),
                nome_macchina = request.form.get('nome_macchina', ''),
                cliente       = request.form.get('cliente', ''),
                colore        = request.form.get('colore', ''),
                data_consegna = request.form.get('data_consegna', ''),
                priorita      = int(request.form.get('priorita', 5)),
                doc_num       = request.form.get('doc_num', ''),
                slot_catena   = request.form.get('slot_catena', ''),
                ganci_slot    = ganci_slot,
                operatore     = request.form.get('operatore', ''),
                stato         = 'da_verniciare',
                piano_json    = _json.dumps(piano_final),
            )
            db.session.add(mac)
            db.session.flush()
            for c in comp_list:
                comp = ComponenteMacchina(
                    macchina_id    = mac.id,
                    codice         = c.get('codice',''),
                    descrizione    = c.get('descrizione',''),
                    L_mm           = c.get('L_mm', 0),
                    A_mm           = c.get('A_mm', 0),
                    P_mm           = c.get('P_mm', 0),
                    peso_unitario  = c.get('peso', 0),
                    ganci_pdf      = c.get('ganci_pdf', 1),
                    qty            = c.get('qty', 1),
                    note           = c.get('note', ''),
                )
                db.session.add(comp)
            db.session.commit()
            flash(f'✅ Commessa {commessa_num} salvata! Piano ottimizzato: {piano_final["sat_pct"]}% saturazione', 'success')
            return redirect(url_for('commessa_detail', mac_id=mac.id))

    return render_template('commessa_nuova_pdf.html', parsed=parsed, piano=piano,
                           ganci_slot_default=GANCI_SLOT_DEFAULT)


@app.route('/commessa/<int:mac_id>')
def commessa_detail(mac_id):
    mac = MacchinaCommessa.query.get_or_404(mac_id)
    import json as _json
    try:
        piano = _json.loads(mac.piano_json or '{}')
    except Exception:
        piano = {}
    # Ricalcola fresco se piano è vuoto
    if not piano and mac.componenti:
        comp_list = [{
            'codice': c.codice, 'descrizione': c.descrizione,
            'L_mm': c.L_mm, 'A_mm': c.A_mm, 'P_mm': c.P_mm,
            'peso': c.peso_unitario, 'ganci_pdf': c.ganci_pdf,
            'qty': c.qty, 'note': c.note or '',
        } for c in mac.componenti]
        piano = ottimizza_slot_commessa(comp_list, mac.ganci_slot)
    return render_template('commessa_detail.html', mac=mac, piano=piano)


@app.route('/commessa/<int:mac_id>/stato', methods=['POST'])
def commessa_stato(mac_id):
    mac = MacchinaCommessa.query.get_or_404(mac_id)
    mac.stato = request.form.get('stato', mac.stato)
    db.session.commit()
    flash(f'Stato aggiornato: {mac.stato}', 'success')
    return redirect(url_for('commessa_detail', mac_id=mac_id))


@app.route('/api/commessa/<int:mac_id>/piano')
def api_commessa_piano(mac_id):
    mac = MacchinaCommessa.query.get_or_404(mac_id)
    import json as _json
    try:
        piano = _json.loads(mac.piano_json or '{}')
    except Exception:
        piano = {}
    if not piano and mac.componenti:
        comp_list = [{
            'codice': c.codice, 'descrizione': c.descrizione,
            'L_mm': c.L_mm, 'peso': c.peso_unitario,
            'ganci_pdf': c.ganci_pdf, 'qty': c.qty, 'note': c.note or '',
        } for c in mac.componenti]
        piano = ottimizza_slot_commessa(comp_list, mac.ganci_slot)
    return jsonify(piano)


