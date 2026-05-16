"""
nesting_catena.py — ENOROSSI Paint Optimizer v5
Genera vista 2D nesting stile CAD/laser della catena di verniciatura.

Ogni gancio è disegnato fisicamente (simbolo a T invertita + asta).
I pezzi sono silhouette 2D estratte dalla geometria 3D (trimesh)
e posizionati in scala millimetrica sulla catena.

Uso standalone:
    python nesting_catena.py

Uso da Flask:
    from nesting_catena import alloca_pezzi, render_nesting_png, PezzoNesting
"""

from __future__ import annotations
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from dataclasses import dataclass, field
from typing import List, Optional
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.affinity import translate

# ─── DATACLASS PEZZO ─────────────────────────────────────────────────────────

@dataclass
class PezzoNesting:
    cod:      str
    nome:     str
    L_mm:     float          # larghezza asse catena (mm)
    H_mm:     float          # altezza verticale (mm)
    P_mm:     float          # profondità fuori piano (mm)
    peso_kg:  float
    ganci_req: int           # ganci fisici occupati
    qty:      int
    colore:   str = ''       # hex, assegnato automaticamente se vuoto
    note:     str = ''
    # interni
    shape:    Optional[object] = field(default=None, repr=False)
    sw_mm:    float = 0.0    # larghezza silhouette reale (mm)
    sh_mm:    float = 0.0    # altezza silhouette reale (mm)

# ─── PALETTE ─────────────────────────────────────────────────────────────────

_PALETTE = [
    '#0D47A1','#4A148C','#880E4F','#1B5E20',
    '#BF360C','#263238','#006064','#4E342E',
    '#1A237E','#33691E','#37474F','#6A1B9A',
]

# ─── GENERATORI MESH 3D ──────────────────────────────────────────────────────

def _mesh_da_dim(L, H, P, ganci_req):
    """
    Genera una mesh 3D approssimata in base alle dimensioni del pezzo.
    Se trimesh non è disponibile, restituisce None.
    """
    try:
        import trimesh
        import trimesh.creation as tc

        if ganci_req >= 3:
            # Telaio a L
            v  = tc.box([max(L*0.04, 60), P*0.15, H])
            hb = tc.box([L, P*0.15, max(H*0.06, 80)])
            hb.apply_translation([L*0.46, 0, -H*0.47])
            cr = tc.box([max(L*0.04,50), P*0.15, H*0.4])
            cr.apply_translation([0, 0, -H*0.15])
            return trimesh.util.concatenate([v, hb, cr])

        elif ganci_req == 2:
            # Lastra con nervature
            base = tc.box([L, P, H])
            r1   = tc.box([L, P*1.5, H*0.04]); r1.apply_translation([0,0,H*0.2])
            r2   = tc.box([L*0.04, P, H]);     r2.apply_translation([-L*0.45,0,0])
            r3   = tc.box([L*0.04, P, H]);     r3.apply_translation([ L*0.45,0,0])
            return trimesh.util.concatenate([base, r1, r2, r3])

        else:
            # Forme varie in base al rapporto L/H
            ratio = L / max(H, 1)
            if ratio > 3:
                # Barra orizzontale con teste
                corpo = tc.box([L, P, H])
                t1 = tc.cylinder(radius=H*0.6, height=P, sections=20)
                t1.apply_translation([-L*0.45, 0, H*0.1])
                t2 = tc.cylinder(radius=H*0.5, height=P, sections=16)
                t2.apply_translation([L*0.45, 0, H*0.1])
                return trimesh.util.concatenate([corpo, t1, t2])
            elif ratio < 0.8:
                # Staffa U verticale
                sx = tc.box([L*0.08, P, H]);  sx.apply_translation([-L*0.46, 0, 0])
                dx = tc.box([L*0.08, P, H]);  dx.apply_translation([ L*0.46, 0, 0])
                fd = tc.box([L, P, H*0.08]);  fd.apply_translation([0, 0, -H*0.46])
                br = tc.box([L*0.6, P*1.3, H*0.5]); br.apply_translation([0, P*0.15, H*0.1])
                return trimesh.util.concatenate([sx, dx, fd, br])
            elif 0.8 <= ratio <= 1.5:
                # Flangia / disco
                r = min(L, H) * 0.48
                disco = tc.cylinder(radius=r, height=P, sections=36)
                perno = tc.cylinder(radius=r*0.22, height=P*3, sections=16)
                return trimesh.util.concatenate([disco, perno])
            else:
                # Lastra con bordi piegati
                la = tc.box([L, P, H])
                b1 = tc.box([L, P*0.3, H*0.06]); b1.apply_translation([0,-P*0.35,-H*0.47])
                b2 = tc.box([L, P*0.3, H*0.06]); b2.apply_translation([0,-P*0.35, H*0.47])
                return trimesh.util.concatenate([la, b1, b2])
    except Exception:
        return None


