import json
import os
import re
import shutil
import requests
import PyPDF2
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
 
app = Flask(__name__)
 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
 
_db_url = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(BASE_DIR, "wms_local.db")}')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
 
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'wms-masterlogistic-secret')
 
db = SQLAlchemy(app)
 
# ── FILTRI JINJA ──────────────────────────────────────────────────────────────
@app.template_filter('dmyToIso')
def dmy_to_iso(dmy):
    """Converte GG/MM/AAAA → YYYY-MM-DD per i datepicker HTML (type=date)."""
    if not dmy:
        return ''
    try:
        p = dmy.split('/')
        if len(p) == 3:
            return f"{p[2]}-{p[1]}-{p[0]}"
    except Exception:
        pass
    return ''
 
# Railway: usa RAILWAY_VOLUME_MOUNT_PATH se disponibile (volume persistente)
# oppure STORAGE_PATH custom, oppure fallback a BASE_DIR (locale)
STORAGE_PATH = (
    os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or
    os.environ.get('STORAGE_PATH') or
    BASE_DIR
)
CARICO_FOLDER        = os.path.join(STORAGE_PATH, 'carico_ddt')
CARICO_DONE_FOLDER   = os.path.join(CARICO_FOLDER, 'elaborati')
CONSEGNA_FOLDER      = os.path.join(STORAGE_PATH, 'consegna_ddt')
CONSEGNA_DONE_FOLDER = os.path.join(CONSEGNA_FOLDER, 'elaborati')
UPLOAD_FOLDER        = os.path.join(STORAGE_PATH, 'uploads')
DONE_FOLDER          = os.path.join(STORAGE_PATH, 'completati')
ACQUISTI_FOLDER      = os.path.join(STORAGE_PATH, 'ordini_acquisto')
ACQUISTI_DONE_FOLDER = os.path.join(ACQUISTI_FOLDER, 'elaborati')
LOG_FILE             = os.path.join(STORAGE_PATH, 'log_sistema.txt')
FASCICOLI_ROOT       = os.path.join(STORAGE_PATH, 'storage', 'fascicoli_ordini')
 
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
 
for _d in [CARICO_FOLDER, CARICO_DONE_FOLDER, CONSEGNA_FOLDER, CONSEGNA_DONE_FOLDER,
           UPLOAD_FOLDER, DONE_FOLDER,
           ACQUISTI_FOLDER, ACQUISTI_DONE_FOLDER, FASCICOLI_ROOT]:
    os.makedirs(_d, exist_ok=True)
 
 
# ==============================================================================
# MODELLI
# ==============================================================================
 
class Articolo(db.Model):
    __tablename__ = 'articoli'
    id             = db.Column(db.Integer, primary_key=True)
    sku            = db.Column(db.String(50), unique=True, nullable=False)
    codice_esterno = db.Column(db.String(50), default='')   # ← CACODICE da Zucchetti
    descrizione    = db.Column(db.String(200), default='')
    stock          = db.Column(db.Integer, default=0)
    ordinati       = db.Column(db.Integer, default=0)
    incoming       = db.Column(db.Integer, default=0)
    fornitore      = db.Column(db.String(100), default='N/D')
    ordine_n       = db.Column(db.String(50),  default='N/D')
    data_evasione  = db.Column(db.String(20),  default='')
    scorta_minima  = db.Column(db.Integer, default=5)
 
    def to_dict(self):
        return {
            'desc': self.descrizione, 'stock': self.stock,
            'ordinati': self.ordinati, 'incoming': self.incoming,
            'fornitore': self.fornitore, 'ordine_n': self.ordine_n,
            'data_evasione': self.data_evasione,
            'scorta_minima': self.scorta_minima,
            'codice_esterno': self.codice_esterno
        }
 
 
class DistintaBase(db.Model):
    """
    Distinta base multilivello.
    codice_padre  = SKU del prodotto finito o semilavorato padre
    codice_figlio = SKU del componente necessario
    quantita      = quante unità di figlio servono per 1 unità di padre
    livello       = 1 = primo livello, 2 = secondo livello, ecc.
    """
    __tablename__ = 'distinta_base'
    id            = db.Column(db.Integer, primary_key=True)
    codice_padre  = db.Column(db.String(50), nullable=False)
    codice_figlio = db.Column(db.String(50), nullable=False)
    quantita      = db.Column(db.Float, default=1.0)
    livello       = db.Column(db.Integer, default=1)
    note          = db.Column(db.String(200), default='')
    creato_il     = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('codice_padre', 'codice_figlio', name='_padre_figlio_uc'),)
 
 
class ArticoloEvaso(db.Model):
    __tablename__ = 'articoli_evasi'
    id     = db.Column(db.Integer, primary_key=True)
    id_art = db.Column(db.String(150), unique=True, nullable=False)
    evaso  = db.Column(db.Boolean, default=True)
    qta_evasa = db.Column(db.Integer, default=0)   # ← NUOVO: quantità evasa totale
 
 
class RegistroEvasione(db.Model):
    __tablename__ = 'registro_evasioni'
    id       = db.Column(db.Integer, primary_key=True)
    data_ora = db.Column(db.DateTime, default=datetime.utcnow)
    conferma = db.Column(db.String(50))
    cliente  = db.Column(db.String(100))
    codice   = db.Column(db.String(50))
    qta      = db.Column(db.Integer)
 
 
class SlotTotem(db.Model):
    __tablename__ = 'slot_totem'
    id       = db.Column(db.Integer, primary_key=True)
    tipo     = db.Column(db.String(20), nullable=False)
    slot     = db.Column(db.Integer,    nullable=False)
    filename = db.Column(db.String(255), default='')
    __table_args__ = (db.UniqueConstraint('tipo', 'slot', name='_tipo_slot_uc'),)
 
 
class StoricoFornitore(db.Model):
    __tablename__ = 'storico_fornitori'
    id        = db.Column(db.Integer, primary_key=True)
    sku       = db.Column(db.String(50), nullable=False)
    fornitore = db.Column(db.String(100))
    posizione = db.Column(db.Integer, default=0)
 
 
class DichiarazioneProduzione(db.Model):
    """
    Manufacturing Order — Dichiarazione di Produzione.
    Registra il carico del prodotto finito (madre) e lo scarico
    dei semilavorati (figli) usati nell'assemblaggio.
    """
    __tablename__ = 'dichiarazioni_produzione'
    id              = db.Column(db.Integer, primary_key=True)
    numero_mo       = db.Column(db.String(20), unique=True, nullable=False)  # es. MO-001/DP
    conferma        = db.Column(db.String(50), default='')   # ordine cliente collegato
    codice_finito   = db.Column(db.String(50), nullable=False)
    desc_finito     = db.Column(db.String(200), default='')
    qta_prodotta    = db.Column(db.Integer, default=1)
    operatore       = db.Column(db.String(100), default='')
    note            = db.Column(db.Text, default='')
    componenti_json = db.Column(db.Text, default='[]')  # [{codice, desc, qta_scaricata}]
    creato_il       = db.Column(db.DateTime, default=datetime.utcnow)


class StoricoOrdineFornitore(db.Model):
    __tablename__ = 'storico_ordini_fornitore'
    id            = db.Column(db.Integer, primary_key=True)
    id_ordine     = db.Column(db.String(50), nullable=False)
    codice        = db.Column(db.String(50), nullable=False)
    qta           = db.Column(db.Integer, default=0)
    fornitore     = db.Column(db.String(200), default='')
    data_evasione = db.Column(db.String(20), default='')
    filename_oa   = db.Column(db.String(255), default='')
    __table_args__ = (db.UniqueConstraint('id_ordine', 'codice', name='_ordine_codice_uc'),)
 
 
class PreBolla(db.Model):
    __tablename__ = 'pre_bolle_ricevute'
    id            = db.Column(db.Integer, primary_key=True)
    id_masterwork = db.Column(db.Integer, default=0)
    cliente       = db.Column(db.String(200), nullable=False)
    dettagli      = db.Column(db.Text, default='')
    stato         = db.Column(db.String(20), default='DA_FARE')
    ricevuta_il   = db.Column(db.DateTime, default=datetime.utcnow)
    confermata_il = db.Column(db.DateTime, nullable=True)
 
 
class NoteOrdine(db.Model):
    """Note libere per ogni conferma ordine (PDF cliente)."""
    __tablename__ = 'note_ordini'
    id       = db.Column(db.Integer, primary_key=True)
    conferma = db.Column(db.String(50), unique=True, nullable=False)
    nota     = db.Column(db.Text, default='')
    aggiornata_il = db.Column(db.DateTime, default=datetime.utcnow)
 
 
class Imballaggio(db.Model):
    """Traccia gli articoli mandati in imballaggio per ogni conferma ordine."""
    __tablename__ = 'imballaggi'
    id            = db.Column(db.Integer, primary_key=True)
    conferma      = db.Column(db.String(50), nullable=False)   # es. "21/IS"
    filename      = db.Column(db.String(255), nullable=False)  # pdf cliente
    codice        = db.Column(db.String(50), nullable=False)
    qta_imballare = db.Column(db.Integer, default=0)
    imballato     = db.Column(db.Boolean, default=False)       # confermato imballato
    creato_il     = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('conferma', 'codice', name='_conf_cod_uc'),)
 
 
 
class ModificaOrdine(db.Model):
    """Modifiche manuali a quantità articoli di un ordine (override del PDF)."""
    __tablename__ = 'modifiche_ordine'
    id        = db.Column(db.Integer, primary_key=True)
    conferma  = db.Column(db.String(50), nullable=False, index=True)
    codice    = db.Column(db.String(50), nullable=False)
    qta_nuova = db.Column(db.Integer, nullable=False)  # 0 = articolo rimosso
    __table_args__ = (db.UniqueConstraint('conferma', 'codice', name='_mod_conf_cod_uc'),)
 
 
class Urgenza(db.Model):
    """Ordini/OA marcati manualmente come urgenti con nota motivazione."""
    __tablename__ = 'urgenze'
    id         = db.Column(db.Integer, primary_key=True)
    tipo       = db.Column(db.String(10), nullable=False)   # 'vendita' | 'acquisto'
    riferimento = db.Column(db.String(100), unique=True, nullable=False)  # conferma o filename OA
    nota       = db.Column(db.Text, default='')
    creata_il  = db.Column(db.DateTime, default=datetime.utcnow)
 
 
class StatoOrdineAcquisto(db.Model):
    """Stato manuale per ogni OA: data consegna prevista + conferma fornitore."""
    __tablename__ = 'stato_ordine_acquisto'
    id             = db.Column(db.Integer, primary_key=True)
    filename       = db.Column(db.String(255), unique=True, nullable=False)
    data_consegna  = db.Column(db.String(20), default='')
    confermato     = db.Column(db.Boolean, default=False)
    ritiro_proprio = db.Column(db.Boolean, default=False)  # True=RITIRIAMO NOI, False=SPEDISCE FORNITORE
    nota_interna   = db.Column(db.Text, default='')
    aggiornato_il  = db.Column(db.DateTime, default=datetime.utcnow)
 
 
class Task(db.Model):
    """Task giornalieri auto-generati o manuali."""
    __tablename__ = 'tasks'
    id            = db.Column(db.Integer, primary_key=True)
    tipo          = db.Column(db.String(30), default='auto')   # auto | manuale
    priorita      = db.Column(db.String(10), default='normale') # urgente | normale
    categoria     = db.Column(db.String(20), default='')        # fornitore | cliente | manuale
    titolo        = db.Column(db.Text, nullable=False)
    descrizione   = db.Column(db.Text, default='')
    riferimento   = db.Column(db.String(100), default='')       # filename OA o CO
    conferma      = db.Column(db.String(50), default='')        # n. conferma cliente
    ordine_n      = db.Column(db.String(50), default='')        # n. OA fornitore
    fornitore     = db.Column(db.String(200), default='')
    cliente       = db.Column(db.String(200), default='')
    data_scadenza = db.Column(db.String(20), default='')
    fatto         = db.Column(db.Boolean, default=False)
    nota_chiusura = db.Column(db.Text, default='')
    creato_il     = db.Column(db.DateTime, default=datetime.utcnow)
    fatto_il      = db.Column(db.DateTime, nullable=True)
    # Chiave univoca per evitare duplicati auto
    chiave        = db.Column(db.String(255), unique=True, nullable=True)
 
 
class DataEvasioneArticolo(db.Model):
    """Data di evasione per singolo articolo in un OA, con flag esatta/presunta."""
    __tablename__ = 'data_evasione_articolo'
    id           = db.Column(db.Integer, primary_key=True)
    filename_oa  = db.Column(db.String(255), nullable=False)
    codice       = db.Column(db.String(50),  nullable=False)
    data         = db.Column(db.String(20),  default='')
    is_presunta  = db.Column(db.Boolean, default=True)   # True=PRESUNTA, False=ESATTA
    tipo_data    = db.Column(db.String(20), default='presunta')  # 'esatta', 'presunta', 'provvisoria'
    aggiornato_il = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('filename_oa', 'codice', name='_oa_codice_data_uc'),)
 
 
class DiarioAccordiOA(db.Model):
    """Registro cronologico degli accordi verbali/scritti per un OA fornitore."""
    __tablename__ = 'diario_accordi_oa'
    id           = db.Column(db.Integer, primary_key=True)
    filename_oa  = db.Column(db.String(255), nullable=False, index=True)
    utente       = db.Column(db.String(50),  nullable=False, default='')
    testo        = db.Column(db.Text,        nullable=False)
    registrato_il = db.Column(db.DateTime,   default=datetime.utcnow)
 
 
class DiarioAccordiCliente(db.Model):
    """Registro cronologico degli accordi verbali/scritti per un ordine cliente."""
    __tablename__ = 'diario_accordi_cliente'
    id           = db.Column(db.Integer, primary_key=True)
    conferma     = db.Column(db.String(50),  nullable=False, index=True)
    utente       = db.Column(db.String(50),  nullable=False, default='')
    testo        = db.Column(db.Text,        nullable=False)
    registrato_il = db.Column(db.DateTime,   default=datetime.utcnow)
 
 
class FascicoloOrdine(db.Model):
    """Checklist documentale per ogni ordine cliente."""
    __tablename__ = 'fascicoli_ordini'
    id               = db.Column(db.Integer, primary_key=True)
    conferma         = db.Column(db.String(50), unique=True, nullable=False)  # es. "23/IS"
    cliente          = db.Column(db.String(200), default='')
    anno             = db.Column(db.String(4),   default='')
    # Documenti
    has_preventivo      = db.Column(db.Boolean, default=False)
    has_ordine_cliente  = db.Column(db.Boolean, default=False)
    has_co              = db.Column(db.Boolean, default=True)   # sempre True, la CO è il file caricato
    has_ddt_acquisto    = db.Column(db.Boolean, default=False)
    has_ddt_consegna    = db.Column(db.Boolean, default=False)
    # Path cartella fascicolo
    path_fascicolo      = db.Column(db.String(500), default='')
    # Priorità allocazione (1=primo, 2=secondo — basata su data caricamento CO)
    priorita            = db.Column(db.Integer, default=0)
    # Data consegna concordata col cliente (inserita manualmente, aggiornabile)
    data_consegna_cliente = db.Column(db.String(20), default='')
    # Stato generale calcolato (persistito per i task auto)
    stato_generale      = db.Column(db.String(50), default='')
    # Articoli per cui ignorare la BOM e acquistare il prodotto finito direttamente
    bom_bypass_json     = db.Column(db.Text, default='[]')
    # Timestamp
    creato_il           = db.Column(db.DateTime, default=datetime.utcnow)
    aggiornato_il       = db.Column(db.DateTime, default=datetime.utcnow)
 
 
class CaricoDDT(db.Model):
    """
    Traccia ogni DDT di arrivo fornitore elaborato con i movimenti di carico.
    Permette lo storno: sottrae lo stock aggiunto e ripristina gli ordinati.
    """
    __tablename__ = 'carichi_ddt'
    id            = db.Column(db.Integer, primary_key=True)
    filename_pdf  = db.Column(db.String(255), nullable=False)
    numero_ddt    = db.Column(db.String(50),  default='')
    fornitore     = db.Column(db.String(200), default='')
    data_ddt      = db.Column(db.String(20),  default='')
    movimenti_json= db.Column(db.Text, default='[]')  # [{codice, qta, stock_prima, stock_dopo, ordinati_prima, ordinati_dopo}]
    pdf_blob      = db.Column(db.LargeBinary, nullable=True)
    elaborato_il  = db.Column(db.DateTime, default=datetime.utcnow)
    stornato      = db.Column(db.Boolean, default=False)
    stornato_il   = db.Column(db.DateTime, nullable=True)


class ScaricoDDT(db.Model):
    """
    Traccia ogni DDT di consegna elaborato con i movimenti di scarico.
    Permette lo storno completo: reinserisce lo stock sottratto e cancella il record.
    """
    __tablename__ = 'scarichi_ddt'
    id            = db.Column(db.Integer, primary_key=True)
    filename_pdf  = db.Column(db.String(255), nullable=False)   # nome file PDF originale
    numero_ddt    = db.Column(db.String(50),  default='')       # numero DDT estratto dal PDF
    cliente       = db.Column(db.String(200), default='')
    conferma      = db.Column(db.String(50),  default='')
    data_ddt      = db.Column(db.String(20),  default='')
    movimenti_json= db.Column(db.Text, default='[]')            # JSON: [{codice, qta, stock_prima, stock_dopo}]
    pdf_blob      = db.Column(db.LargeBinary, nullable=True)    # PDF salvato come blob per poterlo eliminare/riscaricare
    elaborato_il  = db.Column(db.DateTime, default=datetime.utcnow)
    stornato      = db.Column(db.Boolean, default=False)
    stornato_il   = db.Column(db.DateTime, nullable=True)


class FascicoloDocumento(db.Model):
    """
    Documento PDF del fascicolo salvato direttamente nel DB come BLOB.
    Soluzione Railway-safe: nessun filesystem, tutto in PostgreSQL.
    """
    __tablename__ = 'fascicolo_documenti'
    id          = db.Column(db.Integer, primary_key=True)
    conferma    = db.Column(db.String(50), nullable=False, index=True)
    tipo_doc    = db.Column(db.String(50), nullable=False)
    filename    = db.Column(db.String(255), default='')
    contenuto   = db.Column(db.LargeBinary, nullable=False)
    caricato_il = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('conferma', 'tipo_doc', name='_conf_tipodoc_uc'),)
 
 
with app.app_context():
    db.create_all()
    for _tipo in ['clienti', 'fornitori']:
        for _slot in range(1, 7):
            if not SlotTotem.query.filter_by(tipo=_tipo, slot=_slot).first():
                db.session.add(SlotTotem(tipo=_tipo, slot=_slot, filename=''))
    db.session.commit()
 
    # ── Migrazione automatica colonne nuove ──────────────────────────────────
    # Aggiunge colonne mancanti senza distruggere dati esistenti.
    # Ogni ALTER è protetto da try/except: se la colonna esiste già, ignora.
    _migrations = [
        "ALTER TABLE data_evasione_articolo ADD COLUMN IF NOT EXISTS tipo_data VARCHAR(20) DEFAULT 'esatta'",
        "ALTER TABLE modifiche_ordine ADD COLUMN IF NOT EXISTS descrizione VARCHAR(255) DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS diario_accordi_cliente (
            id SERIAL PRIMARY KEY,
            conferma VARCHAR(50) NOT NULL,
            utente VARCHAR(50) NOT NULL DEFAULT '',
            testo TEXT NOT NULL,
            registrato_il TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_diario_accordi_cliente_conferma ON diario_accordi_cliente (conferma)",
    ]
    with db.engine.connect() as _conn:
        for _sql in _migrations:
            try:
                _conn.execute(db.text(_sql))
                _conn.commit()
            except Exception as _e:
                _conn.rollback()
                print(f"[MIGRA] skip: {_e}")
    # ────────────────────────────────────────────────────────────────────────
 
 
# ==============================================================================
# HELPER
# ==============================================================================
 
# ==============================================================================
# FASCICOLO DIGITALE — HELPERS
# ==============================================================================
 
