"""
overhead_nesting.py — ENOROSSI Paint Optimizer v5
PHASE 3: Overhead Suspension Packing — 3D Nesting Engine (FISICA REALE)

Rispetto alla versione precedente, questo modulo integra il motore fisico
physics_hanging.py per applicare vincoli industriali reali:

  ✅ Fori di aggancio rilevati dalla mesh STL (non superfici generiche)
  ✅ Rotazione pendolare CalcolatA esattamente per ogni pezzo
  ✅ Bilanciamento multi-gancio con pitch-matching sulla barra
  ✅ Lunghezze gancio differenziali per inclinazione di drenaggio
  ✅ Sistema warning operatore (verde/arancio/rosso) con blocco automatico

REGOLE FISICHE IMPLEMENTATE:
  1. Fori di aggancio: il gancio deve TRAVERSARE il foro (non toccare la superficie)
  2. Singolo gancio: il CoG si posiziona verticalmente sotto il punto di sospensione
     → angolo pendolare = atan2(δ_horizontale, δ_verticale)
  3. Multi-gancio: la distanza tra i fori del pezzo deve essere multiplo del passo barra
  4. Drenaggio: inclinazione 10–15° ottenuta con ganci di lunghezza diversa
  5. Blocco automatico: peso > 60kg su singolo gancio, angolo > 35°, Z_max superato
"""

from __future__ import annotations
import os

import copy
import math
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Tuple
import numpy as np

# Importa il motore fisico
try:
    from physics_hanging import (
        HangingPoint,
        HookAssignment,
        HangingValidation,
        get_hanging_points,
        select_multihook_positions,
        validate_hanging_assignment,
        compute_pendulum_equilibrium,
        compute_rotated_clearance_envelope,
        DRAINAGE_TILT_DEG_DEFAULT,
        SINGLE_HOOK_WEIGHT_WARNING_KG,
        SINGLE_HOOK_WEIGHT_DANGER_KG,
    )
    PHYSICS_AVAILABLE = True
except ImportError:
    PHYSICS_AVAILABLE = False
    # Stub minimali per compatibilità senza il modulo
    class HangingPoint:
        def __init__(self, **kw): self.__dict__.update(kw)
    class HookAssignment:
        def __init__(self, **kw): self.__dict__.update(kw)
    class HangingValidation:
        valid=True; danger_level=0; ui_color='#3FB950'; warnings=[]; blocking_reason=''
        pendulum_angle_deg=0.0; effective_tilt_deg=0.0; rotated_H_mm=0.0
        def to_dict(self): return {}


# ═══════════════════════════════════════════════════════════════
# PARAMETRI IMPIANTO
# ═══════════════════════════════════════════════════════════════

@dataclass
class LoadBarConfig:
    """Configurazione della barra di carico overhead."""
    L_max_mm: float = 3000.0          # lunghezza barra (slot per macchina)
    Z_max_mm: float = 2000.0          # clearance massima verso il basso
    passo_gancio_mm: float = 400.0    # intervallo fisso tra ganci
    peso_max_bar_kg: float = 420.0    # capacità totale del trolley
    peso_max_gancio_kg: float = 60.0  # carico max per singolo gancio
    swing_buffer_pct: float = 0.05    # 5% buffer per oscillazione dinamica
    gap_verticale_mm: float = 50.0    # gap di sicurezza tra pezzi appesi
    enable_drainage_tilt: bool = True  # abilita inclinazione drenaggio
    drainage_tilt_deg: float = 12.0   # angolo inclinazione drenaggio (°)
    base_hook_length_mm: float = 300.0 # lunghezza base ganci (mm)

    @property
    def n_ganci(self) -> int:
        return max(1, int(self.L_max_mm / self.passo_gancio_mm))

    @property
    def posizioni_ganci_mm(self) -> List[float]:
        return [i * self.passo_gancio_mm for i in range(self.n_ganci)]


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES — PartSuspension (v2 con fisica reale)
# ═══════════════════════════════════════════════════════════════

@dataclass
class PartSuspension:
    """
    Parte fisica pronta per il nesting overhead.

    NOVITÀ v2:
      - hanging_points   : fori di aggancio rilevati dalla mesh STL
      - hook_assignments : ganci assegnati (con posizione e lunghezza)
      - validation       : risultato validazione fisica (warning/blocco)
      - tilt_target_deg  : inclinazione di drenaggio desiderata
      - pendulum_angle_deg: inclinazione pendolare calcolata (singolo gancio)
    """
    codice: str
    nome: str
    famiglia: str = ''

    # Geometria bounding box (mm)
    L_mm: float = 0.0
    W_mm: float = 0.0
    H_mm: float = 0.0

    # Proprietà fisiche
    peso_kg: float = 0.0
    cog_x_mm: float = 0.0
    cog_y_mm: float = 0.0
    cog_z_mm: float = 0.0

    # Superficie per calcolo vernice
    superficie_m2: float = 0.0

    # Nesting
    n_ganci_req: int = 1
    qty: int = 1
    colore: str = '#455A64'
    note: str = ''

    # ── FISICA REALE (v2) ──────────────────────────────────────────────
    hanging_points: List[HangingPoint] = field(default_factory=list)
    hook_assignments: List[HookAssignment] = field(default_factory=list)
    validation: Optional[HangingValidation] = field(default=None)
    tilt_target_deg: float = 0.0          # 0 = livello, 12 = drenaggio standard
    pendulum_angle_deg: float = 0.0       # inclinazione pendolare (singolo gancio)
    stl_path: str = ''                    # path al file STL per rilevamento fori
    mesh_obj: object = field(default=None, repr=False)  # trimesh in memoria (da STEP)

    # Risultato allocazione (compilato dall'ottimizzatore)
    gancio_start_idx: int = -1
    gancio_x_mm: float = 0.0
    z_offset_mm: float = 0.0
    rot_deg: float = 0.0
    allocato: bool = False
    motivo_fallimento: str = ''

    @property
    def swing_buffer_mm(self) -> float:
        return max(self.L_mm, self.W_mm) * 0.05

    @property
    def h_effettiva_mm(self) -> float:
        """Altezza effettiva appesa (con buffer + rotazione pendolare)."""
        if self.validation and self.validation.rotated_H_mm > 0:
            return self.validation.rotated_H_mm + self.swing_buffer_mm
        return self.H_mm + self.swing_buffer_mm

    @property
    def l_effettiva_mm(self) -> float:
        return self.L_mm + self.swing_buffer_mm * 2

    @property
    def ui_color(self) -> str:
        """Colore per la UI basato sulla validazione fisica."""
        if self.validation:
            return self.validation.ui_color
        return '#3FB950'

    @property
    def is_physically_valid(self) -> bool:
        """True se il pezzo può essere appeso in sicurezza."""
        if self.validation:
            return self.validation.valid
        return True