def _silhouette_da_mesh(mesh) -> Polygon:
    """Proiezione frontale reale (XZ) della mesh trimesh."""
    try:
        v = mesh.vertices
        tris = []
        for f in mesh.faces:
            pts = v[f][:, [0, 2]]   # X, Z
            try:
                p = Polygon(pts)
                if p.is_valid and p.area > 0.5:
                    tris.append(p)
            except Exception:
                pass
        if tris:
            u = unary_union(tris)
            bd = u.bounds
            return translate(u, -bd[0], -bd[1])
    except Exception:
        pass
    return None


def _silhouette_geometrica(L, H, ganci_req) -> Polygon:
    """
    Silhouette 2D costruita geometricamente (fallback senza trimesh).
    Forma realistica in base al tipo di pezzo.
    """
    ratio = L / max(H, 1)

    if ganci_req >= 3:
        # Telaio a L
        gw = max(L * 0.04, 50)
        gh = max(H * 0.06, 60)
        pts = [
            (0, 0), (L, 0), (L, gh),              # traversa bassa
            (gw, gh), (gw, H), (0, H),             # gamba sinistra
        ]
        return Polygon(pts)

    elif ganci_req == 2:
        # Lastra rettangolare con intaglio centrale
        outer = Polygon([(0,0),(L,0),(L,H),(0,H)])
        notch = Polygon([(L*0.3, H*0.1),(L*0.7, H*0.1),
                         (L*0.7, H*0.4),(L*0.3, H*0.4)])
        try:
            result = outer.difference(notch)
            if result.is_valid and not result.is_empty:
                return result
        except Exception:
            pass
        return outer

    else:
        if ratio > 3:
            # Barra con rigonfiamenti alle estremità
            r = H * 0.5
            import math
            angles = [i * math.pi / 8 for i in range(17)]
            left  = [(r*math.cos(a)-L*0.45+r, H*0.5+r*math.sin(a)) for a in angles]
            right = [(r*math.cos(a)+L*0.45-r, H*0.5+r*math.sin(a)) for a in reversed(angles)]
            pts   = left + [(L, H*0.3),(L, H*0.7)] + right + [(0, H*0.7),(0, H*0.3)]
            try:
                p = Polygon(pts)
                if p.is_valid:
                    return p
            except Exception:
                pass
            return Polygon([(0,0),(L,0),(L,H),(0,H)])

        elif ratio < 0.8:
            # Staffa U
            tw = max(L * 0.1, 15)
            pts = [
                (0,0),(L,0),(L,tw),
                (L-tw,tw),(L-tw,H),(tw,H),
                (tw,tw),(0,tw),
            ]
            return Polygon(pts)

        elif 0.8 <= ratio <= 1.3:
            # Forma ottagonale (flangia)
            cx, cy = L/2, H/2
            r = min(L, H) * 0.48
            cut = r * 0.29
            pts = [
                (cx-r+cut, cy-r), (cx+r-cut, cy-r),
                (cx+r, cy-r+cut), (cx+r, cy+r-cut),
                (cx+r-cut, cy+r), (cx-r+cut, cy+r),
                (cx-r, cy+r-cut), (cx-r, cy-r+cut),
            ]
            try:
                p = Polygon(pts)
                if p.is_valid:
                    return p
            except Exception:
                pass

        # Default: rettangolo
        return Polygon([(0,0),(L,0),(L,H),(0,H)])


def calcola_silhouette(pezzo: PezzoNesting) -> PezzoNesting:
    """Assegna la silhouette 2D al pezzo (da mesh 3D o geometrica)."""
    shape = None

    # Prova mesh 3D reale
    mesh = _mesh_da_dim(pezzo.L_mm, pezzo.H_mm, pezzo.P_mm, pezzo.ganci_req)
    if mesh is not None:
        shape = _silhouette_da_mesh(mesh)

    # Fallback geometrico
    if shape is None or shape.is_empty:
        shape = _silhouette_geometrica(pezzo.L_mm, pezzo.H_mm, pezzo.ganci_req)

    bd = shape.bounds
    pezzo.shape  = shape
    pezzo.sw_mm  = bd[2] - bd[0]
    pezzo.sh_mm  = bd[3] - bd[1]
    return pezzo


