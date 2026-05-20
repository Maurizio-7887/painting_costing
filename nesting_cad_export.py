"""
nesting_cad_export.py — ENOROSSI Paint Optimizer v5
Esportazione CAD 2D del piano nesting overhead.

Genera:
  • SVG  — visualizzazione web inline con silhouette reali e quote
  • DXF  — disegno tecnico ISO apribile in AutoCAD / DraftSight / FreeCAD

NOTA: usa SOLO bbox reali L×H dalle dimensioni estratte dal parser STEP.
      La pipeline mesh.section() (silhouette mesh vera) è prevista in Fase 2.

Dipendenze:
  matplotlib>=3.8.0   (già in requirements.txt)
  ezdxf>=1.2.0        (aggiungere a requirements.txt)
  numpy>=1.24.0       (già in requirements.txt)

Uso standalone:
  from nesting_cad_export import render_nesting_svg, render_nesting_dxf
  svg_bytes = render_nesting_svg(nesting_data, "Ordine #42 — Grader Box × 3")
  dxf_path  = render_nesting_dxf(nesting_data, "Ordine #42", output_path="/tmp/out.dxf")
"""

from __future__ import annotations
import io
import os
import math
import tempfile
from typing import Optional

import numpy as np

# ══════════════════════════════════════════════════════════════════
# COSTANTI GRAFICHE
# ══════════════════════════════════════════════════════════════════

# Palette industriale — gunmetal + safety colors (Dürr-standard)
COL_BG        = "#1C1F26"
COL_BAR       = "#00C851"       # verde safety = OK
COL_HOOK      = "#8892A0"
COL_GRID      = "#2A2E38"
COL_DIM       = "#F0883E"       # arancione per quote
COL_TEXT_LT   = "#E6EDF3"
COL_TEXT_MUTED= "#566880"
COL_WARN      = "#FF8C00"
COL_DANGER    = "#D32F2F"
COL_GAP_ANNOT = "#00C851"

# Palette colori pezzi (cycling)
PART_COLORS = [
    "#1565C0", "#00695C", "#6A1B9A", "#AD1457",
    "#37474F", "#558B2F", "#4527A0", "#00838F",
    "#BF360C", "#1B5E20", "#880E4F", "#0D47A1",
]

# Gap minimo visualizzato tra pezzi sullo stesso gancio (mm)
GAP_MINIMO_MM = 30.0
# Margine superiore del SVG (sopra la barra, mm equivalenti)
MARGINE_SUP_MM  = 200.0
# Margine laterale (mm)
MARGINE_LAT_MM  = 200.0
# Margine inferiore (mm)
MARGINE_INF_MM  = 300.0


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _hex2rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16)/255.0 for i in (0, 2, 4))

def _cycling_color(idx: int) -> str:
    return PART_COLORS[idx % len(PART_COLORS)]

def _fmt_mm(v: float) -> str:
    """Formatta mm: '3000' se intero, '123.5' se decimale."""
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