def _slug(testo):
    """Converte stringa in nome-cartella sicuro."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(testo).strip())
 
 
def get_or_create_fascicolo(conferma, cliente=''):
    """Restituisce il FascicoloOrdine, creandolo se non esiste."""
    fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
    if not fasc:
        anno = str(date.today().year)
        slug_cliente  = _slug(cliente or 'SCONOSCIUTO')
        slug_conferma = _slug(conferma)
        path = os.path.join(FASCICOLI_ROOT, anno, slug_cliente, slug_conferma)
        # Crea sottocartelle
        for sub in ['04_Ordini_Fornitori', '05_DDT_Ingresso']:
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        fasc = FascicoloOrdine(
            conferma=conferma,
            cliente=cliente,
            anno=anno,
            path_fascicolo=path,
            has_co=True
        )
        db.session.add(fasc)
        db.session.commit()
        scrivi_log(f"FASCICOLO CREATO: {conferma} — {path}")
    return fasc
 
 
def fascicolo_to_dict(fasc):
    """Serializza il fascicolo per il template/JSON.
    La presenza dei documenti viene verificata sia sui flag booleani (retrocompatibilità)
    sia sulla tabella FascicoloDocumento (blob DB — Railway-safe).
    """
    if not fasc:
        return None
    # Leggi documenti blob presenti nel DB
    blob_tipi = {d.tipo_doc for d in FascicoloDocumento.query.filter_by(conferma=fasc.conferma).all()}
    docs = [
        {'key': 'preventivo',     'label': '01 · Preventivo',         'ok': fasc.has_preventivo     or 'preventivo'     in blob_tipi},
        {'key': 'ordine_cliente', 'label': '02 · Ordine Cliente',      'ok': fasc.has_ordine_cliente or 'ordine_cliente' in blob_tipi},
        {'key': 'co',             'label': '03 · Conferma Ordine',     'ok': fasc.has_co},
        {'key': 'ddt_acquisto',   'label': '05 · DDT Ingresso Merce',  'ok': fasc.has_ddt_acquisto   or 'ddt_acquisto'   in blob_tipi},
        {'key': 'ddt_consegna',   'label': '06 · DDT Consegna',        'ok': fasc.has_ddt_consegna   or 'ddt_consegna'   in blob_tipi},
    ]
    presenti  = sum(1 for d in docs if d['ok'])
    totale    = len(docs)
    perc      = int(presenti / totale * 100)
    return {
        'conferma': fasc.conferma,
        'docs':     docs,
        'presenti': presenti,
        'totale':   totale,
        'perc':     perc,
        'completo': presenti == totale,
    }
 
 
def scrivi_log(messaggio):
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {messaggio}\n")
    except Exception as e:
        print(f"Errore log: {e}")
 
 
def get_slots(tipo):
    records = SlotTotem.query.filter_by(tipo=tipo).order_by(SlotTotem.slot).all()
    result = [''] * 6
    for r in records:
        if 1 <= r.slot <= 6:
            result[r.slot - 1] = r.filename or ''
    return result
 
 
def set_slot(tipo, slot, filename):
    records = SlotTotem.query.filter_by(tipo=tipo).all()
    for r in records:
        if r.filename == filename:
            r.filename = ''
    if 1 <= slot <= 6:
        target = SlotTotem.query.filter_by(tipo=tipo, slot=slot).first()
        if target:
            target.filename = filename
    db.session.commit()
 
 
def get_magazzino():
    """Restituisce dict {sku: {...}} e anche {codice_esterno: sku} per il lookup."""
    return {a.sku: a.to_dict() for a in Articolo.query.all()}
 
 
def get_mappa_codici_esterni():
    """Restituisce dict {codice_esterno: sku_interno} per tradurre i codici PDF."""
    result = {}
    for a in Articolo.query.filter(Articolo.codice_esterno != '').all():
        if a.codice_esterno:
            result[a.codice_esterno.strip().upper()] = a.sku
    return result
 
 
def risolvi_codice(codice_raw, mappa_esterni):
    """
    Dato un codice letto dal PDF, restituisce il codice interno corrispondente.
    Prima cerca come codice interno, poi come codice esterno.
    """
    codice_up = codice_raw.strip().upper()
    # Prima prova come codice interno diretto
    art = Articolo.query.filter_by(sku=codice_raw.strip()).first()
    if art:
        return codice_raw.strip()
    # Poi prova come codice esterno
    return mappa_esterni.get(codice_up, codice_raw.strip())
 
 
def get_evasi():
    """Restituisce dict {id_art: qta_evasa}."""
    return {e.id_art: e.qta_evasa for e in ArticoloEvaso.query.all()}  # FIX: tutti i record, non solo evaso=True
 
 
def get_note():
    """Restituisce dict {conferma: nota}."""
    return {n.conferma: n.nota for n in NoteOrdine.query.all()}
 
 
@app.route('/salva_nota', methods=['POST'])
def salva_nota():
    """Salva o aggiorna la nota per una conferma ordine."""
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        nota     = data.get('nota', '').strip()
        existing = NoteOrdine.query.filter_by(conferma=conferma).first()
        if existing:
            existing.nota          = nota
            existing.aggiornata_il = datetime.utcnow()
        else:
            db.session.add(NoteOrdine(conferma=conferma, nota=nota))
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route('/api/data_consegna_cliente', methods=['POST'])
def api_data_consegna_cliente():
    """Salva o aggiorna la data di consegna concordata col cliente."""
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        data_val = data.get('data', '').strip()
        if not conferma:
            return jsonify({"ok": False, "error": "conferma mancante"}), 400
        fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
        if not fasc:
            fasc = get_or_create_fascicolo(conferma)
        fasc.data_consegna_cliente = data_val
        fasc.aggiornato_il         = datetime.utcnow()
        db.session.commit()
        # Calcola se ora è scaduto
        scaduto = False
        if data_val:
            try:
                _dp = data_val.split('/')
                _dc = date(int(_dp[2]), int(_dp[1]), int(_dp[0]))
                scaduto = _dc < date.today()
            except Exception:
                pass
        scrivi_log(f"DATA CONSEGNA CLIENTE: {conferma} → {data_val or 'cancellata'}")
        return jsonify({"ok": True, "scaduto": scaduto}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
def get_imballaggi():
    """Restituisce dict {conferma_codice: {qta_imballare, imballato}}."""
    result = {}
    for i in Imballaggio.query.all():
        key = f"{i.conferma}_{i.codice}".replace(" ", "")
        result[key] = {'qta_imballare': i.qta_imballare, 'imballato': i.imballato}
    return result
 
 
def aggiorna_storico_fornitori(codice, fornitore):
    storico = StoricoFornitore.query.filter_by(sku=codice).order_by(StoricoFornitore.posizione).all()
    if storico and storico[0].fornitore == fornitore:
        return
    for s in storico:
        if s.fornitore == fornitore:
            db.session.delete(s)
    db.session.flush()
    rimasti = StoricoFornitore.query.filter_by(sku=codice).order_by(StoricoFornitore.posizione).all()
    for i, s in enumerate(rimasti):
        s.posizione = i + 1
    db.session.add(StoricoFornitore(sku=codice, fornitore=fornitore, posizione=0))
    db.session.flush()
    tutti = StoricoFornitore.query.filter_by(sku=codice).order_by(StoricoFornitore.posizione).all()
    for old in tutti[3:]:
        db.session.delete(old)
    db.session.commit()
 
 
def get_storico_fornitori_dict():
    result = {}
    for s in StoricoFornitore.query.order_by(StoricoFornitore.sku, StoricoFornitore.posizione).all():
        result.setdefault(s.sku, []).append(s.fornitore)
    return result
 
 

# ==============================================================================
# DISTINTA BASE — ESPLOSIONE BOM MULTILIVELLO
# ==============================================================================

def get_distinta_map():
    """
    Carica tutta la distinta base in un dict:
    { codice_padre: [ {codice_figlio, quantita, livello, note}, ... ] }
    """
    distinta = {}
    try:
        for d in DistintaBase.query.order_by(DistintaBase.livello).all():
            distinta.setdefault(d.codice_padre, []).append({
                'codice':    d.codice_figlio,
                'quantita':  d.quantita,
                'livello':   d.livello,
                'note':      d.note or '',
            })
    except Exception:
        pass
    return distinta


def esplodi_bom(sku, qta_mancante, magazzino, distinta_map, _visitati=None, _livello=0):
    """
    Esplosione ricorsiva BOM per `qta_mancante` unità di `sku`.
    Copertura = stock fisico + ordinati (OA aperti).
    Stati possibili per ogni componente:
      DISPONIBILE  → stock fisico sufficiente
      ORDINATO     → non basta lo stock ma OA coprente (ordinati >= mancanti)
      PARZIALE     → stock+ordinati parzialmente sufficienti
      PRODUCIBILE  → ha BOM figli da cui costruirlo
      DA_ACQUISTARE → nessuna copertura e nessuna BOM
    """
    if _visitati is None:
        _visitati = set()
    if sku in _visitati or _livello > 5:
        return []
    _visitati = _visitati | {sku}

    def _stato_componente(stock, ordinati, oa_list, qta_nec, ha_bom):
        """Determina stato di un componente foglia considerando stock + OA."""
        disp_fisica  = min(stock, qta_nec)
        manca_fisica = qta_nec - disp_fisica
        if manca_fisica == 0:
            return 'DISPONIBILE', disp_fisica, 0
        # Verifica copertura con ordinati (OA aperti)
        copertura_oa = min(ordinati, manca_fisica)
        manca_totale = manca_fisica - copertura_oa
        if manca_totale == 0:
            # Tutto coperto da OA
            return 'ORDINATO', disp_fisica, 0
        if (disp_fisica + copertura_oa) > 0:
            # Copertura parziale stock+OA
            return 'PARZIALE', disp_fisica, manca_totale
        if ha_bom:
            return 'PRODUCIBILE', 0, qta_nec
        return 'DA_ACQUISTARE', 0, qta_nec

    figli_distinta = distinta_map.get(sku, [])
    if not figli_distinta:
        # Foglia senza BOM
        m        = magazzino.get(sku, {})
        stock    = m.get('stock', 0)
        ordinati = m.get('ordinati', 0)
        oa_list  = m.get('oa_dettaglio', [])
        stato, disp, manca = _stato_componente(stock, ordinati, oa_list, qta_mancante, False)
        return [{
            'codice':          sku,
            'desc':            m.get('desc', sku),
            'livello':         _livello,
            'qta_necessaria':  qta_mancante,
            'qta_disponibile': disp,
            'qta_mancante':    manca,
            'ordinati':        ordinati,
            'oa_dettaglio':    oa_list,
            'stato':           stato,
            'componenti':      [],
        }]

    # Ha BOM — esplodi i figli
    risultato = []
    for figlio in figli_distinta:
        cod_f      = figlio['codice']
        qta_f      = qta_mancante * figlio['quantita']
        m_f        = magazzino.get(cod_f, {})
        stock_f    = m_f.get('stock', 0)
        ordinati_f = m_f.get('ordinati', 0)
        oa_f       = m_f.get('oa_dettaglio', [])
        ha_bom_f   = bool(distinta_map.get(cod_f))

        stato_f, disp_f, manca_f = _stato_componente(
            stock_f, ordinati_f, oa_f, qta_f, ha_bom_f
        )

        # Se mancante e ha BOM → esplodi ricorsivamente per il delta
        if manca_f > 0 and ha_bom_f:
            stato_f = 'PRODUCIBILE' if disp_f == 0 and ordinati_f == 0 else stato_f
            sub = esplodi_bom(cod_f, manca_f, magazzino, distinta_map, _visitati, _livello + 1)
        else:
            sub = []

        risultato.append({
            'codice':          cod_f,
            'desc':            m_f.get('desc', cod_f),
            'livello':         _livello + 1,
            'qta_necessaria':  qta_f,
            'qta_disponibile': disp_f,
            'qta_mancante':    manca_f,
            'ordinati':        ordinati_f,
            'oa_dettaglio':    oa_f,
            'stato':           stato_f,
            'componenti':      sub,
        })
    return risultato


def appiattisci_bom(componenti, risultato=None):
    """
    Converte la struttura ad albero BOM in lista piatta
    mantenendo l'ordine gerarchico (per il template Jinja).
    """
    if risultato is None:
        risultato = []
    for c in componenti:
        risultato.append(c)
        if c.get('componenti'):
            appiattisci_bom(c['componenti'], risultato)
    return risultato

def calcola_stato_articolo(qta_richiesta, qta_evasa, stock, ordinati, data_evasione_str, imballato=False, qta_imballare=0, oa_confermato=False):
    """
    Calcola lo stato di un articolo per la card espansa.
    Priorità: EVASO → IMBALLATO → IN_ARRIVO → RITIRABILE → ORDINE_CONFERMATO → ORDINATO_NC → DA_ORDINARE
    """
    qta = int(qta_richiesta)
    evasa = int(qta_evasa or 0)
 
    if evasa >= qta:
        return 'EVASO'
 
    if imballato:
        return 'IMBALLATO'
 
    if qta_imballare > 0:
        return 'DA_IMBALLARE'
 
    if stock >= (qta - evasa):
        return 'ORDINE_CONFERMATO'
 
    if ordinati >= (qta - evasa):
        if data_evasione_str:
            try:
                data_ev = datetime.strptime(data_evasione_str, '%d/%m/%Y').date()
                oggi    = date.today()
                delta   = (data_ev - oggi).days
                if delta <= 0:
                    return 'RITIRABILE'
                elif delta <= 2:
                    return 'IN_ARRIVO'
                else:
                    return 'ORDINE_CONFERMATO'
            except Exception:
                pass
        # BUG FIX: se l'OA è confermato dall'utente → ORDINE_CONFERMATO anche senza data
        if oa_confermato:
            return 'ORDINE_CONFERMATO'
        return 'ORDINATO_NC'
 
    if (stock + ordinati) >= (qta - evasa):
        # BUG FIX: se OA confermato anche qui mostra confermato
        if oa_confermato:
            return 'ORDINE_CONFERMATO'
        return 'ORDINATO_NC'
 
    return 'DA_ORDINARE'
 
 
def calcola_stato_generale(articoli):
    """
    Calcola lo stato generale della card seguendo la CLASSIFICA RIGIDA:
    Lo stato più critico comanda sempre, anche se riguarda un solo articolo.
 
    CLASSIFICA (1 = comanda su tutto):
    1. ORDINARE_ARTICOLI  : almeno 1 articolo senza copertura (no stock, no OA sufficiente)
    2. MANCANO_CONFERME   : tutti coperti da OA, ma almeno 1 OA non ancora confermato
    3. ORDINI_CONFERMATI  : tutti OA confermati, nessuno ancora fisicamente in transito
    4. ORDINI_IN_ARRIVO   : almeno 1 OA fisicamente in transito (🚢/🚚/📦)
    5. EVADIBILE          : tutto fisicamente in casa, pronto per imballaggio/evasione
    6. EVASO              : tutti gli articoli evasi
 
    Priorità speciali (fuori classifica):
    - DA_IMBALLARE        : almeno 1 articolo in fase imballaggio (gestito prima della classifica)
    """
    non_evasi = [a for a in articoli if not a.get('evaso')]
    if not non_evasi:
        return 'EVASO'
 
    # FUORI CLASSIFICA: imballaggio in corso comanda su tutto
    if any(a.get('stato_ordine') in ('DA_IMBALLARE', 'IMBALLATO') for a in non_evasi):
        return 'DA_IMBALLARE'
 
    def qta_res(a):
        return max(0, int(a.get('qta', 0)) - int(a.get('qta_evasa') or 0))
 
    # Helper: OA è confermato (qualsiasi stato post-conferma: ✅ 🚢 🚚 📦)
    def oa_e_confermato(oa):
        return any(x in oa.get('stato_oa', '') for x in ['✅', '🚢', '🚚', '📦'])
 
    # Helper: OA è fisicamente in transito (data imminente: 🚢 IN ARRIVO / 🚚 DA RITIRARE / 📦 IN CONSEGNA)
    def oa_in_transito(oa):
        return any(x in oa.get('stato_oa', '') for x in ['🚢', '🚚', '📦'])
 
    # Per ogni articolo non evaso, determina se è "coperto" (stock+OA bastano)
    # e se il suo OA è confermato / in transito
    ha_articolo_da_ordinare   = False  # almeno 1 articolo senza copertura totale
    ha_oa_non_confermato      = False  # almeno 1 OA presente ma non confermato
    ha_oa_in_transito         = False  # almeno 1 OA fisicamente in transito
    tutti_in_casa             = True   # tutti coperti da stock fisico
    ha_assemblabile           = False  # almeno 1 articolo assemblabile (BOM componenti in casa)
 
    for a in non_evasi:
        qr = qta_res(a)
        if qr == 0:
            continue  # articolo di fatto già evaso, salta
 
        in_casa    = a.get('in_casa', 0)
        in_arrivo  = a.get('in_arrivo_tot', 0)
        mancanti   = a.get('mancanti', 0)
        oa_list    = a.get('oa_dettaglio', [])
 
        # Fisicamente in casa?
        if in_casa < qr:
            tutti_in_casa = False
 
        # Copertura totale = in_casa + in_arrivo_tot >= qr
        coperto = (in_casa + in_arrivo) >= qr
 
        if not coperto:
            # FIX: l'allocazione FIFO può dire "non coperto" perché un ordine più
            # prioritario ha già consumato gli ordinati_residuo, ma l'articolo ha
            # comunque OA confermati in StoricoOrdineFornitore. Se la qta totale
            # degli OA attivi + in_casa copre il residuo, non è da ordinare.
            oa_list_art = a.get('oa_dettaglio', [])
            qta_oa_totale = sum(int(oa.get('qta', 0)) for oa in oa_list_art)
            if (in_casa + qta_oa_totale) >= qr:
                # Coperto dagli OA reali — aggiorna flags come se fosse nel ramo coperto
                tutti_in_casa = False
                for oa in oa_list_art:
                    if oa_in_transito(oa):
                        ha_oa_in_transito = True
                    elif not oa_e_confermato(oa):
                        ha_oa_non_confermato = True
            else:
                # Controlla se la mancanza è coperta da BOM (componenti producibili/disponibili)
                bom_comp = a.get('bom_componenti', [])
                bom_tutti_ok = bom_comp and all(
                    c['stato'] in ('DISPONIBILE', 'ORDINATO', 'PRODUCIBILE') for c in bom_comp
                )
                if not bom_tutti_ok:
                    ha_articolo_da_ordinare = True
                else:
                    # BOM copre — propaga flags dagli OA dei componenti
                    tutti_in_casa = False  # i componenti non sono fisicamente nel prodotto finito
                    # Tutti i componenti disponibili senza OA = assemblabile
                    if all(c['stato'] in ('DISPONIBILE', 'PRODUCIBILE') for c in bom_comp) and \
                       not any(c.get('oa_dettaglio') for c in bom_comp):
                        ha_assemblabile = True
                    for comp in bom_comp:
                        for oa in comp.get('oa_dettaglio', []):
                            if oa_in_transito(oa):
                                ha_oa_in_transito = True
                            elif not oa_e_confermato(oa):
                                ha_oa_non_confermato = True
        else:
            # Coperto da stock/OA diretti — verifica stato degli OA collegati
            if oa_list:
                for oa in oa_list:
                    if oa_in_transito(oa):
                        ha_oa_in_transito = True
                    elif not oa_e_confermato(oa):
                        ha_oa_non_confermato = True
            # Se in_casa >= qr ma c'è un OA aperto non confermato per questo articolo
            # (OA emesso in eccesso o per scorte) — non blocca la classifica
 
    # ── APPLICA LA CLASSIFICA RIGIDA ──────────────────────────────────────────
    # 1. Se anche UN SOLO articolo è senza copertura → ORDINARE ARTICOLI
    if ha_articolo_da_ordinare:
        return 'ORDINARE_ARTICOLI'
 
    # 2. Tutti coperti ma almeno 1 OA non confermato → MANCANO CONFERME
    if ha_oa_non_confermato:
        return 'MANCANO_CONFERME'
 
    # 3. Tutti OA confermati, almeno 1 fisicamente in transito → ORDINI IN ARRIVO
    if ha_oa_in_transito:
        return 'ORDINI_IN_ARRIVO'
 
    # 4. Tutti OA confermati, nessuno ancora in transito → ORDINI CONFERMATI
    # (controlla se esiste almeno un OA — altrimenti si è in casa)
    ha_qualche_oa = any(a.get('oa_dettaglio') for a in non_evasi)
    if ha_qualche_oa and not tutti_in_casa:
        return 'ORDINI_CONFERMATI'
 
    # 5. Almeno 1 articolo assemblabile (BOM pronto) → ASSEMBLABILE
    if ha_assemblabile:
        return 'ASSEMBLABILE'

    # 6. Tutto fisicamente in casa → EVADIBILE
    return 'EVADIBILE'
 
 
# ==============================================================================
# ESTRAZIONE PDF
# ==============================================================================
 
def estrai_dati_completi_pdf(filename, mappa_esterni=None):
    dati = {"conferma": "N/D", "cliente": "Sconosciuto", "data": "N/D", "articoli": []}
    if mappa_esterni is None:
        mappa_esterni = get_mappa_codici_esterni()
    try:
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(path):
            return dati
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo = "".join([page.extract_text() for page in reader.pages])
            match_conf = re.search(r'(\d+)\s*/\s*IS', testo)
            if match_conf:
                dati['conferma'] = f"{match_conf.group(1)}/IS"
            match_data = re.search(r'(\d{2}/\d{2}/\d{4})', testo)
            if match_data:
                dati['data'] = match_data.group(1)
            if "Destinatario:" in testo:
                parte_dest = testo.split("Destinatario:")[1]
                linee = [l.strip() for l in parte_dest.split('\n') if l.strip()]
                if len(linee) >= 2:
                    dati['cliente'] = linee[1]
            for riga in testo.split('\n'):
                match_art = re.search(r'^([A-Z0-9\.\-]+)\s+(.*?)\s+n\.\s+([\d\.]+)', riga)
                if match_art and match_art.group(1) not in ['IRON', 'Cod.', 'Vs.', 'Pag.']:
                    codice_raw = match_art.group(1)
                    codice_interno = risolvi_codice(codice_raw, mappa_esterni)
                    dati['articoli'].append({
                        "codice":          codice_interno,
                        "codice_originale": codice_raw,   # ← conserva il codice del PDF
                        "desc":            match_art.group(2).strip(),
                        "qta":             match_art.group(3).replace('.', '').split(',')[0]
                    })
    except Exception as e:
        print(f"Errore estrazione PDF conferma {filename}: {e}")
    return dati
 
 
def estrai_dati_completi_pdf_carico(filename):
    """Estrae articoli da DDT carico fornitore. Risolve codici fornitore in SKU interni."""
    dati = {"articoli": [], "fornitore": "", "numero_ddt": "", "data_ddt": "", "tipo_ddt": "acquisto"}
    path = os.path.join(CARICO_FOLDER, filename)
    mappa_esterni = get_mappa_codici_esterni()
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo = "".join([page.extract_text() for page in reader.pages])
        linee = [l.strip() for l in testo.split('\n') if l.strip()]
        # ── RILEVAMENTO TIPO DDT ─────────────────────────────────────────────
        # Marker affidabili estratti dai PDF reali Iron Segnaletica:
        #   DDT ACQUISTO (fornitore → Iron): "DDT - Entrata Merci" + "Ns. doc.(ORDFO)"
        #   DDT VENDITA  (Iron → cliente):  "D.D.T. - Uscita Merci" + "Ns. doc.(ORDCL)"
        e_vendita = (
            'Uscita Merci' in testo or
            'ORDCL' in testo or
            bool(re.search(r'Ns\.\s*doc\.\(ORDCL\)', testo))
        )
        e_acquisto = (
            'Entrata Merci' in testo or
            'ORDFO' in testo or
            bool(re.search(r'Ns\.\s*doc\.\(ORDFO\)', testo))
        )
        if e_vendita and not e_acquisto:
            dati['tipo_ddt'] = 'vendita'
            return dati
        # ── ESTRAZIONE DATI DDT ACQUISTO ────────────────────────────────────────
        m_ddt = re.search(r'(\d+)\s*/\s*DDT', testo)
        if m_ddt: dati['numero_ddt'] = m_ddt.group(1)
        m_data = re.search(r'(\d{2}/\d{2}/\d{4})', testo)
        if m_data: dati['data_ddt'] = m_data.group(1)
        if "Destinatario:" in testo:
            parte = testo.split("Destinatario:")[1]
            ll = [l.strip() for l in parte.split('\n') if l.strip()]
            if len(ll) >= 2:
                dati['fornitore'] = ll[1] if ll[0].isdigit() else ll[0]
        skip = {'IRON', 'Cod.', 'Vs.', 'Pag.', 'N.'}
        for riga in linee:
            match_art = re.search(r'([A-Z0-9][A-Z0-9.-]+)\s+(.*?)\s+n\.\s+([\d\.]+)(?:,\d+)?', riga)
            if match_art and match_art.group(1) not in skip:
                codice_raw = match_art.group(1)
                codice_interno = risolvi_codice(codice_raw, mappa_esterni)
                dati['articoli'].append({
                    "codice":           codice_interno,
                    "codice_originale": codice_raw,
                    "desc":             match_art.group(2).strip(),
                    "qta":              match_art.group(3).replace('.', '').split(',')[0]
                })
    except Exception as e:
        print(f"Errore estrazione DDT carico {filename}: {e}")
    return dati
 
 
def estrai_dati_ddt_consegna(filename):
    """Estrae articoli+qta da un DDT di consegna cliente (uscita merci)."""
    dati = {"articoli": [], "conferma": "", "cliente": "", "numero_ddt": "", "data_ddt": "", "tipo_ddt": "vendita"}
    path = os.path.join(CONSEGNA_FOLDER, filename)
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo = "\n".join(page.extract_text() or '' for page in reader.pages)
        # ── RILEVAMENTO TIPO DDT ─────────────────────────────────────────────
        # Marker affidabili dai PDF reali Iron Segnaletica:
        #   DDT VENDITA  (uscita → cliente):  "Uscita Merci" / "ORDCL"
        #   DDT ACQUISTO (entrata fornitore): "Entrata Merci" / "ORDFO"
        e_acquisto = (
            'Entrata Merci' in testo or
            'ORDFO' in testo or
            bool(re.search(r'Ns\.\s*doc\.\(ORDFO\)', testo))
        )
        e_vendita = (
            'Uscita Merci' in testo or
            'ORDCL' in testo or
            bool(re.search(r'Ns\.\s*doc\.\(ORDCL\)', testo))
        )
        if e_acquisto and not e_vendita:
            dati['tipo_ddt'] = 'acquisto'
            return dati

        # Numero DDT
        m = re.search(r'Numero documento\s+(\d+)', testo)
        if m: dati['numero_ddt'] = m.group(1)
 
        # Data DDT
        m = re.search(r'Data documento\s+(\d{2}/\d{2}/\d{4})', testo)
        if m: dati['data_ddt'] = m.group(1)
 
        # Numero conferma ordine cliente (ORDCL)
        m = re.search(r'Ns\.\s*doc\.\(ORDCL\)\s*n\.:?(\d+)', testo)
        if m: dati['conferma'] = m.group(1) + '/IS'
 
        # Cliente (riga dopo "Destinatario:")
        linee = [l.strip() for l in testo.split('\n') if l.strip()]
        for i, l in enumerate(linee):
            if 'Destinatario:' in l and i + 1 < len(linee):
                # Salta se è solo un numero (codice cliente)
                candidato = linee[i+1]
                if candidato.isdigit() and i + 2 < len(linee):
                    candidato = linee[i+2]
                dati['cliente'] = candidato
                break
 
        # Articoli: pattern  CODICE   DESCRIZIONE   n.   QTA
        skip = {'IRON', 'Cod.', 'Vs.', 'Pag.', 'N.', 'Del.', 'Trasporti'}
        pattern = re.compile(
            r'([A-Z0-9][A-Z0-9\.\-]+)\s+(.+?)\s+n\.\s+([\d\.]+)(?:,\d+)?')
        for riga in linee:
            m = pattern.search(riga)
            if m and m.group(1) not in skip:
                qta_str = m.group(3).replace('.', '')
                try:
                    qta = int(qta_str)
                except ValueError:
                    continue
                if qta > 0:
                    dati['articoli'].append({
                        'codice': m.group(1),
                        'desc':   m.group(2).strip(),
                        'qta':    qta
                    })
    except Exception as e:
        print(f"Errore estrazione DDT consegna {filename}: {e}")
    return dati
 
 
def estrai_dati_ordine_acquisto_da_path(path):
    import PyPDF2 as _PyPDF2, re as _re
    dati = {"fornitore": "Sconosciuto", "ordine_n": "N/D", "articoli": []}
    if not os.path.exists(path):
        return dati
    try:
        with open(path, 'rb') as f:
            reader = _PyPDF2.PdfReader(f)
            testo_completo = "\n".join(page.extract_text() or '' for page in reader.pages)
        linee = [l.strip() for l in testo_completo.split('\n') if l.strip()]
        for i, linea in enumerate(linee):
            if "Numero documento" in linea:
                for offset in range(1, 4):
                    if i + offset < len(linee):
                        m = _re.search(r'^(\d+)(\s*/OF)?$', linee[i + offset])
                        if m:
                            dati['ordine_n'] = m.group(1)
                            break
                if dati['ordine_n'] != "N/D":
                    break
        if dati['ordine_n'] == 'N/D':
            m = _re.search(r'(\d+)\s*/OF', testo_completo)
            if m:
                dati['ordine_n'] = m.group(1)
        for i, linea in enumerate(linee):
            if "Destinatario:" in linea and i + 2 < len(linee):
                dati['fornitore'] = linee[i + 2] if linee[i + 1].isdigit() else linee[i + 1]
                break
        pattern = _re.compile(
            r'([A-Z0-9][A-Z0-9.-]+)\s+(.+?)\s+n\.\s+([\d\.]+)(?:,\d+)?(?:\s+(\d{2}/\d{2}/\d{4}))?')
        skip = {'IRON', 'Cod.', 'Vs.', 'Pag.', 'N.', 'Del.'}
        for linea in linee:
            m = pattern.search(linea)
            if m and m.group(1) not in skip:
                dati['articoli'].append({
                    "codice": m.group(1),
                    "descrizione": m.group(2).strip(),
                    "qta": m.group(3).replace('.', ''),
                    "data_evasione": m.group(4) or '',
                })
    except Exception as e:
        print(f"Errore estrazione OA da path {path}: {e}")
    return dati
 
 
def estrai_dati_ordine_acquisto(filename):
    dati = {"fornitore": "Sconosciuto", "ordine_n": "N/D", "articoli": []}
    path = os.path.join(ACQUISTI_DONE_FOLDER, filename)
    if not os.path.exists(path):
        return dati
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo_completo = "\n".join(page.extract_text() for page in reader.pages)
        linee = [l.strip() for l in testo_completo.split('\n') if l.strip()]
 
        # 1. Numero ordine (es. "87 /OF" oppure "Numero documento ... 87")
        for i, linea in enumerate(linee):
            if "Numero documento" in linea:
                for offset in range(1, 4):
                    if i + offset < len(linee):
                        match_n = re.search(r'^(\d+)(\s*/OF)?$', linee[i + offset])
                        if match_n:
                            dati['ordine_n'] = match_n.group(1)
                            break
                if dati['ordine_n'] != "N/D":
                    break
        # Fallback: cerca pattern "NN /OF" nell'intestazione
        if dati['ordine_n'] == 'N/D':
            match_of = re.search(r'(\d+)\s*/OF', testo_completo)
            if match_of:
                dati['ordine_n'] = match_of.group(1)
 
        # 2. Fornitore (da "Destinatario:")
        for i, linea in enumerate(linee):
            if "Destinatario:" in linea and i + 2 < len(linee):
                dati['fornitore'] = linee[i + 2] if linee[i + 1].isdigit() else linee[i + 1]
                break
 
        # 3. Articoli — NUOVO: cattura codice, descrizione, quantità e data evasione
        # Formato riga OA: CODICE  DESCRIZIONE  n.  QTA,000  DD/MM/YYYY  IMPORTO
        for linea in linee:
            # Pattern completo: CODICE DESCRIZIONE n. QTA,000 DD/MM/YYYY [importo]
            match_art = re.search(
                r'([A-Z0-9][A-Z0-9.-]+)\s+(.+?)\s+n\.\s+([\d\.]+)(?:,\d+)?(?:\s+(\d{2}/\d{2}/\d{4}))?',
                linea
            )
            if match_art and match_art.group(1) not in ['IRON', 'Cod.', 'Vs.', 'Pag.', 'N.']:
                dati['articoli'].append({
                    "codice":        match_art.group(1),
                    "descrizione":   match_art.group(2).strip(),
                    "qta":           match_art.group(3).replace('.', ''),
                    "data_evasione": match_art.group(4) or '',
                })
 
        # Arricchisce ogni articolo con i dati salvati in DB (data, tipo, presunta)
        for art in dati['articoli']:
            dea = DataEvasioneArticolo.query.filter_by(
                filename_oa=filename, codice=art['codice']).first()
            if dea:
                art['data_art']    = dea.data or ''
                art['is_presunta'] = dea.is_presunta if dea.is_presunta is not None else False
                try:
                    art['tipo_data'] = dea.tipo_data or 'esatta'
                except Exception:
                    art['tipo_data'] = 'presunta' if art['is_presunta'] else 'esatta'
            else:
                art['data_art']    = ''
                art['is_presunta'] = False
                art['tipo_data']   = 'esatta'
 
        # 4. Riferimento conferma d'ordine fornitore (campo "Rif. Des. Fornitore")
        dati['rif_fornitore'] = ''
        match_rif = re.search(
            r'Rif\.?\s*Des\.?\s*Fornitore[.:\s]*([^\n]+)',
            testo_completo, re.IGNORECASE
        )
        if match_rif:
            rif_val = match_rif.group(1).strip()
            # Tronca eventuale testo extra dopo spazio/tab lungo
            rif_val = re.split(r'\s{2,}|\t', rif_val)[0].strip()
            if rif_val:
                dati['rif_fornitore'] = rif_val
 
    except Exception as e:
        print(f"Errore estrazione OA {filename}: {e}")
    return dati
 
 
# ==============================================================================
# EVADIBILITÀ
# ==============================================================================
 
def calcola_percentuale_evadibilita(articoli, magazzino, allocazione_ordine=None):
    """
    Calcola % evadibilità usando l'allocazione per priorità se disponibile,
    altrimenti lo stock grezzo (fallback per compatibilità).
    allocazione_ordine = {codice: {'in_casa': x, 'in_arrivo': y}} per questo ordine.
    """
    totale_richiesti  = 0
    totale_disponibili = 0
    for art in articoli:
        try:
            qta_evasa    = int(art.get('qta_evasa') or 0)
            qta_richiesta = max(0, int(art['qta']) - qta_evasa)
            totale_richiesti += qta_richiesta
            if allocazione_ordine is not None:
                # Usa in_casa dall'allocazione per priorità
                in_casa = allocazione_ordine.get(art['codice'], {}).get('in_casa', 0)
            else:
                # Fallback: stock grezzo
                in_casa = magazzino.get(art['codice'], {}).get('stock', 0)
            totale_disponibili += min(qta_richiesta, in_casa)
        except (ValueError, KeyError):
            continue
    if totale_richiesti == 0:
        return 100
    return round((totale_disponibili / totale_richiesti) * 100)
 
 
# ==============================================================================
# ROTTE
# ==============================================================================
 
@app.route('/')
def index():
    oa_files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER) if f.lower().endswith('.pdf')] \
               if os.path.exists(ACQUISTI_DONE_FOLDER) else []
 
    magazzino      = get_magazzino()
    distinta_map   = get_distinta_map()   # BOM multilivello
    evasi          = get_evasi()
    imballaggi     = get_imballaggi()
    note           = get_note()
    mappa_esterni  = get_mappa_codici_esterni()

    # Pre-carica bom_bypass per tutti i fascicoli (codici per cui ignorare la BOM)
    bom_bypass_map = {}
    try:
        for _fasc_bp in FascicoloOrdine.query.all():
            if _fasc_bp.bom_bypass_json:
                bom_bypass_map[_fasc_bp.conferma] = set(json.loads(_fasc_bp.bom_bypass_json))
    except Exception:
        pass
 
    # ── Priorità ordini: carica dal DB (ordinato per priorita, poi data) ──
    try:
        _fascicoli_all = FascicoloOrdine.query.order_by(
            FascicoloOrdine.priorita, FascicoloOrdine.creato_il
        ).all()
        fascicoli_priorita = {
            f.conferma: (f.priorita or int((f.creato_il or datetime.utcnow()).timestamp()))
            for f in _fascicoli_all
        }
    except Exception:
        db.session.rollback()
        fascicoli_priorita = {}
 
    # Ordina files per priorità
    all_files_raw = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')]
    _conf_cache = {}
    for _fname in all_files_raw:
        try:
            _ii = estrai_dati_completi_pdf(_fname, mappa_esterni)
            _conf_cache[_fname] = _ii.get('conferma', '')
        except Exception:
            _conf_cache[_fname] = ''
    all_files = sorted(all_files_raw,
        key=lambda fn: fascicoli_priorita.get(_conf_cache.get(fn, ''), 999999))
 
    # ── Precalcola impegno totale E allocazione per priorità ──
    impegni_totali   = {}
    stock_residuo    = {sku: m.get('stock', 0)    for sku, m in magazzino.items()}
    ordinati_residuo = {sku: m.get('ordinati', 0) for sku, m in magazzino.items()}
    allocazione      = {}  # {filename: {codice: {in_casa, in_arrivo, mancanti}}}
 
    for _f in all_files:
        _info   = estrai_dati_completi_pdf(_f, mappa_esterni)
        _conf_i = _info['conferma']
        _mods_i = {m.codice: m.qta_nuova for m in ModificaOrdine.query.filter_by(conferma=_conf_i).all()}
        allocazione[_f] = {}
        for _art in _info['articoli']:
            _qm_i = _mods_i.get(_art['codice'])
            if _qm_i is not None:
                if _qm_i == 0:
                    continue
                _art['qta'] = str(_qm_i)
            _id      = f"{_conf_i}_{_art['codice']}".replace(" ", "")
            _evasa   = evasi.get(_id, 0)
            _residua = max(0, int(_art['qta']) - _evasa)
            impegni_totali[_art['codice']] = impegni_totali.get(_art['codice'], 0) + _residua
            # Allocazione
            sku       = _art['codice']
            in_casa   = min(max(0, stock_residuo.get(sku, 0)), _residua)
            mancanti  = _residua - in_casa
            in_arrivo = min(max(0, ordinati_residuo.get(sku, 0)), mancanti)
            stock_residuo[sku]    = stock_residuo.get(sku, 0) - in_casa
            ordinati_residuo[sku] = ordinati_residuo.get(sku, 0) - in_arrivo
            allocazione[_f][sku]  = {
                'in_casa':   in_casa,
                'in_arrivo': in_arrivo,
                'mancanti':  max(0, mancanti - in_arrivo),
            }
 
    # ── OA dettaglio per codice (tutti gli OA attivi con stato dal DB) ──
    oa_per_codice = {}
    try:
        for r in StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).all():
            _stato_oa = StatoOrdineAcquisto.query.filter_by(filename=r.filename_oa).first()
            _data_arr = _stato_oa.data_consegna if _stato_oa else ''
            if _stato_oa and _stato_oa.confermato:
                # Distingui in base a data
                _data_c = _stato_oa.data_consegna or ''
                _ritiro = _stato_oa.ritiro_proprio if _stato_oa else False
                try:
                    from datetime import date as _date
                    _dp = _data_c.split('/')
                    _dc = _date(int(_dp[2]), int(_dp[1]), int(_dp[0]))
                    _dlt = (_dc - _date.today()).days
                    if _dlt <= 0:
                        _stato_lbl = '🚚 DA RITIRARE' if _ritiro else '📦 IN CONSEGNA'
                    elif _dlt <= 2:
                        _stato_lbl = '🚚 DA RITIRARE' if _ritiro else '🚢 IN ARRIVO'
                    else:
                        _stato_lbl = '✅ IN ATTESA'
                except Exception:
                    _stato_lbl = '✅ IN ATTESA'
            else:
                _stato_lbl = '🔄 IN LAVORAZIONE'
            oa_per_codice.setdefault(r.codice, []).append({
                'oa_n':       r.id_ordine,
                'fornitore':  r.fornitore or '—',
                'qta':        r.qta,
                'data_arr':   _data_arr,
                'stato_oa':   _stato_lbl,
                'filename_oa': r.filename_oa,
            })
    except Exception:
        db.session.rollback()
 
    # ── Arricchisci magazzino con oa_dettaglio per l'esplosione BOM ──
    for _sku, _oa_list in oa_per_codice.items():
        if _sku in magazzino:
            magazzino[_sku]['oa_dettaglio'] = _oa_list

    active_slots     = get_slots('clienti')
    active_solleciti = get_slots('fornitori')
 
    ordini_acquisto = []
    for f in oa_files:
        info = estrai_dati_ordine_acquisto(f)
        info['filename'] = f
        info['in_slots'] = [i + 1 for i, name in enumerate(active_solleciti) if name == f]
        ordini_acquisto.append(info)
 
    consegne, in_essere = [], []
    for f in all_files:  # ordinato per priorità
        info = estrai_dati_completi_pdf(f, mappa_esterni)
        # Applica eventuali modifiche manuali all'ordine
        _conf_ord = info['conferma']
        _mods_ord = {m.codice: m.qta_nuova for m in ModificaOrdine.query.filter_by(conferma=_conf_ord).all()}
        if _mods_ord:
            _arts_filt = []
            for _ao in info['articoli']:
                _qmo = _mods_ord.get(_ao['codice'])
                if _qmo is not None:
                    if _qmo == 0:
                        continue  # articolo rimosso dall'ordine
                    _ao['qta'] = str(_qmo)
                _arts_filt.append(_ao)
            info['articoli'] = _arts_filt
        _alloc_ord = allocazione.get(f, {})
        info.update({
            'filename':    f,
            'evadibilita': calcola_percentuale_evadibilita(info['articoli'], magazzino, _alloc_ord)
        })
        info['in_slots'] = [i + 1 for i, name in enumerate(active_slots) if name == f]
 
        for art in info['articoli']:
            id_art    = f"{info['conferma']}_{art['codice']}".replace(" ", "")
            qta_evasa = evasi.get(id_art, 0)
            imb       = imballaggi.get(id_art, {})
 
            art['evaso']         = qta_evasa >= int(art['qta'])
            art['qta_evasa']     = qta_evasa
            art['qta_imballare'] = imb.get('qta_imballare', 0)
            art['imballato']     = imb.get('imballato', False)
 
            m = magazzino.get(art['codice'], {})
            art['stock']         = m.get('stock', 0)
            art['ordinati']      = m.get('ordinati', 0)
            art['fornitore']     = m.get('fornitore', 'N/D')
            art['ordine_n']      = m.get('ordine_n', 'N/D')
            art['data_evasione'] = m.get('data_evasione', '')
            # Allocazione per priorità
            _alloc = allocazione.get(f, {}).get(art['codice'], {})
            art['in_casa']      = _alloc.get('in_casa', 0)
            art['in_arrivo_tot']= _alloc.get('in_arrivo', 0)
            art['mancanti']     = _alloc.get('mancanti', 0)
            # OA dettaglio inline
            art['oa_dettaglio'] = oa_per_codice.get(art['codice'], [])
            # Dispo netta legacy (per compatibilità con altri calcoli)
            qta_residua_ordine = max(0, int(art['qta']) - int(art['qta_evasa'] or 0))
            impegni_altri      = impegni_totali.get(art['codice'], 0) - qta_residua_ordine
            art['dispo_netta'] = m.get('stock', 0) + m.get('ordinati', 0) - impegni_altri
 
            # Controlla se esiste un OA confermato per questo articolo
            _oa_confermato = False
            try:
                _storico = StoricoOrdineFornitore.query.filter_by(codice=art['codice']).first()
                if _storico and _storico.filename_oa:
                    _stato_oa = StatoOrdineAcquisto.query.filter_by(filename=_storico.filename_oa).first()
                    if _stato_oa:
                        _oa_confermato = bool(_stato_oa.confermato)
            except Exception:
                pass
            art['stato_ordine'] = calcola_stato_articolo(
                art['qta'],
                art['qta_evasa'],
                art['stock'],
                art['ordinati'],
                art['data_evasione'],
                art['imballato'],
                art['qta_imballare'],
                oa_confermato=_oa_confermato
            )

            # ── ESPLOSIONE BOM ────────────────────────────────────────────────
            # Se l'articolo non è evaso e ha mancanti E ha una distinta base,
            # esplodi i componenti per la quantità mancante
            art['bom_componenti'] = []
            art['ha_distinta']    = art['codice'] in distinta_map
            _bom_bypass_set       = bom_bypass_map.get(info.get('conferma', ''), set())
            art['bom_bypassed']   = art['codice'] in _bom_bypass_set  # per template: mostra pulsante
            if not art.get('evaso') and art['ha_distinta'] and not art['bom_bypassed']:
                qta_res_bom = max(0, int(art['qta']) - int(art.get('qta_evasa') or 0))
                qta_mancante_bom = max(0, qta_res_bom - art.get('in_casa', 0))
                if qta_mancante_bom > 0:
                    albero = esplodi_bom(
                        art['codice'],
                        qta_mancante_bom,
                        magazzino,
                        distinta_map
                    )
                    art['bom_componenti'] = appiattisci_bom(albero)

                    # Aggiorna stato_ordine del PADRE in base allo stato peggiore dei componenti
                    # Solo se il padre non è già evaso/imballato
                    if art['stato_ordine'] not in ('EVASO', 'IMBALLATO', 'DA_IMBALLARE'):
                        stati_comp = [c['stato'] for c in art['bom_componenti']]
                        if 'DA_ACQUISTARE' in stati_comp:
                            art['stato_ordine'] = 'DA_ORDINARE'
                        elif 'PARZIALE' in stati_comp:
                            # Parziale: stock+OA non bastano del tutto
                            art['stato_ordine'] = 'ORDINATO_NC'
                        elif 'ORDINATO' in stati_comp:
                            # Tutti componenti coperti da OA
                            # Verifica se OA è confermato
                            oa_confermati = all(
                                any(x in oa.get('stato_oa','') for x in ['✅','🚢','🚚','📦'])
                                for c in art['bom_componenti']
                                for oa in c.get('oa_dettaglio', [])
                            ) if any(c.get('oa_dettaglio') for c in art['bom_componenti']) else False
                            in_transito = any(
                                any(x in oa.get('stato_oa','') for x in ['🚢','🚚','📦'])
                                for c in art['bom_componenti']
                                for oa in c.get('oa_dettaglio', [])
                            )
                            if in_transito:
                                art['stato_ordine'] = 'IN_ARRIVO'
                            elif oa_confermati:
                                art['stato_ordine'] = 'ORDINE_CONFERMATO'
                            else:
                                art['stato_ordine'] = 'ORDINATO_NC'
                        elif all(s in ('DISPONIBILE', 'PRODUCIBILE') for s in stati_comp):
                            # Componenti tutti in casa → da assemblare
                            art['stato_ordine'] = 'ASSEMBLABILE'

        info['stato_generale'] = calcola_stato_generale(info['articoli'])
        # Persisti stato_generale nel fascicolo per i task
        try:
            _fasc_sg = FascicoloOrdine.query.filter_by(conferma=info['conferma']).first()
            if _fasc_sg and _fasc_sg.stato_generale != info['stato_generale']:
                _fasc_sg.stato_generale = info['stato_generale']
                db.session.commit()
        except Exception:
            db.session.rollback()
        info['nota'] = note.get(info['conferma'], '')
        # Fascicolo digitale + data consegna cliente
        fasc = FascicoloOrdine.query.filter_by(conferma=info['conferma']).first()
        if not fasc:
            fasc = get_or_create_fascicolo(info['conferma'], info.get('cliente', ''))
        info['fascicolo'] = fascicolo_to_dict(fasc)
        # Data consegna concordata col cliente
        info['data_consegna_cliente'] = fasc.data_consegna_cliente if fasc else ''

        # ── MIGLIORIA: Lista DDT di consegna collegati a questo ordine ──
        # Mostra tutti i DDT emessi (anche parziali) in ordine cronologico,
        # così l'utente vede quante bolle ha fatto su quell'ordine (1, 2, 3+)
        try:
            _ddt_list = ScaricoDDT.query.filter_by(
                conferma=info['conferma'],
                stornato=False
            ).order_by(ScaricoDDT.elaborato_il.asc()).all()
            info['ddt_consegna_list'] = [{
                'id':         d.id,
                'numero_ddt': d.numero_ddt or f'DDT #{d.id}',
                'data_ddt':   d.data_ddt or (d.elaborato_il.strftime('%d/%m/%Y') if d.elaborato_il else ''),
                'cliente':    d.cliente,
                'n_righe':    len(json.loads(d.movimenti_json or '[]')),
                'ha_pdf':     bool(d.pdf_blob),
            } for d in _ddt_list]
        except Exception:
            info['ddt_consegna_list'] = []
        # Badge NEW: visibile per 48h dall'inserimento dell'ordine
        info['is_new'] = False
        if fasc and fasc.creato_il:
            try:
                delta_h = (datetime.utcnow() - fasc.creato_il).total_seconds() / 3600
                info['is_new'] = delta_h < 48
            except Exception:
                pass
        # Calcola se SCADUTO: data consegna passata e ordine non ancora evaso
        info['scaduto'] = False
        if info['data_consegna_cliente'] and info['stato_generale'] != 'EVASO':
            try:
                _dp = info['data_consegna_cliente'].split('/')
                _dc = date(int(_dp[2]), int(_dp[1]), int(_dp[0]))
                if _dc < date.today():
                    info['scaduto'] = True
                    info['stato_generale'] = 'SCADUTO'
            except Exception:
                pass
 
        if info['in_slots']:
            consegne.append(info)
        else:
            in_essere.append(info)
 
    conta_clienti   = len([x for x in active_slots    if x.strip()])
    conta_fornitori = len([x for x in active_solleciti if x.strip()])
 
    pre_bolle_da_fare = PreBolla.query.filter_by(stato='DA_FARE').order_by(PreBolla.ricevuta_il.desc()).all()
    pre_bolle_fatte   = PreBolla.query.filter_by(stato='FATTA').order_by(PreBolla.confermata_il.desc()).limit(20).all()
    da_fare_count     = len(pre_bolle_da_fare)
 
    return render_template('dashboard_ordini.html',
                           consegne=consegne,
                           in_essere=in_essere,
                           ordini_acquisto=ordini_acquisto,
                           attivi_clienti=conta_clienti,
                           attivi_fornitori=conta_fornitori,
                           attivi_co=conta_clienti,
                           attivi_oa=conta_fornitori,
                           pre_bolle_da_fare=pre_bolle_da_fare,
                           pre_bolle_fatte=pre_bolle_fatte,
                           da_fare_count=da_fare_count)
 
 
@app.route('/reset')
def reset():
    for r in SlotTotem.query.filter_by(tipo='clienti').all():
        r.filename = ''
    db.session.commit()
    return redirect(url_for('index'))
 
 
@app.route('/uploads/<path:filename>')
def custom_static(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)
 
 
@app.route('/ordini_acquisto/elaborati/<path:filename>')
def serve_oa_pdf(filename):
    path = os.path.join(ACQUISTI_DONE_FOLDER, filename)
    if os.path.exists(path):
        return send_from_directory(ACQUISTI_DONE_FOLDER, filename)
    # File non trovato su disco (Railway filesystem effimero)
    return (
        "<html><head><meta charset='utf-8'>"
        "<style>body{font-family:sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;height:100vh;}"
        ".box{background:#141414;border:1px solid #f0ad4e33;border-radius:12px;"
        "padding:30px;text-align:center;max-width:420px;}"
        "h2{color:#f0ad4e;margin-bottom:10px;} p{color:#666;font-size:0.85rem;}"
        ".fn{color:#00d2ff;font-family:monospace;font-size:0.8rem;margin:12px 0;"
        "background:#111;padding:6px 10px;border-radius:5px;word-break:break-all;}"
        "a{color:#28a745;}</style></head><body><div class='box'>"
        "<h2>📄 File non disponibile</h2>"
        f"<div class='fn'>{filename}</div>"
        "<p>Il PDF dell'OA non è presente su disco.<br>"
        "Su Railway il filesystem si resetta ad ogni deploy.<br><br>"
        "Ricarica il PDF dalla pagina Acquisti per renderlo disponibile.</p>"
        "<br><a href='/acquisti'>← Vai agli Acquisti</a>"
        "</div></body></html>"
    ), 404
 
 
@app.route('/carico_ddt/<path:filename>')
def serve_ddt_pdf(filename):
    return send_from_directory(CARICO_FOLDER, filename)
 
 
@app.route('/get_status')
def get_status():
    return ','.join(get_slots('clienti'))
 
 
@app.route('/set_active/<int:slot>/<path:filename>')
def set_active(slot, filename):
    set_slot('clienti', slot, filename)
    return redirect(url_for('index'))
 
 
@app.route('/ritorna_a_cards/<path:filename>')
def ritorna_a_cards(filename):
    set_slot('clienti', 0, filename)
    return redirect(url_for('index'))
 
 
@app.route('/get_solleciti_status')
def get_solleciti_status():
    return ','.join(get_slots('fornitori'))
 
 
@app.route('/set_sollecito_active/<int:slot>/<path:filename>')
def set_sollecito_active(slot, filename):
    set_slot('fornitori', slot, filename)
    print(f"\n>>> SOLLECITO ATTIVATO: Slot {slot} -> {filename}\n")
    return redirect(url_for('index'))
 
 
@app.route('/upload', methods=['POST'])
def upload():
    """FASE 1: salva CO in temp e mostra anteprima — NON processa ancora."""
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return redirect(url_for('index'))
    filename = file.filename
    # Salva in cartella temp (UPLOAD_FOLDER già è persistente su /data)
    dest = os.path.join(UPLOAD_FOLDER, filename)
    file.save(dest)
    scrivi_log(f"UPLOAD CO (anteprima): {filename}")
    return redirect(url_for('anteprima_co', filename=filename))
 
 
@app.route('/anteprima_co/<path:filename>')
def anteprima_co(filename):
    """FASE 1 — mostra anteprima parsing CO prima di processare."""
    path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('index'))
 
    mappa_esterni = get_mappa_codici_esterni()
    info = estrai_dati_completi_pdf(filename, mappa_esterni)
 
    # Arricchisci articoli con info DB
    magazzino = get_magazzino()
    articoli_arricchiti = []
    for art in info['articoli']:
        cod = art['codice']
        m = magazzino.get(cod, {})
        articoli_arricchiti.append({
            'codice':     cod,
            'desc':       art.get('descrizione', m.get('desc', '—')),
            'qta':        art.get('qta', 0),
            'in_db':      cod in magazzino,
            'stock':      m.get('stock', 0),
            'ordinati':   m.get('ordinati', 0),
        })
 
    problemi = []
    if info['conferma'] == 'N/D':  problemi.append('Numero conferma non trovato')
    if info['cliente'] == 'Sconosciuto': problemi.append('Cliente non riconosciuto')
    if not info['articoli']:        problemi.append('Nessun articolo trovato dalla regex')
    for art in articoli_arricchiti:
        if not art['in_db']:
            problemi.append(f"Codice {art['codice']} non nel magazzino")
 
    righe = ""
    for i, art in enumerate(articoli_arricchiti, 1):
        cls = "ok" if art['in_db'] else "warn"
        txt = "OK" if art['in_db'] else "Nuovo"
        righe += (
            "<tr id='co-row-" + str(i) + "'>"
            "<td style='color:#555;text-align:center;'>"
            "<input type='checkbox' name='escludi' value='" + art['codice'] + "' "
            "onchange=\"document.getElementById('co-row-" + str(i) + "').style.opacity=this.checked?'0.35':'1';"
            "document.getElementById('co-row-" + str(i) + "').style.textDecoration=this.checked?'line-through':'none';\" "
            "title='Spunta per escludere questa riga dall\'importazione' "
            "style='width:16px;height:16px;cursor:pointer;accent-color:#ff4d5e;'></td>"
            f"<td style='color:#00d2ff;font-weight:bold;font-family:monospace'>{art['codice']}</td>"
            f"<td style='color:#aaa;font-size:0.78rem'>{art['desc']}</td>"
            f"<td style='color:#28a745;font-weight:bold'>{art['qta']}</td>"
            f"<td style='color:#ccc'>{art['stock']}</td>"
            f"<td class='{cls}'>{txt}</td></tr>"
        )
 
    prob_html = ""
    if problemi:
        items = "".join(f"<li>{p}</li>" for p in problemi)
        prob_html = (
            "<div class='problems'>"
            "<b style='color:#f0ad4e;font-size:0.8rem'>Attenzione — problemi rilevati:</b>"
            f"<ul>{items}</ul></div>"
        )
 
    n_cls = "ok" if info['conferma'] != 'N/D' else "err"
    c_cls = "ok" if info['cliente'] != 'Sconosciuto' else "err"
    a_cls = "ok" if info['articoli'] else "err"
 
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Anteprima CO: {filename}</title>"
        "<style>"
        "*{box-sizing:border-box;margin:0;padding:0;}"
        "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
        ".box{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:28px;"
        "max-width:720px;width:100%;}"
        "h2{color:#007bff;margin-bottom:6px;font-size:1.1rem;}"
        ".sub{color:#555;font-size:0.75rem;margin-bottom:20px;}"
        ".row{display:flex;gap:12px;margin-bottom:16px;}"
        ".field{flex:1;background:#0e0e0e;border:1px solid #222;border-radius:8px;padding:10px 14px;}"
        ".field label{font-size:0.62rem;color:#555;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:4px;}"
        ".field .val{font-size:0.95rem;font-weight:bold;}"
        ".ok{color:#28a745;}.warn{color:#f0ad4e;}.err{color:#ff4d5e;}"
        "table{width:100%;border-collapse:collapse;margin:14px 0;font-size:0.82rem;}"
        "th{background:#1a1a1a;color:#007bff;padding:8px 10px;text-align:left;"
        "font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;}"
        "td{padding:8px 10px;border-bottom:1px solid #1a1a1a;}"
        ".problems{background:#1a0a0a;border:1px solid #dc354533;border-radius:8px;"
        "padding:12px;margin:14px 0;}"
        ".problems li{color:#ff4d5e;font-size:0.8rem;margin-top:4px;list-style:none;padding-left:10px;}"
        ".problems li::before{content:'- ';}"
        ".btns{display:flex;gap:10px;margin-top:20px;}"
        ".btn-ok{flex:1;background:#007bff;color:#fff;border:none;padding:14px;"
        "border-radius:8px;font-weight:900;font-size:1rem;cursor:pointer;}"
        ".btn-ok:hover{background:#0056b3;}"
        ".btn-cancel{background:#1a1a1a;color:#666;border:1px solid #333;padding:14px 20px;"
        "border-radius:8px;font-size:0.9rem;cursor:pointer;}"
        ".btn-cancel:hover{color:#aaa;}"
        "</style></head><body><div class='box'>"
        f"<h2>Anteprima CO — {filename}</h2>"
        "<p class='sub'>Verifica i dati estratti prima di confermare l'inserimento.</p>"
        "<div class='row'>"
        f"<div class='field'><label>Conferma N°</label><div class='val {n_cls}'>{info['conferma']}</div></div>"
        f"<div class='field'><label>Cliente</label><div class='val {c_cls}'>{info['cliente']}</div></div>"
        f"<div class='field'><label>Articoli trovati</label><div class='val {a_cls}'>{len(info['articoli'])}</div></div>"
        f"<div class='field'><label>Data</label><div class='val'>{info.get('data','—')}</div></div>"
        "</div>"
        f"{prob_html}"
        "<table><tr><th style='width:40px;text-align:center'>Escludi</th><th>#</th><th>Codice</th><th>Descrizione</th>"
        "<th>Q.ta</th><th>Stock</th><th>Nel DB</th></tr>"
        f"{righe}</table>"
        "<div class='btns'>"
        "<div style='background:#001020;border:1px solid #007bff33;border-radius:8px;padding:10px;margin-bottom:10px;font-size:0.72rem;color:#888;'>"
        "\u26a0\ufe0f Spunta le righe da <b style='color:#ff4d5e'>ESCLUDERE</b> dall'importazione (es. trasporto, pallet, servizi)."
        "</div>"
        f"<form action='/conferma_co/{filename}' method='post' style='flex:1'>"
        "<button type='submit' class='btn-ok'>CONFERMA — Attiva nel sistema</button>"
        "</form>"
        f"<form action='/annulla_co/{filename}' method='post'>"
        "<button type='submit' class='btn-cancel'>Annulla</button>"
        "</form></div>"
        "</div></body></html>"
    )
    return html
 
 
@app.route('/conferma_co/<path:filename>', methods=['POST'])
def conferma_co(filename):
    """FASE 2 — l'utente ha confermato: processa CO e crea fascicolo."""
    path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('index'))
    # Codici esclusi dall'utente nell'anteprima (es. trasporto, pallet)
    escludi_co = set(request.form.getlist('escludi'))
    if escludi_co:
        scrivi_log(f"CO {filename}: righe escluse dall'utente: {', '.join(escludi_co)}")
    scrivi_log(f"CO CONFERMATA: {filename}")
    try:
        dati = estrai_dati_completi_pdf(filename)
        conferma = dati.get('conferma', 'N/D')
        cliente  = dati.get('cliente', '')
        if conferma != 'N/D':
            fasc = get_or_create_fascicolo(conferma, cliente)
            dst  = os.path.join(fasc.path_fascicolo, '03_Conferma_Ordine.pdf')
            shutil.copy2(path, dst)
            fasc.has_co = True
            fasc.aggiornato_il = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        scrivi_log(f"ERRORE fascicolo CO {filename}: {e}")
    return redirect(url_for('index'))
 
 
