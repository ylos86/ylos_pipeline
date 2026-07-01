#!/usr/bin/env python3
"""
create_project.py - Createur de projet & d'assets, pipeline Ylos Prod (schema 2.0).

Source de verite unique de la logique de creation. Importable par les plugins DCC
(Houdini/hython, Blender) : aucune dependance hors stdlib.

Principes appliques :
  - Racine relocalisable : tout passe par $PROJ_ROOT (source) et $PROJ_CACHE (cache).
    Aucun chemin absolu n'est stocke dans les manifestes.
  - Separation cache / source : source sur disque externe, cache regenerable sur interne.
    Le cache vit sous $PROJ_CACHE/<projet>, JAMAIS co-localise avec la source.
  - project.json = manifeste, source de verite (schema_version 2.x, cf. project.schema.json).
  - Topologie ASSET-CENTRIC : assets/ est la colonne vertebrale ; sets/ et shots/ sont du
    scaffolding optionnel (crees vides).
  - Logique unique : ce module est importe, jamais duplique.
  - Production != pipeline : le manifeste ne gere PAS le suivi de prod (client, deadlines).

Usage CLI :
    python create_project.py project "mon_projet"
    python create_project.py project "mon_projet" --root /Volumes/EXT/3D --cache ~/cache --force
    python create_project.py asset  "/Volumes/EXT/3D/mon_projet" "Lina" --type CHARACTER
    python create_project.py asset  "<projet>" "decor" --entity-type set --steps modeling,lookdev

Usage import (plugin DCC) :
    import create_project
    info  = create_project.create("mon_projet")
    asset = create_project.create_asset(info["source"], "Lina", asset_type="CHARACTER")
    manifest = create_project.read_manifest(info["source"])
    create_project.validate_manifest(manifest)
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------------------
# Constantes - contrat
# --------------------------------------------------------------------------------------

SCHEMA_VERSION = "2.0.0"          # version du contrat (project.json ET manifeste d'asset).
                                  # A bumper a CHAQUE changement de schema (= migration).
MANIFEST_NAME = "project.json"
ASSET_MANIFEST_NAME = "manifest.json"
ASSET_ROOT_NAME = "asset_root.usda"   # fichier de composition USD (ASCII, cf. convention)
PIPELINE_DIR = "_pipeline"        # dossier config/manifeste (renomme de _config en 2.0)
SPOTLIGHT_MARKER = ".metadata_never_index"
GITIGNORE_NAME = ".gitignore"

TOPOLOGY = "asset-centric"

# Noms des variables d'environnement (jamais de chemin absolu en dur dans les scenes DCC)
ENV_ROOT = "PROJ_ROOT"            # racine SOURCE  - disque externe, permanent
ENV_CACHE = "PROJ_CACHE"          # racine CACHE   - disque interne, regenerable

# Fallbacks si les env vars ne sont pas posees (avec avertissement)
FALLBACK_ROOT = Path.home() / "Ylos" / "projects"
FALLBACK_CACHE = Path.home() / "Ylos" / "cache"

# --- Defauts pipeline (taxonomie des steps + assemblage USD) --------------------------
DEFAULT_ASSET_STEPS = ["modeling", "rigging", "lookdev", "fx"]
DEFAULT_SHOT_STEPS  = ["animation", "fx", "lighting", "comp"]
DEFAULT_SET_STEPS   = ["layout", "lookdev", "lighting"]
USD_ROOT_PRIM = "/ROOT"           # prim racine des stages d'ASSEMBLAGE (sets/shots).
                                  # Les assets s'ancrent sous /<NomAsset> (cf. usd-convention.md).

# --- Defauts scene (consommes par les plugins DCC a l'ouverture) ----------------------
DEFAULT_SCENE = {
    "fps": 24,
    "fps_base": 1.0,
    "unit_scale": 1.0,
    "color_management": "AgX",
    "renderer": "CYCLES",
    "resolution_x": 2048,
    "resolution_y": 1152,
    "color_space": "Linear Rec.709",
}

DEFAULT_DELIVERY = {"targets": ["usd", "exr"]}

# --- Convention USD (cf. docs/usd-convention.md) --------------------------------------
USD_UP_AXIS = "Y"                 # axe d'echange USD (conversion Z<->Y geree par les DCC)
USD_METERS_PER_UNIT = 1.0         # aligne sur scene.unit_scale

# Mapping famille d'entite -> dossier parent dans la source
ENTITY_DIR = {"asset": "assets", "set": "sets", "shot": "shots"}
_DEFAULT_STEPS = {"asset": DEFAULT_ASSET_STEPS, "set": DEFAULT_SET_STEPS, "shot": DEFAULT_SHOT_STEPS}
_STEPS_KEY = {"asset": "asset_steps", "set": "set_steps", "shot": "shot_steps"}

# --- Publish LOP (Solaris) - version d'asset complete, hors taxonomie de steps -----------
# Un publish LOP (cf. HDA ylos::publish) n'est PAS un step de pipeline (modeling/rigging/...) :
# c'est un instantane complet du reseau LOP (layer USD + thumb). Vit dans son propre dossier
# reserve, n'entre jamais dans la composition subLayers de asset_root.usda.
ASSET_TYPES = ["CHARACTER", "PROP", "VEHICLE", "CREATURE", "FX_ELEMENT"]
LOP_DIR_NAME = "lop"
LOP_PUBLISH_DIR_NAME = "publish"
LOP_STAGING_DIR_NAME = ".staging"
LOP_THUMB_NAME = "thumb.png"
LOP_LAYER_EXTENSIONS = (".usd", ".usdc", ".usda", ".usdnc")  # .usdnc = watermark Apprentice,
                                                              # jamais suppose a l'avance (cf.
                                                              # gotcha extensions, LOP HDA)
LOP_PUBLISHES_KEY = "lop_publishes"
_DIR_VER_RE = re.compile(r"_v(\d+)$")

# Ordre de force des steps pour l'empilement subLayers (plus fort / downstream en premier).
# USD : le premier sublayer de la liste est le plus fort.
DOWNSTREAM_ORDER = ["fx", "lookdev", "rigging", "uvs", "modeling",
                    "layout", "animation", "lighting", "render", "composite"]

_VER_RE = re.compile(r"_v(\d+)\.")

# Arborescence SOURCE (sous $PROJ_ROOT/<projet>) - permanent, versionne. Asset-centric.
SOURCE_TREE = [
    PIPELINE_DIR,                 # project.json (manifeste)
    "assets",                     # COLONNE VERTEBRALE (asset-centric)
    "sets",                       # assemblage - optionnel (vide au scaffold)
    "shots",                      # shots - optionnel (vide au scaffold)
    "references/ai",              # references IA (Midjourney / NanoBanana) + metadata
    "references/photo",           # references photo
    "references/board",           # moodboards / planches
    "resources/hdri",             # ressources reutilisables intra-projet
    "resources/textures",
    "delivery",                   # masters / sorties finales
    "edit",                       # montage
]

# Arborescence CACHE (sous $PROJ_CACHE/<projet>) - jetable, hors Git, NVMe interne.
CACHE_PER_PROJECT = True
CACHE_TREE = [
    "houdini",                    # caches Houdini (.bgeo.sc, sims, flip...)
    "blender",                    # caches Blender (bake, sims)
    "render",                     # rendus / AOVs regenerables
    "alembic",                    # caches .abc
    "sim",                        # simulations
    "tmp",
]

GITIGNORE_CONTENT = """\
# --- Pipeline Ylos Prod : regenerable / lourd, hors Git ---
# Le cache vit sous $PROJ_CACHE (hors arbre source) : rien a ignorer ici pour ca.

