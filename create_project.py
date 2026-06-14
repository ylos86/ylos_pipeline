#!/usr/bin/env python3
"""
create_project.py - Createur de projet, pipeline Black Kite.

Source de verite unique de la logique de creation. Importable par les plugins DCC
(Houdini/hython, Blender) : aucune dependance hors stdlib.

Principes appliques :
  - Racine relocalisable : tout passe par $PROJ_ROOT (source) et $PROJ_CACHE (cache).
  - Separation cache / source : source sur disque externe, cache regenerable sur interne.
  - project.json = manifeste, source de verite lisible par machine (+ schema_version).
  - Logique unique : ce module est importe, jamais duplique.
  - Production != pipeline : le manifeste ne gere PAS le suivi de prod (statut client,
    deadlines). C'est un probleme distinct, a traiter ailleurs.

Usage CLI :
    python create_project.py "mon_projet"
    python create_project.py "mon_projet" --root /Volumes/EXT/3D --cache ~/cache --force

Usage import (plugin DCC) :
    import create_project
    info = create_project.create("mon_projet")
    manifest = create_project.read_manifest(info["source"])
    create_project.validate_manifest(manifest)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"          # version du contrat project.json. A bumper a CHAQUE
                                  # changement de schema (= migration, pas edit silencieux).
MANIFEST_NAME = "project.json"
SPOTLIGHT_MARKER = ".metadata_never_index"
GITIGNORE_NAME = ".gitignore"

# Noms des variables d'environnement (jamais de chemin absolu en dur dans les scenes DCC)
ENV_ROOT = "PROJ_ROOT"            # racine SOURCE  - disque externe, permanent
ENV_CACHE = "PROJ_CACHE"          # racine CACHE   - disque interne, regenerable

# Fallbacks si les env vars ne sont pas posees (avec avertissement)
FALLBACK_ROOT = Path.home() / "BlackKite" / "projects"
FALLBACK_CACHE = Path.home() / "BlackKite" / "cache"

# Arborescence SOURCE (sous $PROJ_ROOT/<projet>) - permanent, versionne.
# QUESTION OUVERTE (topologie) : tree hybride asset + shot. Si le travail se revele
# purement shot-centric ou asset-centric, elaguer la branche inutile - ou remonter
# 'assets' au-dessus des projets (bibliotheque transverse).
SOURCE_TREE = [
    "_config",                    # config projet + manifeste
    "assets",                     # travail asset-centric (modeles, lookdev, rigs)
    "shots",                      # travail shot-centric (seq/shot crees a la demande)
    "ref/ai",                     # references IA (Midjourney, NanoBanana) + metadata
    "ref/photo",                  # references photo
    "ref/board",                  # moodboards / planches
    "delivery",                   # masters / sorties finales
]

# Arborescence CACHE (sous $PROJ_CACHE/<projet>) - jetable, hors Git.
# QUESTION OUVERTE (placement du cache) : ici cache PAR PROJET sous $PROJ_CACHE.
# Alternative = cache centralise unique. Basculer CACHE_PER_PROJECT pour changer.
CACHE_PER_PROJECT = True
CACHE_TREE = [
    "houdini",                    # caches Houdini (.bgeo.sc, sims, flip...)
    "blender",                    # caches Blender (bake, sims)
    "render",                     # rendus / AOVs regenerables
    "tmp",
]

GITIGNORE_CONTENT = """\
# --- Pipeline Black Kite : regenerable / lourd, hors Git ---
cache/
delivery/**/render/

# Caches DCC
*.bgeo.sc
*.sim

# Sauvegardes DCC
*.hip.bak
*.hiplc.bak
*.blend1
*.blend2

# Rendus
*.exr
*.ass

# macOS
.DS_Store
"""


# --------------------------------------------------------------------------------------
# Resolution des chemins (relocalisable)
# --------------------------------------------------------------------------------------

def _resolve(explicit, env_name, fallback):
    """Resout une racine : argument explicite > variable d'env > fallback (avec warning)."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_val = os.environ.get(env_name)
    if env_val:
        return Path(env_val).expanduser().resolve()
    sys.stderr.write(
        f"[warn] ${env_name} non definie - fallback sur {fallback}. "
        f"Pose ${env_name} pour un design relocalisable.\n"
    )
    return fallback.expanduser().resolve()


def resolve_root(explicit=None):
    return _resolve(explicit, ENV_ROOT, FALLBACK_ROOT)


def resolve_cache(explicit=None):
    return _resolve(explicit, ENV_CACHE, FALLBACK_CACHE)