@app.route('/annulla_co/<path:filename>', methods=['POST'])
def annulla_co(filename):
    """L'utente ha annullato: elimina il file senza processare."""
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(path):
        os.remove(path)
        scrivi_log(f"CO ANNULLATA: {filename} eliminata senza inserimento")
    return redirect(url_for('index'))
 
 
@app.route('/upload_ddt_fornitore', methods=['POST'])
def upload_ddt_fornitore():
    file = request.files.get('file')
    if file and file.filename.lower().endswith('.pdf'):
        dest = os.path.join(CARICO_FOLDER, file.filename)
        file.save(dest)
        scrivi_log(f"UPLOAD DDT FORNITORE: {file.filename}")
        # Matching automatico: cerca riferimento CO nel PDF
        try:
            with open(dest, 'rb') as f:
                testo = "".join(p.extract_text() or '' for p in PyPDF2.PdfReader(f).pages)
            match_co = re.search(r'(\d{1,4})\s*/\s*IS', testo)
            if match_co:
                conferma = f"{match_co.group(1)}/IS"
                fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
                if fasc and fasc.path_fascicolo:
                    sub = os.path.join(fasc.path_fascicolo, '05_DDT_Ingresso')
                    os.makedirs(sub, exist_ok=True)
                    shutil.copy2(dest, os.path.join(sub, file.filename))
                    fasc.has_ddt_acquisto = True
                    fasc.aggiornato_il = datetime.utcnow()
                    db.session.commit()
                    scrivi_log(f"DDT FORNITORE abbinato a fascicolo {conferma}: {file.filename}")
        except Exception as e:
            scrivi_log(f"ERRORE matching DDT {file.filename}: {e}")
        return redirect(url_for('pagina_acquisti'))  # FIX: resta su acquisti per Step 2
    return "Nessun file PDF ricevuto", 400
 
 
@app.route('/upload_oa', methods=['POST'])
def upload_oa():
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return "Nessun file PDF ricevuto", 400
    filename = file.filename
    file_bytes = file.read()
    path_temp = os.path.join(ACQUISTI_FOLDER, filename)
    with open(path_temp, 'wb') as f:
        f.write(file_bytes)
    scrivi_log(f"UPLOAD OA (anteprima): {filename}")
    return redirect(url_for('anteprima_oa', filename=filename))
 
 
@app.route('/upload_ddt_cliente', methods=['POST'])
def upload_ddt_cliente():
    file = request.files.get('file')
    if file and file.filename.lower().endswith('.pdf'):
        file.save(os.path.join(UPLOAD_FOLDER, file.filename))
        scrivi_log(f"UPLOAD DDT CLIENTE: {file.filename}")
    return redirect(url_for('index'))
 
 
@app.route('/upload_ddt_consegna', methods=['POST'])
def upload_ddt_consegna():
    """
    FIX: Salva DDT consegna e restituisce JSON con redirect all'anteprima.
    Il vecchio codice faceva redirect HTML → il JS riceveva HTML invece di JSON → errore.
    Ora restituisce JSON con l'URL dell'anteprima editabile.
    """
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'Nessun file PDF ricevuto'}), 400
    dest = os.path.join(CONSEGNA_FOLDER, file.filename)
    file.save(dest)
    scrivi_log(f"UPLOAD DDT CONSEGNA: {file.filename}")
    return jsonify({
        'ok': True,
        'redirect': url_for('anteprima_ddt_consegna_singolo', filename=file.filename)
    }), 200
 
 
@app.route('/elabora_ddt_consegna')
def elabora_ddt_consegna():
    """Elabora tutti i DDT consegna in attesa: scarica lo stock per ogni articolo.
    Salva i movimenti in ScaricoDDT per permettere lo storno successivo."""
    files = [f for f in os.listdir(CONSEGNA_FOLDER)
             if f.lower().endswith('.pdf')] if os.path.exists(CONSEGNA_FOLDER) else []
    if not files:
        return redirect(url_for('index'))
 
    risultati = []
    errori    = []
    for f in files:
        try:
            info = estrai_dati_ddt_consegna(f)
            movimenti = []
            for art in info['articoli']:
                cod = art['codice']
                qta = art['qta']
                articolo = Articolo.query.filter_by(sku=cod).first()
                if articolo:
                    vecchio_stock = articolo.stock or 0
                    articolo.stock = max(0, vecchio_stock - qta)
                    # FIX 2a: Articolo non ha .impegnato — rimosso
                    movimenti.append({
                        'codice': cod, 'qta': qta,
                        'stock_prima': vecchio_stock,
                        'stock_dopo': articolo.stock
                    })
                    scrivi_log(f"DDT CONSEGNA {f}: {cod} −{qta} (stock {vecchio_stock}→{articolo.stock})")

                    # ── aggiorna ArticoloEvaso + FascicoloOrdine ──────────
                    conferma_ddt = info.get('conferma', '')
                    if conferma_ddt:
                        id_art = f"{conferma_ddt}_{cod}".replace(' ', '')
                        ev     = ArticoloEvaso.query.filter_by(id_art=id_art).first()
                        nuova_qta_evasa = (ev.qta_evasa or 0) + qta if ev else qta

                        # FIX 2: evaso_totale sempre True (qta_evasa accumulata è il dato utile)
                        evaso_totale = True

                        if ev:
                            ev.qta_evasa = nuova_qta_evasa
                            ev.evaso     = evaso_totale
                        else:
                            db.session.add(ArticoloEvaso(
                                id_art    = id_art,
                                evaso     = evaso_totale,
                                qta_evasa = nuova_qta_evasa,
                            ))

                        db.session.add(RegistroEvasione(
                            data_ora = datetime.utcnow(),
                            conferma = conferma_ddt,
                            cliente  = info.get('cliente', ''),
                            codice   = cod,
                            qta      = qta,
                        ))

                        # FIX 4: aggiorna has_ddt_consegna nel fascicolo
                        try:
                            fasc2 = FascicoloOrdine.query.filter_by(conferma=conferma_ddt).first()
                            if fasc2:
                                fasc2.has_ddt_consegna = True
                                fasc2.aggiornato_il    = datetime.utcnow()
                        except Exception:
                            pass

                        scrivi_log(f"DDT CONSEGNA: {conferma_ddt} cod {cod} evasa {qta} (tot {nuova_qta_evasa}, evaso={evaso_totale})")
                else:
                    errori.append(f"DDT {f}: codice {cod} non trovato in magazzino")

            # Leggi il PDF come blob per conservarlo nel DB
            pdf_path = os.path.join(CONSEGNA_FOLDER, f)
            pdf_blob = None
            try:
                with open(pdf_path, 'rb') as pf:
                    pdf_blob = pf.read()
            except Exception:
                pass

            # Salva il record ScaricoDDT
            scarico = ScaricoDDT(
                filename_pdf   = f,
                numero_ddt     = info.get('numero_ddt', ''),
                cliente        = info.get('cliente', ''),
                conferma       = info.get('conferma', ''),
                data_ddt       = info.get('data_ddt', ''),
                movimenti_json = json.dumps(movimenti),
                pdf_blob       = pdf_blob,
                elaborato_il   = datetime.utcnow(),
                stornato       = False
            )
            db.session.add(scarico)
            db.session.commit()

            # Sposta in elaborati
            dest_done = os.path.join(CONSEGNA_DONE_FOLDER, f)
            try:
                shutil.move(pdf_path, dest_done)
            except Exception:
                pass  # Railway: filesystem effimero, non critico — il blob è nel DB

            risultati.append({
                'file':       f,
                'scarico_id': scarico.id,
                'cliente':    info['cliente'],
                'conferma':   info['conferma'],
                'numero_ddt': info['numero_ddt'],
                'movimenti':  movimenti
            })
        except Exception as e:
            db.session.rollback()
            errori.append(f"Errore {f}: {str(e)}")
            scrivi_log(f"ERRORE elabora DDT consegna {f}: {e}")
 
    return redirect(url_for('index'))
 
 