def _parse_nesting(nd: dict) -> dict:
    """
    Normalizza il nesting_data (dal DB JSON) in strutture interne.
    Ritorna un dict con chiavi:
      bar_L, bar_Z, passo, ganci: [{idx, x_mm, libero, parti:[{...}]}]
      kpi, avvisi, titolo_interno
    """
    bc = nd.get('bar_config', {})
    ganci_raw = nd.get('ganci', [])

    parsed_ganci = []
    color_idx = 0
    for g in ganci_raw:
        parti_raw = g.get('parti', [])
        parti = []
        z_cursor = 0.0
        for p in parti_raw:
            z_off = float(p.get('z_offset', z_cursor))
            l_mm  = float(p.get('L_mm', 300))
            h_mm  = float(p.get('H_mm', 200))
            w_mm  = float(p.get('W_mm', l_mm * 0.4))  # larghezza non sempre presente
            parti.append({
                'codice':   p.get('codice', '??'),
                'nome':     p.get('nome', ''),
                'L_mm':     l_mm,
                'H_mm':     h_mm,
                'W_mm':     w_mm,
                'peso_kg':  float(p.get('peso_kg', 0)),
                'sup_m2':   float(p.get('sup_m2', 0)),
                'z_offset': z_off,
                'rot_deg':  float(p.get('rot_deg', 0)),
                'colore':   p.get('colore') or _cycling_color(color_idx),
            })
            z_cursor = z_off + h_mm + GAP_MINIMO_MM
            color_idx += 1

        parsed_ganci.append({
            'idx':     int(g.get('idx', 0)),
            'x_mm':    float(g.get('x_mm', 0)),
            'peso_kg': float(g.get('peso_kg', 0)),
            'z_mm':    float(g.get('z_mm', 0)),
            'libero':  bool(g.get('libero', True)),
            'parti':   parti,
        })

    return {
        'bar_L':  float(bc.get('L_max_mm', 3000)),
        'bar_Z':  float(bc.get('Z_max_mm', 2000)),
        'passo':  float(bc.get('passo_mm', 400)),
        'ganci':  parsed_ganci,
        'kpi':    nd.get('kpi', {}),
        'avvisi': nd.get('avvisi', []),
    }


# ══════════════════════════════════════════════════════════════════
# SVG EXPORT via matplotlib
# ══════════════════════════════════════════════════════════════════