# Rendus / masters lourds
delivery/**/render/
*.exr
*.ass

# Geo USD binaire lourde : hors Git. La compo (.usda) est versionnee, la geo (.usdc)
# est lourde/regeneree. Defaut a affiner par projet.
*.usdc

# Caches DCC ecrits par erreur dans la source
*.bgeo.sc
*.sim

# Sauvegardes DCC
*.hip.bak
*.hiplc.bak
*.blend1
*.blend2

# macOS
.DS_Store
"""


# --------------------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_segment(name):
    """Un nom = un seul segment de chemin, pas d'espace de bord, pas de separateur."""
    if not name or "/" in name or "\\" in name or name.strip() != name:
        raise ValueError(f"Nom invalide (un seul segment, sans /): {name!r}")


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


def _make_tree(base, tree):
    base.mkdir(parents=True, exist_ok=True)
    for rel in tree:
        (base / rel).mkdir(parents=True, exist_ok=True)


def _ver(path):
    """Extrait le numéro de version d'un chemin de publish (ex 'step/publish/A_step_v002.usdc' -> 2)."""
    m = _VER_RE.search(str(path))
    return int(m.group(1)) if m else 0


@contextlib.contextmanager
def acquire_lock(path):
    """Verrou exclusif (fcntl.flock) le temps d'une section critique read-modify-write sur
    'path' (typiquement un manifest.json, mais generique - pas specifique aux manifestes).
    Le verrou vit dans un fichier '.lock' a cote de 'path' (jamais sur 'path' lui-meme) pour
    ne jamais interferer avec sa lecture/ecriture. Bloquant : un second appel concurrent
    attend la liberation plutot que de risquer une collision (ex: version, manifeste corrompu).

    Point de centralisation UNIQUE pour fcntl.flock dans ce module (cf. CLAUDE.md : advisory,
    POSIX-only, non fiable sur NFS/SMB - a faire evoluer ici seul si le stockage change de tier)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --------------------------------------------------------------------------------------
# Manifeste projet (project.json) - contrat lisible par machine
# --------------------------------------------------------------------------------------

def build_manifest(name, display_name=None, prod_type="FILM"):
    """Construit le dict manifeste projet (schema 2.x). Ne stocke AUCUN chemin absolu : le
    projet est relocalisable, il se resout via $PROJ_ROOT / $PROJ_CACHE a l'execution. Un
    launcher / plugin lit ce manifeste et pose les env vars PAR SESSION (ce qui evite la
    collision d'une env var globale entre deux DCC ouverts sur deux projets)."""
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "display_name": display_name or name,
        "prod_type": prod_type,
        "topology": TOPOLOGY,
        "created_utc": now,
        "modified_utc": now,
        # Quelles env vars ce projet attend
        "env": {"root": f"${ENV_ROOT}", "cache": f"${ENV_CACHE}"},
        # Trace de la structure creee (audit / migration)
        "structure": {"source": list(SOURCE_TREE), "cache": list(CACHE_TREE)},
        "cache_per_project": CACHE_PER_PROJECT,
        # Taxonomie des steps + assemblage USD
        "pipeline": {
            "asset_steps": list(DEFAULT_ASSET_STEPS),
            "shot_steps": list(DEFAULT_SHOT_STEPS),
            "set_steps": list(DEFAULT_SET_STEPS),
            "usd_root_prim": USD_ROOT_PRIM,
        },
        # Reglages de scene par defaut (lus par les plugins DCC)
        "scene": dict(DEFAULT_SCENE),
        "delivery": dict(DEFAULT_DELIVERY),
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
    """Lit project.json depuis <projet>/_pipeline. Utile aux plugins / launchers."""
    path = Path(project_dir) / PIPELINE_DIR / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(manifest):
    """Validation stdlib (pas de dependance jsonschema). Leve ValueError si invalide.
    Verifie la compatibilite de version MAJEURE du schema (sinon : migration requise)."""
    required = ("schema_version", "name", "created_utc", "env", "structure", "pipeline", "scene")
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
# Manifeste d'entite (asset/set/shot) + stub USD
# --------------------------------------------------------------------------------------

def build_asset_manifest(name, entity_type, asset_type, steps):
    """Manifeste par entite (cf. asset.schema.json). 'entity_type' = famille (asset/set/
    shot) ; 'type' = sous-type metier (CHARACTER, ENVIRONMENT, PROP...)."""
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "entity_type": entity_type,
        "type": asset_type,
        "steps": list(steps),
        "publishes": {s: [] for s in steps},
        "created_utc": now,
        "modified_utc": now,
    }