@app.route('/api/lista_carichi_ddt')
def api_lista_carichi_ddt():
    """Restituisce tutti i DDT di arrivo fornitore elaborati per il pannello storno."""
    try:
        carichi = CaricoDDT.query.order_by(CaricoDDT.elaborato_il.desc()).all()
        result = []
        for c in carichi:
            try:
                movimenti = json.loads(c.movimenti_json or '[]')
            except Exception:
                movimenti = []
            result.append({
                'id':           c.id,
                'filename_pdf': c.filename_pdf,
                'numero_ddt':   c.numero_ddt,
                'fornitore':    c.fornitore,
                'data_ddt':     c.data_ddt,
                'elaborato_il': c.elaborato_il.strftime('%d/%m/%Y %H:%M') if c.elaborato_il else '',
                'stornato':     c.stornato,
                'stornato_il':  c.stornato_il.strftime('%d/%m/%Y %H:%M') if c.stornato_il else '',
                'movimenti':    movimenti,
                'ha_pdf':       bool(c.pdf_blob)
            })
        return jsonify({'ok': True, 'carichi': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/storno_carico_ddt/<int:carico_id>', methods=['POST'])
def api_storno_carico_ddt(carico_id):
    """
    Storno di un DDT di arrivo fornitore:
    1. Sottrae dallo stock le quantità caricate
    2. Ripristina gli ordinati come erano prima
    3. Marca il record come stornato ed elimina il PDF dal DB
    """
    try:
        carico = CaricoDDT.query.get(carico_id)
        if not carico:
            return jsonify({'ok': False, 'error': 'Carico non trovato'}), 404
        if carico.stornato:
            return jsonify({'ok': False, 'error': 'DDT già stornato in precedenza'}), 400

        movimenti = json.loads(carico.movimenti_json or '[]')
        ripristinati = []
        non_trovati  = []

        for mov in movimenti:
            cod = mov.get('codice')
            qta = mov.get('qta', 0)
            articolo = Articolo.query.filter_by(sku=cod).first()
            if articolo:
                stock_prima = articolo.stock or 0
                # Sottrai lo stock che era stato aggiunto
                articolo.stock = max(0, stock_prima - qta)
                # Ripristina gli ordinati come erano prima del carico
                ord_prima_carico = mov.get('ordinati_prima', 0)
                articolo.ordinati = ord_prima_carico
                ripristinati.append({
                    'codice':      cod,
                    'qta':         qta,
                    'stock_prima': stock_prima,
                    'stock_dopo':  articolo.stock,
                    'ordinati_ripristinati': ord_prima_carico
                })
                scrivi_log(f"STORNO CARICO DDT {carico.filename_pdf}: {cod} −{qta} stock (ora {articolo.stock}), ordinati ripristinati a {ord_prima_carico}")
            else:
                non_trovati.append(cod)

        carico.stornato    = True
        carico.stornato_il = datetime.utcnow()
        carico.pdf_blob    = None
        db.session.commit()

        scrivi_log(f"STORNO CARICO DDT completato: {carico.filename_pdf} — {len(ripristinati)} articoli")
        return jsonify({
            'ok': True,
            'ripristinati': ripristinati,
            'non_trovati':  non_trovati,
            'messaggio':    f"Storno completato: {len(ripristinati)} articoli rimossi dal magazzino, ordinati ripristinati."
        })
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE storno carico DDT {carico_id}: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pdf_carico/<int:carico_id>')
def api_pdf_carico(carico_id):
    """Serve il PDF originale del DDT di carico dal blob DB."""
    carico = CaricoDDT.query.get(carico_id)
    if not carico or not carico.pdf_blob:
        return "PDF non disponibile", 404
    from flask import Response
    return Response(
        carico.pdf_blob,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{carico.filename_pdf}"'}
    )


@app.route('/api/lista_scarichi_ddt')
def api_lista_scarichi_ddt():
    """Restituisce tutti i DDT di consegna elaborati (non stornati) per il pannello storno."""
    try:
        scarichi = ScaricoDDT.query.order_by(ScaricoDDT.elaborato_il.desc()).all()
        result = []
        for s in scarichi:
            try:
                movimenti = json.loads(s.movimenti_json or '[]')
            except Exception:
                movimenti = []
            result.append({
                'id':           s.id,
                'filename_pdf': s.filename_pdf,
                'numero_ddt':   s.numero_ddt,
                'cliente':      s.cliente,
                'conferma':     s.conferma,
                'data_ddt':     s.data_ddt,
                'elaborato_il': s.elaborato_il.strftime('%d/%m/%Y %H:%M') if s.elaborato_il else '',
                'stornato':     s.stornato,
                'stornato_il':  s.stornato_il.strftime('%d/%m/%Y %H:%M') if s.stornato_il else '',
                'movimenti':    movimenti,
                'ha_pdf':       bool(s.pdf_blob)
            })
        return jsonify({'ok': True, 'scarichi': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/storno_ddt/<int:scarico_id>', methods=['POST'])
def api_storno_ddt(scarico_id):
    """
    Storno di un DDT di consegna:
    1. Reinserisce nello stock tutte le quantità scaricate
    2. Marca il record come stornato
    3. Elimina il PDF dal DB (il blob viene azzerato)
    """
    try:
        scarico = ScaricoDDT.query.get(scarico_id)
        if not scarico:
            return jsonify({'ok': False, 'error': 'Scarico non trovato'}), 404
        if scarico.stornato:
            return jsonify({'ok': False, 'error': 'DDT già stornato in precedenza'}), 400

        movimenti = json.loads(scarico.movimenti_json or '[]')
        ripristinati = []
        non_trovati  = []

        for mov in movimenti:
            cod = mov.get('codice')
            qta = mov.get('qta', 0)
            articolo = Articolo.query.filter_by(sku=cod).first()
            if articolo:
                stock_prima = articolo.stock or 0
                articolo.stock = stock_prima + qta
                # Ripristina anche impegnato se era stato scalato
                if articolo.incoming is not None:
                    articolo.incoming = (articolo.incoming or 0) + qta
                ripristinati.append({
                    'codice': cod, 'qta': qta,
                    'stock_prima': stock_prima,
                    'stock_dopo': articolo.stock
                })
                scrivi_log(f"STORNO DDT {scarico.filename_pdf}: {cod} +{qta} (stock {stock_prima}→{articolo.stock})")
            else:
                non_trovati.append(cod)

        # Marca come stornato e azzera il blob PDF
        scarico.stornato    = True
        scarico.stornato_il = datetime.utcnow()
        scarico.pdf_blob    = None  # Elimina il PDF dal DB
        db.session.commit()

        scrivi_log(f"STORNO DDT completato: {scarico.filename_pdf} — {len(ripristinati)} articoli ripristinati")
        return jsonify({
            'ok': True,
            'ripristinati': ripristinati,
            'non_trovati':  non_trovati,
            'messaggio':    f"Storno completato: {len(ripristinati)} articoli reinseriti in magazzino."
        })
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE storno DDT {scarico_id}: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pdf_scarico/<int:scarico_id>')
def api_pdf_scarico(scarico_id):
    """Serve il PDF originale del DDT dal blob DB (per visualizzarlo prima dello storno)."""
    scarico = ScaricoDDT.query.get(scarico_id)
    if not scarico or not scarico.pdf_blob:
        return "PDF non disponibile", 404
    from flask import Response
    return Response(
        scarico.pdf_blob,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{scarico.filename_pdf}"'}
    )



@app.route('/anteprima_ddt_consegna_singolo/<path:filename>')
def anteprima_ddt_consegna_singolo(filename):
    """FASE 1: DDT consegna — anteprima EDITABILE prima di scaricare lo stock.
    Permette di correggere codici errati, modificare quantità, aggiungere/rimuovere righe.
    """
    path = os.path.join(CONSEGNA_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('index'))

    info = estrai_dati_ddt_consegna(filename)
    # ── BLOCCO: DDT DI ACQUISTO caricata per errore nelle consegne ──────────────
    if info.get('tipo_ddt') == 'acquisto':
        html_errore = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Errore DDT</title>"
            "<style>*{box-sizing:border-box;margin:0;padding:0;}"
            "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
            ".box{background:#0a1020;border:2px solid #0d6efd;border-radius:14px;padding:32px;"
            "max-width:560px;width:100%;text-align:center;}"
            "h2{color:#4d9fff;font-size:1.3rem;margin-bottom:12px;}"
            "p{color:#aaa;font-size:0.88rem;line-height:1.6;margin-bottom:20px;}"
            ".badge{display:inline-block;background:#0d6efd22;border:1px solid #0d6efd66;"
            "color:#4d9fff;padding:6px 16px;border-radius:20px;font-size:0.78rem;"
            "font-weight:900;letter-spacing:1px;margin-bottom:20px;}"
            ".btn{background:#1a1a1a;color:#888;border:1px solid #333;padding:12px 24px;"
            "border-radius:8px;font-size:0.9rem;cursor:pointer;text-decoration:none;"
            "display:inline-block;}"
            ".btn:hover{color:#ccc;}"
            "</style></head><body><div class='box'>"
            "<div class='badge'>&#9888; DOCUMENTO NON VALIDO</div>"
            "<h2>DDT di acquisto rilevata!</h2>"
            "<p>Il file <b style='color:#fff'>" + filename + "</b> &egrave; una "
            "<b style='color:#4d9fff'>DDT fornitore / carico acquisto</b> "
            "e non pu&ograve; essere elaborata come <b>DDT Consegna Cliente</b>.</p>"
            "<p>Controlla di aver selezionato il file corretto.<br>"
            "Le DDT fornitore vanno caricate dalla sezione <b>Carico DDT Fornitore</b>.</p>"
            "<a href='javascript:history.back()' class='btn'>&#8592; Torna indietro</a>"
            "</div></body></html>"
        )
        try:
            os.remove(path)
        except Exception:
            pass
        return html_errore
    for art in info.get('articoli', []):
        art_db = Articolo.query.filter_by(sku=art['codice']).first()
        articoli_arricchiti.append({
            'codice':      art['codice'],
            'descrizione': art.get('desc', ''),
            'qta':         art['qta'],
            'in_db':       art_db is not None,
            'stock_att':   art_db.stock if art_db else 0,
            'stock_dopo':  max(0, (art_db.stock or 0) - int(art['qta'])) if art_db else 0,
        })

    problemi = []
    if not info.get('articoli'):
        problemi.append('Nessun articolo trovato nel PDF — aggiungili manualmente')
    for art in articoli_arricchiti:
        if not art['in_db']:
            problemi.append(f"Codice {art['codice']} NON presente in magazzino — verrà ignorato")
        elif art['stock_dopo'] < 0:
            problemi.append(f"{art['codice']}: stock insufficiente ({art['stock_att']} disp., {art['qta']} richiesti)")

    righe = ''
    for i, art in enumerate(articoli_arricchiti, 1):
        rid  = f"cs-row-{i}"
        cls  = ('ok' if art['stock_dopo'] >= 0 else 'err') if art['in_db'] else 'warn'
        icon = "✅" if art['in_db'] else "⚠️"
        cod  = art['codice'].replace("'", "&#39;").replace('"', '&quot;')
        dsc  = (art['descrizione'] or '').replace("'", "&#39;").replace('"', '&quot;')
        righe += (
            f"<tr id='{rid}'>"
            f"<td><button type='button' onclick='delRow(this)' style='background:none;border:none;color:#555;cursor:pointer;font-size:1.1rem;padding:2px 5px;' title='Rimuovi'>✕</button></td>"
            f"<td><input type='text' name='codice[]' value='{cod}' style='background:#0a1520;border:1px solid #1a4060;border-radius:4px;color:#00d2ff;padding:4px 6px;font-family:monospace;font-weight:bold;width:160px;font-size:0.82rem;'></td>"
            f"<td><input type='text' name='desc[]' value='{dsc}' style='background:#0a0a0a;border:1px solid #222;border-radius:4px;color:#ccc;padding:4px 6px;width:100%;font-size:0.78rem;'></td>"
            f"<td><input type='number' name='qta[]' value='{art['qta']}' min='0' style='background:#150a0a;border:1px solid #4a1a20;border-radius:4px;color:#ff4d5e;padding:4px 6px;width:68px;font-weight:bold;font-size:0.82rem;text-align:center;'></td>"
            f"<td style='color:#aaa'>{art['stock_att']}</td>"
            f"<td class='{cls}' style='font-weight:bold'>{art['stock_dopo']}</td>"
            f"<td>{icon}</td>"
            f"</tr>"
        )

    prob_html = ''
    if problemi:
        items = ''.join(f'<li>{p}</li>' for p in problemi)
        prob_html = f"<div style='background:#1a0000;border:1px solid #dc354533;border-radius:8px;padding:12px;margin:14px 0;'><b style='color:#ff4d5e;font-size:0.8rem'>⚠️ Attenzione</b><ul style='color:#ff4d5e;font-size:0.8rem;margin-top:6px;padding-left:16px;'>{items}</ul></div>"

    nddt = (info.get('numero_ddt') or '').replace("'", "&#39;")
    cli  = (info.get('cliente')    or '').replace("'", "&#39;")
    conf = (info.get('conferma')   or '').replace("'", "&#39;")

    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Verifica DDT Consegna: {filename}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.box{{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:28px;max-width:860px;width:100%}}
h2{{color:#dc3545;margin-bottom:6px;font-size:1.1rem}}
.sub{{color:#555;font-size:0.75rem;margin-bottom:20px}}
.row{{display:flex;gap:12px;margin-bottom:16px}}
.field{{flex:1;background:#0e0e0e;border:1px solid #222;border-radius:8px;padding:10px 14px}}
.field label{{font-size:0.62rem;color:#555;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:4px}}
.inp-hdr{{background:#0e0e0e;border:1px solid #333;border-radius:4px;color:#fff;padding:5px 8px;font-size:0.95rem;font-weight:bold;width:100%}}
.ok{{color:#28a745}}.warn{{color:#f0ad4e}}.err{{color:#ff4d5e}}
table{{width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:0.82rem}}
th{{background:#1a1a1a;color:#dc3545;padding:8px 10px;text-align:left;font-size:0.68rem;text-transform:uppercase}}
td{{padding:5px 8px;border-bottom:1px solid #1a1a1a}}
.btn-ok{{background:#dc3545;color:#fff;border:none;padding:14px;border-radius:8px;font-weight:900;font-size:1rem;cursor:pointer;width:100%;margin-top:16px}}
.btn-ok:hover{{background:#c82333}}
.btn-add{{background:#0d1f0d;border:1px solid #28a74540;color:#28a745;padding:7px 16px;border-radius:6px;cursor:pointer;font-size:0.8rem;margin-top:6px;display:inline-block}}
.btn-cancel{{display:block;text-align:center;margin-top:10px;color:#555;font-size:0.8rem;text-decoration:none}}
.btn-cancel:hover{{color:#aaa}}
.note{{background:#001828;border:1px solid #007bff22;border-radius:6px;padding:8px 12px;font-size:0.72rem;color:#557799;margin-bottom:10px}}
</style></head><body>
<div class='box'>
<h2>🚚 Verifica DDT Consegna — {filename}</h2>
<p class='sub'>Correggi articoli e quantità prima di scaricare lo stock. Puoi aggiungere righe mancanti o eliminare quelle errate.</p>
<form action='/conferma_ddt_consegna_singolo/{filename}' method='post'>
<div class='row'>
  <div class='field'><label>N° DDT</label><input class='inp-hdr' name='numero_ddt_override' value='{nddt}'></div>
  <div class='field'><label>Cliente</label><input class='inp-hdr' name='cliente_override' value='{cli}'></div>
  <div class='field'><label>Conferma Ordine (es. 171/IS)</label><input class='inp-hdr' name='conferma_override' value='{conf}'></div>
</div>
{prob_html}
<div class='note'>✏️ Puoi modificare codici, descrizioni e quantità. ✕ rimuove la riga. ➕ aggiunge una riga manuale.</div>
<table>
<tr><th>✕</th><th>Codice SKU</th><th>Descrizione</th><th>Qtà scarico</th><th>Stock ora</th><th>Stock dopo</th><th>DB</th></tr>
<tbody id='tbl-body'>
{righe}
</tbody>
</table>
<button type='button' class='btn-add' onclick='addRow()'>➕ Aggiungi articolo</button>
<button type='submit' class='btn-ok'>⚡ CONFERMA — Scarica Stock</button>
</form>
<a href='javascript:history.back()' class='btn-cancel'>← Annulla</a>
</div>
<script>
function delRow(btn){{btn.closest('tr').remove();}}
function addRow(){{
  var tb=document.getElementById('tbl-body');
  var tr=document.createElement('tr');
  tr.innerHTML='<td><button type="button" onclick="delRow(this)" style="background:none;border:none;color:#555;cursor:pointer;font-size:1.1rem;padding:2px 5px;">✕</button></td>'
    +'<td><input type="text" name="codice[]" placeholder="SKU" style="background:#0a1520;border:1px solid #1a4060;border-radius:4px;color:#00d2ff;padding:4px 6px;font-family:monospace;font-weight:bold;width:160px;font-size:0.82rem;"></td>'
    +'<td><input type="text" name="desc[]" placeholder="Descrizione" style="background:#0a0a0a;border:1px solid #222;border-radius:4px;color:#ccc;padding:4px 6px;width:100%;font-size:0.78rem;"></td>'
    +'<td><input type="number" name="qta[]" value="1" min="0" style="background:#150a0a;border:1px solid #4a1a20;border-radius:4px;color:#ff4d5e;padding:4px 6px;width:68px;font-weight:bold;font-size:0.82rem;text-align:center;"></td>'
    +'<td></td><td></td><td></td>';
  tb.appendChild(tr);tr.querySelector('input').focus();
}}
</script>
</body></html>"""
    return html


@app.route('/conferma_ddt_consegna_singolo/<path:filename>', methods=['POST'])
def conferma_ddt_consegna_singolo(filename):
    """FASE 2: scarica stock con articoli (eventualmente corretti) dal form."""
    path = os.path.join(CONSEGNA_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('index'))

    numero_ddt = request.form.get('numero_ddt_override', '').strip()
    cliente    = request.form.get('cliente_override', '').strip()
    conferma   = request.form.get('conferma_override', '').strip()

    codici = request.form.getlist('codice[]')
    descs  = request.form.getlist('desc[]')
    qtas   = request.form.getlist('qta[]')

    movimenti = []
    for i, cod in enumerate(codici):
        cod = cod.strip()
        if not cod:
            continue
        try:
            qta = int(float(qtas[i])) if i < len(qtas) else 0
        except (ValueError, TypeError):
            continue
        if qta <= 0:
            continue
        articolo = Articolo.query.filter_by(sku=cod).first()
        if articolo:
            stock_prima = articolo.stock or 0
            articolo.stock = max(0, stock_prima - qta)
            movimenti.append({'codice': cod, 'qta': qta,
                              'stock_prima': stock_prima, 'stock_dopo': articolo.stock})
            scrivi_log(f"DDT CONSEGNA {filename}: {cod} -{qta} (stock {stock_prima}->{articolo.stock})")
        else:
            scrivi_log(f"DDT CONSEGNA {filename}: {cod} NON trovato in magazzino — ignorato")

    # Aggiorna ArticoloEvaso per tracciare le evasioni
    if conferma:
        for i, cod in enumerate(codici):
            cod = cod.strip()
            if not cod:
                continue
            try:
                qta = int(float(qtas[i])) if i < len(qtas) else 0
            except (ValueError, TypeError):
                continue
            if qta <= 0:
                continue
            id_art = f"{conferma}_{cod}".replace(' ', '')
            ev = ArticoloEvaso.query.filter_by(id_art=id_art).first()
            if ev:
                ev.qta_evasa = (ev.qta_evasa or 0) + qta
                ev.evaso     = True
            else:
                db.session.add(ArticoloEvaso(id_art=id_art, evaso=True, qta_evasa=qta))
            db.session.add(RegistroEvasione(
                data_ora=datetime.utcnow(), conferma=conferma,
                cliente=cliente, codice=cod, qta=qta))
        try:
            fasc2 = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if fasc2:
                fasc2.has_ddt_consegna = True
                fasc2.aggiornato_il    = datetime.utcnow()
        except Exception:
            pass

    pdf_blob = None
    try:
        with open(path, 'rb') as pf:
            pdf_blob = pf.read()
    except Exception:
        pass
    scarico = ScaricoDDT(
        filename_pdf=filename, numero_ddt=numero_ddt, cliente=cliente,
        conferma=conferma, data_ddt='',
        movimenti_json=json.dumps(movimenti),
        pdf_blob=pdf_blob, elaborato_il=datetime.utcnow(), stornato=False
    )
    db.session.add(scarico)
    try:
        shutil.move(path, os.path.join(CONSEGNA_DONE_FOLDER, filename))
    except Exception:
        pass
    db.session.commit()
    scrivi_log(f"DDT CONSEGNA elaborato: {filename} — {len(movimenti)} articoli scaricati")
    return redirect(url_for('index'))


@app.route('/anteprima_ddt_consegna')
def anteprima_ddt_consegna():
    """Mostra anteprima dei DDT consegna in attesa senza elaborarli."""
    files = [f for f in os.listdir(CONSEGNA_FOLDER)
             if f.lower().endswith('.pdf')] if os.path.exists(CONSEGNA_FOLDER) else []
    risultati = []
    for f in files:
        try:
            info = estrai_dati_ddt_consegna(f)
            # Arricchisci con stock attuale
            for art in info['articoli']:
                a = Articolo.query.filter_by(sku=art['codice']).first()
                art['stock_attuale'] = a.stock if a else '??'
                art['trovato']       = a is not None
            risultati.append({'file': f, **info})
        except Exception as e:
            risultati.append({'file': f, 'errore': str(e), 'articoli': []})
    return jsonify({'ok': True, 'ddt': risultati})
 
 
@app.route('/elimina_pdf/<tipo>/<path:filename>')
def elimina_pdf(tipo, filename):
    cartelle = {'co': UPLOAD_FOLDER, 'ddt': CARICO_FOLDER, 'oa': ACQUISTI_DONE_FOLDER}
    cartella = cartelle.get(tipo)
    if not cartella:
        return "Tipo non valido", 400
    path = os.path.join(cartella, filename)
    if os.path.exists(path):
        os.remove(path)
        scrivi_log(f"ELIMINATO PDF [{tipo}]: {filename}")
 
    # FIX BUG 2: se è un OA, rimuovi dal DB, aggiorna Articolo.ordinati
    # e pulisci le tabelle collegate (DataEvasioneArticolo, DiarioAccordiOA, Urgenza).
    # Ricalcola anche Articolo.ordinati sommando TUTTI i restanti OA attivi per quel codice,
    # così il campo non resta "sporco" con quantità fantasma.
    if tipo == 'oa':
        try:
            righe = StoricoOrdineFornitore.query.filter_by(filename_oa=filename).all()
            codici_da_aggiornare = [r.codice for r in righe]
            for r in righe:
                db.session.delete(r)
            # Rimuovi anche le tabelle collegate
            stato = StatoOrdineAcquisto.query.filter_by(filename=filename).first()
            if stato:
                db.session.delete(stato)
            DataEvasioneArticolo.query.filter_by(filename_oa=filename).delete()
            DiarioAccordiOA.query.filter_by(filename_oa=filename).delete()
            urg = Urgenza.query.filter_by(riferimento=filename).first()
            if urg:
                db.session.delete(urg)
            db.session.flush()

            # Ricalcola Articolo.ordinati per ogni codice coinvolto
            # sommando TUTTI gli OA ancora attivi in StoricoOrdineFornitore
            for codice in codici_da_aggiornare:
                totale_ordinati = db.session.query(
                    db.func.sum(StoricoOrdineFornitore.qta)
                ).filter_by(codice=codice).scalar() or 0
                articolo = Articolo.query.filter_by(sku=codice).first()
                if articolo:
                    articolo.ordinati = max(0, int(totale_ordinati))
                    scrivi_log(f"OA eliminato: ordinati {codice} ricalcolati → {articolo.ordinati}")

            db.session.commit()
            scrivi_log(f"OA eliminato dal DB: {filename} — {len(righe)} righe storico rimosse, ordinati aggiornati")
        except Exception as e:
            db.session.rollback()
            scrivi_log(f"ERRORE eliminazione OA dal DB {filename}: {e}")
 
    return redirect(url_for('pagina_acquisti'))
 
 
@app.route('/evadi/<path:filename>')
def evadi(filename):
    src = os.path.join(UPLOAD_FOLDER, filename)
    dst = os.path.join(DONE_FOLDER, filename)
    if os.path.exists(src):
        shutil.move(src, dst)
    return redirect(url_for('reset'))
 
 
@app.route('/evadi_articolo/<path:filename>/<codice>')
def evadi_articolo(filename, codice):
    info = estrai_dati_completi_pdf(filename)
    for art in info['articoli']:
        if art['codice'] == codice:
            db.session.add(RegistroEvasione(
                data_ora=datetime.now(),
                conferma=info['conferma'],
                cliente=info['cliente'],
                codice=codice,
                qta=int(art['qta'])
            ))
            id_art = f"{info['conferma']}_{codice}".replace(" ", "")
            existing = ArticoloEvaso.query.filter_by(id_art=id_art).first()
            if existing:
                existing.evaso     = True
                existing.qta_evasa = (existing.qta_evasa or 0) + int(art['qta'])
            else:
                db.session.add(ArticoloEvaso(
                    id_art=id_art, evaso=True, qta_evasa=int(art['qta'])
                ))
    db.session.commit()
    return redirect(url_for('index'))
 
 
@app.route('/carica_da_ddt_upload', methods=['POST'])
def carica_da_ddt_upload():
    """Upload PDF DDT arrivo fornitore: salva e mostra anteprima per conferma."""
    files = request.files.getlist('file')
    saved = []
    for file in files:
        if file and file.filename.lower().endswith('.pdf'):
            file.save(os.path.join(CARICO_FOLDER, file.filename))
            scrivi_log(f"UPLOAD DDT ARRIVO: {file.filename}")
            saved.append(file.filename)
    if len(saved) == 1:
        return redirect(url_for('anteprima_ddt_carico', filename=saved[0]))
    return redirect(url_for('magazzino'))
 
 
@app.route('/anteprima_ddt_carico/<path:filename>')
def anteprima_ddt_carico(filename):
    """FASE 1: mostra anteprima DDT carico fornitore prima di elaborarlo."""
    path = os.path.join(CARICO_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('magazzino'))
    info = estrai_dati_completi_pdf_carico(filename)
    # ── BLOCCO: DDT DI VENDITA caricata per errore negli acquisti ───────────────
    if info.get('tipo_ddt') == 'vendita':
        html_errore = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Errore DDT</title>"
            "<style>*{box-sizing:border-box;margin:0;padding:0;}"
            "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
            ".box{background:#1a0505;border:2px solid #dc3545;border-radius:14px;padding:32px;"
            "max-width:560px;width:100%;text-align:center;}"
            "h2{color:#ff4d5e;font-size:1.3rem;margin-bottom:12px;}"
            "p{color:#aaa;font-size:0.88rem;line-height:1.6;margin-bottom:20px;}"
            ".badge{display:inline-block;background:#dc354522;border:1px solid #dc354566;"
            "color:#ff4d5e;padding:6px 16px;border-radius:20px;font-size:0.78rem;"
            "font-weight:900;letter-spacing:1px;margin-bottom:20px;}"
            ".btn{background:#1a1a1a;color:#888;border:1px solid #333;padding:12px 24px;"
            "border-radius:8px;font-size:0.9rem;cursor:pointer;text-decoration:none;"
            "display:inline-block;}"
            ".btn:hover{color:#ccc;}"
            "</style></head><body><div class='box'>"
            "<div class='badge'>&#9888; DOCUMENTO NON VALIDO</div>"
            "<h2>DDT di vendita rilevata!</h2>"
            "<p>Il file <b style='color:#fff'>" + filename + "</b> &egrave; una "
            "<b style='color:#ff4d5e'>DDT di vendita / consegna cliente</b> "
            "e non pu&ograve; essere caricato nella sezione <b>Acquisti / Carico Fornitore</b>.</p>"
            "<p>Controlla di aver selezionato il file corretto.<br>"
            "Le DDT di vendita vanno gestite dalla sezione <b>DDT Consegna Cliente</b>.</p>"
            "<a href='/seleziona_ddt_carico' class='btn'>&#8592; Torna alla selezione</a>"
            "</div></body></html>"
        )
        # Elimina il file caricato per errore dalla cartella carico
        try:
            os.remove(path)
        except Exception:
            pass
        return html_errore
    articoli_arricchiti = []
    for art in info.get('articoli', []):
        art_db = Articolo.query.filter_by(sku=art['codice']).first()
        cod_orig = art.get('codice_originale', art['codice'])
        articoli_arricchiti.append({
            'codice':           art['codice'],
            'codice_originale': cod_orig,
            'descrizione':      art.get('desc', ''),
            'qta':              art['qta'],
            'in_db':            art_db is not None,
            'stock_att':        art_db.stock if art_db else 0,
            'tradotto':         cod_orig != art['codice'],
        })
    problemi = []
    if not info.get('articoli'):
        problemi.append('Nessun articolo trovato dalla regex')
    for art in articoli_arricchiti:
        if not art['in_db']:
            problemi.append('Codice ' + art['codice'] + ' non presente nel magazzino (sara creato)')
    righe_tabella = ''
    for i, art in enumerate(articoli_arricchiti, 1):
        in_db_txt = 'OK' if art['in_db'] else 'Nuovo'
        in_db_cls = 'ok' if art['in_db'] else 'warn'
        trad_badge = (' <span style="color:#f0ad4e;font-size:0.65rem">(PDF: ' + art['codice_originale'] + ')</span>') if art['tradotto'] else ''
        row_id = 'dc-row-' + str(i)
        chk_js = "document.getElementById('" + row_id + "').style.opacity=this.checked?'0.35':'1';"
        righe_tabella += (
            '<tr id="' + row_id + '">'
            '<td style="color:#555;text-align:center;">'
            '<input type="checkbox" name="escludi" value="' + art['codice'] + '" '
            'onchange="' + chk_js + '" '
            'style="width:16px;height:16px;cursor:pointer;accent-color:#ff4d5e;"></td>'
            '<td style="color:#00d2ff;font-weight:bold;font-family:monospace">' + art['codice'] + trad_badge + '</td>'
            '<td style="color:#aaa;font-size:0.78rem">' + art['descrizione'] + '</td>'
            '<td style="color:#28a745;font-weight:bold">' + str(art['qta']) + '</td>'
            '<td class="' + in_db_cls + '">' + in_db_txt + '</td>'
            '<td style="color:#ccc">' + str(art['stock_att']) + '</td>'
            '</tr>'
        )
    problemi_html = ''
    if problemi:
        items = ''.join('<li>' + p + '</li>' for p in problemi)
        problemi_html = (
            '<div class="problems">'
            '<b style="color:#f0ad4e;font-size:0.8rem">Attenzione &mdash; problemi rilevati:</b>'
            '<ul>' + items + '</ul></div>'
        )
    forn_cls = 'ok' if info.get('fornitore') else 'warn'
    art_cls  = 'ok' if info.get('articoli') else 'err'
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Anteprima DDT Carico: " + filename + "</title>"
        "<style>"
        "*{box-sizing:border-box;margin:0;padding:0;}"
        "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
        ".box{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:28px;"
        "max-width:720px;width:100%;}"
        "h2{color:#f0ad4e;margin-bottom:6px;font-size:1.1rem;}"
        ".sub{color:#555;font-size:0.75rem;margin-bottom:20px;}"
        ".row{display:flex;gap:12px;margin-bottom:16px;}"
        ".field{flex:1;background:#0e0e0e;border:1px solid #222;border-radius:8px;padding:10px 14px;}"
        ".field label{font-size:0.62rem;color:#555;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:4px;}"
        ".field .val{font-size:0.95rem;font-weight:bold;}"
        ".ok{color:#28a745;}.warn{color:#f0ad4e;}.err{color:#ff4d5e;}"
        "table{width:100%;border-collapse:collapse;margin:14px 0;font-size:0.82rem;}"
        "th{background:#1a1a1a;color:#f0ad4e;padding:8px 10px;text-align:left;"
        "font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;}"
        "td{padding:8px 10px;border-bottom:1px solid #1a1a1a;}"
        ".problems{background:#1a0a0a;border:1px solid #dc354533;border-radius:8px;"
        "padding:12px;margin:14px 0;}"
        ".problems li{color:#ff4d5e;font-size:0.8rem;margin-top:4px;list-style:none;padding-left:10px;}"
        ".problems li::before{content:'- ';}"
        ".btns{display:flex;gap:10px;margin-top:20px;}"
        ".btn-ok{flex:1;background:#28a745;color:#000;border:none;padding:14px;"
        "border-radius:8px;font-weight:900;font-size:1rem;cursor:pointer;}"
        ".btn-ok:hover{background:#34d058;}"
        ".btn-cancel{background:#1a1a1a;color:#666;border:1px solid #333;padding:14px 20px;"
        "border-radius:8px;font-size:0.9rem;cursor:pointer;}"
        ".btn-cancel:hover{color:#aaa;}"
        "</style></head><body><div class='box'>"
        "<h2>Anteprima DDT Carico &mdash; " + filename + "</h2>"
        "<p class='sub'>Verifica i dati estratti dal PDF prima di confermare il carico a magazzino.</p>"
        "<div class='row'>"
        "<div class='field'><label>Fornitore</label><div class='val " + forn_cls + "'>" + str(info.get('fornitore', 'N/D')) + "</div></div>"
        "<div class='field'><label>N&#176; DDT</label><div class='val'>" + str(info.get('numero_ddt', 'N/D')) + "</div></div>"
        "<div class='field'><label>Data</label><div class='val'>" + str(info.get('data_ddt', 'N/D')) + "</div></div>"
        "<div class='field'><label>Articoli trovati</label><div class='val " + art_cls + "'>" + str(len(info.get('articoli', []))) + "</div></div>"
        "</div>"
        + problemi_html +
        "<table><tr>"
        "<th style='width:40px;text-align:center'>Escludi</th>"
        "<th>Codice</th><th>Descrizione</th><th>Q.ta</th><th>Nel DB</th><th>Stock att.</th>"
        "</tr>" + righe_tabella + "</table>"
        "<div class='btns'>"
        "<div style='background:#1a0a0a;border:1px solid #f0ad4e33;border-radius:8px;"
        "padding:10px;margin-bottom:10px;font-size:0.72rem;color:#888;'>"
        "&#9888;&#65039; Spunta le righe da <b style='color:#ff4d5e'>ESCLUDERE</b> dal carico (es. trasporto, imballi)."
        "</div>"
        "<form action='/conferma_ddt_carico/" + filename + "' method='post' style='flex:1'>"
        "<button type='submit' class='btn-ok'>CONFERMA &mdash; Carica a magazzino</button>"
        "</form>"
        "<form action='/annulla_ddt_carico/" + filename + "' method='post'>"
        "<button type='submit' class='btn-cancel'>Annulla</button>"
        "</form></div>"
        "</div></body></html>"
    )
    return html


@app.route('/conferma_ddt_carico/<path:filename>', methods=['POST'])
def conferma_ddt_carico(filename):
    """FASE 2: elabora il DDT carico confermato: aggiorna stock, ordini e StoricoOA."""
    path = os.path.join(CARICO_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('magazzino'))
    escludi = set(request.form.getlist('escludi'))
    info    = estrai_dati_completi_pdf_carico(filename)
    movimenti = []
    for art in info.get('articoli', []):
        cod = art['codice']
        if cod in escludi:
            scrivi_log(f"DDT CARICO {filename}: riga {cod} ESCLUSA dall'utente")
            continue
        try:
            qta_arrivata = int(art['qta'])
        except ValueError:
            continue
        articolo = Articolo.query.filter_by(sku=cod).first()
        if articolo:
            stock_prima    = articolo.stock    or 0
            ordinati_prima = articolo.ordinati or 0
            articolo.stock    += qta_arrivata
            articolo.ordinati  = max(0, ordinati_prima - qta_arrivata)
            movimenti.append({
                'codice': cod, 'descrizione': art.get('desc', ''),
                'qta': qta_arrivata,
                'stock_prima': stock_prima, 'stock_dopo': articolo.stock,
                'ordinati_prima': ordinati_prima, 'ordinati_dopo': articolo.ordinati,
            })
            # Scala StoricoOrdineFornitore FIFO
            qta_rim = qta_arrivata
            for riga in StoricoOrdineFornitore.query.filter_by(codice=cod).order_by(StoricoOrdineFornitore.id).all():
                if qta_rim <= 0:
                    break
                if riga.qta > 0:
                    scalato   = min(riga.qta, qta_rim)
                    riga.qta -= scalato
                    qta_rim  -= scalato
                    scrivi_log(f"DDT carico: OA {riga.id_ordine} cod {cod} -{scalato} (rimane {riga.qta})")
        else:
            db.session.add(Articolo(
                sku=cod, descrizione=art.get('desc', ''),
                stock=qta_arrivata, ordinati=0, incoming=0
            ))
            movimenti.append({
                'codice': cod, 'descrizione': art.get('desc', ''),
                'qta': qta_arrivata,
                'stock_prima': 0, 'stock_dopo': qta_arrivata,
                'ordinati_prima': 0, 'ordinati_dopo': 0, 'nuovo_articolo': True
            })
    # Salva CaricoDDT
    pdf_blob = None
    try:
        with open(path, 'rb') as pf:
            pdf_blob = pf.read()
    except Exception:
        pass
    carico = CaricoDDT(
        filename_pdf   = filename,
        numero_ddt     = info.get('numero_ddt', ''),
        fornitore      = info.get('fornitore', ''),
        data_ddt       = info.get('data_ddt', ''),
        movimenti_json = json.dumps(movimenti),
        pdf_blob       = pdf_blob,
        elaborato_il   = datetime.utcnow(),
        stornato       = False
    )
    db.session.add(carico)
    try:
        shutil.move(path, os.path.join(CARICO_DONE_FOLDER, filename))
    except Exception:
        pass
    db.session.commit()
    scrivi_log(f"DDT CARICO confermato: {filename} — {len(movimenti)} movimenti")
    return redirect(url_for('pagina_acquisti'))


@app.route('/annulla_ddt_carico/<path:filename>', methods=['POST'])
def annulla_ddt_carico(filename):
    """Annulla il DDT carico: rimuove il file dalla coda."""
    path = os.path.join(CARICO_FOLDER, filename)
    try:
        os.remove(path)
        scrivi_log(f"DDT CARICO annullato: {filename}")
    except Exception:
        pass
    return redirect(url_for('magazzino'))


@app.route('/seleziona_ddt_carico')
def seleziona_ddt_carico():
    """Mostra i DDT in attesa in CARICO_FOLDER e reindirizza all'anteprima."""
    files = sorted([f for f in os.listdir(CARICO_FOLDER) if f.lower().endswith('.pdf')])
    if not files:
        return redirect(url_for('pagina_acquisti'))
    if len(files) == 1:
        return redirect(url_for('anteprima_ddt_carico', filename=files[0]))
    # Multipli: pagina selezione
    items = ''.join(
        '<a href="/anteprima_ddt_carico/' + f + '" style="display:block;background:#141414;'
        'border:1px solid #2a2a2a;border-radius:8px;padding:14px 18px;margin-bottom:10px;'
        'color:#00d2ff;text-decoration:none;font-family:monospace;font-size:0.9rem;">'
        '📄 ' + f + '</a>'
        for f in files
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Seleziona DDT</title>"
        "<style>*{box-sizing:border-box;margin:0;padding:0;}"
        "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
        ".box{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:28px;"
        "max-width:500px;width:100%;}"
        "h2{color:#f0ad4e;margin-bottom:6px;font-size:1.1rem;}"
        ".sub{color:#555;font-size:0.75rem;margin-bottom:20px;}"
        "</style></head><body><div class='box'>"
        "<h2>📋 Seleziona DDT da elaborare</h2>"
        "<p class='sub'>Scegli quale DDT vuoi visualizzare e caricare a magazzino.</p>"
        + items +
        "<a href='/acquisti' style='display:block;margin-top:16px;color:#555;font-size:0.8rem;"
        "text-align:center;text-decoration:none;'>← Torna ad Acquisti</a>"
        "</div></body></html>"
    )


@app.route('/carica_da_ddt')
def carica_da_ddt():
    """Elabora tutti i DDT arrivo fornitore: carica lo stock.
    Salva i movimenti in CaricoDDT per permettere lo storno successivo."""
    files = [f for f in os.listdir(CARICO_FOLDER) if f.lower().endswith('.pdf')]
    if not files:
        return redirect(url_for('pagina_acquisti'))  # FIX: redirect invece di errore 400
    for f in files:
        info = estrai_dati_completi_pdf_carico(f)
        movimenti = []
        for art in info['articoli']:
            cod          = art['codice']
            qta_arrivata = int(art['qta'])
            articolo = Articolo.query.filter_by(sku=cod).first()
            if articolo:
                stock_prima    = articolo.stock or 0
                ordinati_prima = articolo.ordinati or 0
                articolo.stock    += qta_arrivata
                articolo.ordinati  = max(0, ordinati_prima - qta_arrivata)
                movimenti.append({
                    'codice':         cod,
                    'descrizione':    art.get('desc', ''),
                    'qta':            qta_arrivata,
                    'stock_prima':    stock_prima,
                    'stock_dopo':     articolo.stock,
                    'ordinati_prima': ordinati_prima,
                    'ordinati_dopo':  articolo.ordinati,
                })
                # ── scala StoricoOrdineFornitore (OA aperti) FIFO ──
                qta_rim = qta_arrivata
                for riga in StoricoOrdineFornitore.query.filter_by(codice=cod).order_by(StoricoOrdineFornitore.id).all():
                    if qta_rim <= 0:
                        break
                    if riga.qta > 0:
                        scalato    = min(riga.qta, qta_rim)
                        riga.qta  -= scalato
                        qta_rim   -= scalato
                        scrivi_log(f"DDT carico: OA {riga.id_ordine} cod {cod} -{scalato} (rimane {riga.qta})")
            else:
                db.session.add(Articolo(
                    sku=cod, descrizione=art.get('desc', ''),
                    stock=qta_arrivata, ordinati=0, incoming=0
                ))
                movimenti.append({
                    'codice':         cod,
                    'descrizione':    art.get('desc', ''),
                    'qta':            qta_arrivata,
                    'stock_prima':    0,
                    'stock_dopo':     qta_arrivata,
                    'ordinati_prima': 0,
                    'ordinati_dopo':  0,
                    'nuovo_articolo': True
                })

        # Leggi PDF come blob
        pdf_blob = None
        try:
            with open(os.path.join(CARICO_FOLDER, f), 'rb') as pf:
                pdf_blob = pf.read()
        except Exception:
            pass

        # Salva il record CaricoDDT
        carico = CaricoDDT(
            filename_pdf   = f,
            numero_ddt     = info.get('numero_ddt', '') or info.get('ordine_n', ''),
            fornitore      = info.get('fornitore', ''),
            data_ddt       = info.get('data_ddt', '') or info.get('data_evasione', ''),
            movimenti_json = json.dumps(movimenti),
            pdf_blob       = pdf_blob,
            elaborato_il   = datetime.utcnow(),
            stornato       = False
        )
        db.session.add(carico)

        try:
            shutil.move(os.path.join(CARICO_FOLDER, f), os.path.join(CARICO_DONE_FOLDER, f))
        except Exception:
            pass

    db.session.commit()
    scrivi_log(f"DDT carico elaborati: {files}")
    return redirect(url_for('pagina_acquisti'))  # FIX: torna ad acquisti
 
 
@app.route('/processa_acquisti')
def processa_acquisti():
    files = [f for f in os.listdir(ACQUISTI_FOLDER) if f.lower().endswith('.pdf')]
    if not files:
        return redirect(url_for('index'))
    for filename in files:
        info      = estrai_dati_ordine_acquisto(filename)
        id_ordine = info['ordine_n']
        for art in info['articoli']:
            cod       = art['codice']
            nuova_qta = int(art['qta'])
            storico_record = StoricoOrdineFornitore.query.filter_by(
                id_ordine=id_ordine, codice=cod).first()
            vecchia_qta = storico_record.qta if storico_record else 0
            differenza  = nuova_qta - vecchia_qta
            articolo = Articolo.query.filter_by(sku=cod).first()
            if not articolo:
                articolo = Articolo(sku=cod, descrizione=art.get('descrizione', ''))
                db.session.add(articolo)
                db.session.flush()
            if articolo.ordinati is None:
                articolo.ordinati = 0
            articolo.ordinati     += differenza
            articolo.fornitore     = info['fornitore']
            articolo.ordine_n      = id_ordine
            articolo.data_evasione = art.get('data_evasione', '')   # ← SALVA DATA
            aggiorna_storico_fornitori(cod, info['fornitore'])
            if storico_record:
                storico_record.qta           = nuova_qta
                storico_record.fornitore     = info['fornitore']
                storico_record.data_evasione = art.get('data_evasione', '')
                storico_record.filename_oa   = filename
            else:
                db.session.add(StoricoOrdineFornitore(
                    id_ordine=id_ordine, codice=cod, qta=nuova_qta,
                    fornitore=info['fornitore'],
                    data_evasione=art.get('data_evasione', ''),
                    filename_oa=filename))
        shutil.move(
            os.path.join(ACQUISTI_FOLDER, filename),
            os.path.join(ACQUISTI_DONE_FOLDER, filename))
    db.session.commit()
    scrivi_log(f"OA elaborati: {files}")
    return redirect(url_for('index'))
 
 
@app.route('/importazione_massiva', methods=['POST'])
def importazione_massiva():
    file = request.files.get('file_excel')
    if not file:
        return "Nessun file selezionato", 400
    try:
        filename = file.filename.lower()
        raw = file.read()
        import io
        df = None
        if filename.endswith('.xls'):
            try:
                df = pd.read_excel(io.BytesIO(raw), engine='xlrd')
            except Exception:
                try:
                    df = pd.read_excel(io.BytesIO(raw), engine='openpyxl')
                except Exception as e:
                    return f"Errore lettura XLS: {e}", 400
        elif filename.endswith('.xlsx'):
            try:
                df = pd.read_excel(io.BytesIO(raw), engine='openpyxl')
            except Exception as e:
                return f"Errore lettura XLSX: {e}", 400
        elif filename.endswith('.csv'):
            for enc in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    df = pd.read_csv(io.BytesIO(raw), encoding=enc, sep=None, engine='python')
                    break
                except Exception:
                    pass
            if df is None:
                return "Errore lettura CSV", 400
        else:
            return "Formato non supportato.", 400
        df.columns = [str(c).strip() for c in df.columns]
        # Colonne flessibili: accetta sia ARCODART che CACODART (entrambi presenti in Zucchetti)
        col_sku  = 'ARCODART' if 'ARCODART' in df.columns else 'CACODART'
        col_desc = 'ARDESART' if 'ARDESART' in df.columns else 'CADESART'
        if col_sku not in df.columns or 'ESISATTU' not in df.columns:
            return f"Colonne mancanti. Trovate: {list(df.columns)}", 400
        count_new = count_upd = 0
        for _, row in df.iterrows():
            sku_val = str(row[col_sku]).strip()
            if not sku_val or sku_val.lower() in ('nan', ''):
                continue
            desc_val    = str(row.get(col_desc, '')).strip()
            stock_val   = int(float(row.get('ESISATTU', 0) or 0))
            impegnato   = int(float(row.get('RISEATTU', 0) or 0))
            cod_esterno = str(row.get('CACODICE', '')).strip()
            if cod_esterno.lower() in ('nan', 'none', ''):
                cod_esterno = ''
            articolo = Articolo.query.filter_by(sku=sku_val).first()
            if articolo:
                articolo.descrizione    = desc_val
                articolo.stock          = stock_val
                articolo.incoming       = impegnato
                articolo.codice_esterno = cod_esterno
                count_upd += 1
            else:
                db.session.add(Articolo(
                    sku=sku_val, descrizione=desc_val,
                    stock=stock_val, incoming=impegnato,
                    ordinati=0, fornitore='N/D',
                    codice_esterno=cod_esterno))
                count_new += 1
        db.session.commit()
        scrivi_log(f"IMPORTAZIONE ZUCCHETTI: {count_new} nuovi, {count_upd} aggiornati.")
        return redirect(url_for('pagina_magazzino'))
    except Exception as e:
        db.session.rollback()
        return f"Errore importazione: {str(e)}", 500
 
 
@app.route('/rettifica_magazzino', methods=['POST'])
def rettifica_magazzino():
    """Rettifica manuale di Stock e Scorta Minima per un SKU.
    FIX BUG 1: dopo la rettifica ricalcola lo stato_generale di tutti i fascicoli
    che contengono questo SKU, così uno stato EVADIBILE può tornare a ORDINARE_ARTICOLI
    se lo stock è stato ridotto sotto la soglia necessaria.
    """
    try:
        data         = request.get_json(force=True)
        sku          = data.get('sku', '').strip()
        nuovo_stock  = int(data.get('stock', 0))
        nuova_scorta = int(data.get('scorta_minima', 5))
 
        articolo = Articolo.query.filter_by(sku=sku).first()
        if not articolo:
            return jsonify({"ok": False, "error": f"SKU {sku} non trovato"}), 404
 
        vecchio_stock  = articolo.stock
        vecchia_scorta = articolo.scorta_minima
        articolo.stock         = nuovo_stock
        articolo.scorta_minima = nuova_scorta
        db.session.commit()
 
        scrivi_log(
            f"RETTIFICA MANUALE [{sku}]: "
            f"stock {vecchio_stock}→{nuovo_stock} | "
            f"scorta_min {vecchia_scorta}→{nuova_scorta}"
        )

        # ── FIX BUG 1: Ricalcola stato_generale di tutti i fascicoli
        # che hanno questo SKU tra i loro articoli ancora non evasi.
        # Questo permette a uno stato EVADIBILE di tornare indietro
        # (es. a ORDINARE_ARTICOLI) se lo stock viene corretto al ribasso.
        try:
            _ricalcola_stati_fascicoli_per_sku(sku)
        except Exception as _e:
            scrivi_log(f"WARN ricalcolo stati fascicoli dopo rettifica {sku}: {_e}")

        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


def _ricalcola_stati_fascicoli_per_sku(sku):
    """
    Ricalcola lo stato_generale di tutti i fascicoli che contengono lo SKU dato
    tra gli articoli non ancora completamente evasi.
    Chiamato dopo rettifica magazzino o importazione Zucchetti per garantire
    che gli stati non restino "bloccati" (es. EVADIBILE che non torna indietro).
    """
    if not os.path.exists(UPLOAD_FOLDER):
        return
    mappa_esterni  = get_mappa_codici_esterni()
    magazzino      = get_magazzino()
    evasi          = get_evasi()
    distinta_map   = get_distinta_map()
    oa_per_codice  = {}
    try:
        for r in StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).all():
            _stato_oa = StatoOrdineAcquisto.query.filter_by(filename=r.filename_oa).first()
            _stato_lbl = '✅ IN ATTESA' if (_stato_oa and _stato_oa.confermato) else '🔄 IN LAVORAZIONE'
            oa_per_codice.setdefault(r.codice, []).append({
                'oa_n': r.id_ordine, 'fornitore': r.fornitore or '—',
                'qta': r.qta, 'data_arr': (_stato_oa.data_consegna if _stato_oa else ''),
                'stato_oa': _stato_lbl, 'filename_oa': r.filename_oa,
            })
    except Exception:
        pass

    # Precalcola stock/ordinati residuo per allocazione semplificata
    stock_residuo    = {s: m.get('stock', 0) for s, m in magazzino.items()}
    ordinati_residuo = {s: m.get('ordinati', 0) for s, m in magazzino.items()}
    bom_bypass_map   = {}
    try:
        for _f in FascicoloOrdine.query.all():
            try:
                bom_bypass_map[_f.conferma] = set(json.loads(_f.bom_bypass_json or '[]'))
            except Exception:
                bom_bypass_map[_f.conferma] = set()
    except Exception:
        pass

    all_co_files = sorted([f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')])
    for co_file in all_co_files:
        try:
            info = estrai_dati_completi_pdf(co_file, mappa_esterni)
            conferma = info['conferma']
            # Controlla se questo ordine usa lo SKU interessato
            usa_sku = any(a['codice'] == sku for a in info['articoli'])
            if not usa_sku:
                continue
            # Calcola allocazione semplificata per questo ordine
            arts_calc = []
            for art in info['articoli']:
                id_art    = f"{conferma}_{art['codice']}".replace(' ', '')
                qta_evasa = evasi.get(id_art, 0)
                qta_res   = max(0, int(art['qta']) - qta_evasa)
                s = art['codice']
                in_casa   = min(max(0, stock_residuo.get(s, 0)), qta_res)
                mancanti  = qta_res - in_casa
                in_arrivo = min(max(0, ordinati_residuo.get(s, 0)), mancanti)
                stock_residuo[s]    -= in_casa
                ordinati_residuo[s] -= in_arrivo
                imb = Imballaggio.query.filter_by(conferma=conferma, codice=s).first()
                art['evaso']         = qta_evasa >= int(art['qta'])
                art['qta_evasa']     = qta_evasa
                art['in_casa']       = in_casa
                art['in_arrivo_tot'] = in_arrivo
                art['mancanti']      = max(0, mancanti - in_arrivo)
                art['oa_dettaglio']  = oa_per_codice.get(s, [])
                art['stato_ordine']  = 'DA_ORDINARE' if art['mancanti'] > 0 else 'ORDINE_CONFERMATO'
                art['qta_imballare'] = (imb.qta_imballare if imb else 0)
                art['imballato']     = (imb.imballato if imb else False)
                art['bom_componenti']= []
                art['bom_bypassed']  = s in bom_bypass_map.get(conferma, set())
                arts_calc.append(art)
            nuovo_stato = calcola_stato_generale(arts_calc)
            fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if fasc and fasc.stato_generale != nuovo_stato:
                fasc.stato_generale = nuovo_stato
                fasc.aggiornato_il  = datetime.utcnow()
                scrivi_log(f"RICALCOLO STATO [{conferma}]: {fasc.stato_generale} → {nuovo_stato} (dopo rettifica {sku})")
        except Exception as _inner:
            pass
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
 
 
 
@app.route('/get_magazzino_wms')
def get_magazzino_wms():
    """Restituisce TUTTI gli articoli del DB per la vista magazzino WMS."""
    try:
        magazzino = get_magazzino()  # {sku: to_dict()} — dal DB PostgreSQL
        scrivi_log(f"get_magazzino_wms: {len(magazzino)} articoli nel DB")
        if not magazzino:
            scrivi_log("WARN: DB articoli vuoto — importare magazzino_is.xls")
 
        # Impegni dagli ordini attivi (PDF su filesystem — potrebbero mancare su Railway)
        impegni = {}
        try:
            if os.path.exists(UPLOAD_FOLDER):
                files_co = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')]
                evasi    = get_evasi()
                for f in files_co:
                    try:
                        info = estrai_dati_completi_pdf(f)
                        for art in info['articoli']:
                            id_art      = f"{info['conferma']}_{art['codice']}".replace(' ', '')
                            qta_evasa   = evasi.get(id_art, 0)
                            qta_residua = max(0, int(art['qta']) - qta_evasa)
                            if qta_residua == 0:
                                continue
                            cod = art['codice']
                            if cod not in impegni:
                                impegni[cod] = []
                            impegni[cod].append({
                                'conferma':    info['conferma'],
                                'cliente':     info['cliente'],
                                'qta_ord':     int(art['qta']),
                                'qta_evasa':   qta_evasa,
                                'qta_residua': qta_residua,
                            })
                    except Exception:
                        continue
        except Exception:
            pass  # filesystem non disponibile, impegni = 0
 
        # ── ORDINATI: somma da StoricoOrdineFornitore (fonte accurata per dettaglio OA) ──
        # Se storico ha dati usiamo la SUM di tutte le righe attive (qta > 0).
        # Fallback su Articolo.ordinati per articoli non ancora presenti in storico.
        ordinati_da_storico = {}
        try:
            for r in StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).all():
                if r.codice:
                    # SUM: 3 OA da 50 pz ciascuno → ordinati = 150
                    ordinati_da_storico[r.codice] = ordinati_da_storico.get(r.codice, 0) + r.qta
        except Exception:
            pass
 
        # Fallback articoli_evasi rimosso — storico_ordini_fornitore è la fonte unica
 
        ordinati_da_db = {}
        for sku, m in magazzino.items():
            val_storico = ordinati_da_storico.get(sku, 0)
            val_articolo = int(m.get('ordinati', 0) or 0)
            # Preferisci storico (più preciso); fallback su Articolo.ordinati
            ordinati_da_db[sku] = val_storico if val_storico > 0 else val_articolo
        # Aggiungi SKU presenti in storico ma non ancora in magazzino
        for sku, val in ordinati_da_storico.items():
            if sku not in ordinati_da_db:
                ordinati_da_db[sku] = val
 
        tutti_sku = set(magazzino.keys()) | set(ordinati_da_db.keys())

        # ── OA DETTAGLIO per popup: {sku: [{id_ordine, fornitore, qta, data_evasione}]} ──
        oa_dettaglio = {}
        try:
            for r in StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).all():
                if not r.codice:
                    continue
                if r.codice not in oa_dettaglio:
                    oa_dettaglio[r.codice] = []
                oa_dettaglio[r.codice].append({
                    'id_ordine':    r.id_ordine,
                    'fornitore':    r.fornitore or 'N/D',
                    'qta':          r.qta,
                    'data_evasione': r.data_evasione or '—',
                })
        except Exception:
            pass
 
        riepilogo = {}
        for sku in tutti_sku:
            m          = magazzino.get(sku, {})
            imp_list   = impegni.get(sku, [])
            imp_totale = sum(i['qta_residua'] for i in imp_list)
            ordinati   = ordinati_da_db.get(sku, 0)
            stock      = int(m.get('stock', 0) or 0)
            scorta_min = int(m.get('scorta_minima', 5) or 5)
            dispo      = stock + ordinati - imp_totale
            fabbisogno = max(0, -dispo)
 
            if stock <= 0 and imp_totale > 0:
                stato = 'esaurito'
            elif dispo <= scorta_min:
                stato = 'sottoscorta'
            else:
                stato = 'ok'
 
            riepilogo[sku] = {
                'desc':             m.get('desc', sku),
                'stock':            stock,
                'impegnato':        imp_totale,
                'ordinati':         ordinati,
                'scorta_minima':    scorta_min,
                'dispo_netta':      dispo,
                'fabbisogno_netto': fabbisogno,
                'fornitore':        m.get('fornitore', 'N/D'),
                'stato_scorta':     stato,
                'ordini':           imp_list,
                'oa_list':          oa_dettaglio.get(sku, []),
            }
 
        scrivi_log(f"get_magazzino_wms: {len(riepilogo)} SKU, {len(ordinati_da_db)} con ordinati nel DB")
        return jsonify(riepilogo)
 
    except Exception as e:
        scrivi_log(f"ERRORE get_magazzino_wms: {e}")
        return jsonify({'__errore__': str(e)}), 500
 
@app.route('/get_produzione')
def get_produzione():
    files     = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')]
    riepilogo = {}
    magazzino = get_magazzino()
    evasi     = get_evasi()
    storico_fornitori = get_storico_fornitori_dict()
 
    for f in files:
        info = estrai_dati_completi_pdf(f)
        for art in info['articoli']:
            id_art    = f"{info['conferma']}_{art['codice']}".replace(" ", "")
            qta_evasa = evasi.get(id_art, 0)
            qta_ord   = int(art['qta'])
 
            # ← FIX: conta solo la parte NON ancora evasa come impegno
            qta_impegnata = max(0, qta_ord - qta_evasa)
            if qta_impegnata == 0:
                continue   # tutto evaso, non impegna più niente
 
            cod = art['codice']
            if cod not in riepilogo:
                m = magazzino.get(cod, {
                    "stock": 0, "ordinati": 0, "incoming": 0,
                    "fornitore": "N/D", "ordine_n": "N/D", "scorta_minima": 5
                })
                riepilogo[cod] = {
                    "desc": art.get('desc', ''), "impegnato": 0,
                    "stock": m.get('stock', 0), "ordinati": m.get('ordinati', 0),
                    "incoming": m.get('incoming', 0), "fornitore": m.get('fornitore', 'N/D'),
                    "ordine_n": m.get('ordine_n', 'N/D'),
                    "scorta_minima": m.get('scorta_minima', 5),
                    "suggerimenti": storico_fornitori.get(cod, ["Nessun dato storico"]),
                    "ordini": []   # ← lista ordini che impegnano questo SKU
                }
            riepilogo[cod]["impegnato"] += qta_impegnata
            riepilogo[cod]["ordini"].append({
                "conferma": info['conferma'],
                "cliente":  info['cliente'],
                "qta_ord":  qta_ord,
                "qta_evasa": qta_evasa,
                "qta_residua": qta_impegnata
            })
 
    for cod, r in riepilogo.items():
        fabbisogno            = r['impegnato'] - (r['stock'] + r['ordinati'])
        r['fabbisogno_netto'] = max(0, fabbisogno)
        r['dispo_netta']      = (r['stock'] + r['ordinati']) - r['impegnato']
        r['stato_scorta']     = 'ok'
        if r['dispo_netta'] < 0:
            r['stato_scorta'] = 'esaurito'
        elif r['dispo_netta'] <= r['scorta_minima']:
            r['stato_scorta'] = 'sottoscorta'
    return jsonify(riepilogo)
 
 
@app.route('/elimina_ordine', methods=['POST'])
def elimina_ordine():
    """Elimina un ordine (PDF + nota) solo se nessun articolo è stato evaso."""
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        filename = data.get('filename', '').strip()
 
        # Controlla se ci sono articoli evasi per questa conferma
        evasi_esistenti = ArticoloEvaso.query.filter(
            ArticoloEvaso.id_art.like(f"{conferma}_%")
        ).first()
        if evasi_esistenti:
            return jsonify({
                "ok": False,
                "error": f"Impossibile eliminare: l'ordine {conferma} ha articoli già evasi!"
            }), 200
 
        # Elimina PDF fisico
        pdf_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
 
        # Elimina nota se esiste
        nota = NoteOrdine.query.filter_by(conferma=conferma).first()
        if nota:
            db.session.delete(nota)
 
        # Elimina imballaggi se esistono
        Imballaggio.query.filter_by(conferma=conferma).delete()
 
        db.session.commit()
        scrivi_log(f"ORDINE ELIMINATO: {conferma} — {filename}")
        return jsonify({"ok": True}), 200
 
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
def _estrai_da_path(path, mappa_esterni=None):
    """Come estrai_dati_completi_pdf ma accetta un percorso assoluto invece del filename."""
    dati = {"conferma": "N/D", "cliente": "Sconosciuto", "data": "N/D", "articoli": []}
    if mappa_esterni is None:
        mappa_esterni = get_mappa_codici_esterni()
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo  = "".join([page.extract_text() for page in reader.pages])
        m = re.search(r'(\d+)\s*/\s*IS', testo)
        if m: dati['conferma'] = f"{m.group(1)}/IS"
        m = re.search(r'(\d{2}/\d{2}/\d{4})', testo)
        if m: dati['data'] = m.group(1)
        if "Destinatario:" in testo:
            parti = testo.split("Destinatario:")[1]
            ll = [l.strip() for l in parti.split('\n') if l.strip()]
            if len(ll) >= 2: dati['cliente'] = ll[1]
        for riga in testo.split('\n'):
            match_art = re.search(r'^([A-Z0-9\.\-]+)\s+(.*?)\s+n\.\s+([\d\.]+)', riga)
            if match_art and match_art.group(1) not in ['IRON','Cod.','Vs.','Pag.']:
                cod_raw  = match_art.group(1)
                cod_int  = risolvi_codice(cod_raw, mappa_esterni)
                dati['articoli'].append({
                    'codice': cod_int, 'codice_originale': cod_raw,
                    'desc': match_art.group(2).strip(),
                    'qta':  match_art.group(3).replace('.','').split(',')[0]
                })
    except Exception as e:
        print(f"_estrai_da_path {path}: {e}")
    return dati


@app.route('/get_dati_ordine/<path:filename>')
def get_dati_ordine(filename):
    """Restituisce i dati attuali di un ordine con le eventuali modifiche già salvate.
    Cerca il PDF in UPLOAD_FOLDER prima, poi in DONE_FOLDER (ordini già elaborati).
    Se il PDF non è disponibile ricostruisce i dati dal DB.
    """
    try:
        # ── Cerca il PDF in tutte le posizioni possibili ──────────────────────
        mappa_esterni = get_mappa_codici_esterni()
        info = None

        for cartella in [UPLOAD_FOLDER, DONE_FOLDER]:
            path = os.path.join(cartella, filename)
            if os.path.exists(path):
                info = estrai_dati_completi_pdf(filename) if cartella == UPLOAD_FOLDER \
                       else _estrai_da_path(path, mappa_esterni)
                break

        # ── Fallback: ricostruisce dal DB tramite FascicoloOrdine ─────────────
        if info is None or not info.get('articoli'):
            # Prova a ricavare la conferma dal filename (es. "181_IS_..." → "181/IS")
            conf_guess = re.sub(r'[_\s]+', '/', filename.replace('.pdf','').replace('.PDF',''))
            conf_guess = re.search(r'(\d+/IS)', conf_guess)
            conf_guess = conf_guess.group(1) if conf_guess else None

            if conf_guess:
                fasc = FascicoloOrdine.query.filter_by(conferma=conf_guess).first()
                if fasc:
                    # Ricostruisce articoli da ModificaOrdine + ArticoloEvaso
                    evasi    = get_evasi()
                    tutte_mod = ModificaOrdine.query.filter_by(conferma=conf_guess).all()
                    articoli_db = []
                    for m in tutte_mod:
                        id_art    = f"{conf_guess}_{m.codice}".replace(" ", "")
                        qta_evasa = evasi.get(id_art, 0)
                        art_db    = Articolo.query.filter_by(sku=m.codice).first()
                        articoli_db.append({
                            'codice': m.codice,
                            'desc':   art_db.descrizione if art_db else m.codice,
                            'qta':    str(m.qta_nuova) if m.qta_nuova else '0'
                        })
                    info = {
                        'conferma': conf_guess,
                        'cliente':  fasc.cliente or '',
                        'articoli': articoli_db
                    }

        if info is None:
            return jsonify({'ok': False, 'error': 'Ordine non trovato — file PDF e dati DB assenti'}), 404

        # ── Assembla risposta ─────────────────────────────────────────────────
        conferma = info['conferma']
        evasi    = get_evasi()
        modifiche = {m.codice: m.qta_nuova
                     for m in ModificaOrdine.query.filter_by(conferma=conferma).all()}
        articoli_out = []
        for art in info['articoli']:
            id_art        = f"{conferma}_{art['codice']}".replace(" ", "")
            qta_evasa     = evasi.get(id_art, 0)
            qta_originale = int(art['qta'])
            qta_attuale   = modifiche.get(art['codice'], qta_originale)
            articoli_out.append({
                'codice':        art['codice'],
                'desc':          art.get('desc', art['codice']),
                'qta_originale': qta_originale,
                'qta_attuale':   qta_attuale,
                'qta_evasa':     qta_evasa,
                'rimosso':       (qta_attuale == 0)
            })

        if not articoli_out:
            return jsonify({'ok': False,
                            'error': 'Nessun articolo trovato — il PDF potrebbe non essere leggibile'}), 404

        return jsonify({'ok': True, 'conferma': conferma, 'articoli': articoli_out})

    except Exception as e:
        scrivi_log(f"ERRORE get_dati_ordine {filename}: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
@app.route('/salva_modifica_ordine', methods=['POST'])
def salva_modifica_ordine():
    """Salva le modifiche manuali a un ordine (quantità e/o rimozione articoli)."""
    try:
        data      = request.get_json(force=True)
        conferma  = data.get('conferma', '').strip()
        modifiche = data.get('modifiche', [])
        if not conferma:
            return jsonify({'ok': False, 'error': 'Conferma mancante'}), 400

        ModificaOrdine.query.filter_by(conferma=conferma).delete()
        for m in modifiche:
            codice    = m.get('codice', '').strip()
            qta_nuova = int(m.get('qta_nuova', 0))
            if not codice:
                continue
            rec = ModificaOrdine(conferma=conferma, codice=codice, qta_nuova=qta_nuova)
            # FIX: salva anche la descrizione se presente (articoli aggiunti manualmente)
            # Usa setattr per evitare crash se la colonna non esiste ancora nel DB
            desc = m.get('desc', '').strip()
            try:
                rec.descrizione = desc
            except Exception:
                pass
            db.session.add(rec)

        # Resetta stato_generale del fascicolo così viene ricalcolato
        try:
            fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if fasc:
                fasc.stato_generale = ''
                fasc.aggiornato_il  = datetime.utcnow()
        except Exception:
            pass

        db.session.commit()
        scrivi_log(f"ORDINE MODIFICATO: {conferma} — {len(modifiche)} righe")
        return jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE salva_modifica_ordine {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
 
@app.route('/api/aggiorna_evasi', methods=['POST'])
def api_aggiorna_evasi():
    """
    Aggiorna le quantità evase per gli articoli di un ordine.
    Chiamato in parallelo a salva_modifica_ordine dal modale modifica.
    Payload: { evasi: [{codice, qta_evasa, conferma}] }
    """
    try:
        data   = request.get_json(force=True)
        evasi  = data.get('evasi', [])
        if not evasi:
            return jsonify({'ok': True}), 200
        for e in evasi:
            conferma  = str(e.get('conferma', '')).strip()
            codice    = str(e.get('codice', '')).strip()
            qta_evasa = int(e.get('qta_evasa', 0))
            if not conferma or not codice:
                continue
            id_art = f"{conferma}_{codice}".replace(' ', '')
            ev = ArticoloEvaso.query.filter_by(id_art=id_art).first()
            if ev:
                ev.qta_evasa = max(0, qta_evasa)
            else:
                if qta_evasa > 0:
                    db.session.add(ArticoloEvaso(
                        id_art=id_art, evaso=(qta_evasa > 0), qta_evasa=qta_evasa))
        db.session.commit()
        return jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE api_aggiorna_evasi: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/imballaggio_massivo/<path:filename>', methods=['POST'])
def imballaggio_massivo(filename):
    """Manda in imballaggio tutti gli articoli evadibili di un ordine in un colpo solo."""
    info     = estrai_dati_completi_pdf(filename)
    conferma = info['conferma']
    evasi    = get_evasi()
    magazzino = get_magazzino()
 
    for art in info['articoli']:
        codice    = art['codice']
        id_art    = f"{conferma}_{codice}".replace(" ", "")
        qta_evasa = evasi.get(id_art, 0)
        qta_imb   = max(0, int(art['qta']) - qta_evasa)
        m         = magazzino.get(codice, {})
        stock     = m.get('stock', 0)
        ordinati  = m.get('ordinati', 0)
 
        # Manda in imballaggio solo se ha disponibilità
        if qta_imb > 0 and (stock + ordinati) >= qta_imb:
            existing = Imballaggio.query.filter_by(conferma=conferma, codice=codice).first()
            if existing:
                existing.qta_imballare = qta_imb
                existing.imballato     = False
            else:
                db.session.add(Imballaggio(
                    conferma=conferma, filename=filename,
                    codice=codice, qta_imballare=qta_imb, imballato=False
                ))
 
    db.session.commit()
    scrivi_log(f"IMBALLAGGIO MASSIVO: {conferma}")
    return redirect(url_for('index'))
 
 
@app.route('/annulla_ordine', methods=['POST'])
def annulla_ordine():
    """
    Annulla un ordine totalmente o parzialmente.
    - Se nessun articolo evaso → elimina tutto (annullamento totale)
    - Se alcuni articoli evasi → rimuove solo gli articoli non evasi dall'impegno
      e segna l'ordine come annullato parzialmente nel DB
    """
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        filename = data.get('filename', '').strip()
 
        # Verifica articoli evasi
        evasi_list = ArticoloEvaso.query.filter(
            ArticoloEvaso.id_art.like(f"{conferma}_%"),
            ArticoloEvaso.evaso == True
        ).all()
 
        if not evasi_list:
            # ANNULLAMENTO TOTALE — nessun evaso, elimina tutto
            pdf_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            nota = NoteOrdine.query.filter_by(conferma=conferma).first()
            if nota:
                db.session.delete(nota)
            Imballaggio.query.filter_by(conferma=conferma).delete()
            db.session.commit()
            scrivi_log(f"ORDINE ANNULLATO TOTALE: {conferma}")
            return jsonify({"ok": True, "tipo": "totale"}), 200
 
        else:
            # ANNULLAMENTO PARZIALE — segna gli articoli non evasi come annullati
            # usando la tabella NoteOrdine per marcare l'ordine
            nota = NoteOrdine.query.filter_by(conferma=conferma).first()
            prefisso = "[ANNULLATO PARZ.] "
            if nota:
                if prefisso not in nota.nota:
                    nota.nota = prefisso + nota.nota
            else:
                db.session.add(NoteOrdine(
                    conferma=conferma,
                    nota=f"{prefisso}Articoli non evasi rimossi dall'impegno"
                ))
            # Rimuovi imballaggi per articoli non evasi
            info = estrai_dati_completi_pdf(filename)
            evasi_codici = {e.id_art.replace(f"{conferma}_", "") for e in evasi_list}
            for art in info['articoli']:
                if art['codice'] not in evasi_codici:
                    Imballaggio.query.filter_by(
                        conferma=conferma, codice=art['codice']
                    ).delete()
            db.session.commit()
            scrivi_log(f"ORDINE ANNULLATO PARZIALE: {conferma} — evasi: {evasi_codici}")
            return jsonify({"ok": True, "tipo": "parziale"}), 200
 
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route('/api/distinta_base', methods=['GET'])
def api_get_distinta():
    """Restituisce tutte le distinte base con info articoli."""
    articoli = {a.sku: {'desc': a.descrizione, 'stock': a.stock} for a in Articolo.query.all()}
    risultato = {}
    for d in DistintaBase.query.order_by(DistintaBase.codice_padre, DistintaBase.livello).all():
        if d.codice_padre not in risultato:
            info_padre = articoli.get(d.codice_padre, {'desc': '', 'stock': 0})
            risultato[d.codice_padre] = {
                'desc':     info_padre['desc'],
                'stock':    info_padre['stock'],
                'figli':    []
            }
        info_figlio = articoli.get(d.codice_figlio, {'desc': '', 'stock': 0})
        risultato[d.codice_padre]['figli'].append({
            'id':             d.id,
            'codice_figlio':  d.codice_figlio,
            'desc_figlio':    info_figlio['desc'],
            'stock_figlio':   info_figlio['stock'],
            'quantita':       d.quantita,
            'livello':        d.livello,
            'note':           d.note
        })
    return jsonify(risultato)
 
 
@app.route('/api/distinta_base', methods=['POST'])
def api_add_distinta():
    """Aggiunge una riga alla distinta base."""
    try:
        data = request.get_json(force=True)
        padre  = data.get('codice_padre', '').strip().upper()
        figlio = data.get('codice_figlio', '').strip().upper()
        qta    = float(data.get('quantita', 1))
        liv    = int(data.get('livello', 1))
        note   = data.get('note', '').strip()
        if not padre or not figlio:
            return jsonify({"ok": False, "error": "Codice padre e figlio obbligatori"}), 400
        existing = DistintaBase.query.filter_by(codice_padre=padre, codice_figlio=figlio).first()
        if existing:
            existing.quantita = qta
            existing.livello  = liv
            existing.note     = note
        else:
            db.session.add(DistintaBase(
                codice_padre=padre, codice_figlio=figlio,
                quantita=qta, livello=liv, note=note
            ))
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route('/api/distinta_base/<int:id_db>', methods=['DELETE'])
def api_del_distinta(id_db):
    """Elimina una riga dalla distinta base."""
    try:
        riga = DistintaBase.query.get_or_404(id_db)
        db.session.delete(riga)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route('/api/articoli_autocomplete')
@app.route('/api/cerca_articoli')   # FIX: alias usato dal modale modifica ordine
def api_articoli_autocomplete():
    """Restituisce lista articoli per autocomplete (modale modifica + distinta base)."""
    q = request.args.get('q', '').strip()
    query = Articolo.query
    if q:
        query = query.filter(
            db.or_(
                Articolo.sku.ilike(f'%{q}%'),
                Articolo.descrizione.ilike(f'%{q}%')
            )
        )
    articoli = query.order_by(Articolo.sku).limit(15).all()
    return jsonify([{
        'sku':   a.sku,
        'desc':  a.descrizione or '',
        'stock': a.stock or 0
    } for a in articoli])
 
 
@app.route('/distinta_base')
def pagina_distinta_base():
    return render_template('distinta_base.html')
 
 
@app.route('/acquisti')
def pagina_acquisti():
    from datetime import date, timedelta
    oa_files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER) if f.lower().endswith('.pdf')] \
               if os.path.exists(ACQUISTI_DONE_FOLDER) else []
    active_solleciti = get_slots('fornitori')
    conta_fornitori  = len([x for x in active_solleciti if x.strip()])
    oggi    = date.today()
    domani  = oggi + timedelta(days=1)
    ordini_acquisto = []
    for f in oa_files:
        info  = estrai_dati_ordine_acquisto(f)
        stato = StatoOrdineAcquisto.query.filter_by(filename=f).first()
        # FIX 3: arricchisci qta articoli con residuo reale da StoricoOrdineFornitore
        for art in info.get('articoli', []):
            storico = StoricoOrdineFornitore.query.filter_by(
                filename_oa = f,
                codice      = art['codice']
            ).first()
            if storico:
                art['qta_originale'] = art['qta']          # qta nel PDF
                art['qta_residua']   = storico.qta          # qta ancora da ricevere
                art['qta_ricevuta']  = max(0, int(art['qta']) - storico.qta)
            else:
                # FIX v4: nessun record storico → fallback su CaricoDDT movimenti_json
                art['qta_originale'] = art['qta']
                try:
                    qta_orig = int(art['qta'])
                except (ValueError, TypeError):
                    qta_orig = 0
                ricevuto_ddt = 0
                try:
                    carichi = CaricoDDT.query.filter_by(stornato=False).all()
                    for carico in carichi:
                        for mov in json.loads(carico.movimenti_json or '[]'):
                            if mov.get('codice') == art['codice']:
                                ricevuto_ddt += int(mov.get('qta', 0))
                except Exception:
                    pass
                art['qta_ricevuta'] = min(ricevuto_ddt, qta_orig)
                art['qta_residua']  = max(0, qta_orig - art['qta_ricevuta'])
        info['filename']      = f
        info['in_slots']      = [i + 1 for i, name in enumerate(active_solleciti) if name == f]
        info['data_consegna'] = stato.data_consegna if stato else ''
        info['confermato']    = stato.confermato    if stato else False
        # Se il numero OA è stato inserito manualmente sovrascrive quello estratto dal PDF
        ordine_n_manuale = getattr(stato, 'ordine_n', '') if stato else ''
        if ordine_n_manuale:
            info['ordine_n'] = ordine_n_manuale
        nota_salvata = stato.nota_interna if stato else ''
        # Se la nota è vuota e c'è un rif. fornitore estratto dal PDF, pre-compilala
        if not nota_salvata and info.get('rif_fornitore'):
            nota_salvata = f"Rif. fornitore: {info['rif_fornitore']}"
        info['nota_interna']  = nota_salvata
        # Calcola stato_label secondo logica completa
        info['ritiro_proprio'] = stato.ritiro_proprio if stato else False
        info['data_imminente'] = False
        if not stato or not stato.confermato:
            info['stato_label'] = 'ORDINE_DA_CONFERMARE'
        else:
            # Confermato — calcola se in arrivo o da ritirare
            data_cons = stato.data_consegna if stato else ''
            if data_cons:
                try:
                    d_parts = data_cons.split('/')
                    d_cons  = date(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
                    delta   = (d_cons - oggi).days
                    if stato.ritiro_proprio:
                        # RITIRIAMO NOI:
                        # DA RITIRARE solo se data è oggi, scaduta, o entro 2 giorni
                        # Se data è lontana → ORDINE CONFERMATO (ci organizziamo dopo)
                        if delta <= 2:
                            info['stato_label'] = 'ORDINE_DA_RITIRARE'
                        else:
                            info['stato_label'] = 'ORDINE_CONFERMATO'
                    else:
                        # CONSEGNANO LORO: IN ARRIVO se entro 2 giorni, altrimenti CONFERMATO
                        if delta <= 2:
                            info['stato_label'] = 'ORDINE_IN_ARRIVO'
                        else:
                            info['stato_label'] = 'ORDINE_CONFERMATO'
                except Exception:
                    info['stato_label'] = 'ORDINE_CONFERMATO'
            else:
                info['stato_label'] = 'ORDINE_CONFERMATO'
        # Data imminente (entro domani) — solo badge visivo, niente urgenza automatica
        info['data_imminente'] = False
        if stato and stato.data_consegna:
            try:
                d_parts = stato.data_consegna.split('/')
                d_cons  = date(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
                if d_cons <= domani:
                    info['data_imminente'] = True
            except Exception:
                pass
        # Badge NEW: OA caricato nelle ultime 48h (usa mtime del file PDF)
        info['is_new'] = False
        try:
            pdf_path = os.path.join(ACQUISTI_DONE_FOLDER, f)
            mtime = datetime.utcfromtimestamp(os.path.getmtime(pdf_path))
            info['is_new'] = (datetime.utcnow() - mtime).total_seconds() < 48 * 3600
        except Exception:
            pass
        # ORDINE_RICEVUTO: tutti gli articoli dello StoricoOrdineFornitore hanno qta==0
        # OPPURE (fallback) tutti gli articoli hanno qta_residua==0 calcolata sopra
        try:
            all_storico_oa = StoricoOrdineFornitore.query.filter_by(filename_oa=f).all()
            storico_completato = all_storico_oa and all(r.qta == 0 for r in all_storico_oa)
            # Fallback: controlla qta_residua già calcolata sugli articoli dell'OA
            articoli_oa = info.get('articoli', [])
            residuo_completato = bool(articoli_oa) and all(
                int(art.get('qta_residua', art.get('qta', 1))) == 0
                for art in articoli_oa
            )
            if storico_completato or residuo_completato:
                info['stato_label'] = 'ORDINE_RICEVUTO'
                # pct ricevimento 100%
                for art in articoli_oa:
                    art['qta_ricevuta'] = int(art.get('qta', 0))
                    art['qta_residua']  = 0
        except Exception:
            pass
        ordini_acquisto.append(info)
    return render_template('acquisti.html',
                           ordini_acquisto=ordini_acquisto,
                           attivi_fornitori=conta_fornitori)
 
 
@app.route('/programma_ritiro')
def programma_ritiro():
    """Mostra solo gli OA con Ritiriamo Noi e data evasione = DOMANI."""
    from datetime import date, timedelta
    domani = date.today() + timedelta(days=1)
    domani_str = domani.strftime('%d/%m/%Y')
 
    oa_files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER) if f.lower().endswith('.pdf')] \
               if os.path.exists(ACQUISTI_DONE_FOLDER) else []
 
    da_ritirare = []
    for f in oa_files:
        stato = StatoOrdineAcquisto.query.filter_by(filename=f).first()
        if not stato:
            continue
        if not stato.ritiro_proprio:
            continue
        # Controlla data_consegna OA == domani
        data_oa = stato.data_consegna or ''
        if data_oa == domani_str:
            info = estrai_dati_ordine_acquisto(f)
            info['filename']     = f
            info['data_consegna'] = data_oa
            info['confermato']   = stato.confermato
            info['nota_interna'] = stato.nota_interna or ''
            # Articoli con data diversa da domani → evidenziati
            for art in info['articoli']:
                art['ritiro_diverso'] = bool(art['data_art'] and art['data_art'] != domani_str)
            da_ritirare.append(info)
 
    # Ordina per fornitore
    da_ritirare.sort(key=lambda x: x['fornitore'])
    return render_template('programma_ritiro.html',
                           da_ritirare=da_ritirare,
                           domani=domani_str)
 
 
 
@app.route('/api/oa_stato', methods=['POST'])
def oa_stato():
    """Salva data_consegna e/o confermato per un OA."""
    try:
        data     = request.get_json(force=True)
        filename = data.get('filename', '').strip()
        if not filename:
            return jsonify({'ok': False, 'error': 'filename mancante'}), 400
        stato = StatoOrdineAcquisto.query.filter_by(filename=filename).first()
        if not stato:
            stato = StatoOrdineAcquisto(filename=filename)
            db.session.add(stato)
        if 'data_consegna' in data:
            stato.data_consegna = data['data_consegna'].strip()
        if 'confermato' in data:
            stato.confermato = bool(data['confermato'])
        if 'ritiro_proprio' in data:
            stato.ritiro_proprio = bool(data['ritiro_proprio'])
        if 'nota_interna' in data:
            stato.nota_interna = data['nota_interna'].strip()
        if 'ordine_n' in data:
            try:
                stato.ordine_n = data['ordine_n'].strip()
            except Exception:
                pass  # colonna non ancora presente nel DB — ignorare
        stato.aggiornato_il = datetime.utcnow()
        db.session.commit()
        return jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
@app.route('/magazzino')
def pagina_magazzino():
    magazzino = get_magazzino()
    storico   = get_storico_fornitori_dict()
    return render_template('magazzino.html',
                           magazzino=magazzino,
                           storico_fornitori=storico)
 
 
@app.route('/annulla_imballaggio/<path:filename>/<codice>')
def annulla_imballaggio(filename, codice):
    """Riporta un articolo dallo stato imballaggio alla card originale."""
    info     = estrai_dati_completi_pdf(filename)
    conferma = info['conferma']
    existing = Imballaggio.query.filter_by(conferma=conferma, codice=codice).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        scrivi_log(f"ANNULLATO IMBALLAGGIO: {conferma} — {codice}")
    return redirect(url_for('index'))
 
 
@app.route('/manda_imballaggio/<path:filename>/<codice>', methods=['POST'])
def manda_imballaggio(filename, codice):
    """Manda un articolo in imballaggio."""
    info = estrai_dati_completi_pdf(filename)
    conferma = info['conferma']
    # Trova la qta ordinata per calcolare qta_imballare
    qta_ordinata = 0
    for art in info['articoli']:
        if art['codice'] == codice:
            qta_ordinata = int(art['qta'])
            break
    # qta_evasa
    id_art    = f"{conferma}_{codice}".replace(" ", "")
    ev        = ArticoloEvaso.query.filter_by(id_art=id_art).first()
    qta_evasa = ev.qta_evasa if ev else 0
    qta_imb   = max(0, qta_ordinata - qta_evasa)
 
    existing = Imballaggio.query.filter_by(conferma=conferma, codice=codice).first()
    if existing:
        existing.qta_imballare = qta_imb
        existing.imballato     = False
    else:
        db.session.add(Imballaggio(
            conferma=conferma, filename=filename,
            codice=codice, qta_imballare=qta_imb, imballato=False
        ))
    db.session.commit()
    scrivi_log(f"IMBALLAGGIO: {conferma} — {codice} ({qta_imb}pz)")
    return redirect(url_for('index'))
 
 
@app.route('/conferma_imballato/<path:filename>/<codice>')
def conferma_imballato(filename, codice):
    """Segna articolo come imballato (spedito, in attesa DDT)."""
    info     = estrai_dati_completi_pdf(filename)
    conferma = info['conferma']
    existing = Imballaggio.query.filter_by(conferma=conferma, codice=codice).first()
    if existing:
        existing.imballato = True
        db.session.commit()
    return redirect(url_for('index'))
 
 
@app.route('/api/ricevi_prebolla', methods=['POST'])
def ricevi_prebolla():
    try:
        data = request.get_json(force=True)
        pb = PreBolla(
            cliente=data.get('cliente', ''),
            dettagli=data.get('dettagli', ''),
            id_masterwork=int(data.get('id_prebolla', 0)),
            stato='DA_FARE'
        )
        db.session.add(pb)
        db.session.commit()
        print(f">>> [PRE-BOLLA RICEVUTA] Cliente: {pb.cliente}")
        return jsonify({"ok": True, "id": pb.id}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route('/pre_bolle')
def lista_pre_bolle():
    da_fare = PreBolla.query.filter_by(stato='DA_FARE').order_by(PreBolla.ricevuta_il.desc()).all()
    fatte   = PreBolla.query.filter_by(stato='FATTA').order_by(PreBolla.confermata_il.desc()).limit(20).all()
    return render_template('pre_bolle.html', da_fare=da_fare, fatte=fatte)
 
 
@app.route('/conferma_prebolla/<int:id_pb>', methods=['POST'])
def conferma_prebolla(id_pb):
    pb = PreBolla.query.get_or_404(id_pb)
    pb.stato         = 'FATTA'
    pb.confermata_il = datetime.utcnow()
    db.session.commit()
    try:
        mw_url = os.environ.get('MASTERWORK_URL', 'https://masterwork.up.railway.app')
        requests.post(f"{mw_url}/api/bolla_confermata", json={
            "id_prebolla":   pb.id_masterwork,
            "cliente":       pb.cliente,
            "confermata_il": pb.confermata_il.strftime('%d/%m/%Y %H:%M'),
        }, timeout=5)
    except Exception as e:
        print(f">>> [WMS→MW WARN] {e}")
    return redirect(url_for('lista_pre_bolle'))
 
 
# ==============================================================================
# URGENZE
# ==============================================================================
 
@app.route('/api/urgenze', methods=['GET'])
def api_urgenze():
    tipo = request.args.get('tipo', '')
    query = Urgenza.query
    if tipo:
        query = query.filter_by(tipo=tipo)
    return jsonify([{
        'id': u.id, 'tipo': u.tipo,
        'riferimento': u.riferimento,
        'nota': u.nota,
        'creata_il': u.creata_il.strftime('%d/%m/%Y %H:%M')
    } for u in query.order_by(Urgenza.creata_il.desc()).all()])
 
 
@app.route('/api/segna_urgenza', methods=['POST'])
def segna_urgenza():
    try:
        data = request.get_json(force=True)
        tipo  = data.get('tipo', '').strip()
        rif   = data.get('riferimento', '').strip()
        nota  = data.get('nota', '').strip()
        if not tipo or not rif:
            return jsonify({'ok': False, 'error': 'tipo e riferimento obbligatori'}), 400
        existing = Urgenza.query.filter_by(riferimento=rif).first()
        if existing:
            existing.nota = nota
        else:
            db.session.add(Urgenza(tipo=tipo, riferimento=rif, nota=nota))
        db.session.commit()
        scrivi_log(f"URGENZA [{tipo.upper()}]: {rif} — {nota[:60]}")
        return jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
@app.route('/api/rimuovi_urgenza', methods=['POST'])
def rimuovi_urgenza():
    try:
        data = request.get_json(force=True)
        rif  = data.get('riferimento', '').strip()
        u = Urgenza.query.filter_by(riferimento=rif).first()
        if u:
            db.session.delete(u)
            db.session.commit()
        return jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
 
 
# ==============================================================================
# FASCICOLO DIGITALE — ROUTE API
# ==============================================================================
 
@app.route('/api/fascicolo/<path:conferma>', methods=['GET'])
def api_fascicolo_get(conferma):
    """Restituisce lo stato del fascicolo per una CO."""
    fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
    if not fasc:
        # Crea guscio vuoto
        fasc = get_or_create_fascicolo(conferma, '')
    return jsonify(fascicolo_to_dict(fasc))
 
 
@app.route('/api/fascicolo/upload/<path:conferma>/<tipo_doc>', methods=['POST'])
def api_fascicolo_upload(conferma, tipo_doc):
    """Carica un documento nel fascicolo salvandolo come BLOB nel DB (Railway-safe)."""
    TIPI_AMMESSI = {
        'preventivo':     'has_preventivo',
        'ordine_cliente': 'has_ordine_cliente',
        'ddt_acquisto':   'has_ddt_acquisto',
        'ddt_consegna':   'has_ddt_consegna',
    }
    if tipo_doc not in TIPI_AMMESSI:
        return jsonify({'ok': False, 'error': 'tipo non valido'}), 400
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'file PDF richiesto'}), 400
    try:
        contenuto = file.read()
        if not contenuto:
            return jsonify({'ok': False, 'error': 'file vuoto'}), 400
        fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
        if not fasc:
            fasc = get_or_create_fascicolo(conferma, '')
        # Salva o aggiorna il blob nel DB
        doc_esistente = FascicoloDocumento.query.filter_by(
            conferma=conferma, tipo_doc=tipo_doc).first()
        if doc_esistente:
            doc_esistente.contenuto   = contenuto
            doc_esistente.filename    = file.filename
            doc_esistente.caricato_il = datetime.utcnow()
        else:
            db.session.add(FascicoloDocumento(
                conferma=conferma,
                tipo_doc=tipo_doc,
                filename=file.filename,
                contenuto=contenuto
            ))
        # Aggiorna anche il flag booleano per retrocompatibilità
        campo = TIPI_AMMESSI[tipo_doc]
        setattr(fasc, campo, True)
        fasc.aggiornato_il = datetime.utcnow()
        db.session.commit()
        scrivi_log(f"FASCICOLO {conferma}: caricato {tipo_doc} ({file.filename}, {len(contenuto)} bytes)")
        return jsonify({'ok': True, 'fascicolo': fascicolo_to_dict(fasc)})
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE upload fascicolo {conferma}/{tipo_doc}: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
@app.route('/api/fascicolo/apri/<path:conferma>/<tipo_doc>', methods=['GET'])
def api_fascicolo_apri(conferma, tipo_doc):
    """Serve il PDF di un documento del fascicolo direttamente dal DB blob (Railway-safe)."""
    # Prima prova dal DB blob
    doc = FascicoloDocumento.query.filter_by(conferma=conferma, tipo_doc=tipo_doc).first()
    if doc and doc.contenuto:
        from flask import Response
        return Response(
            doc.contenuto,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'inline; filename="{doc.filename or tipo_doc + ".pdf"}"',
                'Content-Length': str(len(doc.contenuto))
            }
        )
    # Fallback: prova dal filesystem (retrocompatibilità con dati pre-migrazione)
    TIPI_FILE = {
        'preventivo':     '01_Preventivo.pdf',
        'ordine_cliente': '02_Ordine_Cliente.pdf',
        'co':             '03_Conferma_Ordine.pdf',
        'ddt_acquisto':   '05_DDT_Ingresso.pdf',
        'ddt_consegna':   '06_DDT_Consegna.pdf',
    }
    nome = TIPI_FILE.get(tipo_doc)
    if not nome:
        return "Tipo non valido", 400
    fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
    if fasc and fasc.path_fascicolo:
        path_file = os.path.join(fasc.path_fascicolo, nome)
        if os.path.exists(path_file):
            return send_from_directory(fasc.path_fascicolo, nome)
    return (
        "<html><head><meta charset='utf-8'>"
        "<style>body{font-family:sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;height:100vh;}"
        ".box{background:#141414;border:1px solid #9b59b633;border-radius:12px;"
        "padding:30px;text-align:center;max-width:420px;}"
        "h2{color:#9b59b6;margin-bottom:10px;} p{color:#666;font-size:0.85rem;}"
        "a{color:#28a745;}</style></head><body><div class='box'>"
        "<h2>📄 Documento non disponibile</h2>"
        "<p>Il documento non è ancora stato caricato nel fascicolo.</p>"
        "<br><a href='javascript:window.close()'>✖ Chiudi</a>"
        "</div></body></html>"
    ), 404
 
 
@app.route('/monitor_fascicoli')
def monitor_fascicoli():
    """Vista fascicoli incompleti."""
    fascicoli = FascicoloOrdine.query.order_by(FascicoloOrdine.aggiornato_il.desc()).all()
    dati = [fascicolo_to_dict(f) for f in fascicoli]
    incompleti = [d for d in dati if not d['completo']]
    completi   = [d for d in dati if d['completo']]
    return render_template('monitor_fascicoli.html',
                           incompleti=incompleti,
                           completi=completi,
                           totale=len(dati))
 
 
 
@app.route('/api/ddt_consegna_ordine/<path:conferma>')
def api_ddt_consegna_ordine(conferma):
    """
    MIGLIORIA: Restituisce la lista di tutti i DDT di consegna emessi per un ordine cliente.
    Permette al frontend di mostrare il fascicolo DDT nella card ordine
    (es. DDT #1234 del 10/05/2026, DDT #1290 del 12/05/2026, ...).
    """
    try:
        scarichi = ScaricoDDT.query.filter_by(
            conferma=conferma,
            stornato=False
        ).order_by(ScaricoDDT.elaborato_il.asc()).all()
        result = []
        for d in scarichi:
            movimenti = json.loads(d.movimenti_json or '[]')
            result.append({
                'id':         d.id,
                'numero_ddt': d.numero_ddt or f'DDT #{d.id}',
                'data_ddt':   d.data_ddt or (d.elaborato_il.strftime('%d/%m/%Y') if d.elaborato_il else ''),
                'cliente':    d.cliente,
                'n_righe':    len(movimenti),
                'movimenti':  movimenti,
                'ha_pdf':     bool(d.pdf_blob),
                'url_pdf':    f'/api/pdf_scarico/{d.id}' if d.pdf_blob else None,
            })
        return jsonify({
            'ok':       True,
            'conferma': conferma,
            'totale':   len(result),
            'ddt':      result,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/ordinati/<sku>')
def api_ordinati_sku(sku):
    """Restituisce tutti gli OA attivi (qta > 0) che contengono questo SKU."""
    records = StoricoOrdineFornitore.query.filter(
        StoricoOrdineFornitore.codice == sku,
        StoricoOrdineFornitore.qta > 0
    ).order_by(StoricoOrdineFornitore.id_ordine).all()
 
    art = Articolo.query.filter_by(sku=sku).first()
    desc = art.descrizione if art else sku
 
    totale_qta = sum(r.qta for r in records)
    ordini = []
    for r in records:
        attivo = os.path.exists(os.path.join(ACQUISTI_DONE_FOLDER, r.filename_oa)) if r.filename_oa else True
        ordini.append({
            'oa_n':          r.id_ordine,
            'fornitore':     r.fornitore or '—',
            'qta':           r.qta,
            'data_evasione': r.data_evasione or '—',
            'filename':      r.filename_oa or '',
            'attivo':        attivo,
        })
 
    # ── FALLBACK: articoli_evasi con vecchio schema OA ──
    try:
        existing_oa_ns = {str(r.id_ordine) for r in records}
        ae_rows = db.session.execute(db.text(
            "SELECT id_ordine, fornitore, qta, data_evasione, filename_oa "
            "FROM articoli_evasi WHERE codice = :sku AND qta > 0"
        ), {'sku': sku}).fetchall()
        for row in ae_rows:
            oa_n = str(row[0]) if row[0] is not None else ''
            if oa_n and oa_n not in existing_oa_ns:
                qta_ae = int(row[2] or 0)
                totale_qta += qta_ae
                fn = row[4] or ''
                attivo = os.path.exists(os.path.join(ACQUISTI_DONE_FOLDER, fn)) if fn else True
                ordini.append({
                    'oa_n':          oa_n,
                    'fornitore':     row[1] or '—',
                    'qta':           qta_ae,
                    'data_evasione': row[3] or '—',
                    'filename':      fn,
                    'attivo':        attivo,
                })
    except Exception:
        pass  # articoli_evasi ha schema nuovo — skip silenzioso
 
    return jsonify({
        'sku':        sku,
        'descrizione': desc,
        'totale_qta': totale_qta,
        'ordini':     ordini,
    })
 
 
 
@app.route('/debug_magazzino')
def debug_magazzino():
    """Route di debug: mostra stato DB e ultimi log."""
    try:
        count_art = Articolo.query.count()
        con_stock = Articolo.query.filter(Articolo.stock > 0).count()
        esempi = [{'sku': a.sku, 'desc': a.descrizione[:30], 'stock': a.stock} 
                  for a in Articolo.query.filter(Articolo.stock > 0).limit(10).all()]
        
        # Leggi ultime righe log
        log_lines = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as lf:
                log_lines = lf.readlines()[-20:]
        
        return jsonify({
            'articoli_totali': count_art,
            'articoli_con_stock': con_stock,
            'esempi': esempi,
            'ultimi_log': log_lines,
            'db_path': app.config.get('SQLALCHEMY_DATABASE_URI', 'N/D'),
            'storage_path': STORAGE_PATH,
        })
    except Exception as e:
        return jsonify({'errore': str(e)}), 500
 
 
@app.route('/importazione_massiva_ajax', methods=['POST'])
def importazione_massiva_ajax():
    """Versione AJAX dell'importazione — restituisce JSON con risultato dettagliato."""
    file = request.files.get('file_excel')
    if not file:
        return jsonify({'ok': False, 'error': 'Nessun file ricevuto'}), 400
    try:
        filename = file.filename.lower() if file.filename else ''
        raw = file.read()  # leggi in memoria una volta sola
        import io
 
        df = None
        errori_tentati = []
 
        # Determina il formato dall'estensione del filename.
        # Se il browser invia senza nome (es. 'blob') proviamo tutti i formati.
        ext = ''
        if '.' in filename:
            ext = filename.rsplit('.', 1)[-1]
 
        def _try_xls(data):
            """Prova xlrd poi openpyxl per file .xls (vecchio formato BIFF)."""
            errs = []
            try:
                return pd.read_excel(io.BytesIO(data), engine='xlrd'), errs
            except Exception as e1:
                errs.append(f'xlrd: {e1}')
            try:
                return pd.read_excel(io.BytesIO(data), engine='openpyxl'), errs
            except Exception as e2:
                errs.append(f'openpyxl(xls): {e2}')
            return None, errs
 
        def _try_xlsx(data):
            errs = []
            try:
                return pd.read_excel(io.BytesIO(data), engine='openpyxl'), errs
            except Exception as e:
                errs.append(f'openpyxl: {e}')
            return None, errs
 
        def _try_csv(data):
            errs = []
            for enc in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    return pd.read_csv(io.BytesIO(data), encoding=enc, sep=None, engine='python'), errs
                except Exception as e:
                    errs.append(f'csv-{enc}: {e}')
            return None, errs
 
        if ext == 'xls':
            df, e = _try_xls(raw); errori_tentati += e
        elif ext == 'xlsx':
            df, e = _try_xlsx(raw); errori_tentati += e
        elif ext == 'csv':
            df, e = _try_csv(raw); errori_tentati += e
        else:
            # Filename senza estensione riconoscibile (es. 'blob') —
            # proviamo in ordine: xls → xlsx → csv
            for fn in [_try_xls, _try_xlsx, _try_csv]:
                df, e = fn(raw)
                errori_tentati += e
                if df is not None:
                    break
 
        if df is None:
            return jsonify({
                'ok': False,
                'error': 'Impossibile leggere il file. '
                         'Assicurarsi che sia .xls/.xlsx/.csv Zucchetti. '
                         'Dettagli: ' + ' | '.join(errori_tentati)
            }), 400
 
        # Normalizza nomi colonne (strip spazi)
        df.columns = [str(c).strip() for c in df.columns]
 
        # Colonne flessibili: accetta sia ARCODART che CACODART
        col_sku  = 'ARCODART' if 'ARCODART' in df.columns else 'CACODART'
        col_desc = 'ARDESART' if 'ARDESART' in df.columns else 'CADESART'
        if col_sku not in df.columns or 'ESISATTU' not in df.columns:
            return jsonify({
                'ok': False,
                'error': f'Colonne SKU o ESISATTU non trovate.',
                'colonne_trovate': list(df.columns)
            }), 400
 
        count_new = count_upd = count_skip = 0
        for _, row in df.iterrows():
            sku_val = str(row[col_sku]).strip()
            if not sku_val or sku_val.lower() in ('nan', ''):
                count_skip += 1
                continue
            desc_val    = str(row.get(col_desc, '')).strip()
            stock_val   = int(float(row.get('ESISATTU', 0) or 0))
            impegnato   = int(float(row.get('RISEATTU', 0) or 0))
            cod_esterno = str(row.get('CACODICE', '')).strip()
            if cod_esterno.lower() in ('nan', 'none', ''):
                cod_esterno = ''
            articolo = Articolo.query.filter_by(sku=sku_val).first()
            if articolo:
                articolo.descrizione    = desc_val
                articolo.stock          = stock_val
                articolo.incoming       = impegnato
                articolo.codice_esterno = cod_esterno
                count_upd += 1
            else:
                db.session.add(Articolo(
                    sku=sku_val, descrizione=desc_val,
                    stock=stock_val, incoming=impegnato,
                    ordinati=0, fornitore='N/D',
                    codice_esterno=cod_esterno))
                count_new += 1
 
        db.session.commit()
        scrivi_log(f"IMPORT AJAX: {count_new} nuovi, {count_upd} aggiornati, {count_skip} saltati. Colonna usata: {col_sku}")
        return jsonify({
            'ok': True,
            'nuovi': count_new,
            'aggiornati': count_upd,
            'saltati': count_skip,
            'totale': count_new + count_upd,
            'colonna_sku': col_sku,
        })
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE IMPORT AJAX: {str(e)}")
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
 
@app.route('/migra_da_vecchio_oa')
def migra_da_vecchio_oa():
    """
    Migra i dati OA da articoli_evasi (vecchio schema) a storico_ordini_fornitore.
    Chiamare UNA VOLTA se la colonna ORDINATI mostra 0 nonostante i PDF caricati.
    """
    try:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns('articoli_evasi')]
        if 'codice' not in cols:
            return "❌ articoli_evasi non ha il vecchio schema OA (colonna 'codice' assente). Niente da migrare.", 200
 
        rows = db.session.execute(db.text(
            "SELECT id_ordine, codice, qta, fornitore, data_evasione, filename_oa "
            "FROM articoli_evasi WHERE qta > 0 AND codice IS NOT NULL AND id_ordine IS NOT NULL"
        )).fetchall()
 
        migrati = 0
        saltati = 0
        for row in rows:
            id_ord = str(row[0])
            codice = row[1]
            existing = StoricoOrdineFornitore.query.filter_by(
                id_ordine=id_ord, codice=codice
            ).first()
            if not existing:
                db.session.add(StoricoOrdineFornitore(
                    id_ordine=id_ord,
                    codice=codice,
                    qta=int(row[2] or 0),
                    fornitore=row[3] or '',
                    data_evasione=row[4] or '',
                    filename_oa=row[5] or ''
                ))
                migrati += 1
                # Aggiorna anche Articolo.ordinati
                art = Articolo.query.filter_by(sku=codice).first()
                if art:
                    art.ordinati = (art.ordinati or 0) + int(row[2] or 0)
            else:
                saltati += 1
 
        db.session.commit()
        msg = f"✅ Migrazione completata: {migrati} righe migrate, {saltati} già presenti in storico."
        scrivi_log(msg)
        return msg + "<br><br><a href='/'>Torna alla dashboard</a>"
    except Exception as e:
        db.session.rollback()
        return f"❌ Errore: {e}", 500
 
 
@app.route('/migrate_db')
def migrate_db():
    """Aggiunge colonne mancanti al DB PostgreSQL senza perdere dati."""
    risultati = []
    migrazioni = [
        ("storico_ordini_fornitore", "fornitore",     "VARCHAR(200) DEFAULT ''"),
        ("storico_ordini_fornitore", "data_evasione",  "VARCHAR(20)  DEFAULT ''"),
        ("storico_ordini_fornitore", "filename_oa",    "VARCHAR(255) DEFAULT ''"),
        ("articoli",                 "codice_esterno", "VARCHAR(50)  DEFAULT ''"),
        ("articoli",                 "scorta_minima",  "INTEGER      DEFAULT 5"),
    ]
    try:
        conn = db.engine.connect()
        for tabella, colonna, tipo in migrazioni:
            try:
                conn.execute(db.text(
                    f"ALTER TABLE {tabella} ADD COLUMN IF NOT EXISTS {colonna} {tipo}"
                ))
                conn.commit()
                risultati.append(f"✅ {tabella}.{colonna} — OK")
            except Exception as e:
                risultati.append(f"⚠️ {tabella}.{colonna} — {str(e)[:80]}")
        conn.close()
        scrivi_log("MIGRATION eseguita: " + ", ".join(risultati))
    except Exception as e:
        risultati.append(f"❌ Errore connessione: {e}")
    return "<br>".join(risultati) + "<br><br><a href='/'>Torna alla dashboard</a>"
 
 
 
@app.route('/ricalcola_ordinati')
def ricalcola_ordinati():
    """
    Ricostruisce StoricoOrdineFornitore dai PDF OA su disco.
    Da chiamare UNA VOLTA dopo ogni restart se i dati mancano.
    """
    risultati = []
    try:
        if not os.path.exists(ACQUISTI_DONE_FOLDER):
            return "Cartella OA non trovata", 404
 
        oa_files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER) if f.lower().endswith('.pdf')]
        if not oa_files:
            return "Nessun PDF OA trovato su disco. I file sono stati persi al restart di Railway.", 200
 
        count = 0
        for filename in oa_files:
            info = estrai_dati_ordine_acquisto(filename)
            id_ordine = info['ordine_n']
            for art in info['articoli']:
                cod = art['codice']
                qta = int(art['qta'])
                existing = StoricoOrdineFornitore.query.filter_by(
                    id_ordine=id_ordine, codice=cod).first()
                if existing:
                    # FIX v4: NON resettare qta se il record esiste già
                    # (potrebbe essere già stato scalato da DDT caricati) — aggiorna solo metadati
                    existing.fornitore     = info['fornitore']
                    existing.data_evasione = art.get('data_evasione', '')
                    existing.filename_oa   = filename
                else:
                    db.session.add(StoricoOrdineFornitore(
                        id_ordine=id_ordine, codice=cod, qta=qta,
                        fornitore=info['fornitore'],
                        data_evasione=art.get('data_evasione', ''),
                        filename_oa=filename))
                # Aggiorna anche Articolo.ordinati
                articolo = Articolo.query.filter_by(sku=cod).first()
                if articolo:
                    articolo.ordinati = (articolo.ordinati or 0) + qta
                count += 1
            risultati.append(f"✅ {filename}: {len(info['articoli'])} articoli")
 
        db.session.commit()
        scrivi_log(f"RICALCOLA ORDINATI: {len(oa_files)} OA, {count} righe")
        return "<br>".join(risultati) + f"<br><br><b>Totale: {count} righe aggiornate</b><br><a href='/magazzino'>Vai al magazzino</a>"
    except Exception as e:
        db.session.rollback()
        return f"Errore: {e}", 500
 
 
 
@app.route('/debug_ordinati')
def debug_ordinati():
    """Debug: mostra cosa c'è in StoricoOrdineFornitore e cosa calcola get_magazzino_wms."""
    try:
        # 1. Conta righe in StoricoOrdineFornitore
        totale = StoricoOrdineFornitore.query.count()
        con_qta = StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).count()
        
        # 2. Prime 10 righe
        righe = StoricoOrdineFornitore.query.limit(10).all()
        esempi = [{'id_ordine': r.id_ordine, 'codice': r.codice, 'qta': r.qta,
                   'fornitore': r.fornitore, 'filename_oa': r.filename_oa} for r in righe]
        
        # 3. Somma ordinati per SKU (come fa get_magazzino_wms)
        ordinati_calcolati = {}
        for r in StoricoOrdineFornitore.query.all():
            if r.codice and r.qta > 0:
                ordinati_calcolati[r.codice] = ordinati_calcolati.get(r.codice, 0) + r.qta
        
        # 4. Primi 10 SKU con ordinati > 0
        top_ordinati = sorted(ordinati_calcolati.items(), key=lambda x: -x[1])[:10]
        
        # 5. Controlla anche Articolo.ordinati
        art_con_ordinati = Articolo.query.filter(Articolo.ordinati > 0).count()
        art_esempi = [{'sku': a.sku, 'ordinati': a.ordinati} 
                      for a in Articolo.query.filter(Articolo.ordinati > 0).limit(5).all()]
        
        return jsonify({
            'storico_totale_righe': totale,
            'storico_righe_con_qta_positiva': con_qta,
            'storico_primi_10': esempi,
            'ordinati_calcolati_top10': top_ordinati,
            'articoli_con_ordinati_gt0': art_con_ordinati,
            'articoli_esempi': art_esempi,
        })
    except Exception as e:
        return jsonify({'errore': str(e)}), 500
 
 
 
@app.route('/forza_ordinati')
def forza_ordinati():
    """
    Legge stato_ordine_acquisto per trovare i filename degli OA attivi,
    poi rilancia processa_acquisti su quelli che trova su disco.
    Alternativa: legge direttamente i filename e ripopola Articolo.ordinati.
    """
    try:
        stati = StatoOrdineAcquisto.query.all()
        risultati = []
        totale_aggiornati = 0
 
        for stato in stati:
            filename = stato.filename
            path = os.path.join(ACQUISTI_DONE_FOLDER, filename)
            if not os.path.exists(path):
                risultati.append(f"❌ File non trovato su disco: {filename}")
                continue
 
            # Riprocessa questo OA
            info = estrai_dati_ordine_acquisto(filename)
            id_ordine = info['ordine_n']
            n_art = 0
 
            for art in info['articoli']:
                cod = art['codice']
                qta = int(art['qta'])
                articolo = Articolo.query.filter_by(sku=cod).first()
                if articolo:
                    articolo.ordinati = (articolo.ordinati or 0) + qta
                    n_art += 1
                else:
                    db.session.add(Articolo(
                        sku=cod,
                        descrizione=art.get('descrizione', ''),
                        stock=0, ordinati=qta, incoming=0
                    ))
                    n_art += 1
 
                # Aggiorna anche StoricoOrdineFornitore
                existing = StoricoOrdineFornitore.query.filter_by(
                    id_ordine=id_ordine, codice=cod).first()
                if existing:
                    existing.qta = qta
                    existing.fornitore = info['fornitore']
                    existing.filename_oa = filename
                else:
                    db.session.add(StoricoOrdineFornitore(
                        id_ordine=id_ordine, codice=cod, qta=qta,
                        fornitore=info['fornitore'],
                        data_evasione=art.get('data_evasione', ''),
                        filename_oa=filename))
 
            db.session.commit()
            totale_aggiornati += n_art
            risultati.append(f"✅ {filename} — OA {id_ordine} — {n_art} articoli aggiornati")
 
        scrivi_log(f"FORZA ORDINATI: {totale_aggiornati} righe aggiornate da {len(stati)} OA")
        return "<br>".join(risultati) + f"<br><br><b>Totale: {totale_aggiornati} articoli aggiornati</b><br><a href='/magazzino'>Vai al magazzino</a>"
 
    except Exception as e:
        db.session.rollback()
        return f"Errore: {e}", 500
 
 
 
@app.route('/verifica_oa/<filename>')
def verifica_oa(filename):
    """
    Mostra un report dettagliato di cosa ha letto la regex dal PDF dell'OA.
    Permette di verificare prima dell'inserimento che tutti i codici siano corretti.
    """
    path = os.path.join(ACQUISTI_DONE_FOLDER, filename)
    if not os.path.exists(path):
        path = os.path.join(ACQUISTI_FOLDER, filename)
    if not os.path.exists(path):
        return f"<h2>File non trovato: {filename}</h2>", 404
 
    import PyPDF2, re as _re
 
    # Estrai testo grezzo
    with open(path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        testo_completo = "\n".join(page.extract_text() or '' for page in reader.pages)
    linee = [l.strip() for l in testo_completo.split('\n') if l.strip()]
 
    # Parsing standard
    dati = estrai_dati_ordine_acquisto(filename)
 
    # Cerca anche righe che NON hanno matchato per capire cosa è stato saltato
    pattern = _re.compile(
        r'([A-Z0-9][A-Z0-9.-]+)\s+(.+?)\s+n\.\s+([\d\.]+)(?:,\d+)?(?:\s+(\d{2}/\d{2}/\d{4}))?'
    )
    no_match_sospetti = []
    for linea in linee:
        # Linee che sembrano articoli ma non hanno matchato
        if ' n. ' in linea and not pattern.search(linea):
            no_match_sospetti.append(linea)
 
    # Genera report HTML
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Verifica OA: {filename}</title>
<style>
body{{font-family:monospace;background:#0a0a0a;color:#ccc;padding:20px;}}
h2{{color:#f0ad4e;}} h3{{color:#28a745;margin-top:30px;}}
table{{border-collapse:collapse;width:100%;margin-top:10px;}}
th{{background:#1a1a1a;color:#f0ad4e;padding:8px 12px;text-align:left;}}
td{{padding:6px 12px;border-bottom:1px solid #222;}}
.ok{{color:#28a745;}} .warn{{color:#f0ad4e;}} .err{{color:#ff4d5e;}}
.raw{{background:#111;padding:10px;border-radius:5px;white-space:pre-wrap;font-size:0.75rem;max-height:300px;overflow-y:auto;border:1px solid #333;}}
a{{color:#00d2ff;}}
</style></head><body>
<h2>🔍 Verifica Parsing OA: {filename}</h2>
<p><a href="/acquisti">← Torna agli acquisti</a></p>
 
<h3>📋 Dati estratti</h3>
<table>
<tr><th>Campo</th><th>Valore</th><th>Stato</th></tr>
<tr><td>Numero OA</td><td>{dati['ordine_n']}</td>
    <td class="{'ok' if dati['ordine_n'] != 'N/D' else 'err'}">
    {'✅ OK' if dati['ordine_n'] != 'N/D' else '❌ NON TROVATO'}</td></tr>
<tr><td>Fornitore</td><td>{dati['fornitore']}</td>
    <td class="{'ok' if dati['fornitore'] != 'Sconosciuto' else 'err'}">
    {'✅ OK' if dati['fornitore'] != 'Sconosciuto' else '❌ NON TROVATO'}</td></tr>
<tr><td>Articoli trovati</td><td>{len(dati['articoli'])}</td>
    <td class="{'ok' if len(dati['articoli']) > 0 else 'err'}">
    {'✅ OK' if len(dati['articoli']) > 0 else '❌ NESSUN ARTICOLO'}</td></tr>
</table>
 
<h3>📦 Articoli riconosciuti dalla regex</h3>
"""
    if dati['articoli']:
        html += """<table>
<tr><th>#</th><th>Codice</th><th>Descrizione</th><th>Q.tà</th><th>Data Ev.</th><th>Nel DB?</th></tr>"""
        for i, art in enumerate(dati['articoli'], 1):
            art_db = Articolo.query.filter_by(sku=art['codice']).first()
            in_db = '✅ Trovato' if art_db else '⚠️ Non nel magazzino'
            cls = 'ok' if art_db else 'warn'
            html += f"""<tr>
<td>{i}</td>
<td style="color:#00d2ff;font-weight:bold;">{art['codice']}</td>
<td>{art['descrizione']}</td>
<td style="color:#28a745;font-weight:bold;">{art['qta']}</td>
<td>{art['data_evasione'] or '—'}</td>
<td class="{cls}">{in_db}</td>
</tr>"""
        html += "</table>"
    else:
        html += '<p class="err">❌ Nessun articolo trovato dalla regex!</p>'
 
    if no_match_sospetti:
        html += f"""<h3>⚠️ Righe sospette non riconosciute ({len(no_match_sospetti)})</h3>
<p style="color:#f0ad4e;font-size:0.85rem;">Queste righe contengono "n." ma la regex non le ha catturate — potrebbero essere articoli persi:</p>
<div class="raw">"""
        for r in no_match_sospetti[:20]:
            html += f"{r}\n"
        html += "</div>"
 
    html += f"""<h3>📄 Testo grezzo estratto dal PDF</h3>
<div class="raw">{testo_completo[:3000].replace('<','&lt;').replace('>','&gt;')}</div>
</body></html>"""
 
    return html
 
 
@app.route('/verifica_tutti_oa')
def verifica_tutti_oa():
    """Verifica rapida di tutti gli OA presenti su disco."""
    if not os.path.exists(ACQUISTI_DONE_FOLDER):
        return "Cartella OA non trovata", 404
    files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER) if f.lower().endswith('.pdf')]
    if not files:
        return "<h2>Nessun OA su disco.</h2><p>I file spariscono ad ogni restart di Railway.</p>"
 
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Verifica tutti OA</title>
<style>body{font-family:monospace;background:#0a0a0a;color:#ccc;padding:20px;}
h2{color:#f0ad4e;} table{border-collapse:collapse;width:100%;} 
th{background:#1a1a1a;color:#f0ad4e;padding:8px 12px;text-align:left;}
td{padding:6px 12px;border-bottom:1px solid #222;}
.ok{color:#28a745;} .warn{color:#f0ad4e;} .err{color:#ff4d5e;}
a{color:#00d2ff;}</style></head><body>
<h2>🔍 Verifica tutti gli OA su disco</h2>
<table><tr><th>File</th><th>OA N°</th><th>Fornitore</th><th>Articoli</th><th>Problemi</th><th>Dettaglio</th></tr>"""
 
    for f in sorted(files):
        dati = estrai_dati_ordine_acquisto(f)
        problemi = []
        if dati['ordine_n'] == 'N/D': problemi.append('N° OA non trovato')
        if dati['fornitore'] == 'Sconosciuto': problemi.append('Fornitore non trovato')
        if not dati['articoli']: problemi.append('Nessun articolo!')
        stati_art = []
        for art in dati['articoli']:
            if not Articolo.query.filter_by(sku=art['codice']).first():
                stati_art.append(f"⚠️ {art['codice']} non nel DB")
        problemi.extend(stati_art)
 
        cls = 'err' if problemi else 'ok'
        stato = '❌ ' + ' | '.join(problemi) if problemi else '✅ OK'
        html += f"""<tr>
<td>{f}</td>
<td>{dati['ordine_n']}</td>
<td>{dati['fornitore']}</td>
<td style="color:#28a745;">{len(dati['articoli'])}</td>
<td class="{cls}">{stato}</td>
<td><a href="/verifica_oa/{f}">🔍 Dettaglio</a></td>
</tr>"""
 
    html += "</table><br><a href='/acquisti' style='color:#00d2ff;'>← Torna agli acquisti</a></body></html>"
    return html
 
 
 
@app.route('/anteprima_oa/<path:filename>')
def anteprima_oa(filename):
    path = os.path.join(ACQUISTI_FOLDER, filename)
    if not os.path.exists(path):
        return redirect(url_for('pagina_acquisti'))
    info = estrai_dati_ordine_acquisto_da_path(path)
    articoli_arricchiti = []
    for art in info['articoli']:
        art_db = Articolo.query.filter_by(sku=art['codice']).first()
        storico = StoricoOrdineFornitore.query.filter_by(
            id_ordine=info['ordine_n'], codice=art['codice']).first()
        articoli_arricchiti.append({
            'codice': art['codice'],
            'descrizione': art['descrizione'],
            'qta': art['qta'],
            'data_evasione': art['data_evasione'],
            'in_db': art_db is not None,
            'stock_att': art_db.stock if art_db else 0,
            'gia_in_oa': storico is not None,
        })
    problemi = []
    if info['ordine_n'] == 'N/D':
        problemi.append('Numero OA non trovato nel PDF')
    if info['fornitore'] == 'Sconosciuto':
        problemi.append('Fornitore non riconosciuto')
    if not info['articoli']:
        problemi.append('Nessun articolo trovato dalla regex')
    for art in articoli_arricchiti:
        if not art['in_db']:
            problemi.append(f"Codice {art['codice']} non presente nel magazzino")
 
    righe_tabella = ""
    for i, art in enumerate(articoli_arricchiti, 1):
        in_db_txt = "OK" if art['in_db'] else "Nuovo"
        in_db_cls = "ok" if art['in_db'] else "warn"
        gia_oa = " (già in OA)" if art['gia_in_oa'] else ""
        righe_tabella += (
            "<tr id='oa-row-" + str(i) + "'>"
            "<td style='color:#555;text-align:center;'>"
            "<input type='checkbox' name='escludi' value='" + art['codice'] + "' "
            "onchange=\"document.getElementById('oa-row-" + str(i) + "').style.opacity=this.checked?'0.35':'1';"
            "document.getElementById('oa-row-" + str(i) + "').style.textDecoration=this.checked?'line-through':'none';\" "
            "title='Spunta per escludere questa riga dall\'importazione' "
            "style='width:16px;height:16px;cursor:pointer;accent-color:#ff4d5e;'></td>"
            f"<td style='color:#00d2ff;font-weight:bold;font-family:monospace'>{art['codice']}"
            f"<span style='color:#f0ad4e;font-size:0.65rem'>{gia_oa}</span></td>"
            f"<td style='color:#aaa;font-size:0.78rem'>{art['descrizione']}</td>"
            f"<td style='color:#28a745;font-weight:bold'>{art['qta']}</td>"
            f"<td style='color:#555'>{art['data_evasione'] or chr(8212)}</td>"
            f"<td class='{in_db_cls}'>{in_db_txt}</td>"
            f"<td style='color:#ccc'>{art['stock_att']}</td></tr>"
        )
 
    problemi_html = ""
    if problemi:
        items = "".join(f"<li>{p}</li>" for p in problemi)
        problemi_html = (
            f"<div class='problems'>"
            f"<b style='color:#f0ad4e;font-size:0.8rem'>Attenzione — problemi rilevati:</b>"
            f"<ul>{items}</ul></div>"
        )
 
    n_cls = "ok" if info['ordine_n'] != 'N/D' else "err"
    f_cls = "ok" if info['fornitore'] != 'Sconosciuto' else "err"
    a_cls = "ok" if info['articoli'] else "err"
 
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Anteprima OA: {filename}</title>"
        "<style>"
        "*{box-sizing:border-box;margin:0;padding:0;}"
        "body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}"
        ".box{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:28px;"
        "max-width:720px;width:100%;}"
        "h2{color:#f0ad4e;margin-bottom:6px;font-size:1.1rem;}"
        ".sub{color:#555;font-size:0.75rem;margin-bottom:20px;}"
        ".row{display:flex;gap:12px;margin-bottom:16px;}"
        ".field{flex:1;background:#0e0e0e;border:1px solid #222;border-radius:8px;padding:10px 14px;}"
        ".field label{font-size:0.62rem;color:#555;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:4px;}"
        ".field .val{font-size:0.95rem;font-weight:bold;}"
        ".ok{color:#28a745;}.warn{color:#f0ad4e;}.err{color:#ff4d5e;}"
        "table{width:100%;border-collapse:collapse;margin:14px 0;font-size:0.82rem;}"
        "th{background:#1a1a1a;color:#f0ad4e;padding:8px 10px;text-align:left;"
        "font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;}"
        "td{padding:8px 10px;border-bottom:1px solid #1a1a1a;}"
        ".problems{background:#1a0a0a;border:1px solid #dc354533;border-radius:8px;"
        "padding:12px;margin:14px 0;}"
        ".problems li{color:#ff4d5e;font-size:0.8rem;margin-top:4px;list-style:none;"
        "padding-left:10px;}"
        ".problems li::before{content:'- ';}"
        ".btns{display:flex;gap:10px;margin-top:20px;}"
        ".btn-ok{flex:1;background:#28a745;color:#000;border:none;padding:14px;"
        "border-radius:8px;font-weight:900;font-size:1rem;cursor:pointer;}"
        ".btn-ok:hover{background:#34d058;}"
        ".btn-cancel{background:#1a1a1a;color:#666;border:1px solid #333;padding:14px 20px;"
        "border-radius:8px;font-size:0.9rem;cursor:pointer;}"
        ".btn-cancel:hover{color:#aaa;}"
        "</style></head><body><div class='box'>"
        f"<h2>Anteprima — {filename}</h2>"
        "<p class='sub'>Verifica i dati estratti dal PDF prima di confermare l'inserimento nel database.</p>"
        "<div class='row'>"
        f"<div class='field'><label>Numero OA</label><div class='val {n_cls}'>{info['ordine_n']}</div></div>"
        f"<div class='field'><label>Fornitore</label><div class='val {f_cls}'>{info['fornitore']}</div></div>"
        f"<div class='field'><label>Articoli trovati</label><div class='val {a_cls}'>{len(info['articoli'])}</div></div>"
        "</div>"
        f"{problemi_html}"
        "<table><tr><th style='width:40px;text-align:center'>Escludi</th><th>#</th><th>Codice</th><th>Descrizione</th>"
        "<th>Q.ta</th><th>Data Ev.</th><th>Nel DB</th><th>Stock</th></tr>"
        f"{righe_tabella}</table>"
        "<div class='btns'>"
        "<div style='background:#1a0a0a;border:1px solid #f0ad4e33;border-radius:8px;padding:10px;margin-bottom:10px;font-size:0.72rem;color:#888;'>"
        "\u26a0\ufe0f Spunta le righe da <b style='color:#ff4d5e'>ESCLUDERE</b> dall'importazione (es. voci di trasporto, pallet, servizi)."
        "</div>"
        f"<form action='/conferma_oa/{filename}' method='post' style='flex:1'>"
        "<button type='submit' class='btn-ok'>CONFERMA — Inserisci nel database</button>"
        "</form>"
        f"<form action='/annulla_oa/{filename}' method='post'>"
        "<button type='submit' class='btn-cancel'>Annulla</button>"
        "</form></div>"
        "</div></body></html>"
    )
    return html
 
 
@app.route('/conferma_oa/<path:filename>', methods=['POST'])
def conferma_oa(filename):
    path_temp = os.path.join(ACQUISTI_FOLDER, filename)
    if not os.path.exists(path_temp):
        return redirect(url_for('pagina_acquisti'))
    # Codici marcati come "escludi" dall'utente nell'anteprima
    escludi = set(request.form.getlist('escludi'))
    try:
        info      = estrai_dati_ordine_acquisto_da_path(path_temp)
        id_ordine = info['ordine_n']
        for art in info['articoli']:
            cod       = art['codice']
            if cod in escludi:
                scrivi_log(f"OA {filename}: riga {cod} ESCLUSA dall'utente")
                continue
            nuova_qta = int(art['qta'])
            storico_record = StoricoOrdineFornitore.query.filter_by(
                id_ordine=id_ordine, codice=cod).first()
            vecchia_qta = storico_record.qta if storico_record else 0
            differenza  = nuova_qta - vecchia_qta
            articolo = Articolo.query.filter_by(sku=cod).first()
            if not articolo:
                articolo = Articolo(sku=cod, descrizione=art.get('descrizione', ''))
                db.session.add(articolo)
                db.session.flush()
            if articolo.ordinati is None:
                articolo.ordinati = 0
            articolo.ordinati     += differenza
            articolo.fornitore     = info['fornitore']
            articolo.ordine_n      = id_ordine
            articolo.data_evasione = art.get('data_evasione', '')
            aggiorna_storico_fornitori(cod, info['fornitore'])
            if storico_record:
                storico_record.qta           = nuova_qta
                storico_record.fornitore     = info['fornitore']
                storico_record.data_evasione = art.get('data_evasione', '')
                storico_record.filename_oa   = filename
            else:
                db.session.add(StoricoOrdineFornitore(
                    id_ordine=id_ordine, codice=cod, qta=nuova_qta,
                    fornitore=info['fornitore'],
                    data_evasione=art.get('data_evasione', ''),
                    filename_oa=filename))
        path_done = os.path.join(ACQUISTI_DONE_FOLDER, filename)
        shutil.move(path_temp, path_done)
        db.session.commit()
        scrivi_log(f"OA CONFERMATO: {filename} OA {id_ordine} {len(info['articoli'])} articoli")
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE conferma OA {filename}: {e}")
    return redirect(url_for('pagina_acquisti'))
 
 
@app.route('/annulla_oa/<path:filename>', methods=['POST'])
def annulla_oa(filename):
    path_temp = os.path.join(ACQUISTI_FOLDER, filename)
    if os.path.exists(path_temp):
        os.remove(path_temp)
        scrivi_log(f"OA ANNULLATO: {filename}")
    return redirect(url_for('pagina_acquisti'))
 
 
 
@app.route('/api/priorita_ordini', methods=['POST'])
def api_priorita_ordini():
    """Salva la nuova classifica ordini nel DB.
    Riceve JSON: { ordine: ['conferma1', 'conferma2', ...] } in ordine di priorità.
    """
    try:
        data   = request.get_json()
        ordine = data.get('ordine', [])  # lista di codici conferma ordinati
        for i, conferma in enumerate(ordine, 1):
            fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if fasc:
                fasc.priorita = i
        db.session.commit()
        scrivi_log(f"PRIORITA SALVATA: {ordine}")
        return jsonify({'ok': True, 'salvati': len(ordine)})
    except Exception as e:
        db.session.rollback()
        scrivi_log(f"ERRORE salvataggio priorità: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
 
@app.route('/api/data_evasione_articolo', methods=['POST'])
def api_data_evasione_articolo():
    """Salva data evasione e flag esatta/presunta/provvisoria per un articolo di un OA."""
    try:
        data       = request.get_json()
        filename   = data.get('filename')
        codice     = data.get('codice')
        data_val   = data.get('data', '')
        tipo_data  = data.get('tipo_data', 'presunta')  # 'esatta', 'presunta', 'provvisoria'
        is_presunta = tipo_data == 'presunta'
        rec = DataEvasioneArticolo.query.filter_by(
            filename_oa=filename, codice=codice).first()
        if rec:
            rec.data        = data_val
            rec.is_presunta = is_presunta
            rec.tipo_data   = tipo_data
            rec.aggiornato_il = datetime.utcnow()
        else:
            rec = DataEvasioneArticolo(
                filename_oa=filename, codice=codice,
                data=data_val, is_presunta=is_presunta, tipo_data=tipo_data)
            db.session.add(rec)
        db.session.commit()
        scrivi_log(f"DATA ART: {codice} OA {filename} → {data_val} ({tipo_data})")
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
# ==============================================================================
# DIARIO ACCORDI OA
# ==============================================================================
 
@app.route('/api/diario_accordi/<path:filename>', methods=['GET'])
def get_diario_accordi(filename):
    """Restituisce la cronologia degli accordi per un OA."""
    voci = DiarioAccordiOA.query.filter_by(filename_oa=filename)\
               .order_by(DiarioAccordiOA.registrato_il.desc()).all()
    return jsonify([{
        'id':           v.id,
        'utente':       v.utente,
        'testo':        v.testo,
        'registrato_il': v.registrato_il.strftime('%d/%m/%Y %H:%M')
    } for v in voci])
 
@app.route('/api/diario_accordi', methods=['POST'])
def add_diario_accordo():
    """Aggiunge una voce al diario accordi di un OA."""
    try:
        data     = request.get_json(force=True)
        filename = data.get('filename', '').strip()
        utente   = data.get('utente', '').strip()[:30]
        testo    = data.get('testo', '').strip()
        if not filename or not testo or not utente:
            return jsonify({'ok': False, 'error': 'filename, utente e testo obbligatori'}), 400
        voce = DiarioAccordiOA(filename_oa=filename, utente=utente, testo=testo)
        db.session.add(voce)
        db.session.commit()
        scrivi_log(f"ACCORDO OA {filename} [{utente}]: {testo[:60]}")
        return jsonify({'ok': True, 'id': voce.id,
                        'registrato_il': voce.registrato_il.strftime('%d/%m/%Y %H:%M')})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
@app.route('/api/diario_accordi/<int:voce_id>', methods=['DELETE'])
def delete_diario_accordo(voce_id):
    """Elimina una voce del diario OA."""
    try:
        voce = DiarioAccordiOA.query.get_or_404(voce_id)
        db.session.delete(voce)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
# ==============================================================================
# DIARIO ACCORDI CLIENTE
# ==============================================================================
 
@app.route('/api/diario_accordi_cliente/<path:conferma>', methods=['GET'])
def get_diario_accordi_cliente(conferma):
    voci = DiarioAccordiCliente.query.filter_by(conferma=conferma)\
               .order_by(DiarioAccordiCliente.registrato_il.desc()).all()
    return jsonify([{
        'id':            v.id,
        'utente':        v.utente,
        'testo':         v.testo,
        'registrato_il': v.registrato_il.strftime('%d/%m/%Y %H:%M')
    } for v in voci])
 
@app.route('/api/bom_bypass', methods=['POST'])
def toggle_bom_bypass():
    """Toggle bypass BOM per un articolo di un ordine specifico.
    Quando attivo, l'articolo viene trattato come prodotto da acquistare finito
    anziché come kit da assemblare dai semilavorati."""
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        codice   = data.get('codice', '').strip()
        if not conferma or not codice:
            return jsonify({'ok': False, 'error': 'conferma e codice obbligatori'}), 400
        fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
        if not fasc:
            return jsonify({'ok': False, 'error': 'fascicolo non trovato'}), 404
        try:
            bypass_set = set(json.loads(fasc.bom_bypass_json or '[]'))
        except Exception:
            bypass_set = set()
        if codice in bypass_set:
            bypass_set.discard(codice)
            attivo = False
        else:
            bypass_set.add(codice)
            attivo = True
        fasc.bom_bypass_json = json.dumps(list(bypass_set))
        db.session.commit()
        return jsonify({'ok': True, 'attivo': attivo, 'conferma': conferma, 'codice': codice})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/diario_accordi_cliente', methods=['POST'])
def add_diario_accordo_cliente():
    try:
        data     = request.get_json(force=True)
        conferma = data.get('conferma', '').strip()
        utente   = data.get('utente',   '').strip()[:30]
        testo    = data.get('testo',    '').strip()
        if not conferma or not testo or not utente:
            return jsonify({'ok': False, 'error': 'conferma, utente e testo obbligatori'}), 400
        voce = DiarioAccordiCliente(conferma=conferma, utente=utente, testo=testo)
        db.session.add(voce)
        db.session.commit()
        scrivi_log(f"ACCORDO CLIENTE {conferma} [{utente}]: {testo[:60]}")
        return jsonify({'ok': True, 'id': voce.id,
                        'registrato_il': voce.registrato_il.strftime('%d/%m/%Y %H:%M')})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
@app.route('/api/diario_accordi_cliente/<int:voce_id>', methods=['DELETE'])
def delete_diario_accordo_cliente(voce_id):
    try:
        voce = DiarioAccordiCliente.query.get_or_404(voce_id)
        db.session.delete(voce)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
 
 
# ══════════════════════════════════════════════════════════════════════════════
# SISTEMA TASK
# ══════════════════════════════════════════════════════════════════════════════
 
def genera_tasks_auto():
    from datetime import date, timedelta
    oggi = date.today()
    nuovi = 0
 
    def upsert_task(chiave, titolo, descrizione, categoria, priorita,
                    riferimento="", conferma="", ordine_n="", fornitore="",
                    cliente="", data_scadenza=""):
        existing = Task.query.filter_by(chiave=chiave, fatto=False).first()
        if not existing:
            t = Task(tipo="auto", chiave=chiave, titolo=titolo,
                     descrizione=descrizione, categoria=categoria,
                     priorita=priorita, riferimento=riferimento,
                     conferma=conferma, ordine_n=ordine_n,
                     fornitore=fornitore, cliente=cliente,
                     data_scadenza=data_scadenza)
            db.session.add(t)
            return True
        return False
 
    # TASK DA ORDINI FORNITORI
    try:
        oa_files = [f for f in os.listdir(ACQUISTI_DONE_FOLDER)
                    if f.lower().endswith(".pdf")] if os.path.exists(ACQUISTI_DONE_FOLDER) else []
        for f in oa_files:
            stato  = StatoOrdineAcquisto.query.filter_by(filename=f).first()
            righe  = StoricoOrdineFornitore.query.filter_by(filename_oa=f).all()
            if not righe:
                continue
            oa_n      = righe[0].id_ordine
            fornitore = righe[0].fornitore or ""
            confermato = stato.confermato    if stato else False
            ritiro     = stato.ritiro_proprio if stato else False
            data_cons  = stato.data_consegna  if stato else ""
 
            delta = None
            if data_cons:
                try:
                    dp = data_cons.split("/")
                    dc = date(int(dp[2]), int(dp[1]), int(dp[0]))
                    delta = (dc - oggi).days
                except Exception:
                    pass
 
            # ── OA già nel Programma Ritiro (Ritiriamo noi + domani) → skip tutti i task ──
            domani_str = (oggi + timedelta(days=1)).strftime('%d/%m/%Y')
            if ritiro and data_cons == domani_str:
                continue
 
            if not confermato:
                chiave = f"oa_da_conf_{f}"
                if upsert_task(chiave=chiave,
                    titolo=f"OA {oa_n} al fornitore {fornitore} — attende conferma",
                    descrizione=f"Ordine inviato al fornitore {fornitore} (OA {oa_n}) ma non c'e' ancora conferma.",
                    categoria="fornitore", priorita="normale",
                    riferimento=f, ordine_n=oa_n, fornitore=fornitore):
                    nuovi += 1
            else:
                # Manca selezione spedizione/ritiro
                if stato and stato.ritiro_proprio is None:
                    chiave = f"oa_ritiro_{f}"
                    if upsert_task(chiave=chiave,
                        titolo=f"OA {oa_n} — concordare spedizione o ritiro con {fornitore}",
                        descrizione=f"Ordine {oa_n} confermato ma non e' stato indicato se spedisce il fornitore o ritiriamo noi.",
                        categoria="fornitore", priorita="normale",
                        riferimento=f, ordine_n=oa_n, fornitore=fornitore):
                        nuovi += 1
 
                # Articoli senza data
                for r in righe:
                    dea = DataEvasioneArticolo.query.filter_by(
                        filename_oa=f, codice=r.codice).first()
                    if not dea or not dea.data:
                        chiave = f"oa_nodata_{f}_{r.codice}"
                        if upsert_task(chiave=chiave,
                            titolo=f"OA {oa_n} — manca data evasione per {r.codice}",
                            descrizione=f"Articolo {r.codice} dell'ordine {oa_n} ({fornitore}) non ha data di evasione.",
                            categoria="fornitore", priorita="normale",
                            riferimento=f, ordine_n=oa_n, fornitore=fornitore):
                            nuovi += 1
 
                # Data domani = urgente
                if delta is not None and delta <= 1:
                    prio = "urgente" if delta <= 1 else "normale"
                    if ritiro:
                        chiave = f"oa_ritirare_{f}"
                        if upsert_task(chiave=chiave,
                            titolo=f"OA {oa_n} DA RITIRARE da {fornitore}" + (" — URGENTE" if prio=="urgente" else ""),
                            descrizione=f"Ordine {oa_n} da ritirare — chiedere conferma materiale pronto e organizzare ritiro.",
                            categoria="fornitore", priorita=prio,
                            riferimento=f, ordine_n=oa_n, fornitore=fornitore, data_scadenza=data_cons):
                            nuovi += 1
                    else:
                        chiave = f"oa_arrivo_{f}"
                        if upsert_task(chiave=chiave,
                            titolo=f"OA {oa_n} IN ARRIVO da {fornitore}" + (" — URGENTE" if prio=="urgente" else ""),
                            descrizione=f"Ordine {oa_n} in arrivo — chiedere conferma avvenuta spedizione.",
                            categoria="fornitore", priorita=prio,
                            riferimento=f, ordine_n=oa_n, fornitore=fornitore, data_scadenza=data_cons):
                            nuovi += 1
    except Exception as e:
        scrivi_log(f"ERRORE genera tasks OA: {e}")
 
    # TASK DA ORDINI CLIENTI
    try:
        mappa_esterni = get_mappa_codici_esterni()
        co_files = [f for f in os.listdir(UPLOAD_FOLDER)
                    if f.lower().endswith(".pdf")] if os.path.exists(UPLOAD_FOLDER) else []
        for f in co_files:
            info     = estrai_dati_completi_pdf(f, mappa_esterni)
            conferma = info.get("conferma", "N/D")
            cliente  = info.get("cliente", "")
            fasc     = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if not fasc:
                continue
 
            # Ricalcola stato generale veloce
            sg = fasc.stato_generale if hasattr(fasc, "stato_generale") else ""
 
            task_map = {
                "ORDINARE_ARTICOLI": (
                    f"co_ordinare_{conferma}",
                    f"Ordine {conferma} — {cliente}: articoli da ordinare",
                    f"Ci sono articoli da ordinare per completare l'evasione dell'ordine {conferma} ({cliente}).",
                    "normale"
                ),
                "MANCANO_CONFERME": (
                    f"co_conferme_{conferma}",
                    f"Ordine {conferma} — {cliente}: mancano conferme fornitori",
                    f"Articoli ordinati ma mancano conferme dai fornitori per l'ordine {conferma} ({cliente}).",
                    "normale"
                ),
                "ORDINI_IN_ARRIVO": (
                    f"co_arrivo_{conferma}",
                    f"Ordine {conferma} — {cliente}: organizzare logistica arrivo",
                    f"Articoli in arrivo o da ritirare per l'ordine {conferma} ({cliente}) — organizzare logistica.",
                    "normale"
                ),
                "EVADIBILE": (
                    f"co_evadibile_{conferma}",
                    f"Ordine {conferma} — {cliente}: PRONTO per imballaggio",
                    f"L'ordine {conferma} ({cliente}) e' evadibile — mandare in imballaggio.",
                    "normale"
                ),
                "SCADUTO": (
                    f"co_scaduto_{conferma}",
                    f"🚨 CONSEGNA SCADUTA — Ordine {conferma} — {cliente}",
                    f"La data di consegna concordata per l'ordine {conferma} ({cliente}) e' scaduta. Chiamare il cliente e riprogrammare.",
                    "urgente"
                ),
            }
            if sg in task_map:
                chiave, titolo, desc, prio = task_map[sg]
                data_sc = fasc.data_consegna_cliente if sg == 'SCADUTO' and fasc else ''
                if upsert_task(chiave=chiave, titolo=titolo, descrizione=desc,
                               categoria="cliente", priorita=prio,
                               riferimento=f, conferma=conferma, cliente=cliente,
                               data_scadenza=data_sc):
                    nuovi += 1
 
            # Task separato per consegna imminente (EVADIBILE con data entro 5 giorni)
            if sg == 'EVADIBILE' and fasc and fasc.data_consegna_cliente:
                try:
                    _dp2 = fasc.data_consegna_cliente.split('/')
                    _dc2 = date(int(_dp2[2]), int(_dp2[1]), int(_dp2[0]))
                    _delta2 = (_dc2 - date.today()).days
                    if 0 <= _delta2 <= 5:
                        chiave2 = f"co_imminente_{conferma}"
                        titolo2 = f"⚡ Consegna entro {_delta2}gg — Ordine {conferma} — {cliente}"
                        desc2   = f"L'ordine {conferma} ({cliente}) e' evadibile e la consegna e' prevista entro {_delta2} giorni ({fasc.data_consegna_cliente}). Imballare e spedire subito."
                        if upsert_task(chiave=chiave2, titolo=titolo2, descrizione=desc2,
                                       categoria="cliente", priorita="urgente",
                                       riferimento=f, conferma=conferma, cliente=cliente,
                                       data_scadenza=fasc.data_consegna_cliente):
                            nuovi += 1
                except Exception:
                    pass
    except Exception as e:
        scrivi_log(f"ERRORE genera tasks CO: {e}")
 
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return nuovi
 
 
@app.route("/tasks")
def pagina_tasks():
    try:
        genera_tasks_auto()
    except Exception as e:
        scrivi_log(f"ERRORE genera_tasks_auto: {e}")
    tasks_aperti = Task.query.filter_by(fatto=False).order_by(
        Task.priorita.desc(), Task.creato_il.asc()).all()
    tasks_fatti  = Task.query.filter_by(fatto=True).order_by(
        Task.fatto_il.desc()).limit(30).all()
    from datetime import date
    return render_template("tasks.html",
                           tasks_aperti=tasks_aperti,
                           tasks_fatti=tasks_fatti,
                           oggi=date.today().strftime("%d/%m/%Y"))
 
 
@app.route("/api/task_fatto/<int:task_id>", methods=["POST"])
def api_task_fatto(task_id):
    try:
        data = request.get_json()
        task = Task.query.get(task_id)
        if not task:
            return jsonify({"ok": False, "error": "Non trovato"}), 404
        task.fatto         = True
        task.nota_chiusura = data.get("nota", "")
        task.fatto_il      = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route("/api/task_nuovo", methods=["POST"])
def api_task_nuovo():
    try:
        data = request.get_json()
        t = Task(tipo="manuale", titolo=data.get("titolo",""),
                 descrizione=data.get("descrizione",""),
                 categoria="manuale", priorita=data.get("priorita","normale"),
                 cliente=data.get("cliente",""), fornitore=data.get("fornitore",""),
                 data_scadenza=data.get("data_scadenza",""))
        db.session.add(t)
        db.session.commit()
        return jsonify({"ok": True, "id": t.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route("/api/task_elimina/<int:task_id>", methods=["POST"])
def api_task_elimina(task_id):
    try:
        task = Task.query.get(task_id)
        if task:
            db.session.delete(task)
            db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
# ─────────────────────────────────────────────────────────────────────────────
#  MANUFACTURING ORDER — DICHIARAZIONI DI PRODUZIONE
# ─────────────────────────────────────────────────────────────────────────────

def _ricalcola_stati_fascicoli_per_sku(sku):
    """
    Ricalcola stato_generale di tutti i fascicoli che contengono lo SKU.
    Chiamato dopo rettifica magazzino o MO per aggiornare gli stati ordini.
    """
    if not os.path.exists(UPLOAD_FOLDER):
        return
    mappa_esterni = get_mappa_codici_esterni()
    magazzino     = get_magazzino()
    evasi         = get_evasi()
    oa_per_codice = {}
    try:
        for r in StoricoOrdineFornitore.query.filter(StoricoOrdineFornitore.qta > 0).all():
            _stato_oa = StatoOrdineAcquisto.query.filter_by(filename=r.filename_oa).first()
            _lbl = '✅ IN ATTESA' if (_stato_oa and _stato_oa.confermato) else '🔄 IN LAVORAZIONE'
            oa_per_codice.setdefault(r.codice, []).append({
                'oa_n': r.id_ordine, 'fornitore': r.fornitore or '—',
                'qta': r.qta, 'data_arr': (_stato_oa.data_consegna if _stato_oa else ''),
                'stato_oa': _lbl, 'filename_oa': r.filename_oa,
            })
    except Exception:
        pass

    stock_res    = {s: m.get('stock', 0) for s, m in magazzino.items()}
    ordinati_res = {s: m.get('ordinati', 0) for s, m in magazzino.items()}

    all_co = sorted([f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')])
    for co_file in all_co:
        try:
            info     = estrai_dati_completi_pdf(co_file, mappa_esterni)
            conferma = info['conferma']
            if not any(a['codice'] == sku for a in info['articoli']):
                continue
            arts = []
            for art in info['articoli']:
                id_art    = f"{conferma}_{art['codice']}".replace(' ', '')
                qta_evasa = evasi.get(id_art, 0)
                qta_res   = max(0, int(art['qta']) - qta_evasa)
                s         = art['codice']
                in_casa   = min(max(0, stock_res.get(s, 0)), qta_res)
                mancanti  = qta_res - in_casa
                in_arrivo = min(max(0, ordinati_res.get(s, 0)), mancanti)
                stock_res[s]    -= in_casa
                ordinati_res[s] -= in_arrivo
                art['evaso']         = qta_evasa >= int(art['qta'])
                art['qta_evasa']     = qta_evasa
                art['in_casa']       = in_casa
                art['in_arrivo_tot'] = in_arrivo
                art['mancanti']      = max(0, mancanti - in_arrivo)
                art['oa_dettaglio']  = oa_per_codice.get(s, [])
                art['stato_ordine']  = 'DA_ORDINARE' if art['mancanti'] > 0 else 'ORDINE_CONFERMATO'
                art['qta_imballare'] = 0
                art['imballato']     = False
                art['bom_componenti']= []
                arts.append(art)
            nuovo_stato = calcola_stato_generale(arts)
            fasc = FascicoloOrdine.query.filter_by(conferma=conferma).first()
            if fasc and fasc.stato_generale != nuovo_stato:
                fasc.stato_generale = nuovo_stato
                fasc.aggiornato_il  = datetime.utcnow()
        except Exception:
            pass
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def estrai_dati_dp_zucchetti(path):
    """
    Parser per PDF Dichiarazione di Produzione Zucchetti (/DP).

    Usa pdftotext -layout per estrarre il testo preservando la struttura spaziale.
    Struttura reale del PDF (riga per riga):
      Riga 5:  Numero Dichiarazione di Produzione: Da num. doc.:
      Riga 6:                                       12 /DP   Del: 12/05/2026
      Riga 9:  Articolo  Descrizione  Quantità assemblata  U.M.
      Riga 10: Intestatario                          ← marker: riga successiva = finito
      Riga 11:  F385-000-TG90L1  FIG.385 ...  10,000  n.  ← finito + qta stessa riga
      Riga 12:  Componente  Descrizione  Codice articolo  U.M.  Qtà da scaricare
      Riga 14:  PU-F385-000-TG90-1  P.U. ...  PU-F385-000-TG90-1  n.  10,000
      Riga 15:  SEM-TG90L-A  SEMILAV. ...  SEM-TG90L-A  n.  10,000
    """
    risultato = {
        'numero_dp':  '',
        'data_dp':    '',
        'finito':     None,
        'componenti': [],
        'errori':     [],
        'testo_raw':  '',
    }
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            testo = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        risultato['testo_raw'] = testo
        linee = testo.split('\n')
        mappa_esterni = get_mappa_codici_esterni()

        pat_cod = re.compile(r'^[A-Z][A-Z0-9]+(?:[.\-][A-Z0-9]+)+$')
        SKIP = {'U.M.', 'Pag.', 'Del.', 'N.', 'Cod.', 'Vs.', 'Componente',
                'Articolo', 'Descrizione', 'Intestatario'}

        # ── Numero DP e data ─────────────────────────────────────────────────
        m = re.search(r'(\d+)\s*/DP', testo)
        if m:
            risultato['numero_dp'] = m.group(1)
        m = re.search(r'Del[:\s]+(\d{2}/\d{2}/\d{4})', testo)
        if m:
            risultato['data_dp'] = m.group(1)

        # ── Trova struttura: indice riga "Intestatario" e "Componente" ───────
        idx_intestatario = None
        idx_componente   = None
        for i, linea in enumerate(linee):
            l = linea.strip()
            if l == 'Intestatario' and idx_intestatario is None:
                idx_intestatario = i
            if l.startswith('Componente') and 'Descrizione' in l and idx_componente is None:
                idx_componente = i

        # ── FINITO: riga subito dopo "Intestatario" ──────────────────────────
        # Formato: " F385-000-TG90L1   FIG.385 ...   10,000   n."
        if idx_intestatario is not None:
            for i in range(idx_intestatario + 1, min(idx_intestatario + 4, len(linee))):
                linea = linee[i].strip()
                if not linea:
                    continue
                tokens = linea.split()
                if not tokens:
                    continue
                cod_raw = tokens[0]
                if not pat_cod.match(cod_raw) or cod_raw in SKIP:
                    continue
                # Cerca quantità: pattern NNN,NNN o NNN.NNN seguito da n.
                m_qta = re.search(r'([\d\.]+,\d+|[\d]+)\s+n\.', linea)
                if m_qta:
                    qta_str = m_qta.group(1).replace('.', '').replace(',', '.')
                    try:
                        qta = int(float(qta_str))
                    except ValueError:
                        continue
                    # Descrizione: testo tra codice e quantità
                    idx_q = linea.find(m_qta.group(0))
                    desc = linea[len(cod_raw):idx_q].strip()
                    desc = re.sub(r'\s+', ' ', desc).strip()
                    cod_int = risolvi_codice(cod_raw, mappa_esterni)
                    risultato['finito'] = {
                        'codice':      cod_int,
                        'codice_raw':  cod_raw,
                        'descrizione': desc or cod_raw,
                        'qta':         qta,
                    }
                    break

        # ── COMPONENTI: righe dopo "Componente Descrizione Codice..." ────────
        # Formato: " PU-F385-000-TG90-1  P.U. ...  PU-F385-000-TG90-1  n.  10,000"
        # Strategia: split su ' n. ' → ultimo token parte sinistra = codice
        #            la quantità è il primo token dopo ' n. '
        inizio_comp = (idx_componente + 1) if idx_componente is not None else \
                      (idx_intestatario + 2) if idx_intestatario is not None else 0

        codici_usati = set()
        if risultato['finito']:
            codici_usati.add(risultato['finito']['codice'])
            codici_usati.add(risultato['finito'].get('codice_raw', ''))

        for linea in linee[inizio_comp:]:
            linea_s = linea.strip()
            if not linea_s:
                continue
            if re.match(r'^Elaborato|^Pag\.|^\f', linea_s):
                continue
            if ' n. ' not in linea and ' n.  ' not in linea:
                continue

            # Normalizza spazi multipli per lo split
            linea_norm = re.sub(r'\s+', ' ', linea_s)
            if ' n. ' not in linea_norm:
                continue

            parti = re.split(r' n\. ', linea_norm)
            if len(parti) < 2:
                continue

            # Parte sinistra: termina con il codice articolo
            parte_sx = parti[-2].strip()
            # Parte destra: inizia con la quantità
            qta_raw  = parti[-1].strip().split()[0] if parti[-1].strip() else ''

            tokens_sx = parte_sx.split()
            if not tokens_sx:
                continue
            cod_raw = tokens_sx[-1]

            if not pat_cod.match(cod_raw) or cod_raw in SKIP:
                continue

            cod_int = risolvi_codice(cod_raw, mappa_esterni)
            if cod_int in codici_usati:
                continue

            try:
                qta = int(float(qta_raw.replace('.', '').replace(',', '.')))
            except ValueError:
                continue
            if qta <= 0:
                continue

            # Descrizione: testo tra prima e seconda occorrenza del codice
            idx1 = parte_sx.find(cod_raw)
            idx2 = parte_sx.rfind(cod_raw)
            if idx1 >= 0 and idx2 > idx1:
                desc_comp = parte_sx[idx1 + len(cod_raw):idx2].strip()
            elif idx1 >= 0:
                desc_comp = parte_sx[idx1 + len(cod_raw):].strip()
            else:
                desc_comp = cod_raw
            desc_comp = re.sub(r'\s+', ' ', desc_comp).strip()

            codici_usati.add(cod_int)
            risultato['componenti'].append({
                'codice':      cod_int,
                'codice_raw':  cod_raw,
                'descrizione': desc_comp or cod_raw,
                'qta':         qta,
            })

        # ── Arricchisci descrizioni dal magazzino ────────────────────────────
        mag = get_magazzino()
        for c in risultato['componenti']:
            m_info = mag.get(c['codice'], {})
            desc_mag = m_info.get('desc') or m_info.get('descrizione', '')
            if desc_mag:
                c['descrizione'] = desc_mag
        if risultato['finito']:
            m_info = mag.get(risultato['finito']['codice'], {})
            desc_mag = m_info.get('desc') or m_info.get('descrizione', '')
            if desc_mag:
                risultato['finito']['descrizione'] = desc_mag

        # ── Validazione ──────────────────────────────────────────────────────
        if not risultato['numero_dp']:
            risultato['errori'].append('Numero /DP non trovato nel PDF')
        if not risultato['finito']:
            risultato['errori'].append('Articolo finito non riconosciuto')
        if not risultato['componenti']:
            risultato['errori'].append('Nessun componente trovato')

    except Exception as e:
        risultato['errori'].append(f'Errore lettura PDF: {e}')

    return risultato
    risultato = {
        'numero_dp':  '',
        'data_dp':    '',
        'finito':     None,
        'componenti': [],
        'errori':     [],
        'testo_raw':  '',
    }
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            testo  = '\n'.join(page.extract_text() or '' for page in reader.pages)
        risultato['testo_raw'] = testo
        linee = [l.strip() for l in testo.split('\n')]
        mappa_esterni = get_mappa_codici_esterni()

        # Pattern codice articolo Zucchetti:
        # inizia con lettera, contiene trattini/punti, es: F385-000-TG90L1, PU-F385-000-TG90-1
        pat_codice = re.compile(r'^([A-Z][A-Z0-9]{2,}(?:[.\-][A-Z0-9]+)+)')
        # Parole da ignorare che matchano il pattern ma non sono codici
        SKIP = {'U.M.', 'Pag.', 'Del.', 'N.', 'Cod.', 'Vs.'}

        # ── Numero DP e data ─────────────────────────────────────────────────
        m = re.search(r'(\d+)\s*/DP', testo)
        if m:
            risultato['numero_dp'] = m.group(1)
        m = re.search(r'Del[:\s]+(\d{2}/\d{2}/\d{4})', testo)
        if m:
            risultato['data_dp'] = m.group(1)

        # ── Trova indici chiave nel testo ────────────────────────────────────
        idx_intestatario = None
        for i, linea in enumerate(linee):
            if linea.strip() == 'Intestatario':
                idx_intestatario = i
                break

        # ── FINITO: cerca prima di "Intestatario" ────────────────────────────
        # La riga del finito è: CODICE DESCRIZIONE
        # La riga successiva con la qta è: "10,000 n." oppure "10 n."
        sezione_finito = linee[:idx_intestatario] if idx_intestatario else linee[:20]

        for i, linea in enumerate(sezione_finito):
            m = pat_codice.match(linea)
            if not m or m.group(1) in SKIP:
                continue
            cod_raw = m.group(1)
            # La descrizione è il resto della riga dopo il codice
            desc = linea[len(cod_raw):].strip()
            # Cerca la quantità nelle righe successive (entro 3 righe)
            qta = None
            for j in range(i + 1, min(i + 4, len(sezione_finito))):
                riga_qta = sezione_finito[j].strip()
                mq = re.match(r'^([\d\.]+(?:,\d+)?)\s*n\.', riga_qta)
                if mq:
                    qta_str = mq.group(1).replace('.', '').replace(',', '.')
                    try:
                        qta = int(float(qta_str))
                    except ValueError:
                        pass
                    break
            if qta and qta > 0:
                cod_int = risolvi_codice(cod_raw, mappa_esterni)
                risultato['finito'] = {
                    'codice':      cod_int,
                    'codice_raw':  cod_raw,
                    'descrizione': desc or cod_raw,
                    'qta':         qta,
                }
                break

        # ── COMPONENTI: righe dopo "Intestatario" ───────────────────────────
        # Formato Zucchetti: CODICE DESC CODICE n. QTA
        # Il codice appare DUE VOLTE — split su ' n. ' e prendi l'ultimo token
        # della parte sinistra: quello è sempre il codice articolo completo.
        # Es: "PU-F385-000-TG90-1 P.U. ... PU-F385-000-TG90-1 n. 10,000"
        #      split → ["...PU-F385-000-TG90-1", "10,000"]
        #      ultimo token → "PU-F385-000-TG90-1" ✅

        pat_cod_valido = re.compile(r'^[A-Z][A-Z0-9]+(?:[.\-][A-Z0-9]+)+$')

        codici_usati = set()
        if risultato['finito']:
            codici_usati.add(risultato['finito']['codice'])
            codici_usati.add(risultato['finito'].get('codice_raw', ''))

        sezione_comp = linee[idx_intestatario + 1:] if idx_intestatario else []

        for linea in sezione_comp:
            linea = linea.strip()
            if not linea:
                continue
            if re.match(r'^\s*\d+\s+Elaborato|^Pag\.|^Elaborato', linea):
                continue
            if ' n. ' not in linea:
                continue

            # Split su ' n. ' — usa l'ultima occorrenza
            parti = re.split(r'\s+n\.\s+', linea)
            if len(parti) < 2:
                continue

            parte_sinistra = parti[-2].strip()
            qta_raw        = parti[-1].strip().split()[0]

            # Ultimo token della parte sinistra = codice articolo
            tokens = parte_sinistra.split()
            if not tokens:
                continue
            cod_raw = tokens[-1]

            if not pat_cod_valido.match(cod_raw) or cod_raw in SKIP:
                continue

            cod_int = risolvi_codice(cod_raw, mappa_esterni)
            if cod_int in codici_usati:
                continue

            try:
                qta = int(float(qta_raw.replace('.', '').replace(',', '.')))
            except ValueError:
                continue
            if qta <= 0:
                continue

            # Descrizione: testo tra prima e seconda occorrenza del codice
            idx_prima   = parte_sinistra.find(cod_raw)
            idx_seconda = parte_sinistra.rfind(cod_raw)
            if idx_prima >= 0 and idx_seconda > idx_prima:
                desc_comp = parte_sinistra[idx_prima + len(cod_raw):idx_seconda].strip()
            elif idx_prima >= 0:
                desc_comp = parte_sinistra[idx_prima + len(cod_raw):].strip()
            else:
                desc_comp = cod_raw

            codici_usati.add(cod_int)
            risultato['componenti'].append({
                'codice':      cod_int,
                'codice_raw':  cod_raw,
                'descrizione': desc_comp or cod_raw,
                'qta':         qta,
            })

        # ── Arricchisci descrizioni dal magazzino ────────────────────────────
        mag = get_magazzino()
        for c in risultato['componenti']:
            m_info = mag.get(c['codice'], {})
            desc_mag = m_info.get('desc') or m_info.get('descrizione', '')
            if desc_mag:
                c['descrizione'] = desc_mag
        if risultato['finito']:
            m_info = mag.get(risultato['finito']['codice'], {})
            desc_mag = m_info.get('desc') or m_info.get('descrizione', '')
            if desc_mag:
                risultato['finito']['descrizione'] = desc_mag

        # ── Validazione ──────────────────────────────────────────────────────
        if not risultato['numero_dp']:
            risultato['errori'].append('Numero /DP non trovato nel PDF')
        if not risultato['finito']:
            risultato['errori'].append('Articolo finito non riconosciuto')
        if not risultato['componenti']:
            risultato['errori'].append('Nessun componente trovato')

    except Exception as e:
        risultato['errori'].append(f'Errore lettura PDF: {e}')

    return risultato


@app.route('/upload_dp_zucchetti', methods=['POST'])
def upload_dp_zucchetti():
    """Step 1: riceve PDF /DP, lo salva temp e mostra anteprima."""
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        flash('Seleziona un file PDF /DP valido.', 'danger')
        return redirect(url_for('lista_dichiarazioni_produzione'))
    filename = file.filename
    tmp_path = os.path.join(UPLOAD_FOLDER, f'_dp_tmp_{filename}')
    file.save(tmp_path)
    scrivi_log(f'UPLOAD DP ZUCCHETTI: {filename}')
    return redirect(url_for('anteprima_dp_zucchetti', filename=filename))


@app.route('/anteprima_dp_zucchetti/<path:filename>')
def anteprima_dp_zucchetti(filename):
    """Step 2: mostra anteprima parsata con stock attuale."""
    tmp_path = os.path.join(UPLOAD_FOLDER, f'_dp_tmp_{filename}')
    if not os.path.exists(tmp_path):
        flash('File temporaneo non trovato. Ricarica il PDF.', 'danger')
        return redirect(url_for('lista_dichiarazioni_produzione'))

    dati      = estrai_dati_dp_zucchetti(tmp_path)
    magazzino = get_magazzino()

    if dati['finito']:
        m = magazzino.get(dati['finito']['codice'], {})
        dati['finito']['stock_attuale'] = m.get('stock', 0)
        dati['finito']['in_db']         = dati['finito']['codice'] in magazzino

    for comp in dati['componenti']:
        m = magazzino.get(comp['codice'], {})
        comp['stock_attuale'] = m.get('stock', 0)
        comp['stock_ok']      = comp['stock_attuale'] >= comp['qta']
        comp['in_db']         = comp['codice'] in magazzino

    finito    = dati['finito']
    comps     = dati['componenti']
    errori    = dati['errori']
    numero_dp = dati['numero_dp']
    data_dp   = dati['data_dp']

    errori_html = ''
    if errori:
        items = ''.join(f"<li style='color:#f87171'>{e}</li>" for e in errori)
        errori_html = f"<div class='warn-box'><b>⚠️ Problemi rilevati:</b><ul>{items}</ul></div>"

    # Mostra anche testo raw per debug se ci sono errori
    raw_html = ''
    if errori and dati.get('testo_raw'):
        raw_esc = dati['testo_raw'][:2000].replace('<','&lt;').replace('>','&gt;')
        raw_html = f"<details style='margin-top:12px'><summary style='color:#555;cursor:pointer;font-size:12px'>🔍 Testo grezzo PDF (debug)</summary><pre style='background:#111;padding:12px;border-radius:6px;font-size:11px;color:#888;overflow-x:auto;margin-top:8px'>{raw_esc}</pre></details>"

    finito_html = ''
    if finito:
        cls = 'ok' if finito['in_db'] else 'warn'
        txt = '✅ Nel magazzino' if finito['in_db'] else '⚠️ Verrà creato nuovo'
        finito_html = f"""<table class='prev-table'>
          <tr><th>Codice</th><th>Descrizione</th><th>Qtà da caricare</th><th>Stock attuale</th><th>DB</th></tr>
          <tr><td style='color:#4ade80;font-weight:bold'>{finito['codice']}</td>
              <td>{finito['descrizione']}</td>
              <td style='color:#4ade80;font-weight:bold'>+{finito['qta']}</td>
              <td>{finito['stock_attuale']}</td>
              <td class='{cls}'>{txt}</td></tr>
        </table>"""
    else:
        finito_html = "<p style='color:#f87171'>❌ Articolo finito non riconosciuto.</p>"

    righe_comp = ''
    for c in comps:
        cls_s = 'ok' if c['stock_ok'] else 'warn'
        w     = '⚠️' if not c['stock_ok'] else ''
        cls_d = 'ok' if c['in_db'] else 'warn'
        righe_comp += f"""<tr>
          <td style='color:#f87171;font-weight:bold'>{c['codice']}</td>
          <td>{c['descrizione']}</td>
          <td style='color:#f87171;font-weight:bold'>-{c['qta']}</td>
          <td class='{cls_s}'>{c['stock_attuale']} {w}</td>
          <td class='{cls_d}'>{'✅' if c['in_db'] else '⚠️ Non trovato'}</td>
        </tr>"""

    comp_html = f"""<table class='prev-table'>
      <tr><th>Codice</th><th>Descrizione</th><th>Qtà da scaricare</th><th>Stock attuale</th><th>DB</th></tr>
      {righe_comp}
    </table>""" if comps else "<p style='color:#f87171'>❌ Nessun componente trovato.</p>"

    can_confirm = bool(finito and comps and not errori)
    btn_confirm = (
        f"<form action='/conferma_dp_zucchetti/{filename}' method='post'>"
        "<button type='submit' class='btn-ok'>✅ CONFERMA — Esegui movimenti magazzino</button>"
        "</form>"
    ) if can_confirm else (
        "<div style='color:#f87171;font-size:13px;margin-top:12px'>"
        "⛔ Correggere i problemi prima di confermare."
        "</div>"
    )

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Anteprima DP: {filename}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;
     display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.box{{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:30px;max-width:860px;width:100%}}
h2{{color:#c084fc;font-size:18px;margin-bottom:4px}}
.sub{{color:#555;font-size:12px;margin-bottom:20px}}
.meta{{background:#1a1a1a;border-radius:8px;padding:12px 16px;display:flex;gap:24px;margin-bottom:20px;flex-wrap:wrap}}
.meta span{{font-size:13px}} .meta b{{color:#f0ad4e}}
.section-title{{font-size:12px;font-weight:bold;text-transform:uppercase;letter-spacing:1px;
                padding:4px 0;margin:18px 0 10px;border-bottom:1px solid #222}}
.section-carico{{color:#4ade80}} .section-scarico{{color:#f87171}}
.prev-table{{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:8px}}
.prev-table th{{background:#1a1a1a;color:#888;font-size:11px;text-transform:uppercase;
                letter-spacing:0.5px;padding:7px 10px;text-align:left;border-bottom:1px solid #333}}
.prev-table td{{padding:7px 10px;border-bottom:1px solid #1e1e1e}}
.ok{{color:#4ade80}} .warn{{color:#f0ad4e}}
.warn-box{{background:#1a0a0a;border:1px solid #f8717133;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px}}
.warn-box ul{{margin:6px 0 0 16px}}
.btn-ok{{width:100%;background:#28a745;color:#000;border:none;padding:14px;border-radius:8px;
         font-weight:900;font-size:15px;cursor:pointer;margin-top:16px}}
.btn-ok:hover{{background:#34d058}}
.btn-cancel{{display:block;text-align:center;margin-top:10px;color:#555;font-size:12px;text-decoration:none}}
.btn-cancel:hover{{color:#aaa}}
.badge-dp{{display:inline-block;background:#1a0a2e;border:1px solid #7b2d8b;
           color:#c084fc;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold}}
</style></head><body>
<div class='box'>
  <h2>🏭 Anteprima Dichiarazione di Produzione</h2>
  <div class='sub'>Verifica i dati estratti dal PDF prima di eseguire i movimenti di magazzino</div>
  <div class='meta'>
    <span><b>File:</b> {filename}</span>
    <span><b>N° DP:</b> <span class='badge-dp'>{numero_dp} /DP</span></span>
    <span><b>Data:</b> {data_dp}</span>
  </div>
  {errori_html}
  {raw_html}
  <div class='section-title section-carico'>▲ CARICO — Prodotto Finito</div>
  {finito_html}
  <div class='section-title section-scarico'>▼ SCARICO — Componenti / Semilavorati</div>
  {comp_html}
  {btn_confirm}
  <a href='/dichiarazioni_produzione' class='btn-cancel'>← Annulla e torna alle MO</a>
</div></body></html>"""


@app.route('/conferma_dp_zucchetti/<path:filename>', methods=['POST'])
def conferma_dp_zucchetti(filename):
    """Step 3: esegue movimenti magazzino dal PDF /DP Zucchetti."""
    tmp_path = os.path.join(UPLOAD_FOLDER, f'_dp_tmp_{filename}')
    if not os.path.exists(tmp_path):
        flash('File temporaneo scaduto. Ricarica il PDF.', 'danger')
        return redirect(url_for('lista_dichiarazioni_produzione'))
    try:
        dati      = estrai_dati_dp_zucchetti(tmp_path)
        magazzino = get_magazzino()
        finito    = dati['finito']
        comps     = dati['componenti']

        if not finito or not comps:
            flash('Dati insufficienti nel PDF.', 'danger')
            return redirect(url_for('lista_dichiarazioni_produzione'))

        # Numerazione progressiva
        ultimo    = DichiarazioneProduzione.query.order_by(DichiarazioneProduzione.id.desc()).first()
        n         = (ultimo.id + 1) if ultimo else 1
        numero_mo = f'MO-{n:03d}/DP'

        # Verifica stock
        errori = []
        for c in comps:
            stk = (magazzino.get(c['codice']) or {}).get('stock', 0)
            if stk < c['qta']:
                errori.append(f"{c['codice']}: stock {stk} < necessario {c['qta']}")
        if errori:
            flash('Stock insufficiente: ' + ' | '.join(errori), 'danger')
            return redirect(url_for('lista_dichiarazioni_produzione'))

        # Scarica componenti
        componenti_salvati = []
        for c in comps:
            art = Articolo.query.filter_by(sku=c['codice']).first()
            if art:
                art.stock = max(0, (art.stock or 0) - c['qta'])
            componenti_salvati.append({
                'codice': c['codice'],
                'descrizione': c['descrizione'],
                'qta_scaricata': c['qta'],
            })

        # Carica finito
        art_fin = Articolo.query.filter_by(sku=finito['codice']).first()
        desc_finito = finito['descrizione']
        if art_fin:
            art_fin.stock = (art_fin.stock or 0) + finito['qta']
            desc_finito   = art_fin.descrizione or desc_finito
        else:
            # Crea articolo se non esiste
            art_fin = Articolo(
                sku=finito['codice'], descrizione=desc_finito,
                stock=finito['qta'], ordinati=0, incoming=0)
            db.session.add(art_fin)

        # Salva record DP
        dp = DichiarazioneProduzione(
            numero_mo       = numero_mo,
            conferma        = '',
            codice_finito   = finito['codice'],
            desc_finito     = desc_finito,
            qta_prodotta    = finito['qta'],
            operatore       = f"Zucchetti DP {dati['numero_dp']}",
            note            = f"PDF: {filename} — N° {dati['numero_dp']}/DP del {dati['data_dp']}",
            componenti_json = json.dumps(componenti_salvati),
        )
        db.session.add(dp)
        db.session.commit()

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        scrivi_log(f"DP ZUCCHETTI {numero_mo}: +{finito['qta']}×{finito['codice']} | {len(comps)} componenti scaricati")
        flash(f'✅ {numero_mo} eseguita — {finito["qta"]}× {finito["codice"]} caricati, {len(comps)} componenti scaricati.', 'success')

    except Exception as e:
        db.session.rollback()
        scrivi_log(f'ERRORE conferma_dp_zucchetti {filename}: {e}')
        flash(f'Errore: {e}', 'danger')

    return redirect(url_for('lista_dichiarazioni_produzione'))


@app.route('/dichiarazioni_produzione')
def lista_dichiarazioni_produzione():
    """Pagina principale: lista storico MO + form nuova DP."""
    dps = DichiarazioneProduzione.query.order_by(
        DichiarazioneProduzione.creato_il.desc()).all()
    return render_template('dichiarazioni_produzione.html', dps=dps)


@app.route('/nuova_dp')
def nuova_dp():
    """
    Pre-compila il form per una nuova Manufacturing Order.
    Parametri GET: conferma=23/IS, codice=ART001, qta=2
    """
    conferma    = request.args.get('conferma', '')
    codice      = request.args.get('codice', '')
    qta         = int(request.args.get('qta', 1))

    magazzino   = get_magazzino()
    distinta    = get_distinta_map()

    art_info    = magazzino.get(codice, {})
    componenti  = []
    for comp in distinta.get(codice, []):
        c_info = magazzino.get(comp['codice'], {})
        componenti.append({
            'codice':       comp['codice'],
            'descrizione':  c_info.get('descrizione', comp['codice']),
            'qta_unit':     comp['quantita'],          # per 1 finito
            'qta_totale':   int(comp['quantita'] * qta),
            'stock':        c_info.get('stock', 0),
        })

    return render_template(
        'dichiarazioni_produzione.html',
        dps=DichiarazioneProduzione.query.order_by(
            DichiarazioneProduzione.creato_il.desc()).all(),
        form={
            'conferma':     conferma,
            'codice':       codice,
            'descrizione':  art_info.get('descrizione', codice),
            'qta':          qta,
            'componenti':   componenti,
        }
    )


@app.route('/conferma_dp', methods=['POST'])
def conferma_dp():
    """
    Esegue la Manufacturing Order:
    - CARICA  +qta  articolo finito
    - SCARICA -qta  ogni semilavorato
    - Salva record DichiarazioneProduzione
    - Ricalcola stato FascicoloOrdine
    """
    try:
        codice_finito = request.form.get('codice_finito', '').strip().upper()
        qta_prodotta  = int(request.form.get('qta_prodotta', 1))
        conferma_ref  = request.form.get('conferma', '').strip()
        operatore     = request.form.get('operatore', '').strip()
        note          = request.form.get('note', '').strip()

        # Componenti inviati come comp_codice[], comp_qta[]
        comp_codici = request.form.getlist('comp_codice[]')
        comp_qtas   = request.form.getlist('comp_qta[]')

        if not codice_finito or qta_prodotta < 1:
            flash('Dati incompleti.', 'danger')
            return redirect(url_for('lista_dichiarazioni_produzione'))

        # Numerazione progressiva MO-NNN/DP
        ultimo = DichiarazioneProduzione.query.order_by(
            DichiarazioneProduzione.id.desc()).first()
        n = (ultimo.id + 1) if ultimo else 1
        numero_mo = f'MO-{n:03d}/DP'

        magazzino = get_magazzino()
        errori    = []

        # ── Verifica stock semilavorati ──────────────────────────────────────
        for cod, qstr in zip(comp_codici, comp_qtas):
            cod  = cod.strip().upper()
            qnec = int(qstr)
            stk  = (magazzino.get(cod) or {}).get('stock', 0)
            if stk < qnec:
                errori.append(f'{cod}: stock {stk} < necessario {qnec}')

        if errori:
            flash('Stock insufficiente: ' + ' | '.join(errori), 'danger')
            return redirect(url_for('nuova_dp',
                                    conferma=conferma_ref,
                                    codice=codice_finito,
                                    qta=qta_prodotta))

        # ── Scarica semilavorati ─────────────────────────────────────────────
        componenti_salvati = []
        for cod, qstr in zip(comp_codici, comp_qtas):
            cod   = cod.strip().upper()
            qscar = int(qstr)
            art   = Articolo.query.filter_by(sku=cod).first()
            if art:
                art.stock = max(0, (art.stock or 0) - qscar)
            c_info = magazzino.get(cod, {})
            componenti_salvati.append({
                'codice':       cod,
                'descrizione':  c_info.get('descrizione', cod),
                'qta_scaricata': qscar,
            })

        # ── Carica prodotto finito ───────────────────────────────────────────
        art_finito = Articolo.query.filter_by(sku=codice_finito).first()
        desc_finito = ''
        if art_finito:
            desc_finito      = art_finito.descrizione or codice_finito
            art_finito.stock = (art_finito.stock or 0) + qta_prodotta
        else:
            desc_finito = codice_finito

        # ── Salva record DP ──────────────────────────────────────────────────
        dp = DichiarazioneProduzione(
            numero_mo       = numero_mo,
            conferma        = conferma_ref,
            codice_finito   = codice_finito,
            desc_finito     = desc_finito,
            qta_prodotta    = qta_prodotta,
            operatore       = operatore,
            note            = note,
            componenti_json = json.dumps(componenti_salvati),
        )
        db.session.add(dp)
        db.session.commit()

        # ── Ricalcola stato ordine cliente collegato (solo se c'è un ordine) ──
        if conferma_ref:
            try:
                fascicolo = FascicoloOrdine.query.filter_by(conferma=conferma_ref).first()
                if fascicolo:
                    _ricalcola_stati_fascicoli_per_sku(codice_finito)
            except Exception:
                pass

        scrivi_log(f'MANUFACTURING ORDER {numero_mo}: +{qta_prodotta}×{codice_finito} '
                   f'| scaricati {len(componenti_salvati)} semilavorati | ordine {conferma_ref}')
        flash(f'✅ {numero_mo} confermata — {qta_prodotta}× {codice_finito} caricati in magazzino.', 'success')
        return redirect(url_for('lista_dichiarazioni_produzione'))

    except Exception as e:
        db.session.rollback()
        scrivi_log(f'ERRORE conferma_dp: {e}')
        flash(f'Errore: {e}', 'danger')
        return redirect(url_for('lista_dichiarazioni_produzione'))


@app.route('/api/dp_pdf/<int:dp_id>')
def dp_pdf(dp_id):
    """Genera e scarica il PDF della Manufacturing Order."""
    dp = DichiarazioneProduzione.query.get_or_404(dp_id)
    componenti = json.loads(dp.componenti_json or '[]')

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 40px; }}
  h1   {{ font-size: 16pt; color: #1a3a5c; margin-bottom: 4px; }}
  h2   {{ font-size: 12pt; color: #444; font-weight: normal; margin-top:0; }}
  .meta {{ background:#f4f6f9; padding:10px 14px; border-radius:6px; margin:16px 0; }}
  table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
  th  {{ background:#1a3a5c; color:#fff; padding:7px 10px; text-align:left; }}
  td  {{ border-bottom:1px solid #dde; padding:6px 10px; }}
  .totale {{ font-weight:bold; background:#e8f5e9; }}
  .footer {{ margin-top:40px; font-size:9pt; color:#888; border-top:1px solid #ddd; padding-top:8px; }}
</style></head><body>
<h1>Manufacturing Order — Dichiarazione di Produzione</h1>
<h2>{dp.numero_mo}</h2>
<div class="meta">
  <b>Ordine cliente:</b> {dp.conferma or '—'} &nbsp;|&nbsp;
  <b>Operatore:</b> {dp.operatore or '—'} &nbsp;|&nbsp;
  <b>Data:</b> {dp.creato_il.strftime('%d/%m/%Y %H:%M')}<br>
  {f'<b>Note:</b> {dp.note}' if dp.note else ''}
</div>

<h3 style="color:#1a6e30">▲ CARICO — Prodotto Finito</h3>
<table>
  <tr><th>Codice</th><th>Descrizione</th><th>Quantità prodotta</th></tr>
  <tr class="totale"><td>{dp.codice_finito}</td><td>{dp.desc_finito}</td><td>{dp.qta_prodotta}</td></tr>
</table>

<h3 style="color:#c0392b; margin-top:24px">▼ SCARICO — Semilavorati utilizzati</h3>
<table>
  <tr><th>Codice</th><th>Descrizione</th><th>Quantità scaricata</th></tr>
  {''.join(f"<tr><td>{c['codice']}</td><td>{c.get('descrizione','')}</td><td>{c['qta_scaricata']}</td></tr>" for c in componenti)}
</table>

<div class="footer">
  Documento generato da MasterLogistic — {dp.numero_mo} — {dp.creato_il.strftime('%d/%m/%Y %H:%M')}
</div>
</body></html>"""

    import subprocess, tempfile, os as _os
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html)
        tmp_html = f.name
    tmp_pdf = tmp_html.replace('.html', '.pdf')
    try:
        subprocess.run(['weasyprint', tmp_html, tmp_pdf], check=True,
                       capture_output=True, timeout=30)
        with open(tmp_pdf, 'rb') as pf:
            pdf_bytes = pf.read()
        _os.unlink(tmp_html)
        _os.unlink(tmp_pdf)
        from flask import Response
        return Response(pdf_bytes,
                        mimetype='application/pdf',
                        headers={'Content-Disposition':
                                 f'attachment; filename="{dp.numero_mo.replace("/", "-")}.pdf"'})
    except Exception as e:
        try: _os.unlink(tmp_html)
        except: pass
        flash(f'Errore generazione PDF: {e}', 'danger')
        return redirect(url_for('lista_dichiarazioni_produzione'))




# ─────────────────────────────────────────────────────────────────────────────



# ==============================================================================
# VALIDAZIONE PORTAFOGLIO ORDINI — Confronto Zucchetti vs WMS
# ==============================================================================

def _parse_portafoglio_zucchetti(pdf_path):
    """
    Legge il PDF "Stampa_portafoglio_ordini_(Stampa_per_cliente_fornitore).pdf"
    esportato da Zucchetti e restituisce la lista degli ordini aperti.
    Struttura: Cliente: CODICE Nome → DATA NUMERO Pagamento
    """
    import pdfplumber as _plumber
    ordini = {}   # {conferma: {cliente, data, numero_raw}}
    try:
        with _plumber.open(pdf_path) as pdf:
            testo = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        linee = testo.split('\n')
        pat_cli = re.compile(r'^Cliente:\s*(\d+)\s+(.+)$')
        pat_ord = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(\d+)\s+.+$')
        cliente_corrente = ''
        for linea in linee:
            l = linea.strip()
            mc = pat_cli.match(l)
            if mc:
                # il nome cliente può continuare sulla riga successiva (es. Scs Soc.Coop.)
                cliente_corrente = mc.group(2).strip()
                continue
            # se la riga è solo testo (continuazione nome cliente spezzato)
            if cliente_corrente and not pat_ord.match(l) and not l.startswith('Magazzino') and not l.startswith('Totale') and not l.startswith('Data') and not l.startswith('Articolo') and not l.startswith('Valuta') and not l.startswith('Selezioni') and not l.startswith('Tipo') and not l.startswith('Causale') and not l.startswith('Da') and not l.startswith('A ') and not l.startswith('Commessa') and l and not re.match(r'^\d', l) and not re.search(r'n\.\s+\d', l) and not re.search(r'Elaborato|pag\.:', l):
                # potrebbe essere continuazione del nome
                pass
            mo = pat_ord.match(l)
            if mo and cliente_corrente:
                num = mo.group(2)
                data = mo.group(1)
                conferma = f"{num}/IS"
                if conferma not in ordini:
                    ordini[conferma] = {
                        'cliente':     cliente_corrente,
                        'data_ordine': data,
                        'conferma':    conferma,
                    }
    except Exception as e:
        scrivi_log(f"ERRORE parse portafoglio Zucchetti: {e}")
    return ordini


@app.route('/valida_portafoglio', methods=['GET', 'POST'])
def valida_portafoglio():
    """
    Pagina di validazione settimanale:
    - GET:  mostra form per caricare il PDF portafoglio Zucchetti
    - POST: esegue il confronto e mostra discrepanze
    """
    if request.method == 'GET':
        return _pagina_upload_portafoglio()

    # POST: riceve il PDF e fa il matching
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return _pagina_upload_portafoglio(errore="Seleziona un file PDF valido.")

    # Salva temporaneamente
    tmp_path = os.path.join(UPLOAD_FOLDER, f'_portafoglio_tmp_{file.filename}')
    file.save(tmp_path)

    try:
        ordini_zucchetti = _parse_portafoglio_zucchetti(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    if not ordini_zucchetti:
        return _pagina_upload_portafoglio(errore="Nessun ordine trovato nel PDF. Verifica che sia il file corretto.")

    # Ordini nel WMS (PDF nella cartella uploads)
    ordini_wms = {}
    mappa_esterni = get_mappa_codici_esterni()
    if os.path.exists(UPLOAD_FOLDER):
        for f_co in os.listdir(UPLOAD_FOLDER):
            if not f_co.lower().endswith('.pdf') or f_co.startswith('_'):
                continue
            try:
                info = estrai_dati_completi_pdf(f_co, mappa_esterni)
                if info['conferma'] != 'N/D':
                    ordini_wms[info['conferma']] = {
                        'cliente':  info.get('cliente', '—'),
                        'filename': f_co,
                        'conferma': info['conferma'],
                    }
            except Exception:
                continue

    # Ordini anche in FascicoloOrdine (DB — più affidabile)
    for fasc in FascicoloOrdine.query.all():
        if fasc.conferma and fasc.conferma not in ordini_wms:
            ordini_wms[fasc.conferma] = {
                'cliente':  fasc.cliente or '—',
                'filename': '',
                'conferma': fasc.conferma,
            }

    z_set   = set(ordini_zucchetti.keys())
    wms_set = set(ordini_wms.keys())

    # Analisi discrepanze
    solo_zucchetti = z_set - wms_set   # in Zucchetti ma NON nel WMS → mancanti nel WMS!
    solo_wms       = wms_set - z_set   # nel WMS ma NON in Zucchetti → forse già evasi o extra
    in_entrambi    = z_set & wms_set   # OK

    scrivi_log(
        f"VALIDAZIONE PORTAFOGLIO: {len(z_set)} ordini Zucchetti, "
        f"{len(wms_set)} WMS, "
        f"{len(solo_zucchetti)} mancanti WMS, "
        f"{len(solo_wms)} extra WMS"
    )

    return _pagina_risultato_matching(
        ordini_zucchetti, ordini_wms,
        solo_zucchetti, solo_wms, in_entrambi
    )


def _pagina_upload_portafoglio(errore=None):
    err_html = f"<div style='background:#1a0000;border:1px solid #dc354555;border-radius:8px;padding:12px 16px;margin-bottom:20px;color:#ff4d5e;font-size:0.9rem;'>⚠️ {errore}</div>" if errore else ''
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Validazione Portafoglio Ordini</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;
     display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.box{{background:#141414;border:1px solid #2a2a2a;border-radius:14px;padding:36px;max-width:600px;width:100%}}
h2{{color:#f0ad4e;font-size:1.2rem;margin-bottom:8px}}
.sub{{color:#555;font-size:0.8rem;margin-bottom:24px;line-height:1.6}}
.upload-area{{border:2px dashed #333;border-radius:10px;padding:40px 20px;text-align:center;
             cursor:pointer;transition:border-color 0.2s;margin-bottom:20px}}
.upload-area:hover{{border-color:#f0ad4e}}
.upload-area input{{display:none}}
.upload-icon{{font-size:3rem;margin-bottom:12px}}
.upload-label{{color:#888;font-size:0.85rem}}
.btn-submit{{width:100%;background:#f0ad4e;color:#000;border:none;padding:14px;
            border-radius:8px;font-weight:900;font-size:1rem;cursor:pointer;margin-top:8px}}
.btn-submit:hover{{background:#d4941e}}
.note{{background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:14px 16px;
       font-size:0.78rem;color:#666;line-height:1.7;margin-top:16px}}
.note b{{color:#888}}
a.back{{display:block;text-align:center;margin-top:16px;color:#555;font-size:0.8rem;text-decoration:none}}
a.back:hover{{color:#aaa}}
</style></head><body>
<div class='box'>
  <div class='upload-icon' style='text-align:center'>📋</div>
  <h2 style='text-align:center'>Validazione Portafoglio Ordini</h2>
  <p class='sub' style='text-align:center'>
    Carica il PDF esportato da Zucchetti (<b>Stampa portafoglio ordini per cliente</b>)
    per confrontarlo con gli ordini presenti nel WMS e rilevare eventuali discrepanze.
  </p>
  {err_html}
  <form method='post' enctype='multipart/form-data'>
    <div class='upload-area' onclick="document.getElementById('f-portafoglio').click()">
      <div class='upload-icon'>📂</div>
      <div class='upload-label'>Clicca per selezionare il PDF portafoglio Zucchetti</div>
      <div id='fname-label' style='color:#f0ad4e;font-size:0.8rem;margin-top:8px;'></div>
      <input type='file' id='f-portafoglio' name='file' accept='.pdf'
             onchange="document.getElementById('fname-label').textContent=this.files[0]?.name||''">
    </div>
    <button type='submit' class='btn-submit'>🔍 Analizza e Confronta</button>
  </form>
  <div class='note'>
    <b>Come usarlo:</b><br>
    1. In Zucchetti vai su <b>Portafoglio ordini → Stampa per cliente/fornitore</b><br>
    2. Esporta in PDF (tutti gli ordini aperti)<br>
    3. Carica qui il PDF — il sistema confronterà automaticamente con il WMS<br>
    4. Vedrai quali ordini mancano nel WMS o sono in eccesso
  </div>
  <a href='/' class='back'>← Torna alla Dashboard</a>
</div>
</body></html>"""


def _pagina_risultato_matching(z_ord, wms_ord, solo_z, solo_wms, in_entrambi):
    """Genera la pagina HTML con i risultati del confronto."""

    ha_alert = len(solo_z) > 0

    # Sezione ordini MANCANTI nel WMS (presenti in Zucchetti ma non nel WMS) — CRITICO
    righe_mancanti = ''
    for conf in sorted(solo_z, key=lambda x: int(x.split('/')[0])):
        o = z_ord[conf]
        righe_mancanti += f"""<tr>
          <td style='color:#ff4d5e;font-weight:bold;font-family:monospace'>{conf}</td>
          <td style='color:#ffaaaa'>{o['cliente']}</td>
          <td style='color:#888'>{o['data_ordine']}</td>
          <td><span style='background:#3a0000;color:#ff4d5e;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold'>❌ MANCANTE NEL WMS</span></td>
        </tr>"""

    # Sezione ordini EXTRA nel WMS (non in Zucchetti) — info/attenzione
    righe_extra = ''
    for conf in sorted(solo_wms, key=lambda x: int(x.split('/')[0]) if x.split('/')[0].isdigit() else 0):
        o = wms_ord[conf]
        righe_extra += f"""<tr>
          <td style='color:#f0ad4e;font-weight:bold;font-family:monospace'>{conf}</td>
          <td style='color:#ffd070'>{o['cliente']}</td>
          <td style='color:#888'>—</td>
          <td><span style='background:#2a1a00;color:#f0ad4e;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold'>⚠️ SOLO NEL WMS</span></td>
        </tr>"""

    # Sezione ordini OK
    righe_ok = ''
    for conf in sorted(in_entrambi, key=lambda x: int(x.split('/')[0]) if x.split('/')[0].isdigit() else 0):
        o = z_ord[conf]
        righe_ok += f"""<tr>
          <td style='color:#4ade80;font-family:monospace'>{conf}</td>
          <td style='color:#aaa'>{o['cliente']}</td>
          <td style='color:#555'>{o['data_ordine']}</td>
          <td><span style='background:#0d2a0d;color:#4ade80;padding:3px 10px;border-radius:12px;font-size:11px;'>✅ OK</span></td>
        </tr>"""

    alert_banner = ''
    if ha_alert:
        alert_banner = f"""
        <div style='background:#1a0000;border:3px solid #ff0000;border-radius:12px;
                    padding:20px 24px;margin-bottom:28px;
                    box-shadow:0 0 30px #ff000055;'>
          <div style='font-size:2rem;margin-bottom:8px'>🚨</div>
          <div style='color:#ff0000;font-size:1.1rem;font-weight:900;margin-bottom:8px;text-transform:uppercase;'>
            ATTENZIONE — {len(solo_z)} ORDINI MANCANTI NEL WMS!
          </div>
          <div style='color:#ffaaaa;font-size:0.85rem;line-height:1.6'>
            Gli ordini elencati qui sotto sono presenti nel portafoglio Zucchetti
            ma <b>non trovati nel WMS</b>. Potrebbero essere stati eliminati accidentalmente
            o non ancora caricati. <b>Verifica immediatamente!</b>
          </div>
        </div>"""
    else:
        alert_banner = """
        <div style='background:#0d2a0d;border:2px solid #28a745;border-radius:12px;
                    padding:20px 24px;margin-bottom:28px;'>
          <div style='font-size:2rem;margin-bottom:8px'>✅</div>
          <div style='color:#4ade80;font-size:1.1rem;font-weight:900;margin-bottom:4px;'>
            TUTTO OK — Nessun ordine mancante!
          </div>
          <div style='color:#aaa;font-size:0.85rem;'>
            Tutti gli ordini del portafoglio Zucchetti sono presenti nel WMS.
          </div>
        </div>"""

    tab_style = "width:100%;border-collapse:collapse;font-size:0.83rem;margin-bottom:24px"
    th_style  = "background:#1a1a1a;color:#888;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;padding:8px 12px;text-align:left;border-bottom:1px solid #2a2a2a"
    td_dyn    = "padding:7px 12px;border-bottom:1px solid #1a1a1a"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Risultato Validazione Portafoglio</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0a0a0a;color:#ccc;padding:28px}}
.container{{max-width:960px;margin:0 auto}}
h1{{color:#f0ad4e;font-size:1.3rem;margin-bottom:6px}}
.sub{{color:#555;font-size:0.8rem;margin-bottom:28px}}
.section-title{{font-size:0.75rem;font-weight:bold;text-transform:uppercase;letter-spacing:1px;
               color:#888;padding:6px 0;margin:20px 0 10px;border-bottom:1px solid #222}}
.stats{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
.stat{{background:#141414;border:1px solid #2a2a2a;border-radius:10px;padding:16px 20px;flex:1;min-width:140px;text-align:center}}
.stat .num{{font-size:2rem;font-weight:900;margin-bottom:4px}}
.stat .lbl{{font-size:0.72rem;color:#555;text-transform:uppercase;letter-spacing:0.5px}}
table{{width:100%;border-collapse:collapse;font-size:0.83rem;margin-bottom:8px}}
th{{background:#1a1a1a;color:#888;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px;padding:8px 12px;text-align:left;border-bottom:1px solid #2a2a2a}}
td{{padding:7px 12px;border-bottom:1px solid #1a1a1a}}
.btn-back{{display:inline-block;background:#1a1a1a;color:#888;border:1px solid #333;
           padding:10px 20px;border-radius:8px;text-decoration:none;font-size:0.85rem;margin-top:20px}}
.btn-back:hover{{color:#fff}}
.btn-ricarica{{display:inline-block;background:#f0ad4e;color:#000;border:none;
               padding:10px 20px;border-radius:8px;text-decoration:none;font-size:0.85rem;
               font-weight:bold;margin-top:20px;margin-left:10px;cursor:pointer}}
</style></head><body>
<div class='container'>
  <h1>📋 Risultato Validazione Portafoglio Ordini</h1>
  <p class='sub'>Confronto tra Zucchetti (portafoglio ordini) e WMS (ordini caricati)</p>

  {alert_banner}

  <div class='stats'>
    <div class='stat'>
      <div class='num' style='color:#00d2ff'>{len(z_ord)}</div>
      <div class='lbl'>Ordini Zucchetti</div>
    </div>
    <div class='stat'>
      <div class='num' style='color:#a78bfa'>{len(wms_ord)}</div>
      <div class='lbl'>Ordini WMS</div>
    </div>
    <div class='stat'>
      <div class='num' style='color:#4ade80'>{len(in_entrambi)}</div>
      <div class='lbl'>Corrispondenti ✅</div>
    </div>
    <div class='stat'>
      <div class='num' style='color:#ff4d5e'>{len(solo_z)}</div>
      <div class='lbl'>Mancanti nel WMS ❌</div>
    </div>
    <div class='stat'>
      <div class='num' style='color:#f0ad4e'>{len(solo_wms)}</div>
      <div class='lbl'>Solo nel WMS ⚠️</div>
    </div>
  </div>

  {"<div class='section-title' style='color:#ff4d5e'>❌ Ordini presenti in Zucchetti ma MANCANTI nel WMS</div><table><tr><th>Ordine</th><th>Cliente</th><th>Data ordine</th><th>Stato</th></tr><tbody>" + righe_mancanti + "</tbody></table>" if solo_z else ''}

  {"<div class='section-title' style='color:#f0ad4e'>⚠️ Ordini nel WMS non trovati in Zucchetti (forse già evasi o caricati in anticipo)</div><table><tr><th>Ordine</th><th>Cliente</th><th>Data</th><th>Stato</th></tr><tbody>" + righe_extra + "</tbody></table>" if solo_wms else ''}

  <div class='section-title' style='color:#4ade80'>✅ Ordini corrispondenti ({len(in_entrambi)})</div>
  <table><tr><th>Ordine</th><th>Cliente</th><th>Data ordine</th><th>Stato</th></tr>
  <tbody>{righe_ok}</tbody></table>

  <a href='/valida_portafoglio' class='btn-ricarica'>🔄 Nuova validazione</a>
  <a href='/' class='btn-back'>← Dashboard</a>
</div>
</body></html>"""


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port,
            debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