def render_nesting_svg(
    nesting_data: dict,
    titolo: str = "NESTING OVERHEAD",
    *,
    dark_mode: bool = True,
    dpi: int = 150,
) -> bytes:
    """
    Genera SVG del piano nesting con:
    - Barra di carico (3000 mm) con tacche ganci
    - Pezzi come rettangoli colorati con dimensioni reali L×H
    - Quote orizzontali (distanze tra bounding box adiacenti)
    - Quote verticali (z_offset di ogni pezzo)
    - Etichette codice + dimensioni + peso
    - KPI box (saturazione, peso tot, n_ganci)
    - Avvisi colorati in calce
    Ritorna bytes SVG.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.ticker as ticker
    from matplotlib.patches import FancyArrowPatch, Rectangle
    from matplotlib.lines import Line2D

    nd = _parse_nesting(nesting_data)
    bar_L = nd['bar_L']
    bar_Z = nd['bar_Z']
    ganci = nd['ganci']
    kpi   = nd['kpi']

    # ─── Layout canvas ──────────────────────────────────────────
    # Coordinate reali in mm: X=larghezza barra, Y=profondità (verso il basso)
    W_mm = bar_L + 2 * MARGINE_LAT_MM
    H_mm = bar_Z + MARGINE_SUP_MM + MARGINE_INF_MM

    # scala: ~1 pt = 1 mm fino a ~A3, poi si adatta
    scale = min(297.0 / H_mm * 25.4, 420.0 / W_mm * 25.4, 1.0)
    fig_w_in = W_mm * scale / 25.4
    fig_h_in = H_mm * scale / 25.4

    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in))

    # Sfondo
    bg = COL_BG if dark_mode else "#F5F5F5"
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    # Asse: X = larghezza (0…bar_L+margini), Y = profondità (0=in alto, bar_Z=in basso)
    ax.set_xlim(0, W_mm)
    ax.set_ylim(H_mm, 0)   # Y invertito: 0 in alto
    ax.set_aspect('equal')
    ax.axis('off')

    # Offset origine (margini)
    OX = MARGINE_LAT_MM
    OY = MARGINE_SUP_MM

    # ─── Griglia leggera (ogni 500 mm) ───────────────────────────
    grid_col = COL_GRID if dark_mode else "#E0E0E0"
    for gx in range(0, int(bar_L)+1, 500):
        ax.plot([OX+gx, OX+gx], [OY, OY+bar_Z],
                color=grid_col, lw=0.3, zorder=0)
    for gy in range(0, int(bar_Z)+1, 500):
        ax.plot([OX, OX+bar_L], [OY+gy, OY+gy],
                color=grid_col, lw=0.3, zorder=0)

    # ─── Bounding box barra (tratteggio) ─────────────────────────
    bar_rect = Rectangle((OX, OY), bar_L, bar_Z,
                          linewidth=0.8, edgecolor=COL_HOOK, facecolor='none',
                          linestyle='--', zorder=2)
    ax.add_patch(bar_rect)

    # ─── Barra di carico ─────────────────────────────────────────
    bar_h = 24.0   # spessore visivo barra (mm equivalenti)
    bar_patch = Rectangle((OX, OY - bar_h), bar_L, bar_h,
                           linewidth=1.0, edgecolor=COL_BAR,
                           facecolor=COL_BAR + "33", zorder=5)
    ax.add_patch(bar_patch)
    txt_col = COL_TEXT_LT if dark_mode else "#1C1F26"
    ax.text(OX + bar_L/2, OY - bar_h/2, "BARRA DI CARICO",
            ha='center', va='center', fontsize=5, color=COL_BAR,
            fontweight='bold', zorder=6)

    # Tacche gancio sulla barra
    for g in ganci:
        xg = OX + g['x_mm']
        # Tacca triangolare
        ax.plot([xg, xg], [OY - bar_h, OY],
                color=COL_BAR, lw=0.6, zorder=6)
        ax.text(xg, OY - bar_h - 8, f"G{g['idx']+1}",
                ha='center', va='bottom', fontsize=3.5, color=COL_HOOK, zorder=7)

    # ─── Pezzi ───────────────────────────────────────────────────
    part_patches = {}   # codice → patch per legenda
    for g in ganci:
        xg   = OX + g['x_mm']
        peso_g = g['peso_kg']

        for p in g['parti']:
            l, h  = p['L_mm'], p['H_mm']
            z_off = p['z_offset']
            col   = p['colore']
            codice= p['codice']

            # Angolo in alto a sinistra
            rx = xg - l/2
            ry = OY + z_off

            # Rettangolo pezzo
            pr = Rectangle((rx, ry), l, h,
                            linewidth=0.7, edgecolor=col,
                            facecolor=col + "44", zorder=10)
            ax.add_patch(pr)
            part_patches[codice] = pr

            # Cross-hair CoG (centro)
            cg_x = rx + l/2
            cg_y = ry + h/2
            cg_size = min(l, h) * 0.05
            ax.plot([cg_x - cg_size, cg_x + cg_size], [cg_y, cg_y],
                    color=col, lw=0.5, zorder=11)
            ax.plot([cg_x, cg_x], [cg_y - cg_size, cg_y + cg_size],
                    color=col, lw=0.5, zorder=11)

            # Etichetta codice (al centro del pezzo)
            fs_code = min(max(h * 0.12, 3.5), 6.5)
            ax.text(cg_x, cg_y - h*0.12, codice,
                    ha='center', va='center', fontsize=fs_code,
                    color=txt_col, fontweight='bold', zorder=12,
                    bbox=dict(boxstyle='round,pad=0.1', facecolor=bg+'cc',
                              edgecolor='none', alpha=0.7))

            # Dimensioni L×H sotto il codice
            fs_dim = max(fs_code * 0.75, 2.8)
            ax.text(cg_x, cg_y + h*0.12,
                    f"{_fmt_mm(l)}×{_fmt_mm(h)} mm",
                    ha='center', va='center', fontsize=fs_dim,
                    color=COL_TEXT_MUTED, zorder=12)

            # Peso
            ax.text(cg_x, cg_y + h*0.28,
                    f"{p['peso_kg']:.1f} kg",
                    ha='center', va='center', fontsize=fs_dim,
                    color=col, zorder=12)

    # ─── Quote orizzontali (gap tra bounding box adiacenti) ───────
    occupied = []  # lista (x_sin, x_des, codice)
    for g in ganci:
        xg = OX + g['x_mm']
        for p in g['parti']:
            occupied.append((xg - p['L_mm']/2, xg + p['L_mm']/2))

    if len(occupied) > 1:
        occupied_sorted = sorted(occupied, key=lambda t: t[0])
        dim_y = OY + bar_Z + 60   # quota sotto la zona
        dim_col = COL_DIM if dark_mode else "#E65100"
        for i in range(len(occupied_sorted) - 1):
            x1 = occupied_sorted[i][1]
            x2 = occupied_sorted[i+1][0]
            gap = x2 - x1
            if gap > 0:
                # Linee di estensione
                ax.plot([x1, x1], [OY + bar_Z + 10, dim_y + 8],
                        color=dim_col, lw=0.4, zorder=8)
                ax.plot([x2, x2], [OY + bar_Z + 10, dim_y + 8],
                        color=dim_col, lw=0.4, zorder=8)
                # Freccia bidirezionale
                ax.annotate('', xy=(x2, dim_y), xytext=(x1, dim_y),
                            arrowprops=dict(arrowstyle='<->', color=dim_col,
                                            lw=0.7), zorder=9)
                ax.text((x1+x2)/2, dim_y - 10,
                        f"∆{_fmt_mm(gap)}",
                        ha='center', va='bottom', fontsize=3.5,
                        color=dim_col, zorder=9)

    # ─── Quota barra totale ────────────────────────────────────────
    q_y = OY - bar_h - 40
    ax.annotate('', xy=(OX + bar_L, q_y), xytext=(OX, q_y),
                arrowprops=dict(arrowstyle='<->', color=COL_BAR, lw=0.8), zorder=8)
    ax.text(OX + bar_L/2, q_y - 10,
            f"L = {_fmt_mm(bar_L)} mm",
            ha='center', va='bottom', fontsize=5,
            color=COL_BAR, fontweight='bold', zorder=9)

    # ─── Quota altezza barra ──────────────────────────────────────
    q_x = OX + bar_L + 50
    ax.annotate('', xy=(q_x, OY + bar_Z), xytext=(q_x, OY),
                arrowprops=dict(arrowstyle='<->', color=COL_HOOK, lw=0.7), zorder=8)
    ax.text(q_x + 8, OY + bar_Z/2,
            f"H = {_fmt_mm(bar_Z)} mm",
            ha='left', va='center', fontsize=4.5,
            color=COL_HOOK, rotation=90, zorder=9)

    # ─── KPI box ─────────────────────────────────────────────────
    kpi_x = OX + bar_L + 80
    kpi_y = OY
    kpi_w = MARGINE_LAT_MM * 0.8
    kpi_h = bar_Z * 0.5

    ax.add_patch(Rectangle((kpi_x, kpi_y), kpi_w, kpi_h,
                            linewidth=0.5, edgecolor=COL_HOOK,
                            facecolor=(COL_BG if dark_mode else "#EEEEEE"),
                            zorder=5))

    kpi_items = [
        ("Saturazione", f"{kpi.get('saturazione_pct', 0):.1f}%",
         COL_BAR if kpi.get('saturazione_pct', 0) < 90 else COL_WARN),
        ("Peso totale",  f"{kpi.get('peso_totale_kg', 0):.1f} kg",
         COL_WARN if kpi.get('peso_totale_kg', 0) > 400 else txt_col),
        ("Ganci usati",  f"{kpi.get('ganci_usati', 0)}", txt_col),
        ("Pezzi allocati", f"{kpi.get('allocate', 0)}", COL_BAR),
    ]

    for ki, (lbl, val, col) in enumerate(kpi_items):
        ky = kpi_y + 30 + ki * 50
        ax.text(kpi_x + 10, ky, lbl,
                fontsize=4, color=COL_TEXT_MUTED, zorder=6)
        ax.text(kpi_x + 10, ky + 18, val,
                fontsize=7, color=col, fontweight='bold', zorder=6)

    # ─── Avvisi ───────────────────────────────────────────────────
    avvisi = nd.get('avvisi', [])
    av_y = OY + bar_Z + MARGINE_INF_MM * 0.35
    for av in avvisi[:4]:
        col_av = COL_DANGER if 'DANGER' in av.upper() or 'PERICOLO' in av.upper() else COL_WARN
        ax.text(OX, av_y, f"⚠ {av}",
                fontsize=4, color=col_av, zorder=6)
        av_y += 22

    # ─── Titolo ───────────────────────────────────────────────────
    ax.text(OX, OY - bar_h - 80, titolo,
            fontsize=7, color=txt_col, fontweight='bold', zorder=6)
    ax.text(OX, OY - bar_h - 65,
            f"Barra {_fmt_mm(bar_L)}×{_fmt_mm(bar_Z)} mm · "
            f"Passo ganci {_fmt_mm(nd['passo'])} mm",
            fontsize=4.5, color=COL_TEXT_MUTED, zorder=6)

    # ─── Riquadro titolo (cartiglio) ──────────────────────────────
    import datetime
    cart_y = H_mm - MARGINE_INF_MM * 0.25
    ax.add_patch(Rectangle((OX, cart_y), bar_L, MARGINE_INF_MM * 0.2,
                            linewidth=0.5, edgecolor=COL_HOOK,
                            facecolor='none', zorder=5))
    ax.text(OX + 10, cart_y + 8,
            f"ENOROSSI — Piano Nesting Overhead — {titolo}",
            fontsize=5, color=txt_col, zorder=6)
    ax.text(OX + bar_L - 10, cart_y + 8,
            datetime.datetime.now().strftime("Data: %d/%m/%Y"),
            fontsize=4.5, color=COL_TEXT_MUTED, ha='right', zorder=6)
    ax.text(OX + 10, cart_y + 22,
            "SCHEMA NON QUOTATO — Dimensioni in mm — Solo per uso interno",
            fontsize=3.5, color=COL_TEXT_MUTED, style='italic', zorder=6)

    # ─── Export SVG ──────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format='svg', bbox_inches='tight',
                facecolor=bg, dpi=dpi, transparent=False)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════
# DXF EXPORT via ezdxf
# ══════════════════════════════════════════════════════════════════

def render_nesting_dxf(
    nesting_data: dict,
    titolo: str = "NESTING OVERHEAD",
    output_path: Optional[str] = None,
) -> str:
    """
    Genera un file DXF ISO del piano nesting.
    Layer structure:
      0       — cornice e bordi
      BAR     — barra di carico e ganci
      PARTI   — rettangoli pezzi (colorati per tipo)
      QUOTE   — linee di quota ISO-128
      TESTO   — etichette codice / dimensioni / peso
      CARTIGLIO — riquadro titolo

    Ritorna il path del file DXF generato.
    Se output_path non specificato, usa tempfile.
    """
    try:
        import ezdxf
        from ezdxf.enums import TextEntityAlignment
    except ImportError:
        raise ImportError(
            "ezdxf non installato. Aggiungere 'ezdxf>=1.2.0' a requirements.txt"
        )

    nd = _parse_nesting(nesting_data)
    bar_L = nd['bar_L']
    bar_Z = nd['bar_Z']
    ganci = nd['ganci']
    kpi   = nd['kpi']

    # ─── Crea documento DXF ──────────────────────────────────────
    doc = ezdxf.new('R2010')
    doc.units = 4  # mm
    msp = doc.modelspace()

    # ─── Layer ───────────────────────────────────────────────────
    def add_layer(name, color, ltype='Continuous', lw=25):
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=ltype, lineweight=lw)

    add_layer('BAR',        3)   # verde
    add_layer('PARTI',      5)   # blu
    add_layer('PARTI_HATCH',253) # grigio chiaro
    add_layer('QUOTE',      1)   # rosso
    add_layer('TESTO',      7)   # bianco/nero
    add_layer('TESTO_MUTED',8)   # grigio
    add_layer('CARTIGLIO',  2)   # giallo
    add_layer('GRIGLIATO',  9, lw=13)

    # ─── Origine ─────────────────────────────────────────────────
    OX = MARGINE_LAT_MM
    OY = 0.0   # in DXF Y=0 è il basso, la barra è a Y=bar_Z+200

    # In DXF: barra in alto → coordinata Y = bar_Z (positivo verso l'alto)
    # Pezzi appendono verso il basso (Y decrescente)
    # Coordinata Y barra
    BAR_Y = bar_Z + 100.0

    def yd(z_offset: float) -> float:
        """Converti z_offset (dall'alto) in coordinata DXF Y (Y decresce verso il basso)."""
        return BAR_Y - z_offset

    # ─── Grigliato ────────────────────────────────────────────────
    for gx in range(0, int(bar_L)+1, 500):
        msp.add_line((OX+gx, BAR_Y), (OX+gx, BAR_Y - bar_Z),
                     dxfattribs={'layer': 'GRIGLIATO'})
    for gz in range(0, int(bar_Z)+1, 500):
        msp.add_line((OX, BAR_Y - gz), (OX+bar_L, BAR_Y - gz),
                     dxfattribs={'layer': 'GRIGLIATO'})

    # ─── Barra di carico ─────────────────────────────────────────
    BAR_H = 30.0  # spessore visivo barra mm
    msp.add_lwpolyline(
        [(OX, BAR_Y), (OX+bar_L, BAR_Y), (OX+bar_L, BAR_Y+BAR_H),
         (OX, BAR_Y+BAR_H), (OX, BAR_Y)],
        dxfattribs={'layer': 'BAR', 'closed': True}
    )
    msp.add_text(
        "BARRA DI CARICO",
        dxfattribs={'layer': 'BAR', 'height': 20, 'color': 3}
    ).set_placement((OX + bar_L/2, BAR_Y + 8), align=TextEntityAlignment.MIDDLE_CENTER)

    # ─── Ganci (tratteggio verticale) ─────────────────────────────
    for g in ganci:
        if g['libero']:
            continue
        xg = OX + g['x_mm']
        msp.add_line(
            (xg, BAR_Y), (xg, BAR_Y - g['z_mm'] - 20),
            dxfattribs={'layer': 'BAR', 'color': 3}
        )
        msp.add_text(
            f"G{g['idx']+1}\n{g['peso_kg']:.1f}kg",
            dxfattribs={'layer': 'TESTO_MUTED', 'height': 12}
        ).set_placement((xg, BAR_Y + BAR_H + 15), align=TextEntityAlignment.BOTTOM_CENTER)

    # ─── Pezzi ───────────────────────────────────────────────────
    # Color map ACI (AutoCAD Color Index) cycling
    ACI_COLORS = [5, 3, 6, 1, 4, 2, 30, 40, 50, 60, 70, 80]

    part_ci = {}   # codice → color index
    ci_idx  = 0

    for g in ganci:
        xg = OX + g['x_mm']
        for p in g['parti']:
            l     = p['L_mm']
            h     = p['H_mm']
            z_off = p['z_offset']
            codice= p['codice']
            nome  = p['nome'][:28]  # tronca per DXF

            if codice not in part_ci:
                part_ci[codice] = ACI_COLORS[ci_idx % len(ACI_COLORS)]
                ci_idx += 1
            aci = part_ci[codice]

            # Angoli rettangolo in DXF (Y verso l'alto, parti pendono verso il basso)
            x0, y0 = xg - l/2, yd(z_off) - h
            x1, y1 = xg + l/2, yd(z_off)

            # Rettangolo pezzo
            msp.add_lwpolyline(
                [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)],
                dxfattribs={'layer': 'PARTI', 'color': aci, 'closed': True, 'lineweight': 35}
            )

            # Hatch leggero
            try:
                hatch = msp.add_hatch(color=aci, dxfattribs={'layer': 'PARTI_HATCH'})
                hatch.set_pattern_fill('ANSI31', scale=15.0)
                hatch.paths.add_polyline_path(
                    [(x0,y0),(x1,y0),(x1,y1),(x0,y1)], is_closed=True
                )
                hatch.transparency = 0.82
            except Exception:
                pass  # hatch opzionale, non blocca

            # Testo codice centrato
            cx, cy = (x0+x1)/2, (y0+y1)/2
            h_testo = min(max(h * 0.10, 10), 22)
            msp.add_text(
                codice,
                dxfattribs={'layer': 'TESTO', 'height': h_testo, 'color': aci}
            ).set_placement((cx, cy + h_testo*0.6), align=TextEntityAlignment.MIDDLE_CENTER)

            msp.add_text(
                f"{_fmt_mm(l)}×{_fmt_mm(h)} mm   {p['peso_kg']:.1f}kg",
                dxfattribs={'layer': 'TESTO_MUTED', 'height': max(h*0.07, 8)}
            ).set_placement((cx, cy - h_testo*0.8), align=TextEntityAlignment.MIDDLE_CENTER)

            # ─── Quote lineari ISO-128 ───────────────────────────
            Q_OFFSET = 50.0  # distanza linea di quota dal pezzo

            # Quota larghezza (L_mm) — sotto il pezzo
            _add_linear_dim(doc, msp, (x0, y0-Q_OFFSET), (x1, y0-Q_OFFSET),
                            f"{_fmt_mm(l)}", layer='QUOTE', aci=1)

            # Quota altezza (H_mm) — a destra del pezzo
            _add_linear_dim(doc, msp, (x1+Q_OFFSET, y0), (x1+Q_OFFSET, y1),
                            f"{_fmt_mm(h)}", layer='QUOTE', aci=1, vertical=True)

    # ─── Quote gap tra hook adiacenti ─────────────────────────────
    ganci_occ = sorted(
        [g for g in ganci if not g['libero']],
        key=lambda g: g['x_mm']
    )
    for i in range(len(ganci_occ) - 1):
        g_a = ganci_occ[i]
        g_b = ganci_occ[i+1]
        # bordo destro del g_a
        max_L_a = max((p['L_mm'] for p in g_a['parti']), default=0)
        min_L_b = max((p['L_mm'] for p in g_b['parti']), default=0)
        x_a = OX + g_a['x_mm'] + max_L_a/2
        x_b = OX + g_b['x_mm'] - min_L_b/2
        gap = x_b - x_a
        if gap > 1:
            q_y_gap = BAR_Y - bar_Z - 80
            _add_linear_dim(doc, msp, (x_a, q_y_gap), (x_b, q_y_gap),
                            f"∆{_fmt_mm(gap)}", layer='QUOTE', aci=6)

    # ─── Quota lunghezza barra ────────────────────────────────────
    q_y_bar = BAR_Y + BAR_H + 70
    _add_linear_dim(doc, msp, (OX, q_y_bar), (OX+bar_L, q_y_bar),
                    f"L={_fmt_mm(bar_L)}", layer='QUOTE', aci=3)

    # ─── Cartiglio / Riquadro titolo ─────────────────────────────
    import datetime
    cart_x, cart_y_bottom = OX, BAR_Y - bar_Z - MARGINE_INF_MM + 20
    cart_h, cart_w = 120, bar_L

    msp.add_lwpolyline(
        [(cart_x, cart_y_bottom),
         (cart_x+cart_w, cart_y_bottom),
         (cart_x+cart_w, cart_y_bottom+cart_h),
         (cart_x, cart_y_bottom+cart_h),
         (cart_x, cart_y_bottom)],
        dxfattribs={'layer': 'CARTIGLIO', 'closed': True, 'lineweight': 50}
    )
    # Linee divisorie
    msp.add_line((cart_x, cart_y_bottom+60), (cart_x+cart_w, cart_y_bottom+60),
                 dxfattribs={'layer': 'CARTIGLIO', 'lineweight': 18})
    msp.add_line((cart_x+cart_w*0.6, cart_y_bottom),
                 (cart_x+cart_w*0.6, cart_y_bottom+60),
                 dxfattribs={'layer': 'CARTIGLIO', 'lineweight': 18})

    msp.add_text(
        f"ENOROSSI — PIANO NESTING OVERHEAD",
        dxfattribs={'layer': 'CARTIGLIO', 'height': 18, 'color': 2}
    ).set_placement((cart_x+10, cart_y_bottom+85), align=TextEntityAlignment.LEFT)

    msp.add_text(titolo,
                 dxfattribs={'layer': 'CARTIGLIO', 'height': 13}
    ).set_placement((cart_x+10, cart_y_bottom+67), align=TextEntityAlignment.LEFT)

    msp.add_text(
        f"Saturazione: {kpi.get('saturazione_pct',0):.1f}%    "
        f"Peso tot: {kpi.get('peso_totale_kg',0):.1f} kg    "
        f"Ganci: {kpi.get('ganci_usati',0)}",
        dxfattribs={'layer': 'CARTIGLIO', 'height': 11}
    ).set_placement((cart_x+10, cart_y_bottom+38), align=TextEntityAlignment.LEFT)

    msp.add_text(
        f"Barra {_fmt_mm(bar_L)}×{_fmt_mm(bar_Z)} mm — Passo ganci {_fmt_mm(nd['passo'])} mm",
        dxfattribs={'layer': 'CARTIGLIO', 'height': 11}
    ).set_placement((cart_x+10, cart_y_bottom+20), align=TextEntityAlignment.LEFT)

    msp.add_text(
        f"Data: {datetime.datetime.now().strftime('%d/%m/%Y')}",
        dxfattribs={'layer': 'CARTIGLIO', 'height': 12}
    ).set_placement((cart_x+cart_w-10, cart_y_bottom+38), align=TextEntityAlignment.RIGHT)

    msp.add_text(
        "USO INTERNO — Quote in mm",
        dxfattribs={'layer': 'TESTO_MUTED', 'height': 9}
    ).set_placement((cart_x+cart_w*0.62, cart_y_bottom+20), align=TextEntityAlignment.LEFT)

    # ─── Salva ────────────────────────────────────────────────────
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix='.dxf', prefix='nesting_')
        os.close(fd)

    doc.saveas(output_path)
    return output_path


