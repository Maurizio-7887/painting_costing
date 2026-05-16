"""
overhead_nesting.py — ENOROSSI Paint Optimizer v5
PHASE 3: Overhead Suspension Packing — 3D Nesting Engine

Ottimizzatore di nesting pensile su barra overhead (catena verniciatura).

Il problema differisce dal classico 3D Bin Packing floor-based:
  - Il contenitore è una barra rigida in quota (asse X = lunghezza catena)
  - Z_max = clearance massima verso il basso (altezza utile sospensione)
  - Ogni parte appende ai GANCI distribuiti lungo la barra (passo fisso 400mm)
  - La GRAVITÀ fa ruotare ogni parte finché il CoG è esattamente sotto il punto di sospensione
  - Collision detection: buffer dinamico (swing ±5% della dimensione maggiore)

Algoritmi:
  1. CoG Alignment: ruota il mesh affinché il CoG sia sotto il punto d'aggancio
  2. Clearance Check: verifica che il pezzo stia nel Z_max
  3. Collision Detection: AABB semplificato + buffer swing
  4. Load Balancing: calcola momento flettente sulla barra + verifica peso trolley
  5. Optimizer: First-Fit Decreasing su peso + area proiezione
"""

from __future__ import annotations
import math
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Tuple
import numpy as np


# ═══════════════════════════════════════════════════════════════
# PARAMETRI IMPIANTO
# ═══════════════════════════════════════════════════════════════

@dataclass
class LoadBarConfig:
    """Configurazione della barra di carico overhead."""
    L_max_mm: float = 3000.0        # lunghezza barra (slot per macchina)
    Z_max_mm: float = 2000.0        # clearance massima verso il basso
    passo_gancio_mm: float = 400.0  # intervallo fisso tra ganci
    peso_max_bar_kg: float = 420.0  # capacità totale del trolley
    peso_max_gancio_kg: float = 60.0  # carico max per singolo gancio
    swing_buffer_pct: float = 0.05  # 5% buffer per oscillazione dinamica
    gap_verticale_mm: float = 50.0  # gap di sicurezza tra pezzi appesi

    @property
    def n_ganci(self) -> int:
        return max(1, int(self.L_max_mm / self.passo_gancio_mm))

    @property
    def posizioni_ganci_mm(self) -> List[float]:
        return [i * self.passo_gancio_mm for i in range(self.n_ganci)]


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class PartSuspension:
    """Parte fisica pronta per il nesting overhead."""
    codice: str
    nome: str
    famiglia: str = ""

    # Geometria bounding box (mm)
    L_mm: float = 0.0   # lunghezza (X nella barra)
    W_mm: float = 0.0   # larghezza (Y, profondità)
    H_mm: float = 0.0   # altezza (Z, verso il basso dopo sospensione)

    # Proprietà fisiche
    peso_kg: float = 0.0
    cog_x_mm: float = 0.0  # CoG relativo al bounding box della parte
    cog_y_mm: float = 0.0
    cog_z_mm: float = 0.0

    # Superficie per calcolo vernice
    superficie_m2: float = 0.0

    # Nesting
    n_ganci_req: int = 1
    qty: int = 1
    colore: str = "#455A64"
    note: str = ""

    # Risultato allocazione (compilato dall'ottimizzatore)
    gancio_start_idx: int = -1       # indice del primo gancio assegnato
    gancio_x_mm: float = 0.0        # posizione X sulla barra
    z_offset_mm: float = 0.0        # offset Z (distanza dal punto di sospensione al top del pezzo)
    rot_deg: float = 0.0            # rotazione applicata per CoG alignment
    allocato: bool = False
    motivo_fallimento: str = ""

    @property
    def swing_buffer_mm(self) -> float:
        """Buffer oscillazione dinamica (mm)."""
        return max(self.L_mm, self.W_mm) * 0.05

    @property
    def h_effettiva_mm(self) -> float:
        """Altezza effettiva appesa (con buffer verticale)."""
        return self.H_mm + self.swing_buffer_mm

    @property
    def l_effettiva_mm(self) -> float:
        """Larghezza effettiva (con buffer laterale)."""
        return self.L_mm + self.swing_buffer_mm * 2