def _meters_per_unit_str():
    mpu = USD_METERS_PER_UNIT
    return str(int(mpu)) if float(mpu).is_integer() else str(mpu)


def asset_root_usda(name):
    """Stub d'assemblage USD pour un asset/set (cf. docs/usd-convention.md).
    defaultPrim = <NomEntite> ; les steps s'empilent en subLayers (rempli au publish,
    du plus fort/downstream au plus faible). Y-up, metersPerUnit aligne sur scene."""
    return (
        "#usda 1.0\n"
        "(\n"
        f'    defaultPrim = "{name}"\n'
        f'    upAxis = "{USD_UP_AXIS}"\n'
        f"    metersPerUnit = {_meters_per_unit_str()}\n"
        "    # subLayers : du plus fort (downstream) au plus faible. Rempli au publish.\n"
        "    subLayers = [\n"
        "    ]\n"
        ")\n"
        "\n"
        f'def Xform "{name}"\n'
        "{\n"
        "}\n"
    )


def build_asset_root(name, latest):
    """Reconstruit asset_root.usda depuis {step: chemin_relatif_du_latest_publish}.
    subLayers dans le header de stage, ordre downstream-fort en premier (cf. usd-convention.md)."""
    ordered = [s for s in DOWNSTREAM_ORDER if s in latest]
    ordered += [s for s in latest if s not in DOWNSTREAM_ORDER]
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{name}"',
        f'    upAxis = "{USD_UP_AXIS}"',
        f"    metersPerUnit = {_meters_per_unit_str()}",
        "    subLayers = [",
    ]
    for s in ordered:
        lines.append(f"        @{latest[s]}@,")
    lines += [
        "    ]",
        ")",
        "",
        f'def Xform "{name}"',
        "{",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _latest_from_publishes(publishes):
    """Retourne {step: chemin_latest} depuis manifest.publishes (dict step -> [paths])."""
    return {step: max(paths, key=_ver) for step, paths in publishes.items() if paths}


def _project_steps(project_dir, entity_type):
    """Steps par defaut pour cette famille : pipeline du manifeste projet si lisible,
    sinon defauts du module."""
    key = _STEPS_KEY[entity_type]
    try:
        steps = read_manifest(project_dir).get("pipeline", {}).get(key)
        if steps:
            return list(steps)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return list(_DEFAULT_STEPS[entity_type])


# --------------------------------------------------------------------------------------
# Creation - projet
# --------------------------------------------------------------------------------------

def create(name, root=None, cache=None, force=False, prod_type="FILM", display_name=None):
    """Cree un projet complet (coquille asset-centric). Retourne {name, source, cache,
    manifest}. Non destructif : 'force' ne fait que lever le garde-fou d'existence, il ne
    supprime jamais rien (les dossiers sont crees avec exist_ok)."""
    _validate_segment(name)

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

    config_dir = source / PIPELINE_DIR   # cree par SOURCE_TREE

    # 3. manifeste (source de verite)
    manifest = build_manifest(name, display_name=display_name, prod_type=prod_type)
    validate_manifest(manifest)
    manifest_path = write_manifest(config_dir, manifest)

    # 4. marqueur anti-indexation Spotlight (sur la source, lourde)
    (source / SPOTLIGHT_MARKER).touch()

    # 5. .gitignore (cache + rendus + geo lourde hors Git)
    (source / GITIGNORE_NAME).write_text(GITIGNORE_CONTENT, encoding="utf-8")

    return {
        "name": name,
        "source": str(source),
        "cache": str(cache_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# Creation - entite (asset / set / shot)
# --------------------------------------------------------------------------------------

def create_asset(project_dir, name, entity_type="asset", asset_type="OTHER",
                 steps=None, force=False):
    """Scaffolde une entite dans un projet existant. Cree <famille>/<name>/ avec un dossier
    par step (+ wip/ + publish/), un manifest.json et, pour asset/set, un stub asset_root.usda.
    Retourne {name, entity_type, path, manifest, asset_root}. Non destructif."""
    project_dir = Path(project_dir)
    if entity_type not in ENTITY_DIR:
        raise ValueError(f"entity_type invalide : {entity_type!r} (asset|set|shot)")
    _validate_segment(name)

    if steps is None:
        steps = _project_steps(project_dir, entity_type)

    entity_dir = project_dir / ENTITY_DIR[entity_type] / name
    if entity_dir.exists() and not force:
        raise FileExistsError(
            f"L'entite existe deja : {entity_dir} (passer force=True pour forcer)"
        )

    # 1. dossiers de step generes depuis les steps declares : wip/ (travail DCC) +
    #    publish/ (sorties USD versionnees), comme le workflow reel.
    entity_dir.mkdir(parents=True, exist_ok=True)
    for step in steps:
        (entity_dir / step / "wip").mkdir(parents=True, exist_ok=True)
        (entity_dir / step / "publish").mkdir(parents=True, exist_ok=True)

    # 2. manifeste d'entite
    manifest = build_asset_manifest(name, entity_type, asset_type, steps)
    manifest_path = entity_dir / ASSET_MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # 3. stub d'assemblage USD (asset/set ; un shot compose differemment)
    asset_root_path = None
    if entity_type in ("asset", "set"):
        asset_root_path = entity_dir / ASSET_ROOT_NAME
        asset_root_path.write_text(asset_root_usda(name), encoding="utf-8")

    return {
        "name": name,
        "entity_type": entity_type,
        "path": str(entity_dir),
        "manifest": str(manifest_path),
        "asset_root": str(asset_root_path) if asset_root_path else None,
    }


# --------------------------------------------------------------------------------------
# Publish - versionner un fichier dans un step d'entite
# --------------------------------------------------------------------------------------

def publish_asset(project_root, asset_name, step, source_file):
    """Publie source_file dans <asset>/<step>/publish/ avec versioning automatique.

    - Scanne manifest.publishes[step] pour determiner la prochaine version (v001, v002...).
    - Copie source_file -> <step>/publish/<asset>_<step>_v<NNN><ext> (jamais d'ecrasement).
    - Met a jour manifest.json (publishes[step] et modified_utc).
    - Reconstruit asset_root.usda (subLayers) pour les entites asset/set.

    Retourne {name, step, version, publish_path, manifest, asset_root}.
    Non-destructif : leve FileExistsError si la version cible existe deja.
    """
    project_root = Path(project_root)
    source_file = Path(source_file)

    if not source_file.is_file():
        raise FileNotFoundError(f"Fichier source introuvable : {source_file}")

    # Localiser l'entite dans assets/ sets/ shots/
    entity_dir = None
    for family in ("assets", "sets", "shots"):
        candidate = project_root / family / asset_name
        if candidate.is_dir() and (candidate / ASSET_MANIFEST_NAME).is_file():
            entity_dir = candidate
            break
    if entity_dir is None:
        raise FileNotFoundError(
            f"Entite '{asset_name}' introuvable dans {project_root} (assets/, sets/, shots/)."
        )

    manifest_path = entity_dir / ASSET_MANIFEST_NAME

    # Section critique : lecture manifeste -> allocation de version -> copie -> ecriture
    # manifeste -> reconstruction asset_root.usda. Verrouillee de bout en bout (fcntl.flock)
    # pour qu'un second publish concurrent ne puisse jamais lire un 'publishes' perime et
    # entrer en collision sur le meme numero de version (cf. acquire_lock).
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        valid_steps = manifest.get("steps", [])
        if step not in valid_steps:
            raise ValueError(
                f"Step '{step}' invalide pour '{asset_name}' (steps declares : {valid_steps})."
            )

        # Prochain numero de version
        existing = manifest.get("publishes", {}).get(step, [])
        next_ver = max((_ver(p) for p in existing), default=0) + 1

        # Chemin cible versionne
        ext = source_file.suffix
        versioned_name = f"{asset_name}_{step}_v{next_ver:03d}{ext}"
        publish_dir = entity_dir / step / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        target = publish_dir / versioned_name

        if target.exists():
            raise FileExistsError(
                f"Version deja presente, non ecrasee : {target}"
            )

        shutil.copy2(source_file, target)

        # Mettre a jour manifest.json
        publishes = manifest.setdefault("publishes", {})
        publishes.setdefault(step, [])
        publishes[step].append(f"{step}/publish/{versioned_name}")
        manifest["modified_utc"] = _now()
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        # Reconstruire asset_root.usda (asset/set uniquement)
        asset_root_path = None
        entity_type = manifest.get("entity_type", "asset")
        if entity_type in ("asset", "set"):
            content = build_asset_root(manifest.get("name", asset_name),
                                       _latest_from_publishes(publishes))
            asset_root_path = entity_dir / ASSET_ROOT_NAME
            asset_root_path.write_text(content, encoding="utf-8")

    return {
        "name": asset_name,
        "step": step,
        "version": next_ver,
        "publish_path": str(target),
        "manifest": str(manifest_path),
        "asset_root": str(asset_root_path) if asset_root_path else None,
    }


# --------------------------------------------------------------------------------------
# Publish LOP (Solaris) - version d'asset complete (layer USD + thumb), staging + replace
# --------------------------------------------------------------------------------------

def validate_publish_asset_name(asset_name, asset_type):
    """Valide asset_name contre la convention TYPE_Asset_Variant (TYPE = asset_type).
    Match par prefixe exact (et non un split('_') naif) car certains types contiennent deja
    un underscore (FX_ELEMENT) : 'FX_ELEMENT_Drone_Default' a 4 segments '_', pas 3."""
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"asset_type invalide : {asset_type!r} (attendu un de {ASSET_TYPES})")
    prefix = f"{asset_type}_"
    if not asset_name.startswith(prefix):
        raise ValueError(
            f"asset_name {asset_name!r} ne respecte pas la convention "
            f"{asset_type}_Asset_Variant (prefixe attendu : {prefix!r})"
        )
    remainder = asset_name[len(prefix):]
    parts = remainder.split("_")
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            f"asset_name {asset_name!r} invalide : attendu {asset_type}_Asset_Variant "
            f"(ex: {asset_type}_Lina_Default)"
        )
    return True


def _find_asset_entity(project_root, asset_name):
    """Localise une entite ASSET deja creee (assets/<asset_name>/manifest.json). Un publish
    LOP n'est jamais createur d'entite : create_asset() doit avoir ete appele avant."""
    entity_dir = Path(project_root) / ENTITY_DIR["asset"] / asset_name
    manifest_path = entity_dir / ASSET_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Asset '{asset_name}' introuvable sous {entity_dir} "
            f"(doit etre cree via create_asset() avant tout publish)."
        )
    return entity_dir, manifest_path


