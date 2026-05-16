"""
cad_parser.py — ENOROSSI Paint Optimizer v5
PHASE 1: CAD Parsing & Automated BOM Extraction

Analizza file STEP/STP con trimesh (open-source, no licenza CAD):
  - Traversal assembly tree → sub-assemblaggi + parti
  - Estrazione geometrica: volume, superficie m², bounding box, CoG
  - Generazione codice articolo deterministico ART-AGRI-XXXXXX
  - Scrittura BOM gerarchica nel DB (modelli ItemMaster + BOMRecord)
"""

from __future__ import annotations
import os, re, hashlib, math, json, warnings
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Tuple
import numpy as np
warnings.filterwarnings("ignore")


# ── Densità materiale default (acciaio) ────────────────────────
DENSITA_ACCIAIO_KG_M3 = 7850.0


# ═══════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════

@dataclass
class PartGeometry:
    """Dati geometrici e fisici estratti da un singolo componente CAD."""
    nome: str
    codice_art: str                    # ART-AGRI-XXXXXX deterministico
    volume_m3: float = 0.0
    superficie_m2: float = 0.0
    lunghezza_mm: float = 0.0          # bounding box X
    larghezza_mm: float = 0.0          # bounding box Y
    altezza_mm: float = 0.0            # bounding box Z
    peso_kg: float = 0.0
    cog_x_mm: float = 0.0             # Center of Gravity
    cog_y_mm: float = 0.0
    cog_z_mm: float = 0.0
    n_facce: int = 0
    n_vertici: int = 0
    is_watertight: bool = False
    passo_gancio_m: float = 0.4        # calcolato da dim max
    complessita_aggancio: int = 1      # 1/2/3
    hash_geom: str = ""                # hash invariante geometrico
    mesh_presente: bool = False        # True se trimesh è riuscito


@dataclass
class BOMItem:
    """Voce BOM con gerarchia."""
    livello: int
    nome_parent: str
    nome_part: str
    codice_art: str
    qty: int
    geom: Optional[PartGeometry] = None


@dataclass
class ParseResult:
    """Risultato completo parsing STEP."""
    file_nome: str
    assembly_nome: str
    n_parti_uniche: int
    n_parti_totali: int
    peso_totale_kg: float
    superficie_totale_m2: float
    parti: List[PartGeometry] = field(default_factory=list)
    bom: List[BOMItem] = field(default_factory=list)
    errori: List[str] = field(default_factory=list)
    warning: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# STEP FILE PARSER (low-level: nessuna licenza CAD richiesta)
# ═══════════════════════════════════════════════════════════════

def _leggi_prodotti_step(path: str) -> Dict[str, dict]:
    """
    Parsing minimale del file STEP per estrarre:
      - Nomi prodotti (PRODUCT)
      - Relazioni parent-child (NEXT_ASSEMBLY_USAGE_OCCURRENCE)
      - Quantità (conteggio istanze)
    Restituisce dict id_step → {nome, parent_id, qty}
    """
    prodotti: Dict[str, dict] = {}
    relazioni: List[Tuple[str, str]] = []  # (child_id, parent_id)

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        contenuto = f.read()

    # Estrai PRODUCT — pattern: #NNN=PRODUCT('codice','nome',...)
    for m in re.finditer(
        r'#(\d+)=PRODUCT\s*\(\s*\'([^\']*)\'\s*,\s*\'([^\']*)\'\s*',
        contenuto, re.IGNORECASE
    ):
        pid, codice, nome = m.group(1), m.group(2), m.group(3)
        prodotti[pid] = {
            'nome': nome or codice,
            'codice_sap': codice,
            'parent_ids': [],
            'child_ids': [],
            'qty': 1,
        }

    # Mappa PRODUCT_DEFINITION → PRODUCT
    pd_to_prod: Dict[str, str] = {}
    for m in re.finditer(
        r'#(\d+)=PRODUCT_DEFINITION\s*\([^,]*,[^,]*,#(\d+)',
        contenuto, re.IGNORECASE
    ):
        pd_id, prod_ref = m.group(1), m.group(2)
        # Cerca la PRODUCT_DEFINITION_FORMATION che referenzia il prodotto
        pass

    # Approccio semplificato: costruisci BOM da PRODUCT direttamente
    # Il primo PRODUCT è l'assembly radice
    return prodotti