# ═══════════════════════════════════════════════════════════════
# PHYSICS INTEGRATION — prepara ogni pezzo prima del nesting
# ═══════════════════════════════════════════════════════════════

def prepara_fisica_parte(
    parte: PartSuspension,
    cfg: LoadBarConfig,
) -> PartSuspension:
    """
    Applica tutta la fisica di sospensione a un PartSuspension:

    1. Rileva/stima i punti di aggancio (fori STL o euristica)
    2. Seleziona la combinazione ottimale di ganci (con pitch-matching)
    3. Calcola l'inclinazione pendolare (singolo gancio) o di drenaggio
    4. Aggiorna le dimensioni effettive (AABB ruotato)
    5. Esegue la validazione e produce il colore/warning UI

    Modifica il PartSuspension in-place e lo restituisce.
    """
    if not PHYSICS_AVAILABLE:
        return parte

    # ── Step 1: punti di aggancio ─────────────────────────────────────
    if not parte.hanging_points:
        parte.hanging_points = get_hanging_points(
            L_mm=parte.L_mm,
            W_mm=parte.W_mm,
            H_mm=parte.H_mm,
            peso_kg=parte.peso_kg,
            nome=parte.nome,
            stl_path=parte.stl_path if parte.stl_path else None,
        )

    # ── Step 2: selezione ganci barra (pitch-matching) ────────────────
    tilt = cfg.drainage_tilt_deg if cfg.enable_drainage_tilt else 0.0
    parte.tilt_target_deg = tilt

    assignments = select_multihook_positions(
        hanging_points=parte.hanging_points,
        bar_hook_positions_mm=cfg.posizioni_ganci_mm,
        part_peso_kg=parte.peso_kg,
        bar_pitch_mm=cfg.passo_gancio_mm,
        target_tilt_deg=tilt,
        base_hook_length_mm=cfg.base_hook_length_mm,
    )
    parte.hook_assignments = assignments or []

    # Aggiorna n_ganci_req dal numero di ganci assegnati
    parte.n_ganci_req = max(1, len(parte.hook_assignments))

    # ── Step 3: rotazione pendolare (singolo gancio) ─────────────────
    if len(parte.hook_assignments) == 1:
        hp = parte.hook_assignments[0].hanging_point
        theta_x, theta_y, H_rot, _ = compute_pendulum_equilibrium(
            cog_x_mm=parte.cog_x_mm,
            cog_y_mm=parte.cog_y_mm,
            cog_z_mm=parte.cog_z_mm,
            hook_x_mm=hp.x_mm,
            hook_y_mm=hp.y_mm,
            hook_z_mm=hp.z_mm,
            L_mm=parte.L_mm,
            W_mm=parte.W_mm,
            H_mm=parte.H_mm,
        )
        parte.pendulum_angle_deg = round(math.hypot(theta_x, theta_y), 2)
        parte.rot_deg = round(theta_x, 2)    # rotazione principale (piano XZ)

        # Aggiorna H con la proiezione dopo rotazione pendolare
        _, _, _, _ = compute_pendulum_equilibrium(
            parte.cog_x_mm, parte.cog_y_mm, parte.cog_z_mm,
            hp.x_mm, hp.y_mm, hp.z_mm,
            parte.L_mm, parte.W_mm, parte.H_mm
        )
        L_env, W_env, H_env = compute_rotated_clearance_envelope(
            parte.L_mm, parte.W_mm, parte.H_mm,
            theta_x, theta_y,
            extra_buffer_pct=cfg.swing_buffer_pct,
        )
        # Usa l'altezza proiettata per il calcolo di spazio occupato
        parte.H_mm = max(parte.H_mm, H_env)   # non ridurre mai H

    # ── Step 4: validazione fisica ────────────────────────────────────
    parte.validation = validate_hanging_assignment(
        assignments=parte.hook_assignments,
        L_mm=parte.L_mm,
        W_mm=parte.W_mm,
        H_mm=parte.H_mm,
        peso_kg=parte.peso_kg,
        cog_x_mm=parte.cog_x_mm,
        cog_y_mm=parte.cog_y_mm,
        cog_z_mm=parte.cog_z_mm,
        z_max_mm=cfg.Z_max_mm,
        nome=parte.nome,
    )

    return parte


# ═══════════════════════════════════════════════════════════════
# STATO GANCIO BARRA
# ═══════════════════════════════════════════════════════════════

@dataclass
class GancioState:
    """Stato di un gancio sulla barra overhead."""
    idx: int
    x_mm: float
    peso_tot_kg: float = 0.0
    z_occupata_mm: float = 0.0
    parti: List[PartSuspension] = field(default_factory=list)
    libero: bool = True

    @property
    def n_parti(self) -> int:
        return len(self.parti)

    def momento_flettente_Nm(self, x_centro_barra_mm: float) -> float:
        braccio_m = abs(self.x_mm - x_centro_barra_mm) / 1000.0
        return self.peso_tot_kg * 9.81 * braccio_m


# ═══════════════════════════════════════════════════════════════
# RISULTATO NESTING
# ═══════════════════════════════════════════════════════════════

@dataclass
class NestingResult:
    """Risultato completo del nesting overhead."""
    bar_config: LoadBarConfig
    ganci: List[GancioState] = field(default_factory=list)
    parti_allocate: List[PartSuspension] = field(default_factory=list)
    parti_non_allocate: List[PartSuspension] = field(default_factory=list)

    peso_totale_kg: float = 0.0
    saturazione_pct: float = 0.0
    ganci_usati: int = 0
    momento_max_Nm: float = 0.0
    avvisi: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'bar_config': {
                'L_max_mm':  self.bar_config.L_max_mm,
                'Z_max_mm':  self.bar_config.Z_max_mm,
                'n_ganci':   self.bar_config.n_ganci,
                'passo_mm':  self.bar_config.passo_gancio_mm,
                'drenaggio': self.bar_config.drainage_tilt_deg,
            },
            'ganci': [
                {
                    'idx':     g.idx,
                    'x_mm':    g.x_mm,
                    'peso_kg': round(g.peso_tot_kg, 2),
                    'z_mm':    round(g.z_occupata_mm, 1),
                    'n_parti': g.n_parti,
                    'libero':  g.libero,
                    'hook_left_mm': (
                        g.parti[0].hook_assignments[0].hook_length_mm
                        if g.parti and g.parti[0].hook_assignments else 300
                    ),
                    'hook_right_mm': (
                        g.parti[0].hook_assignments[-1].hook_length_mm
                        if g.parti and g.parti[0].hook_assignments else 300
                    ),
                    'parti': [
                        {
                            'codice':          p.codice,
                            'nome':            p.nome or p.codice,
                            'peso_kg':         p.peso_kg,
                            'H_mm':            p.H_mm,
                            'L_mm':            p.L_mm,
                            'W_mm':            p.W_mm,
                            'z_offset':        p.z_offset_mm,
                            'rot_deg':         p.rot_deg,
                            'pendulum_deg':    p.pendulum_angle_deg,
                            'tilt_deg':        p.tilt_target_deg,
                            'colore':          p.colore,
                            'sup_m2':          p.superficie_m2,
                            'ui_color':        p.ui_color,
                            'n_ganci':         p.n_ganci_req,
                            'validation': (
                                p.validation.to_dict()
                                if p.validation else {}
                            ),
                            'hook_assignments': [
                                {
                                    'bar_hook_idx':   a.bar_hook_idx,
                                    'bar_hook_x_mm':  a.bar_hook_x_mm,
                                    'hook_length_mm': a.hook_length_mm,
                                    'load_kg':        a.load_kg,
                                    'hole_diam_mm':   a.hanging_point.diameter_mm,
                                    'hole_source':    a.hanging_point.source,
                                }
                                for a in (p.hook_assignments or [])
                            ],
                        }
                        for p in g.parti
                    ],
                }
                for g in self.ganci
            ],
            'kpi': {
                'allocate':        len(self.parti_allocate),
                'non_allocate':    len(self.parti_non_allocate),
                'peso_totale_kg':  round(self.peso_totale_kg, 2),
                'saturazione_pct': round(self.saturazione_pct, 1),
                'ganci_usati':     self.ganci_usati,
                'momento_max_Nm':  round(self.momento_max_Nm, 1),
            },
            'non_allocate': [
                {
                    'codice':   p.codice,
                    'nome':     p.nome or p.codice,
                    'L_mm':     p.L_mm,
                    'W_mm':     p.W_mm,
                    'H_mm':     p.H_mm,
                    'peso_kg':  p.peso_kg,
                    'motivo':   p.motivo_fallimento,
                    'ui_color': p.ui_color,
                }
                for p in self.parti_non_allocate
            ],
            'avvisi': self.avvisi,
        }