def publish_version_from_dir(final_dir):
    """Extrait le numero de version d'un final_dir retourne par allocate_publish_version()
    (ex: 'CHARACTER_Lina_Default_lop_v003' -> 3). Distinct de _ver() : un final_dir est un
    nom de repertoire sans extension (le numero termine le nom), _ver() attend un nom de
    fichier versionne avec extension (cf. publish_asset)."""
    m = _DIR_VER_RE.search(Path(final_dir).name)
    if not m:
        raise ValueError(f"final_dir ne contient pas de suffixe de version : {final_dir!r}")
    return int(m.group(1))


def allocate_publish_version(project_root, asset_name, asset_type, comment=None):
    """Reserve atomiquement (fcntl.flock) le prochain numero de version de publish LOP pour
    un asset existant, et cree un repertoire de staging vide. Ne touche ni au layer USD ni
    au thumbnail : l'appelant (callback HDA) les ecrit dans staging_dir, puis appelle
    finalize_publish_version() pour committer (os.replace atomique, meme filesystem que
    staging_dir car les deux vivent sous entity_dir/lop/) et finaliser le manifeste.

    Retourne (staging_dir, final_dir) en pathlib.Path. staging_dir existe deja (vide) ;
    final_dir n'existe pas encore (c'est la cible du futur replace).
    """
    validate_publish_asset_name(asset_name, asset_type)

    project_root = Path(project_root)
    entity_dir, manifest_path = _find_asset_entity(project_root, asset_name)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        declared_type = manifest.get("type")
        if declared_type != asset_type:
            raise ValueError(
                f"asset_type {asset_type!r} ne correspond pas au type declare de "
                f"'{asset_name}' dans manifest.json ({declared_type!r})."
            )

        existing = manifest.setdefault(LOP_PUBLISHES_KEY, [])
        next_ver = max((e.get("version", 0) for e in existing), default=0) + 1

        versioned_name = f"{asset_name}_lop_v{next_ver:03d}"
        publish_root = entity_dir / LOP_DIR_NAME / LOP_PUBLISH_DIR_NAME
        staging_root = entity_dir / LOP_DIR_NAME / LOP_STAGING_DIR_NAME
        final_dir = publish_root / versioned_name
        staging_dir = staging_root / f"{versioned_name}.staging-{os.getpid()}"

        if final_dir.exists():
            raise FileExistsError(f"Version deja presente, non ecrasee : {final_dir}")

        # publish_root doit exister pour que le futur os.replace() ait un parent valide ;
        # final_dir lui-meme ne doit PAS exister (c'est la cible du replace).
        publish_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=False)

        # Reservation : entree 'pending' pour bloquer toute reattribution de ce numero tant
        # que finalize_publish_version() n'a pas commit (sinon deux publishes concurrents
        # pourraient tous deux calculer le meme next_ver).
        existing.append({
            "version": next_ver,
            "status": "pending",
            "comment": comment or "",
            "reserved_utc": _now(),
        })
        manifest["modified_utc"] = _now()
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return staging_dir, final_dir


