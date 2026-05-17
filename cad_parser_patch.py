"""
cad_parser_patch.py — ENOROSSI Paint Optimizer v5
PATCH per cad_parser.py: aggiunge il rilevamento fori di aggancio.

COME APPLICARE QUESTA PATCH:
  1. Nel campo `PartGeometry`, aggiungi i campi hanging_points e n_hanging_holes.
  2. In `_analizza_stl_file()`, chiama _rileva_fori_aggancio() dopo il parsing mesh.
  3. In `analizza_zip_stl()`, passa lo stl_path a ogni PartGeometry per il viewer 3D.

I campi aggiunti sono retrocompatibili: hanno default vuoto/0.

───────────────────────────────────────────────────────────────────────────────
MODIFICHE A PartGeometry (aggiungere dopo `mesh_presente: bool = False`):
───────────────────────────────────────────────────────────────────────────────

    # Punti di aggancio rilevati dalla mesh STL
    hanging_holes: list = field(default_factory=list)   # List[dict] con x,y,z,diam
    n_hanging_holes: int = 0                             # contatore fori validi
    stl_path: str = ''                                   # path locale al file STL

───────────────────────────────────────────────────────────────────────────────
FUNZIONE DA AGGIUNGERE in cad_parser.py (prima di _analizza_stl_file):
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import os
import math
from typing import List, Dict, Optional
import numpy as np


def _rileva_fori_aggancio(mesh, n_sections: int = 25, z_scan_pct: float = 0.65) -> List[dict]:
    """
    Rileva i fori di aggancio fisicamente validi per il nesting overhead.

    Integra il motore physics_hanging.detect_hanging_holes() direttamente
    durante il parsing del file STL, così i dati sono disponibili nella BOM
    senza richiedere un secondo passaggio.

    Il risultato è una lista di dict JSON-serializzabili:
      [{'x_mm': ..., 'y_mm': ..., 'z_mm': ..., 'diameter_mm': ...,
        'confidence': ..., 'source': 'detected'}, ...]

    Se il modulo physics_hanging non è disponibile, restituisce [].
    """
    try:
        from physics_hanging import detect_hanging_holes
        holes = detect_hanging_holes(
            mesh_or_path=mesh,
            n_sections=n_sections,
            z_scan_pct=z_scan_pct,
        )
        return [
            {
                'x_mm':        round(h.x_mm, 1),
                'y_mm':        round(h.y_mm, 1),
                'z_mm':        round(h.z_mm, 1),
                'diameter_mm': round(h.diameter_mm, 1),
                'confidence':  round(h.confidence, 3),
                'source':      h.source,
                'normal':      list(h.normal),
            }
            for h in holes
        ]
    except Exception:
        return []


# ───────────────────────────────────────────────────────────────────────────
# SNIPPET DA INSERIRE IN _analizza_stl_file(), dopo trimesh.repair.fill_holes(mesh):
# ───────────────────────────────────────────────────────────────────────────

SNIPPET_RILEVA_FORI = """
        # ── Rilevamento fori di aggancio (physics_hanging) ──────────────────
        hanging_holes = _rileva_fori_aggancio(mesh)
"""

SNIPPET_PARTGEOMETRY_EXTRA_FIELDS = """
        hanging_holes=hanging_holes,
        n_hanging_holes=len(hanging_holes),
        stl_path=path_stl,
"""

# ───────────────────────────────────────────────────────────────────────────
# DICT PATCH per parse_result_to_dict() — aggiunge hanging_holes al JSON
# ───────────────────────────────────────────────────────────────────────────

EXTRA_FIELDS_IN_DICT = """
                'hanging_holes':   p.hanging_holes,
                'n_hanging_holes': p.n_hanging_holes,
                'stl_path':        p.stl_path,