def _estrai_nomi_assembly(path: str) -> List[str]:
    """Estrae i nomi delle parti dal file STEP."""
    nomi = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.search(r"PRODUCT\s*\(\s*'([^']*)'\s*,\s*'([^']*)'", line, re.IGNORECASE)
            if m:
                nome = m.group(2) or m.group(1)
                if nome and nome not in nomi:
                    nomi.append(nome)
    return nomi


# ═══════════════════════════════════════════════════════════════
# GENERAZIONE CODICE ARTICOLO DETERMINISTICO
# ═══════════════════════════════════════════════════════════════

def genera_codice_art(nome: str, volume_m3: float = 0.0,
                      superficie_m2: float = 0.0,
                      bbox: Tuple[float,float,float] = (0,0,0)) -> str:
    """
    Genera codice ART-AGRI-XXXXXX deterministico basato su invarianti
    geometrici: stesso oggetto → stesso codice, sempre.

    Hash = SHA256(nome_normalizzato + volume_arrotondato + superficie_arrotondato)
    """
    # Normalizza nome (lowercase, senza spazi multipli)
    nome_norm = re.sub(r'\s+', '_', nome.lower().strip())
    # Arrotonda a 4 cifre significative per tolleranza numerica
    vol_str = f"{volume_m3:.4e}"
    sup_str = f"{superficie_m2:.4e}"
    bbox_str = f"{bbox[0]:.1f}x{bbox[1]:.1f}x{bbox[2]:.1f}"

    payload = f"{nome_norm}|{vol_str}|{sup_str}|{bbox_str}"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    # Usa i primi 6 caratteri hex → 16M combinazioni
    codice = f"ART-AGRI-{digest[:6].upper()}"
    return codice


# ═══════════════════════════════════════════════════════════════
# ANALISI GEOMETRICA CON TRIMESH
# ═══════════════════════════════════════════════════════════════