def _missing_artifacts(staging_dir, expected_artifacts):
    """Verifie que chaque entree de expected_artifacts existe et est non-vide dans staging_dir.
    Une entree avec un '.' est un nom exact (ex: 'thumb.png'). Une entree sans '.' est un stem
    de layer USD : matchee contre LOP_LAYER_EXTENSIONS (jamais d'extension supposee a l'avance -
    licence Apprentice ecrit '.usdnc', licence commerciale '.usd'/'.usdc'/'.usda').

    Retourne la liste des entrees manquantes/vides (liste vide = tout est present)."""
    missing = []
    for artifact in expected_artifacts:
        if "." in artifact:
            candidate = staging_dir / artifact
            if not candidate.is_file() or candidate.stat().st_size == 0:
                missing.append(artifact)
        else:
            matches = [
                staging_dir / f"{artifact}{ext}" for ext in LOP_LAYER_EXTENSIONS
                if (staging_dir / f"{artifact}{ext}").is_file()
                and (staging_dir / f"{artifact}{ext}").stat().st_size > 0
            ]
            if not matches:
                missing.append(artifact)
    return missing


def finalize_publish_version(project_root, asset_name, staging_dir, final_dir, version,
                             expected_artifacts, comment=None):
    """Commit atomique d'un publish LOP prealablement reserve par allocate_publish_version() :
    os.replace(staging_dir, final_dir) - point de commit unique pour TOUT ce que le staging
    contient (layer USD + thumb.png) - puis mise a jour du manifeste sous flock (entree
    'pending' -> 'complete'). A appeler une fois que le callback HDA a ecrit le layer et le
    thumbnail dans staging_dir.

    expected_artifacts : liste de noms requis dans staging_dir avant le commit (ex:
    ['CHARACTER_Lina_Default_lop_v003', 'thumb.png'] - le layer par son stem, resolu contre les
    extensions USD connues ; le thumb par son nom exact). Le thumbnail est REQUIS pour un publish
    LOP. Si un artefact manque ou est vide : leve ValueError, ne touche PAS staging_dir, n'appelle
    PAS os.replace, n'ecrit RIEN au manifeste (la reservation reste 'pending').

    Retourne {name, version, final_dir, manifest}.
    """
    project_root = Path(project_root)
    entity_dir, manifest_path = _find_asset_entity(project_root, asset_name)
    staging_dir = Path(staging_dir)
    final_dir = Path(final_dir)

    if not staging_dir.is_dir():
        raise FileNotFoundError(f"staging_dir introuvable : {staging_dir}")

    missing = _missing_artifacts(staging_dir, expected_artifacts)
    if missing:
        raise ValueError(
            f"Publish incomplet pour '{asset_name}' v{version:03d} - artefact(s) manquant(s) ou "
            f"vide(s) dans {staging_dir} : {missing}. staging_dir preserve, rien commit."
        )

    os.replace(staging_dir, final_dir)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = manifest.setdefault(LOP_PUBLISHES_KEY, [])
        entry = next((e for e in existing if e.get("version") == version), None)
        if entry is None:
            raise ValueError(
                f"Aucune reservation 'pending' trouvee pour la version {version} de "
                f"'{asset_name}' (allocate_publish_version() a-t-il ete appele ?)."
            )
        entry["status"] = "complete"
        # Decouverte des fichiers reellement ecrits plutot qu'une extension supposee : en
        # licence Apprentice, Houdini ecrit '.usdnc' (watermarke) et non '.usd' (cf. contexte
        # hython/licence). Se fier au disque evite un manifeste qui pointe vers un fichier
        # inexistant selon la licence de la machine qui a publie.
        produced = sorted(p.name for p in final_dir.iterdir() if p.is_file())
        thumbs = [n for n in produced if n == LOP_THUMB_NAME]
        layers = [n for n in produced if n != LOP_THUMB_NAME]
        rel_dir = f"{LOP_DIR_NAME}/{LOP_PUBLISH_DIR_NAME}/{final_dir.name}"
        entry["layer"] = f"{rel_dir}/{layers[0]}" if layers else None
        entry["thumb"] = f"{rel_dir}/{thumbs[0]}" if thumbs else None
        entry["published_utc"] = _now()
        if comment:
            entry["comment"] = comment
        manifest["modified_utc"] = _now()
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return {
        "name": asset_name,
        "version": version,
        "final_dir": str(final_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _cli(argv=None):
    p = argparse.ArgumentParser(description="Createur projet & assets - pipeline Ylos.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("project", help="Cree un projet (coquille asset-centric).")
    pp.add_argument("name", help="Nom du projet (un seul segment, pas de /)")
    pp.add_argument("--root", help=f"Racine source (defaut ${ENV_ROOT})")
    pp.add_argument("--cache", help=f"Racine cache (defaut ${ENV_CACHE})")
    pp.add_argument("--prod-type", default="FILM", help="Type de production (defaut FILM)")
    pp.add_argument("--display-name", help="Nom affichable (defaut = name)")
    pp.add_argument("--force", action="store_true", help="Passer outre si le projet existe")

    pa = sub.add_parser("asset", help="Cree une entite (asset/set/shot) dans un projet.")
    pa.add_argument("project", help="Chemin du projet existant")
    pa.add_argument("name", help="Nom de l'entite (un seul segment, pas de /)")
    pa.add_argument("--entity-type", default="asset", choices=["asset", "set", "shot"],
                    help="Famille de l'entite (defaut asset)")
    pa.add_argument("--type", dest="asset_type", default="OTHER",
                    help="Sous-type metier (CHARACTER, ENVIRONMENT, PROP... ; defaut OTHER)")
    pa.add_argument("--steps", help="Steps separes par virgules (defaut : pipeline du projet)")
    pa.add_argument("--force", action="store_true", help="Passer outre si l'entite existe")

    pub = sub.add_parser("publish", help="Publie un fichier USD dans un step d'entite.")
    pub.add_argument("project", help="Chemin du projet existant")
    pub.add_argument("asset", help="Nom de l'entite")
    pub.add_argument("step", help="Step de publication (ex: modeling, lookdev)")
    pub.add_argument("file", help="Fichier source a publier (.usda ou .usdc)")

    args = p.parse_args(argv)

    try:
        if args.cmd == "project":
            info = create(args.name, root=args.root, cache=args.cache, force=args.force,
                          prod_type=args.prod_type, display_name=args.display_name)
            print(f"[ok] projet '{info['name']}' cree")
            print(f"  source    : {info['source']}")
            print(f"  cache     : {info['cache']}")
            print(f"  manifeste : {info['manifest']}")
        elif args.cmd == "asset":
            steps = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
            info = create_asset(args.project, args.name, entity_type=args.entity_type,
                                asset_type=args.asset_type, steps=steps, force=args.force)
            print(f"[ok] {info['entity_type']} '{info['name']}' cree")
            print(f"  path      : {info['path']}")
            print(f"  manifeste : {info['manifest']}")
            if info["asset_root"]:
                print(f"  asset_root: {info['asset_root']}")
        else:  # publish
            info = publish_asset(args.project, args.asset, args.step, args.file)
            print(f"[ok] publish {info['name']} / {info['step']} v{info['version']:03d}")
            print(f"  publish   : {info['publish_path']}")
            print(f"  manifeste : {info['manifest']}")
            if info["asset_root"]:
                print(f"  asset_root: {info['asset_root']}")
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        sys.stderr.write(f"[erreur] {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