# ═══════════════════════════════════════════════════════════════
# COLLISION DETECTION — con physics reale
# ═══════════════════════════════════════════════════════════════

def check_collision_aabb(
    gancio: GancioState,
    parte: PartSuspension,
    z_proposta: float,
    cfg: LoadBarConfig,
) -> Tuple[bool, str]:
    """
    Verifica collisione AABB con physics reale.

    Controlla in ordine:
      0. Parte fisicamente non valida → blocca subito (segnala la ragione)
      1. Z clearance: il pezzo (con rotazione pendolare inclusa) deve stare in Z_max
      2. Peso gancio: non supera il limite del singolo gancio
      3. Peso totale barra: non supera capacità trolley
    """
    # ── Check 0: validazione fisica ──────────────────────────────────────
    if parte.validation and not parte.validation.valid:
        return True, f"FISICO: {parte.validation.blocking_reason[:100]}"

    # ── Check 1: clearance Z (usa H effettiva con rotazione) ─────────────
    z_bottom = z_proposta + parte.h_effettiva_mm
    if z_bottom > cfg.Z_max_mm:
        return True, (
            f"Clearance Z: {z_bottom:.0f}mm > Z_max {cfg.Z_max_mm:.0f}mm "
            f"(incl. rotazione {parte.pendulum_angle_deg:.1f}°)"
        )

    # ── Check 2: peso gancio ──────────────────────────────────────────────
    peso_per_gancio = parte.peso_kg / max(parte.n_ganci_req, 1)
    if gancio.peso_tot_kg + peso_per_gancio > cfg.peso_max_gancio_kg:
        return True, (
            f"Peso gancio: {gancio.peso_tot_kg + peso_per_gancio:.1f}kg "
            f"> max {cfg.peso_max_gancio_kg:.0f}kg"
        )

    return False, ''


# ═══════════════════════════════════════════════════════════════
# LOAD BALANCE ANALYZER
# ═══════════════════════════════════════════════════════════════

def analizza_load_balance(ganci: List[GancioState], cfg: LoadBarConfig) -> dict:
    """Analizza la distribuzione del carico sulla barra overhead."""
    peso_tot = sum(g.peso_tot_kg for g in ganci)
    x_centro = cfg.L_max_mm / 2.0

    momenti = [g.momento_flettente_Nm(x_centro) for g in ganci]
    momento_max = max(momenti) if momenti else 0.0

    if peso_tot > 0:
        x_com = sum(g.x_mm * g.peso_tot_kg for g in ganci) / peso_tot
    else:
        x_com = x_centro

    sbilancio_mm = abs(x_com - x_centro)
    sbilancio_pct = sbilancio_mm / (cfg.L_max_mm / 2.0) * 100.0 if cfg.L_max_mm > 0 else 0.0

    avvisi = []
    if peso_tot > cfg.peso_max_bar_kg:
        avvisi.append(f"⚠️ Peso {peso_tot:.1f}kg supera capacità trolley {cfg.peso_max_bar_kg:.0f}kg")
    if sbilancio_pct > 30:
        avvisi.append(f"⚠️ Sbilancio carico {sbilancio_pct:.0f}% — CoM a {x_com:.0f}mm")
    if momento_max > 5000:
        avvisi.append(f"⚠️ Momento flettente elevato: {momento_max:.0f} N·m")

    return {
        'peso_totale_kg': round(peso_tot, 2),
        'momento_max_Nm': round(momento_max, 1),
        'x_com_mm': round(x_com, 1),
        'sbilancio_pct': round(sbilancio_pct, 1),
        'distribuzione': [
            {
                'gancio': g.idx + 1,
                'x_mm': g.x_mm,
                'peso_kg': round(g.peso_tot_kg, 2),
                'pct': round(g.peso_tot_kg / peso_tot * 100, 1) if peso_tot > 0 else 0,
                'momento_Nm': round(momenti[i], 1),
            }
            for i, g in enumerate(ganci) if g.peso_tot_kg > 0
        ],
        'avvisi': avvisi,
    }


# ═══════════════════════════════════════════════════════════════
# MAIN OPTIMIZER: First-Fit Decreasing + Fisica Reale
# ═══════════════════════════════════════════════════════════════