# ─── ALLOCAZIONE ─────────────────────────────────────────────────────────────

@dataclass
class BloccoGancio:
    pezzo:     PezzoNesting
    slot:      int      # 0..ganci_req-1
    y0_mm:     float    # posizione verticale (mm dall'alto)
    principale: bool = True


@dataclass
class Gancio:
    idx:      int
    blocchi:  List[BloccoGancio] = field(default_factory=list)
    peso_tot: float = 0.0
    y_top:    float = 0.0   # prossima posizione libera (mm)


def alloca_pezzi(
    pezzi: List[PezzoNesting],
    n_ganci: int,
    h_max_mm: float = 2000.0,
    peso_max_kg: float = 60.0,
    gap_mm: float = 30.0,
) -> List[Gancio]:
    """
    Alloca i pezzi sui ganci fisici.
    Pezzi grandi (multi-gancio) prima, poi pezzi singoli
    nel gancio più carico che ha ancora spazio (bin-packing).
    """
    # Assegna colori se mancanti
    for i, p in enumerate(pezzi):
        if not p.colore:
            p.colore = _PALETTE[i % len(_PALETTE)]
        # Calcola silhouette se non già calcolata
        if p.shape is None:
            calcola_silhouette(p)

    ganci = [Gancio(i) for i in range(n_ganci)]

    # Espandi per qty e ordina: prima multi-gancio pesanti
    queue = []
    for p in pezzi:
        for q in range(p.qty):
            queue.append((p, q))
    queue.sort(key=lambda x: (-x[0].ganci_req, -x[0].peso_kg))

    for p, q_idx in queue:
        ng = p.ganci_req
        ph = p.sh_mm
        placed = False

        if ng > 1:
            # Cerca N ganci consecutivi liberi con spazio
            for i in range(n_ganci - ng + 1):
                blk = ganci[i:i + ng]
                y0  = max(g.y_top for g in blk)
                pw  = p.peso_kg / ng
                if (y0 + ph <= h_max_mm and
                        all(g.peso_tot + pw <= peso_max_kg for g in blk)):
                    for si, g in enumerate(blk):
                        g.blocchi.append(BloccoGancio(p, si, y0, principale=(si == 0)))
                        g.peso_tot += pw
                        g.y_top = y0 + ph + gap_mm
                    placed = True
                    break

        if not placed:
            # Pezzo singolo (o fallback): bin-packing nel gancio più carico
            cands = [
                g for g in ganci
                if g.peso_tot + p.peso_kg <= peso_max_kg
                and g.y_top + ph <= h_max_mm
            ]
            if cands:
                best = max(cands, key=lambda g: g.peso_tot)
                best.blocchi.append(BloccoGancio(p, 0, best.y_top, principale=True))
                best.peso_tot += p.peso_kg
                best.y_top += ph + gap_mm

    return ganci


# ─── RENDERING ───────────────────────────────────────────────────────────────