def _analizza_mesh(mesh, nome: str) -> PartGeometry:
    """
    Estrae proprietà geometriche da una trimesh.Trimesh.
    Gestisce mesh non-watertight con fallback all'area della bounding box.
    """
    # Bounding box (mm) — STEP è spesso in mm
    bbox = mesh.bounding_box.extents  # [X, Y, Z]
    L, W, H = float(bbox[0]), float(bbox[1]), float(bbox[2])

    # Scala automatica: se dimensioni < 1.0 → probabilmente in METERS
    # Inventor STEP tipicamente in mm (non scala necessaria)
    if max(L, W, H) < 5.0:
        # Assumiamo metri → converti in mm
        L, W, H = L * 1000, W * 1000, H * 1000
        mesh = mesh.apply_scale(1000.0)

    # Volume
    vol_m3 = 0.0
    if mesh.is_watertight:
        vol_m3 = abs(float(mesh.volume)) / 1e9  # mm³ → m³
    else:
        # Stima: volume bounding box * fattore riempimento 0.35 (struttura aperta)
        vol_m3 = (L * W * H * 0.35) / 1e9

    # Superficie
    sup_m2 = float(mesh.area) / 1e6  # mm² → m²

    # Se mesh degenere → stima da bounding box
    if sup_m2 < 1e-6:
        sup_m2 = 2.0 * (L * W + L * H + W * H) / 1e6 * 1.5  # ×1.5 per rugosità

    # Center of Gravity (centroide bounding box se mesh non watertight)
    if mesh.is_watertight:
        cog = mesh.center_mass  # [x, y, z] in mm
    else:
        cog = mesh.centroid

    cog_x, cog_y, cog_z = float(cog[0]), float(cog[1]), float(cog[2])

    # Peso (acciaio)
    peso_kg = vol_m3 * DENSITA_ACCIAIO_KG_M3

    # Passo gancio (da dimensione massima)
    dim_max_m = max(L, W, H) / 1000.0
    passo_raw = math.ceil(dim_max_m * 1.20 * 20) / 20  # arrotonda a 0.05
    passo_m = max(0.4, min(passo_raw, 4.0))

    # Complessità aggancio
    n_ganci = round(passo_m / 0.4)
    complessita = 3 if n_ganci >= 4 else (2 if n_ganci >= 2 else 1)

    # Codice deterministico
    codice = genera_codice_art(nome, vol_m3, sup_m2, (L, W, H))
    hash_g = codice.split('-')[-1]

    return PartGeometry(
        nome=nome,
        codice_art=codice,
        volume_m3=round(vol_m3, 6),
        superficie_m2=round(sup_m2, 4),
        lunghezza_mm=round(L, 1),
        larghezza_mm=round(W, 1),
        altezza_mm=round(H, 1),
        peso_kg=round(peso_kg, 3),
        cog_x_mm=round(cog_x, 2),
        cog_y_mm=round(cog_y, 2),
        cog_z_mm=round(cog_z, 2),
        n_facce=len(mesh.faces),
        n_vertici=len(mesh.vertices),
        is_watertight=mesh.is_watertight,
        passo_gancio_m=round(passo_m, 2),
        complessita_aggancio=complessita,
        hash_geom=hash_g,
        mesh_presente=True,
    )


def analizza_step(path: str) -> ParseResult:
    """
    Entry point principale: analizza file STEP/STP.

    Strategia multi-layer:
      1. Tenta caricamento trimesh (STEP via open3d/pythonOCC fallback)
      2. Parsing testuale PRODUCT per BOM
      3. Estrazione geometrica per ogni parte trovata
    """
    nome_file = os.path.basename(path)
    result = ParseResult(
        file_nome=nome_file,
        assembly_nome=nome_file.replace('.stp', '').replace('.step', ''),
        n_parti_uniche=0,
        n_parti_totali=0,
        peso_totale_kg=0.0,
        superficie_totale_m2=0.0,
    )

    # ── STEP 1: Estrai nomi parti dal file STEP (parsing testuale) ──
    nomi_parti = _estrai_nomi_assembly(path)
    if not nomi_parti:
        result.errori.append("Nessuna PRODUCT trovata nel file STEP.")
        return result

    assembly_nome = nomi_parti[0]
    result.assembly_nome = assembly_nome
    parti_nomi = nomi_parti[1:]  # escludi assembly radice

    # ── STEP 2: Carica mesh con trimesh ──
    try:
        import trimesh
        scene = trimesh.load(path, force='scene', process=False)
        mesh_dict: Dict[str, object] = {}

        if isinstance(scene, trimesh.Scene):
            for node_name, geom in scene.geometry.items():
                if isinstance(geom, trimesh.Trimesh) and len(geom.faces) > 0:
                    mesh_dict[node_name] = geom
            # Se scene ha mesh dump, usala
            meshes = list(mesh_dict.values())
        elif isinstance(scene, trimesh.Trimesh):
            meshes = [scene]
            mesh_dict = {'assembly': scene}
        else:
            meshes = []
            result.warning.append("trimesh: formato non riconosciuto, uso geometria da BBox")

    except Exception as e:
        meshes = []
        result.warning.append(f"trimesh non disponibile o errore: {e}")

    # ── STEP 3: Combina nomi parti con mesh estratte ──
    parti_geom: List[PartGeometry] = []

    if meshes:
        # Associa mesh a nomi parti (best-effort per nome/ordine)
        mesh_list = list(mesh_dict.items()) if isinstance(mesh_dict, dict) else [(f"part_{i}", m) for i,m in enumerate(meshes)]

        for i, (mkey, mesh) in enumerate(mesh_list):
            # Prendi nome dalla lista parti STEP se disponibile
            if i < len(parti_nomi):
                nome = parti_nomi[i]
            else:
                nome = mkey

            try:
                geom = _analizza_mesh(mesh, nome)
                parti_geom.append(geom)
            except Exception as e:
                result.warning.append(f"Errore analisi mesh '{nome}': {e}")

    # Se non abbiamo mesh, genera geometria da bounding box STEP (dimensioni dai nomi)
    if not parti_geom and parti_nomi:
        result.warning.append("Geometria 3D non caricata — uso stime da nomi/BOM STEP")
        for nome in parti_nomi:
            # Stima dimensionale da keyword nel nome
            geom = _stima_geom_da_nome(nome)
            parti_geom.append(geom)

    # ── STEP 4: Deduplicazione per codice articolo ──
    parti_uniche: Dict[str, PartGeometry] = {}
    for g in parti_geom:
        if g.codice_art not in parti_uniche:
            parti_uniche[g.codice_art] = g

    # ── STEP 5: Costruisci BOM ──
    bom: List[BOMItem] = []
    qty_counter: Dict[str, int] = {}
    for g in parti_geom:
        qty_counter[g.codice_art] = qty_counter.get(g.codice_art, 0) + 1

    for codice, geom in parti_uniche.items():
        bom.append(BOMItem(
            livello=1,
            nome_parent=assembly_nome,
            nome_part=geom.nome,
            codice_art=codice,
            qty=qty_counter.get(codice, 1),
            geom=geom,
        ))

    # ── Totali ──
    n_tot = sum(b.qty for b in bom)
    peso_tot = sum(b.geom.peso_kg * b.qty for b in bom if b.geom)
    sup_tot = sum(b.geom.superficie_m2 * b.qty for b in bom if b.geom)

    result.parti = list(parti_uniche.values())
    result.bom = bom
    result.n_parti_uniche = len(parti_uniche)
    result.n_parti_totali = n_tot
    result.peso_totale_kg = round(peso_tot, 2)
    result.superficie_totale_m2 = round(sup_tot, 4)

    return result