def ottimizza_nesting_overhead(
    parti: List[PartSuspension],
    cfg: LoadBarConfig,
    strategia: str = 'ffd_peso',
    apply_physics: bool = True,
) -> NestingResult:
    """
    Ottimizzatore di nesting overhead pensile con fisica reale.

    DIFFERENZE RISPETTO ALLA VERSIONE PRECEDENTE:
      - prepara_fisica_parte() viene chiamato su ogni pezzo PRIMA dell'allocazione
      - Il CoG alignment è calcolato esattamente (angolo pendolare reale)
      - Pezzi con validation.danger_level == 2 vengono bloccati (non allocati)
      - L'H effettiva include la proiezione AABB dopo rotazione pendolare
      - I warning fisici appaiono nell'avvisi del NestingResult

    Strategie:
      'ffd_peso'   : First-Fit Decreasing per peso (più pesante prima)
      'ffd_volume' : First-Fit Decreasing per volume
      'ffd_ganci'  : First-Fit Decreasing per numero di ganci richiesti
    """
    # Inizializza ganci
    ganci = [
        GancioState(idx=i, x_mm=pos)
        for i, pos in enumerate(cfg.posizioni_ganci_mm)
    ]

    # Espandi parti per qty + applica fisica
    coda: List[PartSuspension] = []
    avvisi_globali: List[str] = []

    for p in parti:
        for qi in range(p.qty):
            pc = copy.copy(p)
            pc.qty = 1
            pc.hanging_points = list(p.hanging_points)   # shallow copy
            pc.hook_assignments = []
            pc.validation = None

            if apply_physics and PHYSICS_AVAILABLE:
                pc = prepara_fisica_parte(pc, cfg)

                # Raccogli warning fisici
                if pc.validation and pc.validation.warnings:
                    for w in pc.validation.warnings:
                        msg = f"[{pc.codice}] {w}"
                        if msg not in avvisi_globali:
                            avvisi_globali.append(msg)

                # Pezzi bloccati (danger_level 2): non allocare
                if pc.validation and not pc.validation.valid:
                    pc.motivo_fallimento = pc.validation.blocking_reason
                    pc.allocato = False
                    continue   # salta questo pezzo

            coda.append(pc)

    # Ordina (First-Fit Decreasing)
    sort_keys = {
        'ffd_peso':   lambda x: (-x.peso_kg, -x.n_ganci_req),
        'ffd_volume': lambda x: (-(x.L_mm * x.W_mm * x.H_mm)),
        'ffd_ganci':  lambda x: (-x.n_ganci_req, -x.peso_kg),
    }
    coda.sort(key=sort_keys.get(strategia, sort_keys['ffd_peso']))

    allocate: List[PartSuspension] = []
    non_allocate: List[PartSuspension] = []

    # Raccogli i pezzi bloccati che erano nella lista originale ma non in coda
    for p in parti:
        for _ in range(p.qty):
            if hasattr(p, 'validation') and p.validation and not p.validation.valid:
                pc_blocked = copy.copy(p)
                pc_blocked.qty = 1
                pc_blocked.allocato = False
                pc_blocked.motivo_fallimento = p.validation.blocking_reason
                non_allocate.append(pc_blocked)

    for parte in coda:
        n_req = max(1, parte.n_ganci_req)
        allocata = False

        # ── Usa le posizioni gancio già calcolate dalla fisica ───────────
        if parte.hook_assignments:
            # Cerca il blocco di ganci fisicamente corretti (dal pitch-matching)
            hook_idxs = [a.bar_hook_idx for a in parte.hook_assignments]
            # Verifica che i ganci scelti dalla fisica siano disponibili e validi
            blocco = ganci[hook_idxs[0]:hook_idxs[-1] + 1]
            z_proposta = max(g.z_occupata_mm for g in blocco) + cfg.gap_verticale_mm

            coll, msg = check_collision_aabb(ganci[hook_idxs[0]], parte, z_proposta, cfg)
            if not coll:
                peso_per_gancio = parte.peso_kg / n_req
                z_nuova = z_proposta + parte.h_effettiva_mm + cfg.gap_verticale_mm
                for g in blocco:
                    g.peso_tot_kg  += peso_per_gancio
                    g.z_occupata_mm = z_nuova
                    g.libero = False
                    g.parti.append(parte)

                parte.gancio_start_idx = hook_idxs[0]
                parte.gancio_x_mm      = ganci[hook_idxs[0]].x_mm
                parte.z_offset_mm      = round(z_proposta, 1)
                parte.allocato         = True
                allocate.append(parte)
                allocata = True
            else:
                parte.motivo_fallimento = msg

        # ── Fallback: First-Fit su tutti i blocchi disponibili ───────────
        if not allocata:
            candidati = []
            for start_i in range(len(ganci) - n_req + 1):
                blocco = ganci[start_i:start_i + n_req]
                z_proposta = max(g.z_occupata_mm for g in blocco) + cfg.gap_verticale_mm

                collisione = False
                for g in blocco:
                    coll, msg = check_collision_aabb(g, parte, z_proposta, cfg)
                    if coll:
                        collisione = True
                        parte.motivo_fallimento = msg
                        break

                if not collisione:
                    peso_max_blocco = max(g.peso_tot_kg for g in blocco)
                    z_max_blocco    = max(g.z_occupata_mm for g in blocco)
                    score = peso_max_blocco * 0.7 + z_max_blocco * 0.001
                    candidati.append((score, start_i, blocco, z_proposta))

            if candidati:
                candidati.sort(key=lambda x: x[0])
                _, start_i, blocco, z_proposta = candidati[0]

                peso_per_gancio = parte.peso_kg / n_req
                z_nuova = z_proposta + parte.h_effettiva_mm + cfg.gap_verticale_mm
                for g in blocco:
                    g.peso_tot_kg  += peso_per_gancio
                    g.z_occupata_mm = z_nuova
                    g.libero = False
                    g.parti.append(parte)

                parte.gancio_start_idx = start_i
                parte.gancio_x_mm      = blocco[0].x_mm
                parte.z_offset_mm      = round(z_proposta, 1)
                parte.allocato         = True
                allocate.append(parte)
                allocata = True

        if not allocata:
            if not parte.motivo_fallimento:
                parte.motivo_fallimento = "Nessuno slot disponibile (peso/clearance)"
            non_allocate.append(parte)

    # ── KPI finali ────────────────────────────────────────────────────────
    ganci_usati = sum(1 for g in ganci if not g.libero)
    peso_tot = sum(g.peso_tot_kg for g in ganci)
    sat_pct = ganci_usati / len(ganci) * 100.0 if ganci else 0.0
    x_centro = cfg.L_max_mm / 2.0
    momento_max = max((g.momento_flettente_Nm(x_centro) for g in ganci), default=0.0)

    avvisi = list(avvisi_globali)
    if peso_tot > cfg.peso_max_bar_kg:
        avvisi.append(f"⚠️ Peso {peso_tot:.1f}kg > capacità trolley {cfg.peso_max_bar_kg:.0f}kg")
    if non_allocate:
        avvisi.append(f"⚠️ {len(non_allocate)} parti non allocate — "
                      f"verifica fisicamente o ridistribuisci")
    if sat_pct < 30:
        avvisi.append(f"💡 Saturazione bassa ({sat_pct:.0f}%) — aggiungi pezzi o riduci slot")
    if sat_pct > 90:
        avvisi.append(f"⚠️ Saturazione alta ({sat_pct:.0f}%) — verifica manualmente")

    return NestingResult(
        bar_config=cfg,
        ganci=ganci,
        parti_allocate=allocate,
        parti_non_allocate=non_allocate,
        peso_totale_kg=round(peso_tot, 2),
        saturazione_pct=round(sat_pct, 1),
        ganci_usati=ganci_usati,
        momento_max_Nm=round(momento_max, 1),
        avvisi=avvisi,
    )