# --------------------------------------------------------------------------------------
# Manifeste (project.json) - contrat lisible par machine
# --------------------------------------------------------------------------------------

def build_manifest(name):
    """Construit le dict manifeste. Ne stocke AUCUN chemin absolu machine : le projet
    est relocalisable, il se resout via $PROJ_ROOT / $PROJ_CACHE a l'execution. Un
    launcher / plugin lit ce manifeste et pose les env vars PAR SESSION (ce qui evite
    la collision d'une env var globale entre deux DCC ouverts sur deux projets)."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "created_utc": now,
        "modified_utc": now,
        # Quelles env vars ce projet attend
        "env": {"root": f"${ENV_ROOT}", "cache": f"${ENV_CACHE}"},
        # Trace de la structure creee (audit / migration)
        "structure": {"source": SOURCE_TREE, "cache": CACHE_TREE},
        "cache_per_project": CACHE_PER_PROJECT,
        # Reserve aux reglages par DCC (rempli par les plugins)
        "dcc": {"houdini": {}, "blender": {}},
        # 'status' minimal. Le VRAI suivi de production (deadlines, client) vit ailleurs.
        "status": "created",
    }


def write_manifest(config_dir, manifest):
    path = config_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_manifest(project_dir):
    """Lit project.json depuis <projet>/_config. Utile aux plugins / launchers."""
    path = Path(project_dir) / "_config" / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(manifest):
    """Validation stdlib (pas de dependance jsonschema). Leve ValueError si invalide.
    Verifie la compatibilite de version MAJEURE du schema (sinon : migration requise)."""
    required = ("schema_version", "name", "created_utc", "env", "structure")
    missing = [k for k in required if k not in manifest]
    if missing:
        raise ValueError(f"project.json invalide - cles manquantes : {missing}")
    major = str(manifest["schema_version"]).split(".")[0]
    if major != SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"Incompatibilite de schema : projet={manifest['schema_version']} "
            f"vs outil={SCHEMA_VERSION}. Migration requise."
        )
    return True


# --------------------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------------------

def _make_tree(base, tree):
    base.mkdir(parents=True, exist_ok=True)
    for rel in tree:
        (base / rel).mkdir(parents=True, exist_ok=True)


def create(name, root=None, cache=None, force=False):
    """Cree un projet complet. Retourne {name, source, cache, manifest}.
    Non destructif : 'force' ne fait que lever le garde-fou d'existence, il ne supprime
    jamais rien (les dossiers sont crees avec exist_ok)."""
    if not name or "/" in name or name.strip() != name:
        raise ValueError(f"Nom de projet invalide : {name!r}")

    root_dir = resolve_root(root)
    cache_root = resolve_cache(cache)

    source = root_dir / name
    cache_dir = (cache_root / name) if CACHE_PER_PROJECT else cache_root

    if source.exists() and not force:
        raise FileExistsError(
            f"Le projet existe deja : {source} (passer force=True pour forcer)"
        )

    # 1. arborescence source (externe, permanente)
    _make_tree(source, SOURCE_TREE)
    # 2. arborescence cache (tier separe, disque interne)
    _make_tree(cache_dir, CACHE_TREE)

    config_dir = source / "_config"   # cree par SOURCE_TREE

    # 3. manifeste (source de verite)
    manifest = build_manifest(name)
    validate_manifest(manifest)
    manifest_path = write_manifest(config_dir, manifest)

    # 4. marqueur anti-indexation Spotlight (sur la source, lourde)
    (source / SPOTLIGHT_MARKER).touch()

    # 5. .gitignore (cache + rendus hors Git)
    (source / GITIGNORE_NAME).write_text(GITIGNORE_CONTENT, encoding="utf-8")

    return {
        "name": name,
        "source": str(source),
        "cache": str(cache_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _cli(argv=None):
    p = argparse.ArgumentParser(description="Createur de projet - pipeline Black Kite.")
    p.add_argument("name", help="Nom du projet (un seul segment, pas de /)")
    p.add_argument("--root", help=f"Racine source (defaut ${ENV_ROOT})")
    p.add_argument("--cache", help=f"Racine cache (defaut ${ENV_CACHE})")
    p.add_argument("--force", action="store_true", help="Passer outre si le projet existe")
    args = p.parse_args(argv)
    try:
        info = create(args.name, root=args.root, cache=args.cache, force=args.force)
    except (ValueError, FileExistsError) as e:
        sys.stderr.write(f"[erreur] {e}\n")
        return 1
    print(f"[ok] projet '{info['name']}' cree")
    print(f"  source    : {info['source']}")
    print(f"  cache     : {info['cache']}")
    print(f"  manifeste : {info['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