"""


def applica_patch_automatica(cad_parser_path: str) -> bool:
    """
    Applica automaticamente la patch al file cad_parser.py originale.
    Modifica minima e sicura: aggiunge solo i nuovi campi, non tocca la logica esistente.

    Returns True se applicata con successo, False se già applicata o errore.
    """
    try:
        with open(cad_parser_path, 'r', encoding='utf-8') as f:
            source = f.read()

        # Verifica che non sia già patchato
        if 'hanging_holes' in source and 'n_hanging_holes' in source:
            print(f"Patch già applicata a {cad_parser_path}")
            return False

        # 1. Aggiungi importazione della funzione patch (dopo imports numpy)
        import_line = "import numpy as np\nwarnings.filterwarnings(\"ignore\")"
        patch_import = (
            "import numpy as np\n"
            "warnings.filterwarnings(\"ignore\")\n\n"
            "# ── PATCH physics_hanging ───────────────────────────────────────\n"
            "try:\n"
            "    from cad_parser_patch import _rileva_fori_aggancio\n"
            "except ImportError:\n"
            "    def _rileva_fori_aggancio(mesh, **kw): return []\n"
        )
        source = source.replace(import_line, patch_import, 1)

        # 2. Aggiungi campi a PartGeometry (dopo mesh_presente: bool = False)
        old_field = "    mesh_presente: bool = False        # True se trimesh è riuscito"
        new_field = (
            "    mesh_presente: bool = False        # True se trimesh è riuscito\n"
            "\n"
            "    # Punti di aggancio rilevati dalla mesh STL (physics_hanging)\n"
            "    hanging_holes: list = field(default_factory=list)  # List[dict]\n"
            "    n_hanging_holes: int = 0\n"
            "    stl_path: str = ''\n"
        )
        source = source.replace(old_field, new_field, 1)

        # 3. Chiama _rileva_fori_aggancio() in _analizza_stl_file()
        # Inserisci PRIMA del return PartGeometry()
        old_return = "        return PartGeometry(\n            nome=nome,"
        new_return = (
            "        # ── Rileva fori di aggancio ────────────────────────\n"
            "        hanging_holes = _rileva_fori_aggancio(mesh)\n\n"
            "        return PartGeometry(\n            nome=nome,"
        )
        source = source.replace(old_return, new_return, 1)

        # 4. Aggiungi i campi extra nella costruzione PartGeometry in _analizza_stl_file
        # Cerca la fine del PartGeometry constructor
        old_end = (
            "            hash_geom=codice.split('-')[-1],\n"
            "            mesh_presente=True,\n"
            "        )\n"
            "    except Exception as e:\n"
            "        return None"
        )
        new_end = (
            "            hash_geom=codice.split('-')[-1],\n"
            "            mesh_presente=True,\n"
            "            hanging_holes=hanging_holes,\n"
            "            n_hanging_holes=len(hanging_holes),\n"
            "            stl_path=path_stl,\n"
            "        )\n"
            "    except Exception as e:\n"
            "        return None"
        )
        source = source.replace(old_end, new_end, 1)

        # 5. Aggiungi campi in parse_result_to_dict()
        old_dict_field = "                'mesh_presente': p.mesh_presente,"
        new_dict_field = (
            "                'mesh_presente':   p.mesh_presente,\n"
            "                'hanging_holes':   getattr(p, 'hanging_holes', []),\n"
            "                'n_hanging_holes': getattr(p, 'n_hanging_holes', 0),\n"
            "                'stl_path':        getattr(p, 'stl_path', ''),\n"
        )
        source = source.replace(old_dict_field, new_dict_field, 1)

        # Scrivi il file patchato
        with open(cad_parser_path, 'w', encoding='utf-8') as f:
            f.write(source)

        print(f"✅ Patch applicata con successo a {cad_parser_path}")
        return True

    except Exception as e:
        print(f"❌ Errore applicazione patch: {e}")
        return False


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        result = applica_patch_automatica(sys.argv[1])
        print("Patch applicata" if result else "Patch NON applicata")
    else:
        print("Uso: python cad_parser_patch.py /path/to/cad_parser.py")