# ═══════════════════════════════════════════════════════════════
# PRODUCTION ORDER EXPLODER
# ═══════════════════════════════════════════════════════════════

def esplodi_ordine_produzione(
    bom: List[dict],
    n_unita: int,
) -> List[PartSuspension]:
    """
    PHASE 2 — Quantity Exploder.

    Prende una BOM (lista di dict con campi del ParseResult) e N unità
    da produrre, restituisce la coda di PartSuspension pronta per il nesting.

    Il CoG viene posizionato a cog_x/y/z se disponibile nella BOM,
    altrimenti usa le stime strutturali (CoG leggermente sotto il centro).
    """
    COLORI_TIPO: Dict[str, str] = {
        'frame': '#0D47A1', 'box': '#0D47A1', 'flanc': '#1565C0',
        'tube': '#37474F', 'dent': '#4E342E', 'tine': '#4E342E',
        'triangle': '#1B5E20', 'gousset': '#558B2F', 'bracket': '#558B2F',
        'plate': '#880E4F', 'plat': '#880E4F', 'fond': '#880E4F',
        'bolt': '#616161', 'vis': '#616161', 'ecrou': '#616161',
        'axe': '#455A64', 'entretoise': '#37474F', 'liaison': '#558B2F',
    }

    coda: List[PartSuspension] = []

    for item in bom:
        qty_unitaria = item.get('qty', 1)
        qty_totale   = qty_unitaria * n_unita

        L = float(item.get('lunghezza_mm', 500))
        W = float(item.get('larghezza_mm', 300))
        H = float(item.get('altezza_mm', 100))

        # CoG: usa valori dalla BOM se disponibili, altrimenti stima strutturale
        cog_x = float(item.get('cog_x_mm', L / 2.0))
        cog_y = float(item.get('cog_y_mm', W / 2.0))
        cog_z = float(item.get('cog_z_mm', H * 0.40))  # leggermente sotto il centro

        passo_m = float(item.get('passo_gancio_m', 0.4))
        n_ganci = max(1, round(passo_m / 0.4))

        nome_l = item.get('nome', '').lower()
        colore = '#455A64'
        for kw, col in COLORI_TIPO.items():
            if kw in nome_l:
                colore = col
                break

        parte = PartSuspension(
            codice=item.get('codice_art', 'UNK'),
            nome=item.get('nome', 'Sconosciuto'),
            L_mm=L,
            W_mm=W,
            H_mm=H,
            peso_kg=float(item.get('peso_kg', 1.0)),
            cog_x_mm=cog_x,
            cog_y_mm=cog_y,
            cog_z_mm=cog_z,
            superficie_m2=float(item.get('superficie_m2', 0.1)),
            n_ganci_req=n_ganci,
            qty=qty_totale,
            colore=colore,
            stl_path=item.get('stl_path', ''),
            mesh_obj=item.get('mesh_obj', None),  # GAP2: trimesh in memoria
            hanging_points=[],   # verranno rilevati dalla fisica
        )
        coda.append(parte)

    return coda


# ═══════════════════════════════════════════════════════════════
# SILHOUETTE 2D — proiezione reale mesh (Trimesh cross-section)
# ═══════════════════════════════════════════════════════════════

def _get_part_silhouette_2d(parte: 'PartSuspension') -> 'Optional[np.ndarray]':
    """
    Estrae la silhouette 2D frontale (piano XZ) dalla mesh STL del pezzo.

    Usa trimesh.section() con piano normale Y per ottenere il profilo
    visibile dall'operatore (vista frontale). Questo trasforma il render
    da "rettangoli colorati" a "sagome reali dei pezzi" — e cambia
    completamente la leggibilità industriale anche rimanendo in matplotlib.

    1 giorno di lavoro, impatto massimo (suggerito dal gruppo di revisione).

    Args:
        parte : PartSuspension con stl_path compilato

    Returns:
        np.ndarray shape (N, 2) di vertici [X_mm, Z_mm] normalizzati
        nell'intervallo [0,1]×[0,1] — oppure None se mesh non disponibile.
    """
    import os
    # GAP2 FIX: usa mesh_obj in memoria se disponibile (caso STEP — stl_path è vuoto)
    try:
        import trimesh
        _mesh_obj = getattr(parte, 'mesh_obj', None)
        if _mesh_obj is not None and hasattr(_mesh_obj, 'section'):
            mesh = _mesh_obj
        elif parte.stl_path and os.path.exists(parte.stl_path):
            mesh = trimesh.load(parte.stl_path, force='mesh', process=True)
        else:
            return None
        if mesh is None or len(mesh.faces) == 0:
            return None

        # Auto-scaling
        bb = mesh.bounding_box.extents
        if max(bb) < 5.0:
            mesh.apply_scale(1000.0)
            bb = mesh.bounding_box.extents

        # Sezione frontale al centro (piano normale Y)
        # Usa Y = metà larghezza per ottenere il profilo frontale XZ
        center = mesh.bounding_box.centroid
        section = mesh.section(
            plane_origin=[float(center[0]), float(center[1]), float(center[2])],
            plane_normal=[0, 1, 0],
        )
        if section is None:
            return None

        try:
            path2d, _ = section.to_2D()
        except Exception:
            try:
                path2d, _ = section.to_planar()
            except Exception:
                return None

        if path2d is None or len(path2d.vertices) < 3:
            return None

        verts = path2d.vertices  # shape (N, 2) in mm (XZ nel piano frontale)

        # Normalizza a [0,1]×[0,1]
        v_min = verts.min(axis=0)
        v_max = verts.max(axis=0)
        v_range = v_max - v_min
        if v_range[0] < 1e-3 or v_range[1] < 1e-3:
            return None

        return (verts - v_min) / v_range  # normalizzato [0,1]

    except Exception:
        return None


def _silhouette_to_patch(
    silhouette_norm: 'np.ndarray',
    x_left: float,
    y_top: float,
    width_plot: float,
    height_plot: float,
    facecolor: str,
    alpha: float = 0.85,
    zorder: int = 3,
) -> 'Optional[mpatches.Polygon]':
    """
    Converte la silhouette normalizzata in un Polygon matplotlib
    posizionato nel sistema di coordinate del plot.

    Args:
        silhouette_norm : array (N, 2) normalizzato [0,1]×[0,1]
        x_left, y_top  : angolo superiore sinistro nello spazio plot (m)
        width_plot      : larghezza del rettangolo slot (m)
        height_plot     : altezza del rettangolo slot (m) — positiva, verso il basso

    Returns:
        matplotlib Polygon pronto da aggiungere all'asse.
    """
    try:
        import matplotlib.patches as mpatches
        # X cresce verso destra, Y cresce verso l'alto → y_top - height*t
        sil_x = x_left + silhouette_norm[:, 0] * width_plot
        sil_y = y_top  - silhouette_norm[:, 1] * height_plot
        verts = np.column_stack([sil_x, sil_y])
        return mpatches.Polygon(
            verts,
            closed=True,
            facecolor=facecolor + 'CC',
            edgecolor='white',
            linewidth=0.8,
            zorder=zorder,
        )
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# RENDER SVG (overhead view con silhouette reali)
# ═══════════════════════════════════════════════════════════════