def _stima_geom_da_nome(nome: str) -> PartGeometry:
    """
    Stima dimensioni geometriche da keyword nel nome della parte.
    Usato come fallback quando trimesh non carica la mesh.
    """
    n = nome.lower()

    # Tabella keyword → (L_mm, W_mm, H_mm, peso_kg_est)
    if 'frame' in n or 'box' in n or 'chassis' in n:
        L, W, H, kg = 1800, 900, 600, 85.0
    elif 'flanc' in n or 'flangia' in n or 'plate' in n or 'tole' in n:
        L, W, H, kg = 800, 500, 12, 15.0
    elif 'tube' in n or 'tub' in n or 'bar' in n:
        L, W, H, kg = 1500, 80, 80, 12.0
    elif 'dent' in n or 'tine' in n or 'griffe' in n:
        L, W, H, kg = 300, 60, 20, 1.5
    elif 'triangle' in n or 'attelage' in n or 'hitch' in n:
        L, W, H, kg = 600, 400, 80, 18.0
    elif 'gousset' in n or 'bracket' in n or 'staffa' in n:
        L, W, H, kg = 200, 150, 10, 2.5
    elif 'entretoise' in n or 'spacer' in n:
        L, W, H, kg = 300, 50, 50, 3.0
    elif 'plat' in n or 'plaque' in n:
        L, W, H, kg = 400, 200, 10, 6.0
    elif 'vis' in n or 'ecrou' in n or 'rondelle' in n or 'bolt' in n:
        L, W, H, kg = 90, 16, 16, 0.2
    elif 'axe' in n or 'shaft' in n or 'pin' in n:
        L, W, H, kg = 200, 20, 20, 0.5
    else:
        L, W, H, kg = 500, 300, 100, 8.0

    vol_m3 = (L * W * H * 0.35) / 1e9
    sup_m2 = round(2.0 * (L*W + L*H + W*H) / 1e6 * 1.3, 4)
    dim_max_m = max(L, W, H) / 1000.0
    passo_m = round(math.ceil(dim_max_m * 1.2 * 20) / 20, 2)
    passo_m = max(0.4, min(passo_m, 4.0))
    n_g = round(passo_m / 0.4)
    comp = 3 if n_g >= 4 else (2 if n_g >= 2 else 1)
    codice = genera_codice_art(nome, vol_m3, sup_m2, (L, W, H))

    return PartGeometry(
        nome=nome,
        codice_art=codice,
        volume_m3=round(vol_m3, 6),
        superficie_m2=sup_m2,
        lunghezza_mm=float(L),
        larghezza_mm=float(W),
        altezza_mm=float(H),
        peso_kg=round(kg, 2),
        cog_x_mm=round(L/2, 1),
        cog_y_mm=round(W/2, 1),
        cog_z_mm=round(H/2, 1),
        passo_gancio_m=passo_m,
        complessita_aggancio=comp,
        hash_geom=codice.split('-')[-1],
        mesh_presente=False,
    )


