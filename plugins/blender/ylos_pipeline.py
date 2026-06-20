"""Ylos Prod pipeline - bootstrap Blender.

Rend la logique de creation (create_project.py, migrate_to_2.0.py) importable dans le
Python embarque de Blender, SANS dupliquer le code : on ajoute le repo (source de verite
unique) au sys.path. Respecte le principe "logique unique, jamais dupliquee".

Installation : voir plugins/blender/README.md (lien symbolique vers scripts/startup/).
- Si le repo bouge : mettre a jour _REPO ci-dessous.

Une fois Blender relance, dans la console Python :
    import create_project
    info = create_project.create("MonProjet")
    create_project.create_asset(info["source"], "Hero", asset_type="CHARACTER")
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent

if _REPO.is_dir():
    _p = str(_REPO)
    if _p not in sys.path:
        sys.path.append(_p)
        print(f"[ylos] pipeline disponible ({_p})")
else:
    print(f"[ylos] repo introuvable : {_REPO} - 'import create_project' indisponible")
