"""
nesting_learning.py — ENOROSSI Paint Optimizer v26
===================================================
Modulo di apprendimento ibrido: CBR (Case-Based Reasoning) + base per ML futuro.

Funzionamento:
  1. Ogni layout manuale salvato viene registrato in nesting_manual_history
  2. Per ogni nuovo ordine, il sistema cerca il layout più simile (Jaccard similarity)
  3. Con abbastanza dati (>= MIN_SAMPLES_FOR_ML) si attiva un layer ML semplice
     basato su KNN (scikit-learn, opzionale — fallback automatico su CBR)

Flusso:
  - record_layout(session_key, codici_qty, layout_json)  → salva
  - suggest_layout(codici_qty)                           → proposta migliore
  - get_stats()                                          → statistiche dataset

Nota: usa SOLO la tabella nesting_manual_history già esistente.
Non richiede migrazioni aggiuntive.
"""

import json
import sqlite3
import math
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timezone


# ─── Costanti ──────────────────────────────────────────────────────────────────
MIN_SAMPLES_FOR_ML   = 20   # soglia per attivare KNN (futuro)
JACCARD_THRESHOLD    = 0.25  # similarità minima per proporre un layout
MAX_HISTORY_RESULTS  = 50   # quanti record analizzare nella ricerca CBR
VERSION              = "1.0.0"


# ─── Utility ───────────────────────────────────────────────────────────────────
def session_key_from_ordine_cad(ordine_id: int) -> str:
    """Chiave univoca per OrdineCAD."""
    return f"cad_{ordine_id}"


def session_key_from_ordine_ordine(ordine_id: int) -> str:
    """Chiave univoca per OrdineOrdine (SAP/NetPro)."""
    return f"ord_{ordine_id}"