# ═══════════════════════════════════════════════════════════════
# SCRITTURA BOM NEL DB (integrazione Flask-SQLAlchemy)
# ═══════════════════════════════════════════════════════════════

def scrivi_bom_nel_db(result: ParseResult, db, ItemMasterCAD, BOMRecordCAD,
                      assembly_id: int = None) -> dict:
    """
    Scrive il ParseResult nel database (modelli ItemMasterCAD + BOMRecordCAD).
    Ritorna statistiche di inserimento.
    """
    inseriti = 0
    aggiornati = 0
    bom_records = 0

    for bom_item in result.bom:
        g = bom_item.geom
        if not g:
            continue

        # Upsert ItemMasterCAD per codice articolo
        esistente = ItemMasterCAD.query.filter_by(codice_art=g.codice_art).first()
        if esistente:
            # Aggiorna dati geometrici
            esistente.superficie_m2  = g.superficie_m2
            esistente.volume_m3      = g.volume_m3
            esistente.peso_kg        = g.peso_kg
            esistente.lunghezza_mm   = g.lunghezza_mm
            esistente.larghezza_mm   = g.larghezza_mm
            esistente.altezza_mm     = g.altezza_mm
            esistente.cog_x_mm       = g.cog_x_mm
            esistente.cog_y_mm       = g.cog_y_mm
            esistente.cog_z_mm       = g.cog_z_mm
            esistente.passo_gancio_m = g.passo_gancio_m
            aggiornati += 1
        else:
            item = ItemMasterCAD(
                codice_art       = g.codice_art,
                nome             = g.nome,
                assembly_file    = result.file_nome,
                superficie_m2    = g.superficie_m2,
                volume_m3        = g.volume_m3,
                peso_kg          = g.peso_kg,
                lunghezza_mm     = g.lunghezza_mm,
                larghezza_mm     = g.larghezza_mm,
                altezza_mm       = g.altezza_mm,
                cog_x_mm         = g.cog_x_mm,
                cog_y_mm         = g.cog_y_mm,
                cog_z_mm         = g.cog_z_mm,
                passo_gancio_m   = g.passo_gancio_m,
                complessita      = g.complessita_aggancio,
                hash_geom        = g.hash_geom,
                mesh_presente    = g.mesh_presente,
            )
            db.session.add(item)
            inseriti += 1

        db.session.flush()

        # Scrivi BOM record
        if assembly_id is not None:
            bom_rec = BOMRecordCAD(
                assembly_id  = assembly_id,
                codice_art   = g.codice_art,
                nome_part    = g.nome,
                livello      = bom_item.livello,
                nome_parent  = bom_item.nome_parent,
                qty          = bom_item.qty,
            )
            db.session.add(bom_rec)
            bom_records += 1

    db.session.commit()
    return {
        'inseriti': inseriti,
        'aggiornati': aggiornati,
        'bom_records': bom_records,
        'n_parti_uniche': result.n_parti_uniche,
        'n_parti_totali': result.n_parti_totali,
        'peso_totale_kg': result.peso_totale_kg,
        'superficie_totale_m2': result.superficie_totale_m2,
    }


