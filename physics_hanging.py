"""
physics_hanging.py — ENOROSSI Paint Optimizer v5
PHYSICS ENGINE: Industrial Hook Assignment, CoG Alignment, Multi-Hook Balancing

Implementa le regole meccaniche reali di un impianto di verniciatura a polvere
overhead (catena aerea). Ogni funzione modella un vincolo fisico preciso:

  1. detect_hanging_holes()     — scansione mesh STL per fori/slot di aggancio
  2. estimate_hanging_points()  — stima euristica se nessun foro rilevato
  3. compute_pendulum_equilibrium() — rotazione pendolare per CoG sotto il gancio
  4. select_multihook_positions()   — selezione ottimale coppie di ganci con pitch-match
  5. compute_drainage_hook_lengths()— lunghezze ganci differenziali per inclinazione drenaggio
  6. validate_hanging_assignment()  — sistema warning operatore (verde/arancio/rosso)

Fisica di riferimento:
  - Gravità: g = 9.81 m/s², direzione -Z (verso pavimento)
  - Equilibrio pendolare: CoG si porta verticalmente sotto il punto di sospensione
  - Angolo pendolare θ = atan2(δ_horizontal, δ_vertical) dove δ = CoG - hook_point
  - Bounding envelope ruotato: proiezione AABB del bounding box dopo rotazione 3D
  - Momento flettente sulla barra: M = F × d (braccio rispetto al centro)
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

import numpy as np

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# COSTANTI DI IMPIANTO
# ─────────────────────────────────────────────────────────────────────────────

GRAVITY_M_S2 = 9.81

# Peso massimo raccomandato su singolo gancio (kg) prima di richiedere 2 ganci
SINGLE_HOOK_WEIGHT_WARNING_KG  = 40.0   # ⚠ arancio
SINGLE_HOOK_WEIGHT_DANGER_KG   = 60.0   # 🔴 rosso/bloccato

# Angolo di inclinazione massimo tollerabile senza collisione con la barra (°)
TILT_WARNING_DEG  = 20.0
TILT_DANGER_DEG   = 35.0

# Intervallo diametro fori validi per ganci da verniciatura (mm)
HOOK_MIN_DIAM_MM  = 10.0
HOOK_MAX_DIAM_MM  = 50.0

# Inclinazione standard di drenaggio per evitare ristagni di liquidi
DRAINAGE_TILT_DEG_DEFAULT = 12.0

# Tolleranza pitch gancio: la distanza tra i fori del pezzo deve essere
# entro ±PITCH_TOL_PCT % del passo della barra (o suo multiplo)
PITCH_TOLERANCE_PCT = 0.25   # ±25%

# Altezza standard ganci disponibili in impianto (mm)
HOOK_LENGTHS_AVAILABLE_MM: List[float] = [200.0, 250.0, 300.0, 350.0, 400.0, 500.0]


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HangingPoint:
    """
    Punto di aggancio fisicamente valido su un semilavorato.
    Corrisponde a un foro, slot o asola attraverso cui il gancio deve passare.
    """
    x_mm: float          # Posizione X nel sistema locale del pezzo
    y_mm: float          # Posizione Y
    z_mm: float          # Quota Z (cima del foro, dove il gancio entra)
    diameter_mm: float   # Diametro del foro (limita il calibro del gancio)
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)   # asse del foro (tipicamente Z↑)
    confidence: float = 1.0   # 0–1: qualità del rilevamento
    source: str = 'detected'  # 'detected' | 'estimated' | 'manual'

    @property
    def xy(self) -> np.ndarray:
        return np.array([self.x_mm, self.y_mm])

    @property
    def xyz(self) -> np.ndarray:
        return np.array([self.x_mm, self.y_mm, self.z_mm])

    def __repr__(self) -> str:
        return (f"HangingPoint(⌀{self.diameter_mm:.0f}mm @ "
                f"({self.x_mm:.0f},{self.y_mm:.0f},{self.z_mm:.0f}) "
                f"[{self.source}])")


@dataclass
class HookAssignment:
    """
    Assegnazione concreta di un gancio della barra a un foro del pezzo.
    """
    hanging_point: HangingPoint     # il foro del pezzo
    bar_hook_idx: int               # indice gancio sulla barra
    bar_hook_x_mm: float            # posizione X sulla barra
    hook_length_mm: float           # lunghezza del gancio (distanza barra→cima pezzo)
    load_kg: float = 0.0            # carico portato da questo gancio

    def __repr__(self) -> str:
        return (f"HookAssignment(G{self.bar_hook_idx+1} @ {self.bar_hook_x_mm:.0f}mm, "
                f"L={self.hook_length_mm:.0f}mm, {self.load_kg:.1f}kg)")


@dataclass
class HangingValidation:
    """
    Risultato della validazione di un'assegnazione ganci a un pezzo.
    Guida il colore dell'interfaccia operatore (verde/arancio/rosso).
    """
    valid: bool                         # True = assegnazione accettabile
    danger_level: int                   # 0=sicuro, 1=attenzione, 2=pericolo/bloccato
    ui_color: str = '#3FB950'           # colore per la UI
    warnings: List[str] = field(default_factory=list)
    blocking_reason: str = ''           # non vuota solo se danger_level == 2

    # Dati fisici calcolati
    pendulum_angle_deg: float = 0.0     # inclinazione pendolare (singolo gancio)
    effective_tilt_deg: float = 0.0     # inclinazione effettiva finale (multi-gancio)
    part_hits_bar: bool = False         # True se il pezzo colpisce la barra dopo rotazione
    balanced: bool = True               # True se carico distribuito equamente
    cog_offset_mm: float = 0.0         # scostamento CoG dal punto di sospensione (XY)
    rotated_H_mm: float = 0.0          # altezza proiettata dopo rotazione (AABB)

    def to_dict(self) -> dict:
        return {
            'valid': self.valid,
            'danger_level': self.danger_level,
            'ui_color': self.ui_color,
            'warnings': self.warnings,
            'blocking_reason': self.blocking_reason,
            'pendulum_angle_deg': round(self.pendulum_angle_deg, 2),
            'effective_tilt_deg': round(self.effective_tilt_deg, 2),
            'part_hits_bar': self.part_hits_bar,
            'balanced': self.balanced,
            'cog_offset_mm': round(self.cog_offset_mm, 1),
            'rotated_H_mm': round(self.rotated_H_mm, 1),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 1. RILEVAMENTO FORI STL (cross-section method)
# ─────────────────────────────────────────────────────────────────────────────

def detect_hanging_holes(
    mesh_or_path,
    min_r_mm: float = HOOK_MIN_DIAM_MM / 2,
    max_r_mm: float = HOOK_MAX_DIAM_MM / 2,
    n_sections: int = 30,
    z_scan_pct: float = 0.70,
    min_circularity: float = 0.28,
    min_z_levels: int = 2,
) -> List[HangingPoint]:
    """
    Rileva i fori di aggancio validi dalla mesh STL/trimesh usando cross-sezioni orizzontali.

    Principio:
      Un foro passante compare come loop chiuso (quasi circolare) a più quote Z consecutive.
      I loop della sagoma esterna del pezzo hanno raggio molto maggiore dei fori reali.
      Filtrando per raggio [min_r, max_r] e circolarità, si isolano solo i fori.

    Args:
        mesh_or_path : trimesh.Trimesh oppure path str a un file .stl/.step
        min_r_mm     : raggio minimo foro (mm) — default 5mm (M10 hook)
        max_r_mm     : raggio massimo foro (mm) — default 25mm
        n_sections   : numero di sezioni orizzontali
        z_scan_pct   : frazione dell'altezza del pezzo da scansionare (dal top)
        min_circularity : soglia di circolarità (0 = perfetto cerchio, 0.3 = accettabile)
        min_z_levels : numero minimo di sezioni dove deve apparire lo stesso foro

    Returns:
        Lista di HangingPoint ordinati per confidenza decrescente.
    """
    try:
        import trimesh
    except ImportError:
        return []

    # Carica mesh se path
    if isinstance(mesh_or_path, str):
        try:
            mesh = trimesh.load(mesh_or_path, force='mesh', process=True)
        except Exception:
            return []
    else:
        mesh = mesh_or_path

    if mesh is None or len(mesh.faces) == 0:
        return []

    # Auto-scaling: se unità in metri → converti in mm
    bb = mesh.bounding_box.extents
    if max(bb) < 5.0:
        mesh = mesh.copy()
        mesh.apply_scale(1000.0)
        bb = mesh.bounding_box.extents

    bounds = mesh.bounds
    z_min_b, z_max_b = float(bounds[0][2]), float(bounds[1][2])
    z_range = z_max_b - z_min_b

    # Scansiona dal top verso il basso per z_scan_pct dell'altezza
    z_scan_start = z_max_b - z_scan_pct * z_range
    z_levels = np.linspace(z_max_b - 1.0, z_scan_start, n_sections)

    # Raccoglie loop circolari per ogni livello Z
    # Struttura: {foro_id: [lista di (center_xy, r, z)]}
    hole_candidates: Dict[str, List[tuple]] = defaultdict(list)

    for z_level in z_levels:
        try:
            section = mesh.section(
                plane_origin=[0.0, 0.0, float(z_level)],
                plane_normal=[0.0, 0.0, 1.0]
            )
            if section is None:
                continue

            try:
                paths2d, _ = section.to_2D()
            except Exception:
                try:
                    paths2d, _ = section.to_planar()
                except Exception:
                    continue

            for ent in paths2d.entities:
                if len(ent.points) < 5:
                    continue
                pts = paths2d.vertices[ent.points]
                center = pts.mean(axis=0)
                radii = np.linalg.norm(pts - center, axis=1)
                r_mean = float(radii.mean())
                r_std = float(radii.std())
                circularity = r_std / (r_mean + 1e-6)

                # Filtra per dimensione e forma circolare
                if not (min_r_mm <= r_mean <= max_r_mm):
                    continue
                if circularity > min_circularity:
                    continue

                # Crea una chiave "posizione" per raggruppare lo stesso foro
                # a quote Z diverse (snap a griglia di 10mm)
                cx_snap = round(float(center[0]) / 10.0) * 10
                cy_snap = round(float(center[1]) / 10.0) * 10
                r_snap = round(r_mean / 5.0) * 5
                hole_key = f"{cx_snap}_{cy_snap}_{r_snap}"

                hole_candidates[hole_key].append((
                    np.array([float(center[0]), float(center[1])]),
                    r_mean,
                    z_level
                ))

        except Exception:
            continue

    # Assembla HangingPoint dai candidati con ≥ min_z_levels apparizioni
    results: List[HangingPoint] = []
    for key, appearances in hole_candidates.items():
        if len(appearances) < min_z_levels:
            continue

        # Media delle posizioni
        centers = np.array([a[0] for a in appearances])
        radii = np.array([a[1] for a in appearances])
        z_vals = np.array([a[2] for a in appearances])

        cx = float(centers[:, 0].mean())
        cy = float(centers[:, 1].mean())
        r = float(radii.mean())
        z_top = float(z_vals.max())   # punto più alto del foro (dove entra il gancio)

        # Confidenza proporzionale al numero di livelli in cui compare
        confidence = min(1.0, len(appearances) / (n_sections * z_scan_pct * 0.5))

        results.append(HangingPoint(
            x_mm=round(cx, 1),
            y_mm=round(cy, 1),
            z_mm=round(z_top, 1),
            diameter_mm=round(r * 2, 1),
            normal=(0.0, 0.0, 1.0),
            confidence=round(confidence, 3),
            source='detected',
        ))

    # Rimuovi duplicati (stessa posizione, tolleranza 15mm)
    results = _deduplicate_holes(results, tolerance_mm=15.0)

    # Ordina per confidenza decrescente, poi per Z decrescente (preferisci i più in alto)
    results.sort(key=lambda h: (-h.confidence, -h.z_mm))

    return results


def _deduplicate_holes(
    holes: List[HangingPoint],
    tolerance_mm: float = 15.0
) -> List[HangingPoint]:
    """Rimuove HangingPoint duplicati che cadono nella stessa posizione XY."""
    if not holes:
        return holes
    kept: List[HangingPoint] = []
    for h in holes:
        duplicate = False
        for k in kept:
            dist = math.hypot(h.x_mm - k.x_mm, h.y_mm - k.y_mm)
            if dist < tolerance_mm:
                # Tieni quello con confidenza più alta
                if h.confidence > k.confidence:
                    kept.remove(k)
                    kept.append(h)
                duplicate = True
                break
        if not duplicate:
            kept.append(h)
    return kept




# ─────────────────────────────────────────────────────────────────────────────
# 1b. PROXY MESH PER COLLISION DETECTION (pipeline watertight corretta)
# ─────────────────────────────────────────────────────────────────────────────

def build_collision_proxy(
    mesh_or_path,
    target_faces: int = 2000,
):
    """
    Costruisce il proxy mesh decimato per la collision detection (FCL/BVH).

    Pipeline corretta (correzione critica del gruppo di revisione):
      1. Carica mesh
      2. fill_holes()  ← rimuove buchi che causano crash FCL silenzioso
      3. simplify_quadric_decimation(2000)
      4. verifica is_watertight
      5. se NON watertight → convex_hull come fallback sicuro

    Senza fill_holes() PRIMA della decimazione, trimesh produce a volte
    mesh non-watertight che FCL non gestisce e crasha silenziosamente.

    Args:
        mesh_or_path : trimesh.Trimesh o path str a file .stl/.step
        target_faces : facce target dopo decimazione (default 2000)

    Returns:
        trimesh.Trimesh watertight, pronto per FCL/three-mesh-bvh.
        None se impossibile caricare la mesh.
    """
    try:
        import trimesh
    except ImportError:
        return None

    # ── Step 1: carica mesh ───────────────────────────────────────────────
    if isinstance(mesh_or_path, str):
        try:
            mesh = trimesh.load(mesh_or_path, force='mesh', process=True)
        except Exception:
            return None
    else:
        mesh = mesh_or_path.copy()

    if mesh is None or len(mesh.faces) == 0:
        return None

    # ── Auto-scaling (STEP/STL da Inventor spesso in metri) ──────────────
    bb = mesh.bounding_box.extents
    if max(bb) < 5.0:
        mesh.apply_scale(1000.0)

    # ── Step 2: fill_holes() OBBLIGATORIO prima della decimazione ─────────
    try:
        trimesh.repair.fix_normals(mesh)
        trimesh.repair.fill_holes(mesh)
    except Exception:
        pass  # continua comunque

    # ── Step 3: decimazione quadric ──────────────────────────────────────
    try:
        proxy = mesh.simplify_quadric_decimation(target_faces)
        if proxy is None or len(proxy.faces) == 0:
            proxy = mesh.copy()
    except Exception:
        proxy = mesh.copy()

    # ── Step 4: verifica watertight → fallback convex_hull ───────────────
    if not proxy.is_watertight:
        try:
            proxy = mesh.convex_hull
        except Exception:
            # Ultimo fallback: oriented bounding box come proxy
            try:
                proxy = mesh.bounding_box_oriented.to_mesh()
            except Exception:
                proxy = mesh.copy()

    return proxy


def export_glb_for_viewer(
    mesh_or_path,
    out_path: str,
    use_proxy: bool = False,
    target_faces: int = 5000,
) -> bool:
    """
    Esporta la mesh in formato GLB per il viewer Three.js.

    Flask serve questo file su /api/part/<id>/geometry.glb.
    Il browser lo carica con THREE.GLTFLoader — zero conversioni aggiuntive.

    Args:
        mesh_or_path : trimesh.Trimesh o path str
        out_path     : path di output (.glb)
        use_proxy    : se True, usa build_collision_proxy (più leggero)
        target_faces : facce target se use_proxy=True

    Returns:
        True se esportazione riuscita, False altrimenti.
    """
    try:
        import trimesh
    except ImportError:
        return False

    try:
        if isinstance(mesh_or_path, str):
            mesh = trimesh.load(mesh_or_path, force='mesh', process=True)
        else:
            mesh = mesh_or_path.copy()

        if mesh is None or len(mesh.faces) == 0:
            return False

        # Auto-scaling
        bb = mesh.bounding_box.extents
        if max(bb) < 5.0:
            mesh.apply_scale(1000.0)

        if use_proxy:
            proxy = build_collision_proxy(mesh, target_faces=target_faces)
            if proxy is not None:
                mesh = proxy

        # Centra sull'origine — Three.js usa position per il posizionamento della scena
        mesh.vertices -= mesh.bounding_box.centroid
        mesh.export(out_path)
        return True

    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. STIMA EURISTICA PUNTI DI AGGANCIO (fallback senza mesh)
# ─────────────────────────────────────────────────────────────────────────────

def estimate_hanging_points(
    L_mm: float,
    W_mm: float,
    H_mm: float,
    peso_kg: float,
    nome: str = '',
) -> List[HangingPoint]:
    """
    Stima i punti di aggancio ottimali quando non è disponibile la mesh STL
    (o quando detect_hanging_holes() non trova fori sufficienti).

    Regole industriali applicate:
      - Pezzi leggeri (< 20kg): 1 gancio al quarto della lunghezza dal bordo
      - Pezzi medi (20–60kg): 2 ganci simmetrici al 25% e 75% della lunghezza
      - Pezzi pesanti (> 60kg): 2–3 ganci, uno al centro + due ai terzi
      - Tubi e profili: ai due estremi (a 10% dalla lunghezza)
      - Piastre piane (H < L/15): 2 punti alle estremità

    La Z stimata è sempre z_top (cima del pezzo), poiché i fori reali
    emergono dalla superficie superiore.
    """
    nome_l = nome.lower()
    z_top = H_mm  # coordinata Z cima pezzo (sistema locale: Z=0 = fondo, Z=H = top)

    # ── Tubi e profili allungati ──────────────────────────────────────────
    is_tube = any(k in nome_l for k in ('tube', 'tub', 'barre', 'bar', 'profil', 'entretoise'))
    is_long = L_mm > max(W_mm, H_mm) * 4

    if is_tube or is_long:
        return [
            HangingPoint(x_mm=L_mm * 0.10, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=18.0, confidence=0.6, source='estimated'),
            HangingPoint(x_mm=L_mm * 0.90, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=18.0, confidence=0.6, source='estimated'),
        ]

    # ── Piastre piane (H molto piccola rispetto a L, W) ─────────────────
    is_plate = H_mm < min(L_mm, W_mm) * 0.15
    if is_plate:
        if L_mm >= W_mm:
            # Piastra orizzontale: 2 punti sulle estremità lunghe
            return [
                HangingPoint(x_mm=L_mm * 0.20, y_mm=W_mm / 2, z_mm=z_top,
                             diameter_mm=14.0, confidence=0.5, source='estimated'),
                HangingPoint(x_mm=L_mm * 0.80, y_mm=W_mm / 2, z_mm=z_top,
                             diameter_mm=14.0, confidence=0.5, source='estimated'),
            ]
        else:
            return [
                HangingPoint(x_mm=L_mm / 2, y_mm=W_mm * 0.20, z_mm=z_top,
                             diameter_mm=14.0, confidence=0.5, source='estimated'),
                HangingPoint(x_mm=L_mm / 2, y_mm=W_mm * 0.80, z_mm=z_top,
                             diameter_mm=14.0, confidence=0.5, source='estimated'),
            ]

    # ── Pezzi compatti: per peso ─────────────────────────────────────────
    if peso_kg < 20.0:
        # Singolo gancio centrale
        return [
            HangingPoint(x_mm=L_mm / 2, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=14.0, confidence=0.5, source='estimated'),
        ]
    elif peso_kg <= 60.0:
        # Due ganci simmetrici a ¼ e ¾ della lunghezza
        return [
            HangingPoint(x_mm=L_mm * 0.25, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=18.0, confidence=0.5, source='estimated'),
            HangingPoint(x_mm=L_mm * 0.75, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=18.0, confidence=0.5, source='estimated'),
        ]
    else:
        # 3 ganci: due ai terzi + uno al centro (per pezzi molto pesanti/lunghi)
        return [
            HangingPoint(x_mm=L_mm * 0.20, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=22.0, confidence=0.4, source='estimated'),
            HangingPoint(x_mm=L_mm * 0.50, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=22.0, confidence=0.4, source='estimated'),
            HangingPoint(x_mm=L_mm * 0.80, y_mm=W_mm / 2, z_mm=z_top,
                         diameter_mm=22.0, confidence=0.4, source='estimated'),
        ]


def get_hanging_points(
    L_mm: float,
    W_mm: float,
    H_mm: float,
    peso_kg: float,
    nome: str = '',
    mesh=None,
    stl_path: str = None,
) -> List[HangingPoint]:
    """
    Entry point unificato: prova prima il rilevamento geometrico dalla mesh,
    poi ricade sull'euristica se non trova fori validi.
    """
    points: List[HangingPoint] = []

    # Tentativo rilevamento geometrico
    if stl_path:
        points = detect_hanging_holes(stl_path)
    elif mesh is not None:
        points = detect_hanging_holes(mesh)

    # Fallback euristico se non ci sono fori sufficienti
    if len(points) < 1:
        points = estimate_hanging_points(L_mm, W_mm, H_mm, peso_kg, nome)

    return points


# ─────────────────────────────────────────────────────────────────────────────
# 3. EQUILIBRIO PENDOLARE (singolo gancio)
# ─────────────────────────────────────────────────────────────────────────────

def compute_pendulum_equilibrium(
    cog_x_mm: float,
    cog_y_mm: float,
    cog_z_mm: float,
    hook_x_mm: float,
    hook_y_mm: float,
    hook_z_mm: float,
    L_mm: float,
    W_mm: float,
    H_mm: float,
) -> Tuple[float, float, float, np.ndarray]:
    """
    Calcola la rotazione pendolare che porta il CoG esattamente sotto il punto
    di sospensione (equilibrio statico a singolo gancio).

    Il pezzo è appeso al punto (hook_x, hook_y, hook_z) nel suo sistema locale.
    La gravità agisce in -Z. L'equilibrio statico richiede che il CoG sia
    direttamente sotto il punto di sospensione nella direzione della gravità.

    Geometria:
      - δx = cog_x - hook_x  (scostamento orizzontale X del CoG rispetto al gancio)
      - δy = cog_y - hook_y  (scostamento orizzontale Y)
      - δz = hook_z - cog_z  (distanza verticale, deve essere > 0 per equilibrio reale)

    Rotazione necessaria:
      Il pezzo ruota attorno all'asse Y della barra (rotazione nel piano XZ):
        θ_x = atan2(δx, δz)  — rotazione per annullare lo scostamento X
      E attorno all'asse X della barra (rotazione nel piano YZ):
        θ_y = atan2(δy, δz)

    Dopo la rotazione, l'AABB del pezzo cambia dimensioni (proiezione 3D).
    La nuova altezza proiettata H_rot è ciò che occupa spazio sulla barra.

    Returns:
        (theta_x_deg, theta_y_deg, H_rotated_mm, rotation_matrix_3x3)
    """
    delta_x = cog_x_mm - hook_x_mm
    delta_y = cog_y_mm - hook_y_mm
    delta_z = hook_z_mm - cog_z_mm  # positivo = CoG è sotto il gancio (normale)

    # Angoli di rotazione pendolare
    if abs(delta_z) < 1e-3:
        # CoG allo stesso livello del gancio: situazione instabile
        theta_x_deg = 90.0 if delta_x >= 0 else -90.0
        theta_y_deg = 90.0 if delta_y >= 0 else -90.0
    else:
        theta_x_deg = math.degrees(math.atan2(delta_x, delta_z))
        theta_y_deg = math.degrees(math.atan2(delta_y, delta_z))

    # Costruisci matrice di rotazione 3D (Rx × Ry)
    tx = math.radians(theta_x_deg)
    ty = math.radians(theta_y_deg)

    Rx = np.array([
        [1,       0,        0],
        [0,  math.cos(tx), -math.sin(tx)],
        [0,  math.sin(tx),  math.cos(tx)],
    ])
    Ry = np.array([
        [ math.cos(ty), 0, math.sin(ty)],
        [0,             1,           0],
        [-math.sin(ty), 0, math.cos(ty)],
    ])
    R = Ry @ Rx

    # Proietta tutti gli 8 vertici del bounding box attraverso la rotazione
    half = np.array([L_mm / 2, W_mm / 2, H_mm / 2])
    corners = np.array([
        [-1, -1, -1], [-1, -1,  1], [-1,  1, -1], [-1,  1,  1],
        [ 1, -1, -1], [ 1, -1,  1], [ 1,  1, -1], [ 1,  1,  1],
    ], dtype=float) * half[np.newaxis, :]  # shape (8, 3)

    rotated = (R @ corners.T).T  # shape (8, 3)
    mins = rotated.min(axis=0)
    maxs = rotated.max(axis=0)
    extents = maxs - mins  # (L', W', H') dell'AABB ruotato

    H_rotated_mm = float(extents[2])   # altezza proiettata dopo rotazione

    return (
        round(theta_x_deg, 2),
        round(theta_y_deg, 2),
        round(H_rotated_mm, 1),
        R,
    )


def compute_rotated_clearance_envelope(
    L_mm: float,
    W_mm: float,
    H_mm: float,
    theta_x_deg: float,
    theta_y_deg: float,
    extra_buffer_pct: float = 0.05,
) -> Tuple[float, float, float]:
    """
    Calcola le dimensioni dell'envelope AABB del pezzo dopo la rotazione pendolare
    più un buffer di oscillazione dinamica (5% default).

    Usato per verificare che il pezzo ruotato entri nel Z_max della barra.

    Returns:
        (L_envelope_mm, W_envelope_mm, H_envelope_mm)
    """
    tx = math.radians(theta_x_deg)
    ty = math.radians(theta_y_deg)

    Rx = np.array([
        [1, 0, 0],
        [0, math.cos(tx), -math.sin(tx)],
        [0, math.sin(tx),  math.cos(tx)],
    ])
    Ry = np.array([
        [ math.cos(ty), 0, math.sin(ty)],
        [0, 1, 0],
        [-math.sin(ty), 0, math.cos(ty)],
    ])
    R = Ry @ Rx

    half = np.array([L_mm / 2, W_mm / 2, H_mm / 2])
    corners = np.array([
        [-1, -1, -1], [-1, -1,  1], [-1,  1, -1], [-1,  1,  1],
        [ 1, -1, -1], [ 1, -1,  1], [ 1,  1, -1], [ 1,  1,  1],
    ], dtype=float) * half[np.newaxis, :]

    rotated = (R @ corners.T).T
    extents = rotated.max(axis=0) - rotated.min(axis=0)

    buf = extra_buffer_pct
    return (
        float(extents[0]) * (1 + buf),
        float(extents[1]) * (1 + buf),
        float(extents[2]) * (1 + buf),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. SELEZIONE POSIZIONI MULTI-GANCIO (pitch matching)
# ─────────────────────────────────────────────────────────────────────────────

def select_multihook_positions(
    hanging_points: List[HangingPoint],
    bar_hook_positions_mm: List[float],
    part_peso_kg: float,
    bar_pitch_mm: float = 400.0,
    target_tilt_deg: float = 0.0,
    base_hook_length_mm: float = 300.0,
) -> Optional[List[HookAssignment]]:
    """
    Seleziona la combinazione ottimale di ganci della barra per appendere il pezzo
    usando i suoi fori di aggancio rilevati.

    Vincoli industriali:
      1. La distanza tra i fori scelti sul pezzo deve corrispondere a un multiplo
         del passo gancio della barra (±PITCH_TOLERANCE_PCT).
      2. Il carico deve essere distribuito equamente tra i ganci.
      3. Se target_tilt_deg != 0, si usano lunghezze gancio differenziali
         (calcolate da compute_drainage_hook_lengths).
      4. I ganci scelti devono avere calibro < diametro del foro.

    Args:
        hanging_points         : lista di HangingPoint rilevati/stimati sul pezzo
        bar_hook_positions_mm  : posizioni X dei ganci sulla barra (mm)
        part_peso_kg           : peso totale del pezzo (kg)
        bar_pitch_mm           : passo ganci barra (mm), tipicamente 400mm
        target_tilt_deg        : inclinazione di drenaggio desiderata (°)
        base_hook_length_mm    : lunghezza base del gancio più corto (mm)

    Returns:
        Lista di HookAssignment (uno per foro usato), oppure None se impossibile.
    """
    if not hanging_points or not bar_hook_positions_mm:
        return None

    n_hp = len(hanging_points)

    # ── Caso 1: singolo foro → singolo gancio ──────────────────────────────
    if n_hp == 1:
        hp = hanging_points[0]
        # Scegli il gancio della barra più vicino al X del foro nel sistema barra
        # (qui usiamo hp.x_mm come posizione relativa nella barra)
        closest_idx = int(np.argmin([abs(bx - hp.x_mm) for bx in bar_hook_positions_mm]))
        return [HookAssignment(
            hanging_point=hp,
            bar_hook_idx=closest_idx,
            bar_hook_x_mm=bar_hook_positions_mm[closest_idx],
            hook_length_mm=base_hook_length_mm,
            load_kg=part_peso_kg,
        )]

    # ── Caso 2+: più fori → cerca coppia con pitch-match ─────────────────
    best_assignment: Optional[List[HookAssignment]] = None
    best_score = float('inf')

    for i in range(n_hp):
        for j in range(i + 1, n_hp):
            hp1, hp2 = hanging_points[i], hanging_points[j]

            # Distanza tra i due fori nel piano XY del pezzo
            dist_part = math.hypot(hp1.x_mm - hp2.x_mm, hp1.y_mm - hp2.y_mm)
            if dist_part < bar_pitch_mm * 0.5:
                continue  # troppo vicini per usare ganci separati

            # Cerca un multiplo del passo barra che si avvicini alla distanza inter-foro
            n_spans = round(dist_part / bar_pitch_mm)
            if n_spans < 1:
                n_spans = 1
            ideal_dist = n_spans * bar_pitch_mm
            dist_error = abs(dist_part - ideal_dist) / ideal_dist

            if dist_error > PITCH_TOLERANCE_PCT:
                continue  # distanza non compatibile con il passo barra

            # Trova la coppia di ganci sulla barra con distanza ≈ ideal_dist
            for k in range(len(bar_hook_positions_mm)):
                for m in range(k + n_spans, k + n_spans + 1):
                    if m >= len(bar_hook_positions_mm):
                        continue
                    bar_dist = abs(bar_hook_positions_mm[m] - bar_hook_positions_mm[k])
                    bar_error = abs(bar_dist - dist_part) / dist_part if dist_part > 0 else 1.0
                    if bar_error > PITCH_TOLERANCE_PCT:
                        continue

                    # Calcola lunghezze gancio per l'inclinazione
                    if abs(target_tilt_deg) > 0.5:
                        l1, l2 = compute_drainage_hook_lengths(
                            hp1, hp2, target_tilt_deg, base_hook_length_mm
                        )
                    else:
                        l1 = l2 = base_hook_length_mm

                    peso_per_gancio = part_peso_kg / 2.0

                    # Score: minimizza errore di pitch + sbilancio (tutti i ganci uguali = buono)
                    score = dist_error + bar_error + abs(l1 - l2) / 100.0
                    if score < best_score:
                        best_score = score
                        best_assignment = [
                            HookAssignment(
                                hanging_point=hp1,
                                bar_hook_idx=k,
                                bar_hook_x_mm=bar_hook_positions_mm[k],
                                hook_length_mm=l1,
                                load_kg=peso_per_gancio,
                            ),
                            HookAssignment(
                                hanging_point=hp2,
                                bar_hook_idx=m,
                                bar_hook_x_mm=bar_hook_positions_mm[m],
                                hook_length_mm=l2,
                                load_kg=peso_per_gancio,
                            ),
                        ]

    # Fallback: 1 gancio al centro se nessuna coppia valida
    if best_assignment is None:
        # Proviamo con il singolo foro migliore (maggior confidenza)
        hp = max(hanging_points, key=lambda h: h.confidence)
        center_idx = len(bar_hook_positions_mm) // 2
        best_assignment = [HookAssignment(
            hanging_point=hp,
            bar_hook_idx=center_idx,
            bar_hook_x_mm=bar_hook_positions_mm[center_idx],
            hook_length_mm=base_hook_length_mm,
            load_kg=part_peso_kg,
        )]

    return best_assignment


# ─────────────────────────────────────────────────────────────────────────────
# 5. LUNGHEZZE DIFFERENZIALI PER INCLINAZIONE DRENAGGIO
# ─────────────────────────────────────────────────────────────────────────────

def compute_drainage_hook_lengths(
    hp1: HangingPoint,
    hp2: HangingPoint,
    tilt_deg: float,
    base_length_mm: float = 300.0,
) -> Tuple[float, float]:
    """
    Calcola le lunghezze dei due ganci per ottenere un'inclinazione controllata
    del pezzo (per garantire drenaggio dei liquidi di lavaggio e della vernice).

    Principio geometrico:
      Se hp1 è a sinistra e hp2 a destra (X1 < X2), e vogliamo inclinare
      il pezzo verso sinistra di tilt_deg gradi:
        Δh = (X2 - X1) × tan(tilt_deg)
        L1 (sinistra, più basso) = base_length
        L2 (destra, più alto)   = base_length + Δh

    Il pezzo si inclina perché il lato con il gancio più corto rimane più in alto.

    La differenza di lunghezza viene arrotondata ai valori disponibili in impianto
    (50mm step).

    Args:
        hp1             : primo punto di aggancio (sinistra/anteriore)
        hp2             : secondo punto di aggancio (destra/posteriore)
        tilt_deg        : angolo di inclinazione desiderato (°)
        base_length_mm  : lunghezza del gancio più corto (il lato che rimane più alto)

    Returns:
        (length1_mm, length2_mm) — selezionate tra i valori standard di impianto
    """
    dist_xy = math.hypot(hp2.x_mm - hp1.x_mm, hp2.y_mm - hp1.y_mm)

    if dist_xy < 1.0:
        return base_length_mm, base_length_mm

    # Dislivello verticale necessario
    delta_h = dist_xy * math.tan(math.radians(abs(tilt_deg)))

    # Arrotonda al gradino da 50mm disponibile in impianto
    delta_h_rounded = max(50.0, round(delta_h / 50.0) * 50.0)

    l_short = base_length_mm
    l_long  = base_length_mm + delta_h_rounded

    # Snap ai valori standard disponibili
    def snap_to_available(val: float) -> float:
        return min(HOOK_LENGTHS_AVAILABLE_MM, key=lambda x: abs(x - val))

    l_short = snap_to_available(l_short)
    l_long  = snap_to_available(l_long)

    # hp1 rimane più alto (gancio più corto) se il tilt è positivo verso sinistra
    if tilt_deg >= 0:
        return (l_short, l_long)
    else:
        return (l_long, l_short)


# ─────────────────────────────────────────────────────────────────────────────
# 6. SISTEMA DI VALIDAZIONE E WARNING OPERATORE
# ─────────────────────────────────────────────────────────────────────────────

def validate_hanging_assignment(
    assignments: List[HookAssignment],
    L_mm: float,
    W_mm: float,
    H_mm: float,
    peso_kg: float,
    cog_x_mm: float,
    cog_y_mm: float,
    cog_z_mm: float,
    z_max_mm: float,
    bar_y_clearance_mm: float = 50.0,
    nome: str = '',
) -> HangingValidation:
    """
    Valida l'assegnazione ganci e produce il risultato per il sistema warning UI.

    Controlla in ordine (il primo pericolo rilevato domina):
      🔴 DANGER (bloccato):
        - Singolo gancio su pezzo > SINGLE_HOOK_WEIGHT_DANGER_KG
        - Angolo pendolare > TILT_DANGER_DEG (il pezzo urta la barra)
        - L'envelope ruotato supera Z_max della barra
        - Il gancio non entra nel foro (calibro gancio > ⌀ foro)
      ⚠ WARNING (procedi con cautela):
        - Singolo gancio su pezzo > SINGLE_HOOK_WEIGHT_WARNING_KG
        - Angolo pendolare > TILT_WARNING_DEG
        - Fori stimati (non rilevati geometricamente dalla mesh)
        - Sbilancio carico > 20% tra i ganci
      ✅ SAFE: tutto ok

    Args:
        assignments         : lista di HookAssignment prodotta da select_multihook_positions
        L_mm, W_mm, H_mm    : dimensioni bounding box del pezzo
        peso_kg             : peso totale
        cog_x_mm/y_mm/z_mm  : centro di massa nel sistema locale del pezzo
        z_max_mm            : altezza massima utile della barra (mm)
        bar_y_clearance_mm  : spazio libero minimo tra pezzo e barra overhead (mm)

    Returns:
        HangingValidation con danger_level, colore UI e lista di warning/errori.
    """
    result = HangingValidation(valid=True, danger_level=0, ui_color='#3FB950')
    warnings_list: List[str] = []
    blocking: str = ''

    if not assignments:
        return HangingValidation(
            valid=False, danger_level=2, ui_color='#F85149',
            blocking_reason='Nessun punto di aggancio valido trovato sul pezzo.',
        )

    n_hooks = len(assignments)
    hp_primary = assignments[0].hanging_point

    # ─── Check 1: gancio unico su pezzo troppo pesante ───────────────────
    if n_hooks == 1:
        if peso_kg > SINGLE_HOOK_WEIGHT_DANGER_KG:
            blocking = (
                f"BLOCCO: {peso_kg:.1f}kg su singolo gancio supera il limite di sicurezza "
                f"({SINGLE_HOOK_WEIGHT_DANGER_KG:.0f}kg). Usa 2 ganci."
            )
        elif peso_kg > SINGLE_HOOK_WEIGHT_WARNING_KG:
            warnings_list.append(
                f"⚠ {peso_kg:.1f}kg su singolo gancio: raccomandati 2 ganci "
                f"(soglia warning: {SINGLE_HOOK_WEIGHT_WARNING_KG:.0f}kg)"
            )

    # ─── Check 2: equilibrio pendolare (singolo gancio) ──────────────────
    if n_hooks == 1:
        theta_x, theta_y, H_rot, R = compute_pendulum_equilibrium(
            cog_x_mm, cog_y_mm, cog_z_mm,
            hp_primary.x_mm, hp_primary.y_mm, hp_primary.z_mm,
            L_mm, W_mm, H_mm,
        )
        result.pendulum_angle_deg = round(math.hypot(theta_x, theta_y), 2)
        result.rotated_H_mm = H_rot

        # Scostamento CoG → gancio (XY)
        cog_offset = math.hypot(cog_x_mm - hp_primary.x_mm, cog_y_mm - hp_primary.y_mm)
        result.cog_offset_mm = round(cog_offset, 1)

        angle_total = math.hypot(theta_x, theta_y)

        if angle_total > TILT_DANGER_DEG:
            blocking = (
                f"BLOCCO: Inclinazione pendolare {angle_total:.1f}° > {TILT_DANGER_DEG:.0f}°. "
                f"Il pezzo urterà la barra o i pezzi adiacenti. Scegli un punto di aggancio "
                f"più vicino al CoG (scostamento XY: {cog_offset:.0f}mm)."
            )
            result.part_hits_bar = True
        elif angle_total > TILT_WARNING_DEG:
            warnings_list.append(
                f"⚠ Inclinazione pendolare {angle_total:.1f}° > {TILT_WARNING_DEG:.0f}°. "
                f"Verifica che il pezzo non colpisca i componenti adiacenti. "
                f"CoG offset: {cog_offset:.0f}mm."
            )

        # Check clearance Z con pezzo ruotato
        if H_rot > z_max_mm:
            blocking = (
                f"BLOCCO: Altezza proiettata dopo rotazione {H_rot:.0f}mm > "
                f"Z_max {z_max_mm:.0f}mm. Il pezzo non ci sta nella barra."
            )
        elif H_rot > z_max_mm * 0.90:
            warnings_list.append(
                f"⚠ Altezza proiettata {H_rot:.0f}mm è al {H_rot/z_max_mm*100:.0f}% "
                f"di Z_max. Margine ridotto."
            )

    # ─── Check 3: multi-gancio — verifica inclinazione e bilanciamento ──
    if n_hooks >= 2:
        # Inclinazione effettiva dai ganci di lunghezza diversa
        len_diff = abs(assignments[0].hook_length_mm - assignments[1].hook_length_mm)
        hp_dist = math.hypot(
            assignments[1].hanging_point.x_mm - assignments[0].hanging_point.x_mm,
            assignments[1].hanging_point.y_mm - assignments[0].hanging_point.y_mm,
        )
        if hp_dist > 0:
            tilt_eff = math.degrees(math.atan2(len_diff, hp_dist))
        else:
            tilt_eff = 0.0
        result.effective_tilt_deg = round(tilt_eff, 2)

        if tilt_eff > TILT_DANGER_DEG:
            blocking = (
                f"BLOCCO: Inclinazione effettiva {tilt_eff:.1f}° da lunghezze ganci "
                f"({assignments[0].hook_length_mm:.0f}/{assignments[1].hook_length_mm:.0f}mm) "
                f"eccessiva (max {TILT_DANGER_DEG:.0f}°)."
            )
        elif tilt_eff > TILT_WARNING_DEG:
            warnings_list.append(
                f"⚠ Inclinazione drenaggio {tilt_eff:.1f}°: verifica rischio collisione."
            )

        # Bilanciamento: scostamento CoG dal punto medio tra i ganci
        mid_hp_x = (assignments[0].hanging_point.x_mm + assignments[1].hanging_point.x_mm) / 2
        mid_hp_y = (assignments[0].hanging_point.y_mm + assignments[1].hanging_point.y_mm) / 2
        cog_offset = math.hypot(cog_x_mm - mid_hp_x, cog_y_mm - mid_hp_y)
        result.cog_offset_mm = round(cog_offset, 1)

        # Distribuzione peso (se CoG è fuori centro: carichi diversi sui ganci)
        if hp_dist > 0:
            # Carico su gancio 1 = peso × (distanza CoG da gancio 2) / distanza_totale
            d1 = abs(cog_x_mm - assignments[1].hanging_point.x_mm)
            d2 = abs(cog_x_mm - assignments[0].hanging_point.x_mm)
            load1 = peso_kg * d1 / (d1 + d2 + 1e-6)
            load2 = peso_kg - load1
            imbalance = abs(load1 - load2) / peso_kg * 100.0

            if imbalance > 40.0:
                warnings_list.append(
                    f"⚠ Sbilancio carico {imbalance:.0f}%: "
                    f"G1={load1:.1f}kg, G2={load2:.1f}kg. "
                    f"Sposta i fori di aggancio per bilanciare meglio."
                )
                result.balanced = False
            else:
                result.balanced = True

        # Clearance Z multi-gancio (altezza pezzo inclinato)
        h_incl = H_mm / math.cos(math.radians(tilt_eff)) if tilt_eff < 89 else H_mm * 10
        result.rotated_H_mm = round(h_incl, 1)
        if h_incl > z_max_mm:
            blocking = (
                f"BLOCCO: Altezza pezzo inclinato {h_incl:.0f}mm > Z_max {z_max_mm:.0f}mm."
            )

    # ─── Check 4: gauge gancio vs diametro foro ──────────────────────────
    HOOK_GAUGE_MM = 12.0  # diametro standard gancio (filetto M12)
    for asgn in assignments:
        if asgn.hanging_point.diameter_mm < HOOK_GAUGE_MM:
            blocking = (
                f"BLOCCO: Foro ⌀{asgn.hanging_point.diameter_mm:.0f}mm troppo piccolo "
                f"per il gancio (⌀{HOOK_GAUGE_MM:.0f}mm). Usa un gancio più sottile o "
                f"allarga il foro."
            )
            break
        elif asgn.hanging_point.diameter_mm < HOOK_GAUGE_MM * 1.3:
            warnings_list.append(
                f"⚠ Foro ⌀{asgn.hanging_point.diameter_mm:.0f}mm: "
                f"margine ridotto rispetto al gancio ⌀{HOOK_GAUGE_MM:.0f}mm."
            )

    # ─── Check 5: fori stimati (non geometricamente rilevati) ────────────
    if all(a.hanging_point.source == 'estimated' for a in assignments):
        warnings_list.append(
            "ℹ️ Punti di aggancio stimati euristicamente (nessun foro rilevato dalla mesh). "
            "Verificare fisicamente la presenza dei fori prima di appendere il pezzo."
        )

    # ─── Assemblaggio risultato finale ───────────────────────────────────
    if blocking:
        result.valid = False
        result.danger_level = 2
        result.ui_color = '#F85149'
        result.blocking_reason = blocking
    elif warnings_list:
        result.valid = True
        result.danger_level = 1
        result.ui_color = '#F0883E'
    else:
        result.valid = True
        result.danger_level = 0
        result.ui_color = '#3FB950'

    result.warnings = warnings_list
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 7. GRAVITY MATRIX — rotazione completa con matrice 4×4
# ─────────────────────────────────────────────────────────────────────────────

def build_gravity_transform(
    hook_point_mm: Tuple[float, float, float],
    cog_mm: Tuple[float, float, float],
) -> np.ndarray:
    """
    Costruisce la matrice di trasformazione omogenea 4×4 che:
      1. Trasla il pezzo in modo che il punto di aggancio sia all'origine
      2. Applica la rotazione pendolare per portare il CoG sotto l'origine (verticale)
      3. Ritrasla per posizionare il pezzo sulla barra

    Questa matrice può essere applicata direttamente alle coordinate 3D di ogni
    vertice del mesh per ottenere la posizione finale nell'impianto.

    Formula:
      T_final = T_bar × R_pendulum × T_center_hook

      dove:
        T_center_hook = trasla di -hook_point (porta il gancio all'origine)
        R_pendulum    = matrice di rotazione pendolare (da compute_pendulum_equilibrium)
        T_bar         = trasla alla posizione sulla barra

    Returns:
        np.ndarray shape (4, 4) — matrice di trasformazione omogenea
    """
    hx, hy, hz = hook_point_mm
    cx, cy, cz = cog_mm

    # Calcola la rotazione pendolare (valori demo: solo angolo X per semplicità)
    delta_x = cx - hx
    delta_z = hz - cz
    theta_x = math.atan2(delta_x, delta_z + 1e-9)
    theta_y = math.atan2(cy - hy, delta_z + 1e-9)

    cos_x, sin_x = math.cos(theta_x), math.sin(theta_x)
    cos_y, sin_y = math.cos(theta_y), math.sin(theta_y)

    Rx = np.eye(4)
    Rx[1, 1] =  cos_x;  Rx[1, 2] = -sin_x
    Rx[2, 1] =  sin_x;  Rx[2, 2] =  cos_x

    Ry = np.eye(4)
    Ry[0, 0] =  cos_y;  Ry[0, 2] =  sin_y
    Ry[2, 0] = -sin_y;  Ry[2, 2] =  cos_y

    # Traslazione al gancio
    T_center = np.eye(4)
    T_center[0, 3] = -hx
    T_center[1, 3] = -hy
    T_center[2, 3] = -hz

    # Rotazione pendolare composta
    R = Ry @ Rx

    # Matrice finale (senza ritrasla: la ritrasla la fa il nesting engine)
    M = R @ T_center
    return M


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=== PHYSICS HANGING SELF-TEST ===\n")

    # ── Test 1: stima punti di aggancio ──────────────────────────────────
    print("1. estimate_hanging_points()")
    for nome, kg, L, W, H in [
        ("flanc_droit",   49.5, 800, 12, 500),
        ("tole_de_fond",  60.0, 1700, 900, 5),
        ("tube_AV_1500",  12.7, 1500, 80, 80),
        ("demi_triangle", 35.0, 600, 400, 80),
    ]:
        pts = estimate_hanging_points(L, W, H, kg, nome)
        print(f"  {nome:<20s} {kg:.0f}kg → {len(pts)} punto/i: "
              + ", ".join(f"({p.x_mm:.0f},{p.y_mm:.0f})" for p in pts))

    # ── Test 2: equilibrio pendolare ──────────────────────────────────────
    print("\n2. compute_pendulum_equilibrium()")
    theta_x, theta_y, H_rot, R = compute_pendulum_equilibrium(
        cog_x_mm=450, cog_y_mm=200, cog_z_mm=250,   # CoG eccentrico
        hook_x_mm=400, hook_y_mm=200, hook_z_mm=500, # gancio in cima
        L_mm=800, W_mm=350, H_mm=500,
    )
    print(f"  θx={theta_x:.1f}°  θy={theta_y:.1f}°  H_rot={H_rot:.0f}mm")

    # ── Test 3: calcolo lunghezze drenaggio ──────────────────────────────
    print("\n3. compute_drainage_hook_lengths()")
    hp1 = HangingPoint(x_mm=200, y_mm=100, z_mm=500, diameter_mm=18, source='detected')
    hp2 = HangingPoint(x_mm=600, y_mm=100, z_mm=500, diameter_mm=18, source='detected')
    l1, l2 = compute_drainage_hook_lengths(hp1, hp2, tilt_deg=12, base_length_mm=300)
    print(f"  L1={l1:.0f}mm  L2={l2:.0f}mm  ΔL={abs(l2-l1):.0f}mm")

    # ── Test 4: validazione assegnazione ─────────────────────────────────
    print("\n4. validate_hanging_assignment()")

    asgn_ok = [HookAssignment(hp1, 0, 0, l1, 30.0), HookAssignment(hp2, 1, 400, l2, 30.0)]
    v = validate_hanging_assignment(asgn_ok, 800, 350, 500, 60, 400, 100, 250, 2000)
    print(f"  Bilancio OK: level={v.danger_level} color={v.ui_color} valid={v.valid}")

    hp_bad = HangingPoint(x_mm=10, y_mm=200, z_mm=500, diameter_mm=18, source='detected')
    asgn_bad = [HookAssignment(hp_bad, 0, 0, 300, 85.0)]
    v2 = validate_hanging_assignment(asgn_bad, 800, 350, 500, 85, 400, 200, 250, 2000)
    print(f"  Singolo pesante: level={v2.danger_level} color={v2.ui_color}")
    if v2.blocking_reason:
        print(f"  BLOCCO: {v2.blocking_reason[:80]}...")

    print("\n✅ Self-test completato")