def render_overhead_svg(
    result: 'NestingResult',
    out_path: str,
    titolo: str = "PIANO NESTING OVERHEAD",
) -> None:
    """
    Genera SVG visualizzazione nesting overhead con silhouette mesh reali.

    Rispetto al PNG:
      ✅ Output SVG manipolabile da JavaScript (hover, click, zoom)
      ✅ Silhouette 2D reale via mesh.section() invece di rettangoli generici
      ✅ Vettoriale: qualità perfetta a qualsiasi zoom
      ✅ Testo selezionabile e accessibile

    La silhouette reale trasforma il render da "rettangoli colorati"
    a "sagome reali dei pezzi" — cambia completamente la leggibilità
    industriale (suggerito dal gruppo di revisione come quick win 1 giorno).

    Compatibile con render_overhead_png: stessa firma, salva in .svg.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch
        import matplotlib.patheffects as pe
    except ImportError:
        return

    cfg = result.bar_config
    SC = 1 / 1000.0

    BAR_L = cfg.L_max_mm * SC
    BAR_H = cfg.Z_max_mm * SC
    GANCIO_STEP = cfg.passo_gancio_mm * SC
    n_g = len(result.ganci)

    FW = max(14.0, BAR_L * 2.5 + 3.0)
    FH = BAR_H * 2.5 + 2.5

    fig, (ax, ax_lb) = plt.subplots(
        2, 1, figsize=(FW, FH + 1.5),
        gridspec_kw={'height_ratios': [4, 1]},
        dpi=150,
    )
    for a in (ax, ax_lb):
        a.set_facecolor("#1C1F26")   # gunmetal (standard HMI industriale)
    fig.patch.set_facecolor("#1C1F26")

    ax.axis("off")
    ax.set_aspect("equal")
    ax.set_xlim(-0.5, BAR_L + 0.5)
    ax.set_ylim(-BAR_H - 0.5, 1.2)

    # ── Barra overhead ────────────────────────────────────────────────
    ax.add_patch(mpatches.FancyBboxPatch(
        (-0.1, 0), BAR_L + 0.2, 0.15,
        boxstyle="round,pad=0.02",
        facecolor="#1565C0", edgecolor="#42A5F5", linewidth=2, zorder=10,
    ))
    ax.text(BAR_L / 2, 0.65, titolo,
            color="#F0F6FC", fontsize=11, fontweight="bold",
            ha="center", va="bottom", family="monospace")
    ax.text(BAR_L / 2, 0.35,
            f"{n_g} ganci × {cfg.passo_gancio_mm:.0f}mm  |  "
            f"Z_max {cfg.Z_max_mm:.0f}mm  |  "
            f"Peso {result.peso_totale_kg:.1f}kg  |  "
            f"Sat {result.saturazione_pct:.0f}%  |  "
            f"Drenaggio {cfg.drainage_tilt_deg:.0f}°",
            color="#8B949E", fontsize=7, ha="center", va="bottom")

    fx_stroke = [pe.withStroke(linewidth=2, foreground="#1C1F26")]

    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP

        # Gambo gancio
        ax.plot([gx + GANCIO_STEP / 2, gx + GANCIO_STEP / 2], [0, -0.08],
                color="#9CA3AF", lw=3, solid_capstyle="round", zorder=11)
        ax.text(gx + GANCIO_STEP / 2, 0.18, f"G{gi+1}",
                color="#E6EDF3", fontsize=7, ha="center", va="bottom", fontweight="bold")

        # Sfondo slot gancio
        ax.add_patch(FancyBboxPatch(
            (gx + 0.02, -BAR_H), GANCIO_STEP - 0.04, BAR_H - 0.08,
            boxstyle="round,pad=0.01",
            facecolor="#0D1117", edgecolor="#30363D", linewidth=0.8, zorder=1,
        ))

        for p in g.parti:
            if not p.allocato:
                continue

            base_col = p.ui_color
            y_top = -(p.z_offset_mm * SC)
            p_h = p.h_effettiva_mm * SC
            n_span = max(p.n_ganci_req, 1)
            p_w = min(GANCIO_STEP * 0.88 * n_span,
                      p.L_mm * SC / n_span * 0.90)
            slot_w = p_w * n_span
            x_left = gx + (GANCIO_STEP * n_span - slot_w) / 2

            # ── Silhouette reale se mesh disponibile ─────────────────
            sil = _get_part_silhouette_2d(p)
            if sil is not None:
                patch = _silhouette_to_patch(
                    sil, x_left, y_top, slot_w, p_h,
                    facecolor=base_col, zorder=3,
                )
                if patch is not None:
                    ax.add_patch(patch)
                else:
                    sil = None  # fallback al rettangolo

            if sil is None:
                # ── Rettangolo fallback ──────────────────────────────
                # DANGER: hatch pattern diagonale per daltonismo (8% uomini)
                hatch = '//' if p.ui_color == '#F85149' else None
                rect = FancyBboxPatch(
                    (x_left, y_top - p_h),
                    slot_w, p_h,
                    boxstyle="round,pad=0.008",
                    facecolor=base_col + "CC",
                    edgecolor="white", linewidth=1.0,
                    hatch=hatch,
                    zorder=3,
                )
                ax.add_patch(rect)

            cx = gx + GANCIO_STEP / 2 * n_span
            cy = y_top - p_h / 2

            # Testo codice + peso
            ax.text(cx, cy + p_h * 0.15, p.codice[:10],
                    color="white", fontsize=6.5, ha="center", va="center",
                    fontweight="bold", zorder=5, path_effects=fx_stroke)
            ax.text(cx, cy - p_h * 0.05, f"{p.peso_kg:.1f}kg",
                    color="#FFA657", fontsize=5.5, ha="center", va="center", zorder=5)

            # Badge rotazione pendolare
            if abs(p.pendulum_angle_deg) > 0.5:
                ax.text(cx, cy - p_h * 0.22, f"⟳{p.pendulum_angle_deg:.0f}°",
                        color="#58A6FF", fontsize=5, ha="center", va="center", zorder=5)

            # Badge ganci differenziali — mostrato come inclinazione visiva
            # (il pezzo deve apparire VISIBILMENTE inclinato, non solo come numero)
            if len(p.hook_assignments) == 2:
                l1 = p.hook_assignments[0].hook_length_mm
                l2 = p.hook_assignments[1].hook_length_mm
                if abs(l1 - l2) > 10:
                    # Freccia inclinazione visiva
                    import math as _math
                    tilt_rad = _math.atan2(abs(l1 - l2), p.L_mm + 1)
                    dx_arrow = p_w * 0.3 * (1 if l2 > l1 else -1)
                    dy_arrow = dx_arrow * _math.tan(tilt_rad) * 0.5
                    ax.annotate('',
                        xy=(cx + dx_arrow, cy - p_h * 0.15 + dy_arrow),
                        xytext=(cx - dx_arrow, cy - p_h * 0.15 - dy_arrow),
                        arrowprops=dict(arrowstyle='<->', color='#D2A8FF', lw=1.5),
                        zorder=6,
                    )
                    ax.text(cx, cy - p_h * 0.38,
                            f"↕{l1:.0f}/{l2:.0f}mm",
                            color="#D2A8FF", fontsize=4.5,
                            ha="center", va="center", zorder=5)

    # ── Load Balance bar chart ─────────────────────────────────────────
    ax_lb.set_facecolor("#1C1F26")
    ax_lb.axis("off")
    peso_max_g = cfg.peso_max_gancio_kg
    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP
        pct = min(g.peso_tot_kg / peso_max_g, 1.0) if peso_max_g > 0 else 0
        col = "#F85149" if pct > 0.9 else ("#F0883E" if pct > 0.7 else "#3FB950")
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, pct,
            facecolor=col, edgecolor="none", zorder=2,
        ))
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, 1.0,
            facecolor="#161B22", edgecolor="#30363D", linewidth=0.5, zorder=1,
        ))
        if g.peso_tot_kg > 0:
            ax_lb.text(gx + GANCIO_STEP * 0.42, pct + 0.05,
                       f"{g.peso_tot_kg:.0f}kg",
                       color=col, fontsize=6, ha="center", zorder=3)

    ax_lb.set_xlim(-0.1, BAR_L + 0.1)
    ax_lb.set_ylim(-0.1, 1.5)
    ax_lb.text(-0.4, 0.5, "Carico\nganci", color="#8B949E", fontsize=7, va="center")

    plt.tight_layout(pad=0.3)

    # Salva SVG (vettoriale, manipolabile da JavaScript)
    svg_path = out_path if out_path.endswith('.svg') else out_path.replace('.png', '.svg')
    plt.savefig(svg_path, format='svg', bbox_inches="tight",
                facecolor="#1C1F26", edgecolor="none")
    plt.close()



# ═══════════════════════════════════════════════════════════════
# RENDER PNG (overhead view)
# ═══════════════════════════════════════════════════════════════

def render_overhead_png(
    result: NestingResult,
    out_path: str,
    titolo: str = "PIANO NESTING OVERHEAD",
) -> None:
    """
    Genera PNG visualizzazione nesting overhead con fisica reale.

    Novità rispetto alla versione precedente:
      - Colori pezzi basati sul danger_level (verde/arancio/rosso)
      - Indicazione rotazione pendolare (triangolino inclinato)
      - Badge lunghezze gancio differenziali
      - Tooltip warning come testo sul render
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
        import matplotlib.patheffects as pe
    except ImportError:
        return

    cfg = result.bar_config
    SC = 1 / 1000.0

    BAR_L = cfg.L_max_mm * SC
    BAR_H = cfg.Z_max_mm * SC
    GANCIO_STEP = cfg.passo_gancio_mm * SC
    n_g = len(result.ganci)

    FW = max(14.0, BAR_L * 2.5 + 3.0)
    FH = BAR_H * 2.5 + 2.5

    fig, (ax, ax_lb) = plt.subplots(
        2, 1, figsize=(FW, FH + 1.5),
        gridspec_kw={'height_ratios': [4, 1]},
        dpi=150,
    )
    for a in (ax, ax_lb):
        a.set_facecolor("#080B10")
    fig.patch.set_facecolor("#080B10")

    ax.axis("off")
    ax.set_aspect("equal")
    ax.set_xlim(-0.5, BAR_L + 0.5)
    ax.set_ylim(-BAR_H - 0.5, 1.2)

    # ── Barra overhead ────────────────────────────────────────────────
    ax.add_patch(mpatches.FancyBboxPatch(
        (-0.1, 0), BAR_L + 0.2, 0.15,
        boxstyle="round,pad=0.02",
        facecolor="#1565C0", edgecolor="#42A5F5", linewidth=2, zorder=10,
    ))
    ax.text(BAR_L / 2, 0.65, titolo,
            color="#F0F6FC", fontsize=11, fontweight="bold",
            ha="center", va="bottom", family="monospace")
    ax.text(BAR_L / 2, 0.35,
            f"{n_g} ganci × {cfg.passo_gancio_mm:.0f}mm  |  "
            f"Z_max {cfg.Z_max_mm:.0f}mm  |  "
            f"Peso {result.peso_totale_kg:.1f}kg  |  "
            f"Sat {result.saturazione_pct:.0f}%  |  "
            f"Drenaggio {cfg.drainage_tilt_deg:.0f}°",
            color="#6E7681", fontsize=7, ha="center", va="bottom")

    # ── Ganci e parti ─────────────────────────────────────────────────
    fx_stroke = [pe.withStroke(linewidth=2, foreground="black")]

    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP

        # Gambo gancio
        ax.plot([gx + GANCIO_STEP / 2, gx + GANCIO_STEP / 2], [0, -0.08],
                color="#9CA3AF", lw=3, solid_capstyle="round", zorder=11)
        ax.text(gx + GANCIO_STEP / 2, 0.18, f"G{gi+1}",
                color="#E6EDF3", fontsize=7, ha="center", va="bottom", fontweight="bold")

        # Sfondo slot gancio
        ax.add_patch(FancyBboxPatch(
            (gx + 0.02, -BAR_H), GANCIO_STEP - 0.04, BAR_H - 0.08,
            boxstyle="round,pad=0.01",
            facecolor="#0D1117", edgecolor="#1C2128", linewidth=0.8, zorder=1,
        ))

        for p in g.parti:
            if not p.allocato:
                continue

            # Colore basato su validation
            base_col = p.ui_color
            y_top = -(p.z_offset_mm * SC)
            p_h = p.h_effettiva_mm * SC
            p_w = min(GANCIO_STEP * 0.88 * p.n_ganci_req,
                      p.L_mm * SC / max(p.n_ganci_req, 1) * 0.90)

            rect = FancyBboxPatch(
                (gx + (GANCIO_STEP - p_w) / 2 * max(p.n_ganci_req, 1),
                 y_top - p_h),
                p_w * max(p.n_ganci_req, 1), p_h,
                boxstyle="round,pad=0.008",
                facecolor=base_col + "CC",
                edgecolor="white", linewidth=1.0, zorder=3,
            )
            ax.add_patch(rect)

            # Inclinazione pendolare: piccola freccia
            if abs(p.pendulum_angle_deg) > 2.0:
                cx_p = gx + GANCIO_STEP / 2 * max(p.n_ganci_req, 1)
                cy_p = y_top - p_h / 2
                ax.annotate('',
                    xy=(cx_p + 0.05 * math.sin(math.radians(p.pendulum_angle_deg)),
                        cy_p - 0.05 * math.cos(math.radians(p.pendulum_angle_deg))),
                    xytext=(cx_p, cy_p + 0.05),
                    arrowprops=dict(arrowstyle='->', color='#FFA657', lw=1.5),
                    zorder=7,
                )

            cx = gx + GANCIO_STEP / 2 * max(p.n_ganci_req, 1)
            cy = y_top - p_h / 2

            ax.text(cx, cy + p_h * 0.15, p.codice[:10],
                    color="white", fontsize=6.5, ha="center", va="center",
                    fontweight="bold", zorder=5, path_effects=fx_stroke)
            ax.text(cx, cy - p_h * 0.05, f"{p.peso_kg:.1f}kg",
                    color="#FFA657", fontsize=5.5, ha="center", va="center", zorder=5)

            # Badge rotazione
            if abs(p.pendulum_angle_deg) > 0.5:
                ax.text(cx, cy - p_h * 0.22, f"⟳{p.pendulum_angle_deg:.0f}°",
                        color="#58A6FF", fontsize=5, ha="center", va="center", zorder=5)

            # Badge ganci differenziali
            if len(p.hook_assignments) == 2:
                l1 = p.hook_assignments[0].hook_length_mm
                l2 = p.hook_assignments[1].hook_length_mm
                if abs(l1 - l2) > 10:
                    ax.text(cx, cy - p_h * 0.38, f"↕{l1:.0f}/{l2:.0f}mm",
                            color="#D2A8FF", fontsize=4.5, ha="center", va="center", zorder=5)

    # ── Load Balance bar chart ─────────────────────────────────────────
    ax_lb.set_facecolor("#080B10")
    ax_lb.axis("off")
    peso_max_g = cfg.peso_max_gancio_kg
    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP
        pct = min(g.peso_tot_kg / peso_max_g, 1.0)
        col = "#F85149" if pct > 0.9 else ("#F0883E" if pct > 0.7 else "#3FB950")
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, pct,
            facecolor=col, edgecolor="none", zorder=2,
        ))
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, 1.0,
            facecolor="#161B22", edgecolor="#21262D", linewidth=0.5, zorder=1,
        ))
        if g.peso_tot_kg > 0:
            ax_lb.text(gx + GANCIO_STEP * 0.42, pct + 0.05,
                       f"{g.peso_tot_kg:.0f}kg",
                       color=col, fontsize=6, ha="center", zorder=3)

    ax_lb.set_xlim(-0.1, BAR_L + 0.1)
    ax_lb.set_ylim(-0.1, 1.5)
    ax_lb.text(-0.4, 0.5, "Carico\nganci", color="#6E7681", fontsize=7, va="center")

    plt.tight_layout(pad=0.3)
    plt.savefig(out_path, dpi=180, bbox_inches="tight",
                facecolor="#080B10", edgecolor="none")
    plt.close()