# ═══════════════════════════════════════════════════════════════
# UTILITY: serializza per JSON
# ═══════════════════════════════════════════════════════════════

def parse_result_to_dict(result: ParseResult) -> dict:
    """Converte ParseResult in dict JSON-serializzabile."""
    return {
        'file_nome': result.file_nome,
        'assembly_nome': result.assembly_nome,
        'n_parti_uniche': result.n_parti_uniche,
        'n_parti_totali': result.n_parti_totali,
        'peso_totale_kg': result.peso_totale_kg,
        'superficie_totale_m2': result.superficie_totale_m2,
        'errori': result.errori,
        'warning': result.warning,
        'parti': [
            {
                'codice_art':    p.codice_art,
                'nome':          p.nome,
                'volume_m3':     p.volume_m3,
                'superficie_m2': p.superficie_m2,
                'lunghezza_mm':  p.lunghezza_mm,
                'larghezza_mm':  p.larghezza_mm,
                'altezza_mm':    p.altezza_mm,
                'peso_kg':       p.peso_kg,
                'cog_x_mm':      p.cog_x_mm,
                'cog_y_mm':      p.cog_y_mm,
                'cog_z_mm':      p.cog_z_mm,
                'passo_gancio_m':p.passo_gancio_m,
                'complessita':   p.complessita_aggancio,
                'mesh_presente': p.mesh_presente,
            }
            for p in result.parti
        ],
        'bom': [
            {
                'livello':    b.livello,
                'nome_parent': b.nome_parent,
                'nome_part':  b.nome_part,
                'codice_art': b.codice_art,
                'qty':        b.qty,
            }
            for b in result.bom
        ],
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "grader_box.stp"
    print(f"Analisi: {path}")
    result = analizza_step(path)
    print(f"Assembly: {result.assembly_nome}")
    print(f"Parti uniche: {result.n_parti_uniche}  |  Totali: {result.n_parti_totali}")
    print(f"Peso totale: {result.peso_totale_kg:.1f} kg")
    print(f"Superficie totale: {result.superficie_totale_m2:.3f} m²")
    print("\nBOM:")
    for b in result.bom:
        print(f"  [{b.livello}] {b.codice_art}  {b.nome_part:<30s}  qty={b.qty}  "
              f"kg={b.geom.peso_kg if b.geom else '?'}  m²={b.geom.superficie_m2 if b.geom else '?'}")
    if result.errori:
        print("\nERRORI:", result.errori)
    if result.warning:
        print("\nWARNING:", result.warning[:3])


# ═══════════════════════════════════════════════════════════════
# ANALISI STL — singolo file o ZIP con più STL
# ═══════════════════════════════════════════════════════════════

SCALA_STL = 1000.0   # cascadio/trimesh carica STEP-derived STL in METRI → mm

def _analizza_stl_file(path_stl: str, nome_override: str = None) -> Optional[PartGeometry]:
    """
    Carica un singolo file STL con trimesh ed estrae le proprietà geometriche.
    Gestisce auto-scaling da metri a mm (output di cascadio).
    """
    try:
        import trimesh
        mesh = trimesh.load(path_stl, force='mesh', process=True)
        if mesh is None or len(mesh.faces) == 0:
            return None

        nome = nome_override or os.path.splitext(os.path.basename(path_stl))[0]
        # Rimuovi prefisso numerico tipo "01_nome" → "nome"
        nome = re.sub(r'^\d+_', '', nome)

        bb = mesh.bounding_box.extents
        L, W, H = float(bb[0]), float(bb[1]), float(bb[2])

        # Auto-scala: se dimensioni < 5 → probabilmente in metri
        scala = 1.0
        if max(L, W, H) < 5.0:
            scala = SCALA_STL
            mesh = mesh.copy()
            mesh.apply_scale(scala)
            bb = mesh.bounding_box.extents
            L, W, H = float(bb[0]), float(bb[1]), float(bb[2])

        # Repair
        trimesh.repair.fix_normals(mesh)
        trimesh.repair.fill_holes(mesh)

        # Volume
        if mesh.is_watertight and abs(mesh.volume) > 0:
            vol_m3 = abs(float(mesh.volume)) / 1e9   # mm³ → m³
        else:
            vol_m3 = (L * W * H * 0.30) / 1e9       # stima 30% fill

        # Superficie
        sup_m2 = float(mesh.area) / 1e6              # mm² → m²
        if sup_m2 < 1e-4:
            sup_m2 = 2.0 * (L*W + L*H + W*H) / 1e6 * 1.4

        # CoG
        cog = mesh.center_mass if mesh.is_watertight else mesh.centroid
        cog_x, cog_y, cog_z = float(cog[0]), float(cog[1]), float(cog[2])

        peso_kg = vol_m3 * DENSITA_ACCIAIO_KG_M3

        dim_max_m = max(L, W, H) / 1000.0
        passo_m = round(max(0.4, min(math.ceil(dim_max_m * 1.2 * 20) / 20, 4.0)), 2)
        n_g = round(passo_m / 0.4)
        comp = 3 if n_g >= 4 else (2 if n_g >= 2 else 1)

        codice = genera_codice_art(nome, vol_m3, sup_m2, (L, W, H))

        return PartGeometry(
            nome=nome,
            codice_art=codice,
            volume_m3=round(vol_m3, 6),
            superficie_m2=round(sup_m2, 4),
            lunghezza_mm=round(L, 1),
            larghezza_mm=round(W, 1),
            altezza_mm=round(H, 1),
            peso_kg=round(peso_kg, 3),
            cog_x_mm=round(cog_x, 2),
            cog_y_mm=round(cog_y, 2),
            cog_z_mm=round(cog_z, 2),
            n_facce=len(mesh.faces),
            n_vertici=len(mesh.vertices),
            is_watertight=mesh.is_watertight,
            passo_gancio_m=passo_m,
            complessita_aggancio=comp,
            hash_geom=codice.split('-')[-1],
            mesh_presente=True,
        )
    except Exception as e:
        return None


def analizza_stl_singolo(path_stl: str, assembly_nome: str = None) -> ParseResult:
    """
    Analizza un singolo file STL.
    Crea un assembly con una sola parte.
    """
    nome_file = os.path.basename(path_stl)
    asm_nome = assembly_nome or os.path.splitext(nome_file)[0]

    result = ParseResult(
        file_nome=nome_file,
        assembly_nome=asm_nome,
        n_parti_uniche=0,
        n_parti_totali=0,
        peso_totale_kg=0.0,
        superficie_totale_m2=0.0,
    )

    geom = _analizza_stl_file(path_stl)
    if geom is None:
        result.errori.append(f"Impossibile caricare STL: {nome_file}")
        return result

    result.parti = [geom]
    result.bom = [BOMItem(
        livello=1,
        nome_parent=asm_nome,
        nome_part=geom.nome,
        codice_art=geom.codice_art,
        qty=1,
        geom=geom,
    )]
    result.n_parti_uniche = 1
    result.n_parti_totali = 1
    result.peso_totale_kg = round(geom.peso_kg, 2)
    result.superficie_totale_m2 = round(geom.superficie_m2, 4)
    return result


def analizza_zip_stl(path_zip: str) -> ParseResult:
    """
    Analizza un file ZIP contenente uno o più STL.
    Ogni STL = un componente della BOM.
    Gestisce:
      - ZIP flat (tutti STL in root)
      - ZIP con sottocartella
      - Nomi tipo "01_nome_pezzo.stl" → nome pulito "nome_pezzo"
      - Deduplicazione automatica per codice ART deterministico
        (stessa geometria → stesso codice → qty incrementata)
    """
    import zipfile, tempfile

    nome_zip = os.path.basename(path_zip)
    asm_nome = os.path.splitext(nome_zip)[0]

    result = ParseResult(
        file_nome=nome_zip,
        assembly_nome=asm_nome,
        n_parti_uniche=0,
        n_parti_totali=0,
        peso_totale_kg=0.0,
        superficie_totale_m2=0.0,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Estrai ZIP
        try:
            with zipfile.ZipFile(path_zip, 'r') as zf:
                stl_names = [n for n in zf.namelist()
                             if n.lower().endswith('.stl') and not n.startswith('__')]
                if not stl_names:
                    result.errori.append("Nessun file STL trovato nel ZIP.")
                    return result
                zf.extractall(tmpdir)
        except Exception as e:
            result.errori.append(f"Errore apertura ZIP: {e}")
            return result

        result.warning.append(f"ZIP: trovati {len(stl_names)} file STL")

        # Analizza ogni STL
        parti_uniche: Dict[str, PartGeometry] = {}   # codice → geom
        qty_counter:  Dict[str, int] = {}

        for stl_rel in sorted(stl_names):
            path_stl = os.path.join(tmpdir, stl_rel)
            nome_raw = os.path.splitext(os.path.basename(stl_rel))[0]
            # Pulisci prefisso numerico "01_nome" → "nome"
            nome = re.sub(r'^\d+_', '', nome_raw).replace('_', ' ').strip()

            geom = _analizza_stl_file(path_stl, nome_override=nome)
            if geom is None:
                result.warning.append(f"Skip (mesh vuota): {stl_rel}")
                continue

            if geom.codice_art in parti_uniche:
                # Stessa geometria → incrementa qty
                qty_counter[geom.codice_art] += 1
            else:
                parti_uniche[geom.codice_art] = geom
                qty_counter[geom.codice_art] = 1

        # Costruisci BOM
        bom: List[BOMItem] = []
        for codice, geom in parti_uniche.items():
            bom.append(BOMItem(
                livello=1,
                nome_parent=asm_nome,
                nome_part=geom.nome,
                codice_art=codice,
                qty=qty_counter[codice],
                geom=geom,
            ))

    n_tot = sum(b.qty for b in bom)
    peso_tot = sum(b.geom.peso_kg * b.qty for b in bom if b.geom)
    sup_tot  = sum(b.geom.superficie_m2 * b.qty for b in bom if b.geom)

    result.parti = list(parti_uniche.values())
    result.bom   = bom
    result.n_parti_uniche   = len(parti_uniche)
    result.n_parti_totali   = n_tot
    result.peso_totale_kg   = round(peso_tot, 2)
    result.superficie_totale_m2 = round(sup_tot, 4)
    return result


def analizza_file_automatico(path: str) -> ParseResult:
    """
    Router automatico: scegli il parser giusto in base all'estensione.
      .stp / .step → analizza_step()
      .stl          → analizza_stl_singolo()
      .zip          → analizza_zip_stl()
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.stp', '.step'):
        return analizza_step(path)
    elif ext == '.stl':
        return analizza_stl_singolo(path)
    elif ext == '.zip':
        return analizza_zip_stl(path)
    else:
        r = ParseResult(file_nome=os.path.basename(path),
                        assembly_nome='', n_parti_uniche=0,
                        n_parti_totali=0, peso_totale_kg=0,
                        superficie_totale_m2=0)
        r.errori.append(f"Formato non supportato: {ext}. Usa .stp, .step, .stl o .zip")
        return r