def codici_qty_to_feature(codici_qty: Dict[str, int]) -> Dict[str, int]:
    """
    Normalizza il dizionario {codice: quantita} come feature vettore.
    Usato sia per Jaccard che per futura embedding.
    """
    return {k: v for k, v in codici_qty.items() if v > 0}


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity sui codici (ignora quantità — prima versione)."""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


def weighted_similarity(
    query: Dict[str, int],
    candidate: Dict[str, int]
) -> float:
    """
    Similarità pesata per quantità: combina Jaccard (codici) + coseno (quantità).
    Score in [0, 1].
    """
    q_set = set(query.keys())
    c_set = set(candidate.keys())
    jac   = jaccard_similarity(q_set, c_set)

    # Coseno sulle quantità dei codici in comune
    common = q_set & c_set
    if common:
        dot   = sum(query[k] * candidate[k] for k in common)
        norm_q = math.sqrt(sum(v**2 for v in query.values()))
        norm_c = math.sqrt(sum(v**2 for v in candidate.values()))
        cos = dot / (norm_q * norm_c) if norm_q * norm_c > 0 else 0
    else:
        cos = 0.0

    return 0.6 * jac + 0.4 * cos


# ─── NestingLearner ─────────────────────────────────────────────────────────────
class NestingLearner:
    """
    Gestisce la memoria dei layout manuale e fornisce suggerimenti CBR.

    Args:
        db_path: percorso al database SQLite (Flask: db.engine.url).
                 Se None, usa il raw sqlite3 con db_conn_fn().
        db_conn_fn: callable che restituisce una connessione SQLite già aperta.
                    Usato quando integrato in Flask.
    """

    def __init__(self, db_conn_fn=None, db_path: Optional[str] = None):
        self._conn_fn  = db_conn_fn
        self._db_path  = db_path

    def _get_conn(self):
        if self._conn_fn:
            return self._conn_fn()
        if self._db_path:
            return sqlite3.connect(self._db_path)
        raise RuntimeError("NestingLearner: serve db_conn_fn oppure db_path")

    # ── Inizializzazione tabella ──────────────────────────────────────────────
    def ensure_table(self):
        """Crea la tabella se non esiste (idempotente)."""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nesting_manual_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key  TEXT NOT NULL,
                    codici_json  TEXT NOT NULL,
                    layout_json  TEXT NOT NULL,
                    source       TEXT DEFAULT 'manual',
                    score_sat    REAL DEFAULT 0,
                    score_bal    REAL DEFAULT 0,
                    operatore    TEXT,
                    salvato_il   TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nmh_sk ON nesting_manual_history(session_key)"
            )
            conn.commit()
        finally:
            conn.close()

    # ── Salvataggio ──────────────────────────────────────────────────────────
    def record_layout(
        self,
        session_key:  str,
        codici_qty:   Dict[str, int],
        layout_json:  Dict[str, Any],
        source:       str  = 'manual',
        score_sat:    float = 0.0,
        score_bal:    float = 0.0,
        operatore:    Optional[str] = None,
    ) -> int:
        """
        Salva un layout nella history.

        Args:
            session_key:  chiave unica (cad_<id> o ord_<id>)
            codici_qty:   {codice_art: quantita}
            layout_json:  dizionario del layout completo
            source:       'manual' | 'auto' | 'import'
            score_sat:    saturazione barra [0-100]
            score_bal:    bilanciamento [0-100]
            operatore:    nome operaio (opzionale)

        Returns:
            id del record inserito
        """
        conn = self._get_conn()
        try:
            cur = conn.execute("""
                INSERT INTO nesting_manual_history
                    (session_key, codici_json, layout_json, source, score_sat, score_bal, operatore, salvato_il)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_key,
                json.dumps(codici_qty, ensure_ascii=False),
                json.dumps(layout_json, ensure_ascii=False),
                source,
                round(score_sat, 2),
                round(score_bal, 2),
                operatore,
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    # ── Suggerimento CBR ─────────────────────────────────────────────────────
    def suggest_layout(
        self,
        codici_qty: Dict[str, int],
        source_filter: Optional[str] = None,
        top_k: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Trova i layout più simili al set di codici fornito.

        Args:
            codici_qty:    {codice_art: quantita} dell'ordine corrente
            source_filter: filtra per source ('manual', 'auto', None=tutti)
            top_k:         quanti suggerimenti restituire

        Returns:
            Lista di dizionari {session_key, similarity, layout, codici, score_sat, score_bal, salvato_il}
            ordinati per similarity decrescente.
        """
        if not codici_qty:
            return []

        conn = self._get_conn()
        try:
            sql = "SELECT id, session_key, codici_json, layout_json, score_sat, score_bal, salvato_il FROM nesting_manual_history"
            params = []
            if source_filter:
                sql += " WHERE source = ?"
                params.append(source_filter)
            sql += f" ORDER BY id DESC LIMIT {MAX_HISTORY_RESULTS}"

            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        query_features = codici_qty_to_feature(codici_qty)
        results: List[Tuple[float, Dict]] = []

        for row in rows:
            row_id, sk, codici_raw, layout_raw, sat, bal, ts = row
            try:
                candidate_codici = json.loads(codici_raw)
            except Exception:
                continue

            sim = weighted_similarity(query_features, candidate_codici)
            if sim >= JACCARD_THRESHOLD:
                try:
                    layout = json.loads(layout_raw)
                except Exception:
                    layout = {}
                results.append((sim, {
                    'id':          row_id,
                    'session_key': sk,
                    'similarity':  round(sim, 4),
                    'similarity_pct': round(sim * 100, 1),
                    'layout':      layout,
                    'codici':      candidate_codici,
                    'score_sat':   sat,
                    'score_bal':   bal,
                    'salvato_il':  ts,
                }))

        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:top_k]]

    # ── Storico per sessione ─────────────────────────────────────────────────
    def get_history_for_session(
        self,
        session_key: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Restituisce la history di layout per una specifica sessione."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT id, source, score_sat, score_bal, operatore, salvato_il, layout_json
                FROM nesting_manual_history
                WHERE session_key = ?
                ORDER BY id DESC LIMIT ?
            """, (session_key, limit)).fetchall()
        finally:
            conn.close()

        return [
            {
                'id':        r[0],
                'source':    r[1],
                'score_sat': r[2],
                'score_bal': r[3],
                'operatore': r[4],
                'salvato_il': r[5],
                'layout':    json.loads(r[6]) if r[6] else {},
            }
            for r in rows
        ]

    # ── Ultimo layout salvato ────────────────────────────────────────────────
    def get_latest_layout(self, session_key: str) -> Optional[Dict[str, Any]]:
        """Restituisce l'ultimo layout salvato per questa sessione."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT layout_json, codici_json, source, salvato_il
                FROM nesting_manual_history
                WHERE session_key = ?
                ORDER BY id DESC LIMIT 1
            """, (session_key,)).fetchone()
        finally:
            conn.close()

        if not row:
            return None
        try:
            return {
                'layout':    json.loads(row[0]) if row[0] else {},
                'codici':    json.loads(row[1]) if row[1] else {},
                'source':    row[2],
                'salvato_il': row[3],
            }
        except Exception:
            return None

    # ── Statistiche dataset ──────────────────────────────────────────────────
    def get_stats(self) -> Dict[str, Any]:
        """
        Statistiche sul dataset di apprendimento.

        Returns:
            {total, manual, auto, avg_sat, avg_bal, ml_ready, ml_threshold}
        """
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM nesting_manual_history").fetchone()[0]
            manual = conn.execute(
                "SELECT COUNT(*) FROM nesting_manual_history WHERE source='manual'"
            ).fetchone()[0]
            auto = conn.execute(
                "SELECT COUNT(*) FROM nesting_manual_history WHERE source='auto'"
            ).fetchone()[0]
            avgs = conn.execute(
                "SELECT AVG(score_sat), AVG(score_bal) FROM nesting_manual_history WHERE source='manual'"
            ).fetchone()
        finally:
            conn.close()

        return {
            'total':         total,
            'manual':        manual,
            'auto':          auto,
            'avg_sat':       round(avgs[0] or 0, 1),
            'avg_bal':       round(avgs[1] or 0, 1),
            'ml_ready':      manual >= MIN_SAMPLES_FOR_ML,
            'ml_threshold':  MIN_SAMPLES_FOR_ML,
            'ml_progress':   min(100, round(manual / MIN_SAMPLES_FOR_ML * 100)),
        }


# ─── Factory per Flask ──────────────────────────────────────────────────────────
def make_learner_for_flask(db) -> NestingLearner:
    """
    Crea un NestingLearner usando la connessione SQLAlchemy di Flask.

    Usage in Flask app:
        from nesting_learning import make_learner_for_flask
        learner = make_learner_for_flask(db)

    Args:
        db: istanza SQLAlchemy (db = SQLAlchemy(app))
    """
    def _conn_fn():
        raw = db.engine.raw_connection()
        return raw
    learner = NestingLearner(db_conn_fn=_conn_fn)
    return learner