def render_nesting_png(
    ganci:    List[Gancio],
    pezzi:    List[PezzoNesting],
    out_path: str,
    titolo:   str = 'PIANO VERNICIATURA — NESTING CATENA',
    commessa: str = '',
    n_ganci:  int  = 10,
    passo_mm: float = 400.0,
    h_max_mm: float = 2000.0,
    peso_max: float = 60.0,
):
    """
    Genera il PNG nesting.
    I ganci sono disegnati come simboli fisici (barra + uncino).
    I pezzi sono silhouette 2D in scala millimetrica.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import Polygon as MPoly, FancyBboxPatch
    import matplotlib.patheffects as pe

    # Scala: 1mm = sc unità plot
    sc   = 1 / 100.0
    CW   = passo_mm * sc          # larghezza colonna (unità plot)
    AH   = h_max_mm * sc          # altezza area
    GAP  = 0.22                   # gap tra colonne
    ML   = 1.0; MT = 1.8; MB = 1.5
    LW   = 7.5                    # larghezza legenda

    N = len(ganci)
    FW = ML + N * (CW + GAP) + LW + 0.5
    FH = MT + AH + MB

    fig, ax = plt.subplots(figsize=(FW * 2.4, FH * 2.4), dpi=150)
    ax.set_facecolor('#080B10')
    fig.patch.set_facecolor('#080B10')
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_xlim(-ML, N * (CW + GAP) + LW + 0.4)
    ax.set_ylim(-MB, AH + MT)

    # ── Titolo ──────────────────────────────────────────────────
    ax.text(N * (CW + GAP) / 2, AH + 1.30, titolo,
            color='#F0F6FC', fontsize=13, fontweight='bold',
            ha='center', va='bottom', family='monospace')
    sub = f'{commessa}   |   {N} ganci × {passo_mm:.0f}mm   |   H utile {h_max_mm:.0f}mm   |   scala 1:1 (mm)'
    ax.text(N * (CW + GAP) / 2, AH + 0.90, sub,
            color='#6E7681', fontsize=7.5, ha='center', va='bottom')

    # ── Rail catena ─────────────────────────────────────────────
    ry = AH + 0.38
    rx0 = -0.08
    rx1 = N * (CW + GAP) - GAP + 0.08
    # Doppia barra (profilo a I semplificato)
    ax.plot([rx0, rx1], [ry,      ry],      color='#58A6FF', lw=6,  solid_capstyle='round', zorder=14)
    ax.plot([rx0, rx1], [ry+0.06, ry+0.06], color='#2D6DB5', lw=2,  solid_capstyle='round', zorder=14)
    ax.text(-0.14, ry, '◀ INGRESSO', color='#58A6FF', fontsize=7.5, va='center', ha='right')
    ax.text(rx1 + 0.14, ry, 'USCITA ▶',  color='#58A6FF', fontsize=7.5, va='center', ha='left')

    # ── Ganci e pezzi ───────────────────────────────────────────
    for gi, g in enumerate(ganci):
        x0 = gi * (CW + GAP)
        gx = x0 + CW / 2

        # --- Simbolo gancio fisico ---
        # Asta verticale
        ax.plot([gx, gx], [AH, AH + 0.30],
                color='#8B949E', lw=3.5, solid_capstyle='round', zorder=15)
        # Traversa orizzontale (testa del gancio)
        ax.plot([gx - 0.20, gx + 0.20], [AH + 0.30, AH + 0.30],
                color='#8B949E', lw=5, solid_capstyle='round', zorder=15)
        # Uncino (curva a J in basso a destra)
        theta = np.linspace(np.pi, 0, 30)
        hook_r  = 0.08
        hook_cx = gx + 0.20 - hook_r
        hook_cy = AH + 0.30 - hook_r
        hx = hook_cx + hook_r * np.cos(theta)
        hy = hook_cy + hook_r * np.sin(theta)
        ax.plot(hx, hy, color='#8B949E', lw=5, solid_capstyle='round', zorder=15)
        # Punta uncino verso il basso
        ax.plot([hook_cx - hook_r, hook_cx - hook_r + 0.04],
                [hook_cy, hook_cy - 0.04],
                color='#8B949E', lw=5, solid_capstyle='round', zorder=15)

        # Numerazione
        ax.text(gx, AH + 0.72, f'G{gi + 1}',
                color='#E6EDF3', fontsize=9, ha='center', va='bottom', fontweight='bold')
        ax.text(gx, AH + 0.52, f'{gi * passo_mm / 1000:.2f}m',
                color='#6E7681', fontsize=6.5, ha='center', va='bottom')

        # Box area gancio
        over = g.peso_tot > peso_max
        ax.add_patch(FancyBboxPatch(
            (x0, 0), CW, AH,
            boxstyle='round,pad=0.04', lw=1.2,
            edgecolor='#F85149' if over else '#1C2128',
            facecolor='#0D1117', zorder=1))

        # Griglia altezza ogni 500mm
        for hh in [500, 1000, 1500]:
            yg = hh * sc
            ax.plot([x0 + 0.04, x0 + CW - 0.04], [yg, yg],
                    '--', color='#161B22', lw=0.7, zorder=2)
            ax.text(x0 + 0.05, yg + 0.025, f'{hh}mm',
                    color='#21262D', fontsize=4.5, va='bottom')

        # ── Pezzi (silhouette 2D) ──
        for b in g.blocchi:
            if not b.principale:
                continue
            p  = b.pezzo
            ng = p.ganci_req
            span_w = ng * CW + (ng - 1) * GAP   # larghezza span in plot

            shape = p.shape
            bds   = shape.bounds
            sw    = bds[2] - bds[0]   # larghezza silhouette mm
            sh    = bds[3] - bds[1]   # altezza silhouette mm

            # Scala X per riempire lo span (Y rimane proporzionale)
            scx  = span_w / (sw * sc) * 0.88
            y0p  = b.y0_mm * sc
            col  = p.colore

            polys = list(shape.geoms) if hasattr(shape, 'geoms') else [shape]
            for poly in polys:
                if poly.is_empty or not poly.is_valid:
                    continue

                raw = np.array(poly.exterior.coords)
                xs  = x0 + (raw[:, 0] - bds[0]) * sc * scx + span_w * 0.06
                ys  = y0p + (raw[:, 1] - bds[1]) * sc

                # Fill pezzo
                ax.add_patch(MPoly(np.column_stack([xs, ys]), closed=True,
                                   facecolor=col + 'E0', edgecolor='#FFFFFF',
                                   linewidth=1.2, zorder=3))
                # Bordo interno (spessore)
                ax.add_patch(MPoly(np.column_stack([xs, ys]), closed=True,
                                   facecolor='none', edgecolor=col,
                                   linewidth=3.0, alpha=0.30, zorder=2))

                # Fori (holes) se presenti
                for hole in poly.interiors:
                    hr  = np.array(hole.coords)
                    hxs = x0 + (hr[:, 0] - bds[0]) * sc * scx + span_w * 0.06
                    hys = y0p + (hr[:, 1] - bds[1]) * sc
                    ax.add_patch(MPoly(np.column_stack([hxs, hys]), closed=True,
                                       facecolor='#0D1117', edgecolor='#FFFFFF55',
                                       linewidth=0.6, zorder=4))

            # ── Label centrata sul pezzo ──
            cx  = x0 + span_w / 2
            cy  = y0p + sh * sc / 2
            fx  = [pe.withStroke(linewidth=3, foreground='black')]
            ax.text(cx, cy + sh * sc * 0.14, p.cod,
                    color='white', fontsize=7.5, ha='center', va='center',
                    fontweight='bold', zorder=6, path_effects=fx)
            ax.text(cx, cy - sh * sc * 0.08,
                    f'{int(sw)}×{int(sh)} mm',
                    color='#D1D5DB', fontsize=5.5, ha='center', va='center',
                    zorder=6, path_effects=fx)
            ax.text(cx, cy - sh * sc * 0.28,
                    f'{p.peso_kg:.1f} kg',
                    color='#FFA657', fontsize=5.5, ha='center', va='center',
                    fontweight='bold', zorder=6, path_effects=fx)
            if p.note:
                ax.text(cx, y0p + sh * sc * 0.04, p.note,
                        color='#FFD700', fontsize=4.5, ha='center', va='bottom',
                        style='italic', zorder=6, path_effects=fx)

        # ── Barra peso + valori ──
        pct = min(g.peso_tot / peso_max, 1.0)
        bc  = '#F85149' if pct > 1 else ('#F0883E' if pct > 0.8 else '#3FB950')
        ax.add_patch(mpatches.Rectangle((x0, -0.70), CW, 0.14,
                     facecolor='#161B22', zorder=3))
        ax.add_patch(mpatches.Rectangle((x0, -0.70), CW * pct, 0.14,
                     facecolor=bc, zorder=4))
        ax.text(gx, -0.78, f'{g.peso_tot:.0f} kg',
                color=bc, fontsize=7, ha='center', va='top', fontweight='bold')
        ax.text(gx, -1.02, f'{pct * 100:.0f}%',
                color='#6E7681', fontsize=6, ha='center', va='top')

    # ── Legenda destra ──────────────────────────────────────────
    lx = N * (CW + GAP) + 0.45
    ax.text(lx, AH + 0.05, 'COMPONENTI', color='#E6EDF3',
            fontsize=9, fontweight='bold', va='top')
    ax.plot([lx, lx + LW - 0.5], [AH - 0.14, AH - 0.14],
            color='#21262D', lw=0.7)

    pezzi_unici = list({p.cod: p for p in pezzi}.values())
    for yi, p in enumerate(pezzi_unici):
        ly = AH - 0.50 - yi * 0.75

        # Miniatura silhouette
        if p.shape and not p.shape.is_empty:
            bd2 = p.shape.bounds
            sw2 = bd2[2] - bd2[0]
            sh2 = bd2[3] - bd2[1]
            scm = min(0.35 / max(sw2 * sc, 0.01), 0.48 / max(sh2 * sc, 0.01))
            polys2 = list(p.shape.geoms) if hasattr(p.shape, 'geoms') else [p.shape]
            for poly2 in polys2:
                if poly2.is_empty: continue
                raw2 = np.array(poly2.exterior.coords)
                mxs  = lx + 0.02 + (raw2[:, 0] - bd2[0]) * sc * scm
                mys  = ly + 0.02 + (raw2[:, 1] - bd2[1]) * sc * scm
                ax.add_patch(MPoly(np.column_stack([mxs, mys]), closed=True,
                                   facecolor=p.colore + 'CC', edgecolor='white',
                                   linewidth=0.6, zorder=5))
        else:
            ax.add_patch(mpatches.Rectangle(
                (lx, ly), 0.35, 0.45,
                facecolor=p.colore + 'CC', edgecolor='white', lw=0.6, zorder=5))

        ax.text(lx + 0.50, ly + 0.38, p.cod,
                color='white', fontsize=8.5, fontweight='bold', va='center')
        ax.text(lx + 0.50, ly + 0.22, p.nome,
                color='#8B949E', fontsize=6.5, va='center')
        ax.text(lx + 0.50, ly + 0.06,
                f"{p.ganci_req}g · {p.peso_kg}kg · ×{p.qty}   "
                f"({int(p.sw_mm)}×{int(p.sh_mm)}mm)",
                color='#6E7681', fontsize=5.8, va='center')

    # Statistiche
    usati = sum(1 for g in ganci if g.peso_tot > 0)
    ptot  = sum(g.peso_tot for g in ganci)
    sy = AH - len(pezzi_unici) * 0.75 - 1.0
    ax.plot([lx, lx + LW - 0.5], [sy + 0.22, sy + 0.22], color='#21262D', lw=0.7)
    for lbl, val in [
        ('Ganci occupati',  f'{usati}/{N}  ({usati / N * 100:.0f}%)'),
        ('Peso totale slot', f'{ptot:.1f} kg'),
        ('Saturazione media',f'{ptot / N / peso_max * 100:.0f}%'),
        ('Lunghezza slot',   f'{N * passo_mm / 1000:.1f} m'),
    ]:
        ax.text(lx,       sy, lbl + ':', color='#6E7681', fontsize=7, va='top')
        ax.text(lx + 3.8, sy, val,       color='#E6EDF3', fontsize=7, va='top', fontweight='bold')
        sy -= 0.35

    plt.tight_layout(pad=0.2)
    plt.savefig(out_path, dpi=180, bbox_inches='tight',
                facecolor='#080B10', edgecolor='none')
    plt.close()


# ─── MAIN (test standalone) ──────────────────────────────────────────────────

if __name__ == '__main__':
    pezzi_test = [
        PezzoNesting('TEL-01',   'Telaio princ. L',  2000,1600,1800, 310.0, 3, 1),
        PezzoNesting('SPALL-DX', 'Spalla DX',          820, 900,  80,  38.0, 2, 1),
        PezzoNesting('SPALL-SX', 'Spalla SX',           820, 900,  80,  38.0, 2, 1),
        PezzoNesting('TENS-01',  'Tensionatore',         600, 350, 200,   8.5, 1, 2),
        PezzoNesting('CORT-01',  'Cortina guida',        400, 340,  60,   2.8, 1, 3),
        PezzoNesting('GUID-01',  'Guida catena U',       280, 200, 100,   1.4, 1, 4),
        PezzoNesting('FLAN-01',  'Flangia perno',         320, 300,  40,   4.2, 1, 2),
        PezzoNesting('DAD-ES',   'Dado esagonale',        200, 180,  80,   0.8, 1, 6),
    ]

    print("Calcolo silhouette 3D...")
    for p in pezzi_test:
        calcola_silhouette(p)
        print(f"  {p.cod}: {p.sw_mm:.0f}×{p.sh_mm:.0f}mm  forma={p.shape.geom_type}")

    ganci = alloca_pezzi(pezzi_test, n_ganci=10)
    print(f"\nAllocati {sum(len(g.blocchi) for g in ganci)} blocchi su 10 ganci")

    render_nesting_png(
        ganci, pezzi_test,
        out_path='/mnt/user-data/outputs/nesting_finale.png',
        titolo='PIANO VERNICIATURA — NESTING CATENA LOOP',
        commessa='RB100 · Rotopresse · Demo',
        n_ganci=10, passo_mm=400,
    )
    print("✓ Salvato: nesting_finale.png")