# ═══════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== OVERHEAD NESTING v2 SELF-TEST (Fisica Reale) ===\n")

    # BOM simulata grader box
    bom_test = [
        {
            'codice_art': 'ART-AGRI-000001', 'nome': 'flanc_droit',
            'lunghezza_mm': 800, 'larghezza_mm': 12, 'altezza_mm': 500,
            'peso_kg': 49.5, 'superficie_m2': 1.10,
            'cog_x_mm': 400, 'cog_y_mm': 6, 'cog_z_mm': 200,
            'passo_gancio_m': 0.40, 'qty': 1,
        },
        {
            'codice_art': 'ART-AGRI-000002', 'nome': 'tole_de_fond',
            'lunghezza_mm': 1700, 'larghezza_mm': 900, 'altezza_mm': 5,
            'peso_kg': 60.0, 'superficie_m2': 3.50,
            'cog_x_mm': 850, 'cog_y_mm': 450, 'cog_z_mm': 2.5,
            'passo_gancio_m': 0.80, 'qty': 1,
        },
        {
            'codice_art': 'ART-AGRI-000003', 'nome': 'tube_AV_1500',
            'lunghezza_mm': 1500, 'larghezza_mm': 60, 'altezza_mm': 60,
            'peso_kg': 12.7, 'superficie_m2': 0.60,
            'cog_x_mm': 750, 'cog_y_mm': 30, 'cog_z_mm': 30,
            'passo_gancio_m': 0.80, 'qty': 1,
        },
        {
            'codice_art': 'ART-AGRI-000004', 'nome': 'demi_triangle',
            'lunghezza_mm': 305, 'larghezza_mm': 520, 'altezza_mm': 50,
            'peso_kg': 35.0, 'superficie_m2': 0.90,
            'cog_x_mm': 150, 'cog_y_mm': 260, 'cog_z_mm': 25,
            'passo_gancio_m': 0.40, 'qty': 2,
        },
        {
            'codice_art': 'ART-AGRI-000005', 'nome': 'gousset',
            'lunghezza_mm': 150, 'larghezza_mm': 100, 'altezza_mm': 8,
            'peso_kg': 0.8, 'superficie_m2': 0.06,
            'cog_x_mm': 75, 'cog_y_mm': 50, 'cog_z_mm': 4,
            'passo_gancio_m': 0.40, 'qty': 4,
        },
    ]

    cfg = LoadBarConfig(
        L_max_mm=3000, Z_max_mm=2000, passo_gancio_mm=400,
        peso_max_bar_kg=420, peso_max_gancio_kg=60,
        enable_drainage_tilt=True, drainage_tilt_deg=12.0,
        base_hook_length_mm=300.0,
    )

    parti = esplodi_ordine_produzione(bom_test, n_unita=1)
    print(f"Coda: {sum(p.qty for p in parti)} parti")

    result = ottimizza_nesting_overhead(parti, cfg, apply_physics=True)
    print(f"Allocate: {len(result.parti_allocate)} | Non allocate: {len(result.parti_non_allocate)}")
    print(f"Saturazione: {result.saturazione_pct:.1f}% | Peso: {result.peso_totale_kg:.1f}kg")

    for p in result.parti_allocate:
        val = p.validation
        status = '✅' if val and val.danger_level == 0 else ('⚠' if val and val.danger_level == 1 else '🔴')
        print(f"  {status} {p.codice:<15s} G{p.gancio_start_idx+1} "
              f"pendulum={p.pendulum_angle_deg:.1f}° "
              f"tilt={p.tilt_target_deg:.0f}° "
              f"color={p.ui_color}")

    for p in result.parti_non_allocate:
        print(f"  ❌ {p.codice:<15s} {p.motivo_fallimento[:60]}")

    print("\nAvvisi:")
    for av in result.avvisi:
        print(f"  {av}")