# ──────────────────────────────────────────────────────────────────
# Helper quote lineari (semplificato senza entity DIMENSION)
# usa linee + frecce + testo (massima compatibilità DXF viewer)
# ──────────────────────────────────────────────────────────────────
def _add_linear_dim(doc, msp, p1, p2, label, *,
                    layer='QUOTE', aci=1, vertical=False,
                    ext_len=30.0, arrow_size=15.0):
    """
    Disegna una quota lineare con linee di estensione, frecce e testo.
    Usa solo primitive LINE + SOLID + TEXT per massima compatibilità.
    """
    x1, y1 = p1
    x2, y2 = p2

    # Linea di quota
    msp.add_line(p1, p2, dxfattribs={'layer': layer, 'color': aci, 'lineweight': 18})

    # Frecce (solidi triangolari)
    if not vertical:
        # Direzione: orizzontale
        size = arrow_size
        for (ax_, ay_), sign in [((x1, y1), +1), ((x2, y2), -1)]:
            msp.add_solid(
                [(ax_, ay_-size*0.3), (ax_+sign*size, ay_),
                 (ax_, ay_+size*0.3), (ax_+sign*size, ay_)],
                dxfattribs={'layer': layer, 'color': aci}
            )
        # Linee di estensione
        pass  # semplificate — le omette per non sporcare disegno
        # Testo
        msp.add_text(
            label,
            dxfattribs={'layer': layer, 'height': 18, 'color': aci}
        ).set_placement(
            ((x1+x2)/2, y1 - 22),
            align=__import__('ezdxf').enums.TextEntityAlignment.MIDDLE_CENTER
        )
    else:
        # Direzione: verticale
        size = arrow_size
        for (ax_, ay_), sign in [((x1, y1), +1), ((x2, y2), -1)]:
            msp.add_solid(
                [(ax_-size*0.3, ay_), (ax_, ay_+sign*size),
                 (ax_+size*0.3, ay_), (ax_, ay_+sign*size)],
                dxfattribs={'layer': layer, 'color': aci}
            )
        msp.add_text(
            label,
            dxfattribs={'layer': layer, 'height': 18, 'color': aci}
        ).set_placement(
            (x1 + 25, (y1+y2)/2),
            align=__import__('ezdxf').enums.TextEntityAlignment.MIDDLE_LEFT
        )