@dataclass
class GancioState:
    """Stato di un gancio sulla barra overhead."""
    idx: int
    x_mm: float
    peso_tot_kg: float = 0.0
    z_occupata_mm: float = 0.0   # quota verticale attualmente occupata (da Z_max verso il basso)
    parti: List[PartSuspension] = field(default_factory=list)
    libero: bool = True

    @property
    def n_parti(self) -> int:
        return len(self.parti)

    def momento_flettente_Nm(self, x_centro_barra_mm: float) -> float:
        """Momento flettente al centro della barra (N·m)."""
        braccio_m = abs(self.x_mm - x_centro_barra_mm) / 1000.0
        return self.peso_tot_kg * 9.81 * braccio_m


@dataclass
class NestingResult:
    """Risultato completo del nesting overhead."""
    bar_config: LoadBarConfig
    ganci: List[GancioState] = field(default_factory=list)
    parti_allocate: List[PartSuspension] = field(default_factory=list)
    parti_non_allocate: List[PartSuspension] = field(default_factory=list)

    # KPI
    peso_totale_kg: float = 0.0
    saturazione_pct: float = 0.0
    ganci_usati: int = 0
    momento_max_Nm: float = 0.0
    avvisi: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializza per JSON/template."""
        return {
            'bar_config': {
                'L_max_mm': self.bar_config.L_max_mm,
                'Z_max_mm': self.bar_config.Z_max_mm,
                'n_ganci':  self.bar_config.n_ganci,
                'passo_mm': self.bar_config.passo_gancio_mm,
            },
            'ganci': [
                {
                    'idx':      g.idx,
                    'x_mm':     g.x_mm,
                    'peso_kg':  round(g.peso_tot_kg, 2),
                    'z_mm':     round(g.z_occupata_mm, 1),
                    'n_parti':  g.n_parti,
                    'libero':   g.libero,
                    'parti': [
                        {
                            'codice':   p.codice,
                            'nome':     p.nome,
                            'peso_kg':  p.peso_kg,
                            'H_mm':     p.H_mm,
                            'L_mm':     p.L_mm,
                            'z_offset': p.z_offset_mm,
                            'rot_deg':  p.rot_deg,
                            'colore':   p.colore,
                            'sup_m2':   p.superficie_m2,
                        } for p in g.parti
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
                {'codice': p.codice, 'nome': p.nome, 'motivo': p.motivo_fallimento}
                for p in self.parti_non_allocate
            ],
            'avvisi': self.avvisi,
        }


# ═══════════════════════════════════════════════════════════════
# COG ALIGNMENT ENGINE
# ═══════════════════════════════════════════════════════════════

def calcola_cog_alignment(parte: PartSuspension) -> Tuple[float, float]:
    """
    Calcola la rotazione ottimale del pezzo per allineare il CoG
    verticalmente sotto il punto di sospensione.

    Principio fisico:
      La gravità agisce lungo -Z (verso il basso).
      Il punto di aggancio è in (cog_x, cog_y, 0) nel sistema barra.
      Per equilibrio statico: CoG deve stare direttamente sotto il gancio.

    Restituisce (rot_deg, z_offset_mm):
      rot_deg    = rotazione da applicare al pezzo (intorno all'asse Y della barra)
      z_offset_mm = distanza verticale dal gancio al top del pezzo dopo rotazione
    """
    # CoG relativo al centro del bounding box
    cog_x_rel = parte.cog_x_mm - parte.L_mm / 2
    cog_z_rel = parte.cog_z_mm - parte.H_mm / 2

    # Angolo di inclinazione per portare il CoG sotto il gancio
    if abs(cog_z_rel) > 1e-6 and abs(cog_x_rel) < 1e-6:
        rot_deg = 0.0  # già verticale
    elif abs(cog_x_rel) > 1e-6:
        rot_deg = math.degrees(math.atan2(cog_x_rel, abs(cog_z_rel) + 1e-6))
        rot_deg = max(-45.0, min(45.0, rot_deg))  # limita a ±45°
    else:
        rot_deg = 0.0

    # Z offset = distanza dal gancio al bordo superiore del pezzo
    # (con rotazione applicata, il pezzo si inclina ma il CoG rimane sotto)
    rot_rad = math.radians(rot_deg)
    h_proiettata = parte.H_mm * math.cos(rot_rad) + parte.L_mm * abs(math.sin(rot_rad))
    z_offset_mm = 0.0  # il gancio è direttamente sul bordo superiore

    return round(rot_deg, 2), round(h_proiettata, 1)


# ═══════════════════════════════════════════════════════════════
# COLLISION DETECTION (AABB + buffer)
# ═══════════════════════════════════════════════════════════════

def check_collision_aabb(
    gancio: GancioState,
    parte: PartSuspension,
    z_proposta: float,
    cfg: LoadBarConfig
) -> Tuple[bool, str]:
    """
    Verifica collisione AABB (Axis-Aligned Bounding Box) con buffer swing.

    Controlla:
      1. Z clearance: il pezzo deve stare entro Z_max
      2. Peso gancio: non supera il limite del gancio
      3. Peso totale barra: non supera capacità trolley

    Restituisce (collisione_rilevata, messaggio).
    """
    # Check Z clearance
    z_bottom = z_proposta + parte.h_effettiva_mm
    if z_bottom > cfg.Z_max_mm:
        return True, f"Clearance Z: {z_bottom:.0f}mm > Z_max {cfg.Z_max_mm:.0f}mm"

    # Check peso gancio
    peso_gancio = gancio.peso_tot_kg + parte.peso_kg / max(parte.n_ganci_req, 1)
    if peso_gancio > cfg.peso_max_gancio_kg:
        return True, f"Peso gancio: {peso_gancio:.1f}kg > max {cfg.peso_max_gancio_kg:.0f}kg"

    return False, ""


# ═══════════════════════════════════════════════════════════════
# LOAD BALANCE ANALYZER
# ═══════════════════════════════════════════════════════════════

def analizza_load_balance(ganci: List[GancioState], cfg: LoadBarConfig) -> dict:
    """
    Analizza la distribuzione del carico sulla barra overhead.

    Calcola:
      - Peso totale e distribuzione percentuale per gancio
      - Momento flettente massimo (N·m) al centro della barra
      - Verifica limite peso totale trolley
      - Centro di massa del carico complessivo
    """
    peso_tot = sum(g.peso_tot_kg for g in ganci)
    x_centro = cfg.L_max_mm / 2.0

    momenti = [g.momento_flettente_Nm(x_centro) for g in ganci]
    momento_max = max(momenti) if momenti else 0.0

    # Centro di massa del carico
    if peso_tot > 0:
        x_com = sum(g.x_mm * g.peso_tot_kg for g in ganci) / peso_tot
    else:
        x_com = x_centro

    sbilancio_mm = abs(x_com - x_centro)
    sbilancio_pct = sbilancio_mm / (cfg.L_max_mm / 2.0) * 100.0 if cfg.L_max_mm > 0 else 0.0

    avvisi = []
    if peso_tot > cfg.peso_max_bar_kg:
        avvisi.append(f"⚠️ Peso totale {peso_tot:.1f}kg supera la capacità trolley {cfg.peso_max_bar_kg:.0f}kg")
    if sbilancio_pct > 30:
        avvisi.append(f"⚠️ Sbilancio carico {sbilancio_pct:.0f}% — centro massa a {x_com:.0f}mm")
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
# MAIN OPTIMIZER: First-Fit Decreasing + CoG Alignment
# ═══════════════════════════════════════════════════════════════

def ottimizza_nesting_overhead(
    parti: List[PartSuspension],
    cfg: LoadBarConfig,
    strategia: str = 'ffd_peso'
) -> NestingResult:
    """
    Ottimizzatore di nesting overhead pensile.

    Strategie:
      'ffd_peso'   : First-Fit Decreasing per peso (parte più pesante prima)
      'ffd_volume' : First-Fit Decreasing per volume (parte più grande prima)
      'ffd_ganci'  : First-Fit Decreasing per numero di ganci richiesti

    Algoritmo per ogni parte:
      1. Calcola CoG alignment → rotazione ottimale + H proiettata
      2. Espandi per qty
      3. Cerca blocco di ganci consecutivi che soddisfi:
         - n_ganci_req ganci liberi/disponibili
         - clearance Z sufficiente
         - peso gancio sotto limite
      4. Alloca o registra fallimento
      5. Calcola load balance finale
    """
    # Inizializza ganci
    ganci = [
        GancioState(idx=i, x_mm=pos)
        for i, pos in enumerate(cfg.posizioni_ganci_mm)
    ]

    # Espandi parti per qty e applica CoG alignment
    coda: List[PartSuspension] = []
    for p in parti:
        rot_deg, h_proj = calcola_cog_alignment(p)
        for _ in range(p.qty):
            import copy
            pc = copy.copy(p)
            pc.qty = 1
            pc.rot_deg = rot_deg
            # Aggiorna H con la proiezione dopo rotazione
            pc.H_mm = h_proj
            coda.append(pc)

    # Ordina (First-Fit Decreasing)
    sort_keys = {
        'ffd_peso':   lambda x: -x.peso_kg,
        'ffd_volume': lambda x: -(x.L_mm * x.W_mm * x.H_mm),
        'ffd_ganci':  lambda x: (-x.n_ganci_req, -x.peso_kg),
    }
    coda.sort(key=sort_keys.get(strategia, sort_keys['ffd_peso']))

    allocate: List[PartSuspension] = []
    non_allocate: List[PartSuspension] = []

    for parte in coda:
        n_req = max(1, parte.n_ganci_req)
        allocata = False

        # Cerca blocco di n_req ganci consecutivi con spazio
        for start_i in range(len(ganci) - n_req + 1):
            blocco = ganci[start_i:start_i + n_req]

            # z proposta = massimo z_occupata nel blocco (parti già presenti)
            z_proposta = max(g.z_occupata_mm for g in blocco) + cfg.gap_verticale_mm

            # Controlla collisione su tutti i ganci del blocco
            collisione = False
            for g in blocco:
                coll, msg = check_collision_aabb(g, parte, z_proposta, cfg)
                if coll:
                    collisione = True
                    parte.motivo_fallimento = msg
                    break

            if not collisione:
                # Allocazione
                peso_per_gancio = parte.peso_kg / n_req
                z_nuova = z_proposta + parte.h_effettiva_mm + cfg.gap_verticale_mm
                for g in blocco:
                    g.peso_tot_kg += peso_per_gancio
                    g.z_occupata_mm = z_nuova
                    g.libero = False
                    g.parti.append(parte)

                parte.gancio_start_idx = start_i
                parte.gancio_x_mm = blocco[0].x_mm
                parte.z_offset_mm = round(z_proposta, 1)
                parte.allocato = True
                allocate.append(parte)
                allocata = True
                break

        if not allocata:
            if not parte.motivo_fallimento:
                parte.motivo_fallimento = "Nessuno slot disponibile (peso/clearance)"
            non_allocate.append(parte)

    # ── KPI finali ──
    ganci_usati = sum(1 for g in ganci if not g.libero)
    peso_tot = sum(g.peso_tot_kg for g in ganci)
    sat_pct = ganci_usati / len(ganci) * 100.0 if ganci else 0.0
    x_centro = cfg.L_max_mm / 2.0
    momento_max = max((g.momento_flettente_Nm(x_centro) for g in ganci), default=0.0)

    avvisi = []
    if peso_tot > cfg.peso_max_bar_kg:
        avvisi.append(f"⚠️ Peso {peso_tot:.1f}kg > capacità trolley {cfg.peso_max_bar_kg:.0f}kg")
    if non_allocate:
        avvisi.append(f"⚠️ {len(non_allocate)} parti non allocate — considera più slot/barre")
    if sat_pct < 30:
        avvisi.append(f"💡 Saturazione bassa ({sat_pct:.0f}%) — aggiungi pezzi o riduci lo slot")
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

    Prende una BOM (lista di dict con campi del ParseResult)
    e N unità da produrre, restituisce la coda esplosa di PartSuspension
    pronta per il nesting.

    Input dict keys: codice_art, nome, superficie_m2, peso_kg,
                     lunghezza_mm, larghezza_mm, altezza_mm,
                     passo_gancio_m, complessita, qty (per unità)
    """
    COLORI_TIPO: Dict[str, str] = {
        'frame': '#0D47A1', 'box': '#0D47A1', 'flanc': '#1565C0',
        'tube': '#37474F', 'dent': '#4E342E', 'tine': '#4E342E',
        'triangle': '#1B5E20', 'gousset': '#558B2F', 'bracket': '#558B2F',
        'plate': '#880E4F', 'plat': '#880E4F', 'bolt': '#616161',
        'vis': '#616161', 'ecrou': '#616161',
    }

    coda: List[PartSuspension] = []

    for item in bom:
        qty_unitaria = item.get('qty', 1)
        qty_totale = qty_unitaria * n_unita

        L = float(item.get('lunghezza_mm', 500))
        W = float(item.get('larghezza_mm', 300))
        H = float(item.get('altezza_mm', 100))
        passo_m = float(item.get('passo_gancio_m', 0.4))
        n_ganci = max(1, round(passo_m / 0.4))

        # Colore per tipo
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
            cog_x_mm=L / 2.0,
            cog_y_mm=W / 2.0,
            cog_z_mm=H * 0.4,   # CoG leggermente sotto il centro (struttura)
            superficie_m2=float(item.get('superficie_m2', 0.1)),
            n_ganci_req=n_ganci,
            qty=qty_totale,
            colore=colore,
        )
        coda.append(parte)

    return coda


# ═══════════════════════════════════════════════════════════════
# RENDER PNG (overhead view)
# ═══════════════════════════════════════════════════════════════

def render_overhead_png(result: NestingResult, out_path: str,
                        titolo: str = "PIANO NESTING OVERHEAD") -> None:
    """
    Genera PNG visualizzazione nesting overhead:
    - Vista frontale (X = lunghezza barra, Y = altezza sospensione)
    - Barra overhead in blu, ganci, parti appese colorate
    - Load balance bar per ogni gancio
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    except ImportError:
        return  # matplotlib non disponibile

    cfg = result.bar_config
    SC = 1 / 1000.0  # mm → unità grafiche

    n_g = len(result.ganci)
    BAR_L = cfg.L_max_mm * SC
    BAR_H = cfg.Z_max_mm * SC
    GANCIO_STEP = cfg.passo_gancio_mm * SC

    FW = max(14.0, BAR_L * 2.5 + 3.0)
    FH = BAR_H * 2.5 + 2.5

    fig, (ax, ax_lb) = plt.subplots(
        2, 1, figsize=(FW, FH + 1.5),
        gridspec_kw={'height_ratios': [4, 1]},
        dpi=150
    )
    for a in (ax, ax_lb):
        a.set_facecolor("#080B10")
    fig.patch.set_facecolor("#080B10")

    ax.axis("off")
    ax.set_aspect("equal")
    ax.set_xlim(-0.5, BAR_L + 0.5)
    ax.set_ylim(-BAR_H - 0.5, 1.2)

    # ── Barra overhead ──
    ax.add_patch(mpatches.FancyBboxPatch(
        (-0.1, 0), BAR_L + 0.2, 0.15,
        boxstyle="round,pad=0.02",
        facecolor="#1565C0", edgecolor="#42A5F5", linewidth=2, zorder=10
    ))
    ax.text(BAR_L / 2, 0.65, titolo,
            color="#F0F6FC", fontsize=11, fontweight="bold",
            ha="center", va="bottom", family="monospace")
    ax.text(BAR_L / 2, 0.35,
            f"{n_g} ganci × {cfg.passo_gancio_mm:.0f}mm  |  "
            f"Z_max {cfg.Z_max_mm:.0f}mm  |  "
            f"Peso {result.peso_totale_kg:.1f}kg  |  "
            f"Sat {result.saturazione_pct:.0f}%",
            color="#6E7681", fontsize=7.5, ha="center", va="bottom")

    # ── Ganci e parti ──
    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP

        # Gambo gancio
        ax.plot([gx + GANCIO_STEP / 2, gx + GANCIO_STEP / 2], [0, -0.08],
                color="#9CA3AF", lw=3, solid_capstyle="round", zorder=11)

        # Label gancio
        ax.text(gx + GANCIO_STEP / 2, 0.18, f"G{gi+1}",
                color="#E6EDF3", fontsize=7, ha="center", va="bottom", fontweight="bold")

        # Area gancio (sfondo)
        ax.add_patch(FancyBboxPatch(
            (gx + 0.02, -BAR_H), GANCIO_STEP - 0.04, BAR_H - 0.08,
            boxstyle="round,pad=0.01",
            facecolor="#0D1117", edgecolor="#1C2128", linewidth=0.8, zorder=1
        ))

        # Parti appese
        for p in g.parti:
            if not p.allocato:
                continue
            y_top = -(p.z_offset_mm * SC)
            p_h = p.H_mm * SC
            p_w = min(GANCIO_STEP * 0.88 * p.n_ganci_req,
                      p.L_mm * SC / max(p.n_ganci_req, 1) * 0.90)

            rect = FancyBboxPatch(
                (gx + (GANCIO_STEP - p_w) / 2 * max(p.n_ganci_req, 1),
                 y_top - p_h),
                p_w * max(p.n_ganci_req, 1), p_h,
                boxstyle="round,pad=0.008",
                facecolor=p.colore + "CC",
                edgecolor="white", linewidth=1.0, zorder=3
            )
            ax.add_patch(rect)

            # Label parte
            cy = y_top - p_h / 2
            cx = gx + GANCIO_STEP / 2 * max(p.n_ganci_req, 1)
            ax.text(cx, cy + p_h * 0.15, p.codice[:10],
                    color="white", fontsize=6.5, ha="center", va="center",
                    fontweight="bold", zorder=5)
            ax.text(cx, cy - p_h * 0.12, f"{p.peso_kg:.1f}kg",
                    color="#FFA657", fontsize=5.5, ha="center", va="center", zorder=5)

    # ── Load Balance bar chart ──
    ax_lb.set_facecolor("#080B10")
    ax_lb.axis("off")
    peso_max_g = cfg.peso_max_gancio_kg
    for gi, g in enumerate(result.ganci):
        gx = gi * GANCIO_STEP
        pct = min(g.peso_tot_kg / peso_max_g, 1.0)
        col = "#F85149" if pct > 0.9 else ("#F0883E" if pct > 0.7 else "#3FB950")
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, pct,
            facecolor=col, edgecolor="none", zorder=2
        ))
        ax_lb.add_patch(mpatches.Rectangle(
            (gx, 0), GANCIO_STEP * 0.85, 1.0,
            facecolor="#161B22", edgecolor="#21262D", linewidth=0.5, zorder=1
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
    # Test con grader_box BOM simulata
    bom_test = [
        {'codice_art': 'ART-AGRI-000001', 'nome': 'Box_frame_A',    'lunghezza_mm': 1800, 'larghezza_mm': 900, 'altezza_mm': 600, 'peso_kg': 85.0, 'superficie_m2': 4.20, 'passo_gancio_m': 0.80, 'qty': 1},
        {'codice_art': 'ART-AGRI-000002', 'nome': 'Flanc droit',    'lunghezza_mm': 800,  'larghezza_mm': 12,  'altezza_mm': 500, 'peso_kg': 15.0, 'superficie_m2': 1.10, 'passo_gancio_m': 0.40, 'qty': 2},
        {'codice_art': 'ART-AGRI-000003', 'nome': 'tube_AV_1500',   'lunghezza_mm': 1500, 'larghezza_mm': 80,  'altezza_mm': 80,  'peso_kg': 12.0, 'superficie_m2': 0.60, 'passo_gancio_m': 0.80, 'qty': 1},
        {'codice_art': 'ART-AGRI-000004', 'nome': 'dent',           'lunghezza_mm': 300,  'larghezza_mm': 60,  'altezza_mm': 20,  'peso_kg': 1.5,  'superficie_m2': 0.08, 'passo_gancio_m': 0.40, 'qty': 12},
        {'codice_art': 'ART-AGRI-000005', 'nome': 'demi_triangle',  'lunghezza_mm': 600,  'larghezza_mm': 400, 'altezza_mm': 80,  'peso_kg': 18.0, 'superficie_m2': 0.90, 'passo_gancio_m': 0.40, 'qty': 2},
        {'codice_art': 'ART-AGRI-000006', 'nome': 'tole de fond',   'lunghezza_mm': 1700, 'larghezza_mm': 900, 'altezza_mm': 5,   'peso_kg': 60.0, 'superficie_m2': 3.50, 'passo_gancio_m': 0.80, 'qty': 1},
    ]

    cfg = LoadBarConfig(L_max_mm=3000, Z_max_mm=2000, passo_gancio_mm=400,
                        peso_max_bar_kg=420, peso_max_gancio_kg=60)

    parti = esplodi_ordine_produzione(bom_test, n_unita=2)
    print(f"Coda esplosa: {sum(p.qty for p in parti)} istanze totali")

    result = ottimizza_nesting_overhead(parti, cfg)
    print(f"Allocate: {len(result.parti_allocate)} / Non allocate: {len(result.parti_non_allocate)}")
    print(f"Saturazione: {result.saturazione_pct:.1f}%  |  Peso: {result.peso_totale_kg:.1f}kg")
    print(f"Momento max: {result.momento_max_Nm:.0f} N·m")
    for av in result.avvisi:
        print(f"  {av}")
