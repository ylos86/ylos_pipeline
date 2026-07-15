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
    python create_project.py clean-staging "<projet>"            # dry-run (rapport seul)
    python create_project.py clean-staging "<projet>" --apply    # supprime les orphelins

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
import hashlib
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

SCHEMA_VERSION = "2.1.0"          # version du contrat (project.json ET manifeste d'asset).
                                  # A bumper a CHAQUE changement de schema (= migration).
                                  # 2.1.0 : ajout de 'frame_range' (shots) - additif, aucun
                                  # manifeste 2.0 invalide (cf. docs/migration-2.0-to-2.1.md).
MANIFEST_NAME = "project.json"
ASSET_MANIFEST_NAME = "manifest.json"
ASSET_ROOT_NAME = "asset_root.usda"   # composition USD d'un asset/set (ASCII, cf. convention)
SHOT_ROOT_NAME = "shot_root.usda"     # composition USD d'un shot (root prim /ROOT, timecodes)
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

# Sous-types par famille, convention de nommage TYPE_Nom_Variant (cf. validate_entity_name).
# SET_TYPES/SHOT_TYPES miroitent app.html::FAMILY_CONFIG (seule source deja decidee pour
# ces deux familles - create_project.py etait le seul endroit qui ne les connaissait pas).
SET_TYPES = ["EXTERIOR", "INTERIOR", "HERO_SET", "MODULAR_KIT"]
SHOT_TYPES = ["LAYOUT", "ANIMATION", "FX", "LIGHTING", "COMP"]
_TYPES_BY_ENTITY = {"asset": ASSET_TYPES, "set": SET_TYPES, "shot": SHOT_TYPES}

# Types de production (project.json["prod_type"], defaut de build_manifest()). Union
# RETRO-COMPATIBLE des valeurs reellement emises, jamais inventees ni retirees (la relecture
# d'un project.json existant prime) : app.html (FILM/SERIES/GAME/XR), addon Blender
# (FILM/AR/VR) et les manifests existants (ex: Pachamama = 'XR'). Seule source de vocab pour
# le prod_type - avant, create_project ne le connaissait pas et l'enum Blender (FILM/AR/VR)
# CRASHAIT en lisant un manifest 'XR'/'SERIES'/'GAME' (cf. plugins/blender/core/vocab.py,
# op_open_context). N'implique aucune logique de scene preset (celle-ci reste cote DCC et
# no-op proprement pour un type qu'elle ne connait pas).
PROD_TYPES = ["FILM", "SERIES", "GAME", "XR", "AR", "VR"]
# Cible de pipeline par type de prod : decide le FORMAT d'artifact du publish. Decision
# d'ORCHESTRATEUR, jamais du DCC (principe 5) : les bridges consomment la cible, ne la
# calculent pas. 'web' -> GLB (Three.js) ; 'offline' -> USD. Source unique.
PROD_TYPE_TO_TARGET = {
    "XR": "web", "AR": "web", "VR": "web", "GAME": "web",
    "FILM": "offline", "SERIES": "offline",
}
DEFAULT_PIPELINE_TARGET = "offline"
LOP_DIR_NAME = "lop"
LOP_PUBLISH_DIR_NAME = "publish"
LOP_STAGING_DIR_NAME = ".staging"
LOP_THUMB_NAME = "thumb.png"
# Extensions USD composables (layer d'assemblage). '.usdnc' = watermark Apprentice, jamais
# suppose a l'avance (cf. gotcha extensions, LOP HDA). Un cache consommable ou un GLB (cf.
# PUBLISH_ARTIFACT_EXTENSIONS) n'en fait PAS partie : il passe le contrat deux-phases mais
# n'entre JAMAIS dans la composition subLayers de asset_root/shot_root (cf. _is_usd_layer).
USD_LAYER_EXTENSIONS = (".usd", ".usdc", ".usda", ".usdnc")
# Extensions d'artefact acceptees par le contrat deux-phases (_missing_artifacts) : les layers
# USD + '.glb' (bridge Blender/Three.js) + caches consommables FX publies en kind=step
# ('.vdb', '.bgeo.sc' suffixe double, '.abc'). Ces quatre derniers ne sont PAS des layers USD.
PUBLISH_ARTIFACT_EXTENSIONS = USD_LAYER_EXTENSIONS + (".glb", ".vdb", ".bgeo.sc", ".abc")
LOP_PUBLISHES_KEY = "lop_publishes"
# Publishes DCC par step (Blender USD/GLB...), generalisation du contrat deux-phases LOP a
# tout 'kind' != 'lop' (cf. allocate_publish_version). {step: [version-entry, ...]} - cle
# distincte de 'publishes' (liste de chemins, ecrite par publish_asset() legacy) pour ne
# jamais melanger les deux formes d'entree dans la meme liste.
STEP_PUBLISHES_KEY = "step_publishes"
_DIR_VER_RE = re.compile(r"_v(\d+)$")

# Ordre de force des steps pour l'empilement subLayers (plus fort / downstream en premier).
# USD : le premier sublayer de la liste est le plus fort.
DOWNSTREAM_ORDER = ["fx", "lookdev", "rigging", "uvs", "modeling",
                    "layout", "animation", "lighting", "render", "composite"]

# Ordre de force propre au SHOT (distinct de DOWNSTREAM_ORDER, qui est correct pour un asset
# mais faux pour un shot) : sur un shot le lighting override l'animation, l'inverse d'un asset.
# 'comp' est declare pour l'ordre mais ne produit jamais de layer USD (2D) - simplement jamais
# present dans les publishes. NE PAS reutiliser DOWNSTREAM_ORDER ici (cf. plan Increment 1).
SHOT_DOWNSTREAM_ORDER = ["comp", "lighting", "fx", "animation", "layout"]

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


def entity_cache_dir(project_root, entity_name, step, label):
    """Dossier de cache scratch d'un step : $PROJ_CACHE/<projet>/houdini/<entite>/<step>/
    <label>/ (tier regenerable, cf. CLAUDE.md - stockage 3 tiers). Logique UNIQUE de
    resolution (principe 5) : le bridge Houdini pose sur le noeud filecache l'EXPRESSION
    litterale '$PROJ_CACHE/...' (relocalisable, cf. ylos_houdini.cache_dir_expression),
    tandis que le chemin resolu (cette fonction) vit ici. Cree les parents (mkdir), retourne
    le Path. Aucune trace au manifeste : un cache scratch est jetable, son versioning est
    celui natif du filecache (v1/v2...), pas un contrat deux-phases."""
    _validate_segment(label)
    cache_dir = (resolve_cache() / Path(project_root).name / "houdini"
                 / entity_name / step / label)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


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


def _atomic_write_text(path, content, encoding="utf-8"):
    """Ecrit 'content' dans 'path' de facon atomique (tmp + os.replace, meme motif que
    finalize_publish_version() utilise deja pour le rename staging->final). Protege contre
    un fichier tronque/corrompu si le process crashe pendant l'ecriture - acquire_lock()
    protege la concurrence entre process, pas un crash mi-ecriture ; les deux sont
    complementaires."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)


def _atomic_write_json(path, data, indent=2):
    """Serialise 'data' en JSON et l'ecrit via _atomic_write_text (cf. sa docstring)."""
    _atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


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
        # Cible de pipeline = FORMAT d'artifact du publish (derive du prod_type, source unique
        # PROD_TYPE_TO_TARGET). Ecrite a la creation ; lue tolerablement par get_pipeline_target.
        "pipeline_target": PROD_TYPE_TO_TARGET.get(prod_type, DEFAULT_PIPELINE_TARGET),
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
    _atomic_write_json(path, manifest)
    return path


def read_manifest(project_dir):
    """Lit project.json depuis <projet>/_pipeline. Utile aux plugins / launchers."""
    path = Path(project_dir) / PIPELINE_DIR / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def get_pipeline_target(project_root):
    """Cible de pipeline d'un projet : 'web' (artifacts GLB, Three.js) ou 'offline' (USD).
    Le FORMAT d'artifact est une decision d'ORCHESTRATEUR (principe 5), jamais du DCC : les
    bridges (Blender op_publish) consomment cette cible, ils ne la calculent pas. Lecture
    TOLERANTE (ne leve jamais pour un cas metier) : champ 'pipeline_target' du manifeste s'il
    est valide, sinon derive du prod_type (PROD_TYPE_TO_TARGET), defaut 'offline' -> un projet
    2.0 sans le champ (ou manifeste illisible) degrade proprement."""
    try:
        manifest = read_manifest(project_root)
    except (OSError, ValueError):
        return DEFAULT_PIPELINE_TARGET
    target = manifest.get("pipeline_target")
    if target in ("web", "offline"):
        return target
    return PROD_TYPE_TO_TARGET.get(manifest.get("prod_type"), DEFAULT_PIPELINE_TARGET)


def read_active_project(path=None):
    """Projet actif de la machine (Path) ou None. Contrat : ~/.ylos/active_project, une
    ligne, chemin absolu - ecrit par l'UI web (POST /api/set-project). Lecteur UNIQUE,
    partage par ylos_ui et le module Houdini (le default_expression du HDA ylos::publish
    garde sa copie inline : une expression de parametre embarquee ne peut pas dependre
    d'un import). Path.home() resolu a l'appel, pas a l'import (les tests hython basculent
    HOME en cours de session, cf. test_publish_hda_e2e)."""
    if path is None:
        path = Path.home() / ".ylos" / "active_project"
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


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
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "entity_type": entity_type,
        "type": asset_type,
        "steps": list(steps),
        "publishes": {s: [] for s in steps},
        "created_utc": now,
        "modified_utc": now,
    }
    # Un shot porte sa plage d'images (schema 2.1). Defaut editable via set_frame_range().
    # Les autres familles n'ont pas de frame_range (cle absente = pas de timecodes).
    if entity_type == "shot":
        manifest["frame_range"] = {
            "start": 1001, "end": 1100, "fps": DEFAULT_SCENE["fps"],
        }
    return manifest


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


def _num_str(value):
    """Serialise un nombre USD sans '.0' parasite (24 plutot que 24.0), float sinon."""
    return str(int(value)) if float(value).is_integer() else str(value)


def build_shot_root(name, latest, frame_range=None):
    """Reconstruit shot_root.usda depuis {step: chemin_relatif_du_latest_publish}. Miroir de
    build_asset_root pour un SHOT : root prim /ROOT (defaultPrim "ROOT", cf. USD_ROOT_PRIM),
    subLayers ordonnes SHOT_DOWNSTREAM_ORDER (plus fort/downstream en premier - lighting
    override l'anim). 'frame_range' ({start, end, fps}) present (schema 2.1) -> timecodes
    dans le header de stage ; absent -> pas de timecodes (cf. docs/usd-convention.md)."""
    ordered = [s for s in SHOT_DOWNSTREAM_ORDER if s in latest]
    ordered += [s for s in latest if s not in SHOT_DOWNSTREAM_ORDER]
    prim = USD_ROOT_PRIM.lstrip("/")
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{prim}"',
        f'    upAxis = "{USD_UP_AXIS}"',
        f"    metersPerUnit = {_meters_per_unit_str()}",
    ]
    if frame_range:
        lines += [
            f"    startTimeCode = {int(frame_range['start'])}",
            f"    endTimeCode = {int(frame_range['end'])}",
            f"    timeCodesPerSecond = {_num_str(frame_range['fps'])}",
        ]
    lines.append("    subLayers = [")
    for s in ordered:
        lines.append(f"        @{latest[s]}@,")
    lines += [
        "    ]",
        ")",
        "",
        f'def Xform "{prim}"',
        "{",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _is_usd_layer(path):
    """True si 'path' pointe un layer USD composable (extension USD, watermark Apprentice
    inclus). Un cache consommable (.vdb/.bgeo.sc/.abc) ou un GLB publie en kind=step passe le
    contrat deux-phases mais n'est PAS un layer USD : il n'entre jamais dans la composition
    subLayers (asset_root/shot_root) - filtre explicite dans _latest_by_step (plan Increment 5).
    Un dossier de sequence (artefact = nom de dossier, sans extension) n'est pas USD non plus."""
    return str(path).endswith(USD_LAYER_EXTENSIONS)


def _latest_from_publishes(publishes):
    """Retourne {step: chemin_latest} depuis manifest.publishes (dict step -> [paths])."""
    return {step: max(paths, key=_ver) for step, paths in publishes.items() if paths}


def _latest_by_step(manifest):
    """{step: chemin_relatif_du_latest_publish 'complete'} pour la composition d'un root,
    en FUSIONNANT les deux sources d'un manifeste :
    - 'publishes' legacy (dict step -> [chemins], ecrit par publish_asset() deprecie) ;
    - 'step_publishes' (contrat deux-phases, dict step -> [entrees], cle 'artifact',
      statut 'complete').
    A step egal, le contrat deux-phases prime (source vivante). Les publishes LOP
    (lop_publishes) ne sont JAMAIS lus ici : un LOP est un instantane complet hors
    taxonomie de steps, il n'entre pas dans la composition subLayers."""
    latest = _latest_from_publishes(manifest.get("publishes", {}))
    for step, entries in manifest.get(STEP_PUBLISHES_KEY, {}).items():
        # Seuls les layers USD entrent en composition : un cache consommable (.vdb/.bgeo.sc/
        # .abc) ou un GLB publie en kind=step est filtre AVANT le max (un step avec un VDB plus
        # recent mais un USD plus ancien compose quand meme son latest USD, pas le VDB).
        complete = [e for e in entries
                    if e.get("status") == "complete" and e.get("artifact")
                    and _is_usd_layer(e["artifact"])]
        if complete:
            latest[step] = max(complete, key=lambda e: e["version"])["artifact"]
    return latest


def _compose_entity_root(entity_dir, manifest, entity_name):
    """Ecrit le fichier root d'assemblage depuis un manifeste DEJA charge - composeur UNIQUE
    (principe 5, CLAUDE.md). Appele SOUS le flock du manifeste, par les deux entrees :
    refresh_entity_root() (publique, prend le flock) et finalize_publish_version() (deja
    dans son flock). Retourne le Path ecrit. NE prend PAS le flock lui-meme (acquire_lock
    ouvre un nouveau fd bloquant a chaque appel : re-verrouiller ici = interblocage)."""
    latest = _latest_by_step(manifest)
    name = manifest.get("name", entity_name)
    if manifest.get("entity_type") == "shot":
        content = build_shot_root(name, latest, manifest.get("frame_range"))
        root_path = entity_dir / SHOT_ROOT_NAME
    else:
        content = build_asset_root(name, latest)
        root_path = entity_dir / ASSET_ROOT_NAME
    _atomic_write_text(root_path, content)
    return root_path


def refresh_entity_root(project_root, entity_name):
    """Recompose le fichier root d'assemblage d'une entite depuis ses publishes 'complete'
    (latest par step) - point d'entree public, prend le flock du manifeste :
    - asset/set -> asset_root.usda (defaultPrim <Nom>, ordre DOWNSTREAM_ORDER) ;
    - shot      -> shot_root.usda  (root prim /ROOT, ordre SHOT_DOWNSTREAM_ORDER, timecodes
                   depuis frame_range si present au manifeste).
    Chemins de subLayers relatifs a l'entite (le root vit a sa racine). Retourne le Path ecrit.
    finalize_publish_version() appelle _compose_entity_root() directement (deja sous flock)."""
    entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _compose_entity_root(entity_dir, manifest, entity_name)


def set_frame_range(project_root, shot_name, start, end, fps=None):
    """Pose / actualise la plage d'images d'un SHOT (schema 2.1) puis recompose son
    shot_root.usda (timecodes). 'start' < 'end' requis ; l'entite doit etre un shot. 'fps'
    None -> conserve le fps existant du manifeste, sinon le defaut scene. Ecriture atomique
    sous acquire_lock ; la recomposition est faite APRES relachement du flock (via
    refresh_entity_root, qui reprend son propre flock - acquire_lock n'est pas reentrant,
    cf. CLAUDE.md). Retourne le frame_range ecrit."""
    start, end = int(start), int(end)
    if start >= end:
        raise ValueError(f"frame_range invalide : start ({start}) doit etre < end ({end})")
    entity_dir, manifest_path = _find_asset_entity(project_root, shot_name)
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("entity_type") != "shot":
            raise ValueError(
                f"frame_range reserve aux shots : '{shot_name}' est de type "
                f"{manifest.get('entity_type')!r}."
            )
        if fps is None:
            fps = manifest.get("frame_range", {}).get("fps", DEFAULT_SCENE["fps"])
        frame_range = {"start": start, "end": end, "fps": fps}
        manifest["frame_range"] = frame_range
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)
    refresh_entity_root(project_root, shot_name)
    return frame_range


# --------------------------------------------------------------------------------------
# Resolution du fichier a OUVRIR pour une entite+step (consomme par les bridges DCC)
# --------------------------------------------------------------------------------------

_WIP_VER_RE = re.compile(r"_v(\d+)\.blend$")


def _latest_wip(entity_dir, step):
    """Dernier WIP .blend d'un step Blender : entity_dir/<step>/wip/<name>_<step>_vNNN.blend
    (plus haut numero). Retourne (Path, version) ou (None, 0). Ne leve jamais - un dossier
    absent (step non scaffolde) renvoie simplement (None, 0)."""
    wip_dir = Path(entity_dir) / step / "wip"
    if not wip_dir.is_dir():
        return None, 0
    best, best_ver = None, -1
    for f in wip_dir.iterdir():
        if not f.is_file() or f.suffix.lower() != ".blend":
            continue
        m = _WIP_VER_RE.search(f.name)
        if m and int(m.group(1)) > best_ver:
            best, best_ver = f, int(m.group(1))
    return best, (best_ver if best is not None else 0)


def _latest_step_publish_rel(manifest, step):
    """Chemin RELATIF (a l'entite) du dernier publish USD 'complete' du step - contrat
    deux-phases (step_publishes[step], cle 'artifact'). Resolution CORRECTE du dossier
    par-version niche (entity_dir/<step>/publish/<versioned_name>/<fichier>), la ou l'addon
    Blender scannait a tort des fichiers PLATS et ne trouvait donc jamais un publish deux-
    phases (cf. resolve_open_target, fix op_open_context). Filtre aux layers USD composables
    (_is_usd_layer) - un cache consommable/GLB n'est pas un fichier a ouvrir comme scene."""
    entries = manifest.get(STEP_PUBLISHES_KEY, {}).get(step, [])
    complete = [e for e in entries
                if e.get("status") == "complete" and e.get("artifact")
                and _is_usd_layer(e["artifact"])]
    if not complete:
        return None
    return max(complete, key=lambda e: e.get("version", 0))["artifact"]


# Extensions USD reconnues comme publish a plat legacy (publish_asset() deprecie ecrivait
# <step>/publish/<name>_<step>_vNNN.<ext>). '.usdz' inclus (livrable) ; les autres = layers
# composables. Un publish deux-phases est un DOSSIER, jamais un fichier -> jamais confondu.
_LEGACY_PUBLISH_EXTS = USD_LAYER_EXTENSIONS + (".usdz",)


def list_publishes(project_root, entity_name, step, entity_type="asset"):
    """API publique de LECTURE des publishes d'un step (la logique vit dans l'orchestrateur,
    les consommateurs DCC/UI sont minces - principe 5). Ne leve JAMAIS pour un cas metier
    (entite/step introuvable) : retourne []. Fusionne deux sources, sans doublon de version :

    - **manifest-first** : contrat deux-phases (manifest['step_publishes'][step]). Chaque
      entree est renvoyee telle quelle (copie) + enrichie 'abs_path' (chemin absolu de
      'artifact', ou None) et 'exists' (bool). 'legacy'=False.
    - **fallback fichiers plats legacy** : scan disque de <entity>/<step>/publish/ pour les
      FICHIERS versionnes (pattern '_vNNN.<ext>' USD, cf. _LEGACY_PUBLISH_EXTS - un dossier
      deux-phases a `f.is_file()` False, jamais capte). Entrees {version, status:'complete',
      artifact (rel), abs_path, exists:True, legacy:True}. Un numero deja present cote
      deux-phases n'est PAS ecrase (le contrat vivant prime).

    Retour trie par version croissante."""
    project_root = Path(project_root)
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    except FileNotFoundError:
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    by_version = {}  # version -> entree enrichie

    for e in manifest.get(STEP_PUBLISHES_KEY, {}).get(step, []):
        ver = e.get("version")
        if ver is None:
            continue
        entry = dict(e)
        artifact = e.get("artifact")
        abs_path = (entity_dir / artifact) if artifact else None
        entry["abs_path"] = str(abs_path) if abs_path is not None else None
        entry["exists"] = bool(abs_path is not None and abs_path.exists())
        entry["legacy"] = False
        by_version[ver] = entry

    pub_dir = entity_dir / step / "publish"
    if pub_dir.is_dir():
        for f in sorted(pub_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _LEGACY_PUBLISH_EXTS:
                continue
            m = _VER_RE.search(f.name)
            if not m:
                continue
            ver = int(m.group(1))
            if ver in by_version:
                continue  # le deux-phases prime a version egale
            by_version[ver] = {
                "version": ver,
                "status": "complete",
                "artifact": f"{step}/{LOP_PUBLISH_DIR_NAME}/{f.name}",
                "abs_path": str(f),
                "exists": True,
                "legacy": True,
            }

    return [by_version[v] for v in sorted(by_version)]


def latest_publish_artifact(project_root, entity_name, step, entity_type="asset"):
    """Entree publish 'complete' de version max pour le step (deux-phases + legacy fusionnes,
    cf. list_publishes), enrichie 'abs_path'/'exists'/'legacy'. dict ou None (aucun publish
    'complete'). Ne leve jamais pour un cas metier. Generalisation disque-aware de
    _latest_step_publish_rel() (qui, lui, opere sur un manifeste deja en memoire et filtre
    aux seuls layers USD pour la composition/ouverture)."""
    complete = [e for e in list_publishes(project_root, entity_name, step, entity_type)
                if e.get("status") == "complete"]
    if not complete:
        return None
    return max(complete, key=lambda e: e.get("version", 0))


def resolve_open_target(entity_name, dcc="blender", step=None, project_root=None):
    """Resout QUEL fichier un DCC doit ouvrir pour une entite+step. La logique vit dans
    l'orchestrateur (principe 5) : reutilisable par Blender ET Houdini, l'addon ne fait que
    consommer. NE LEVE JAMAIS pour un cas metier (projet/entite/step introuvable, valeur
    d'enum inconnue lue au manifeste, aucun fichier candidat) : renvoie un dict exists=False
    avec 'reason'. Les seules exceptions possibles seraient des bugs de programmation.

    Parametres :
      entity_name  : nom d'entite (asset/set/shot) - localise via _find_asset_entity.
      dcc          : DCC cible ('blender' par defaut). Seul 'blender' resout des WIP .blend.
      step         : step vise ; None -> premier step declare au manifeste (fallback).
      project_root : racine projet ; None -> projet actif (read_active_project(), contrat
                     ~/.ylos/active_project).

    Ordre de resolution (dcc='blender') :
      1. dernier WIP .blend du step                                    -> kind='wip'
      2. scene par defaut de l'entite = son root d'assemblage
         (shot_root.usda / asset_root.usda, qui reference deja les latest
         publishes en subLayers). Pas de template .blend par step au
         scaffold : le root compose est la scene par defaut a ouvrir.       -> kind='scene_default'
      3. dernier publish USD 'complete' du step (chemin niche correct)   -> kind='publish'
      4. echec explicite                                                -> exists=False

    Retour : {"path": str|None, "kind": "wip"|"scene_default"|"publish"|None,
              "step": str|None, "exists": bool, "reason": str (present si exists=False)}."""
    if project_root is None:
        project_root = read_active_project()
    if project_root is None:
        return {"path": None, "kind": None, "step": step, "exists": False,
                "reason": "aucun projet actif (project_root=None et ~/.ylos/active_project absent)"}

    project_root = Path(project_root)
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    except FileNotFoundError as exc:
        return {"path": None, "kind": None, "step": step, "exists": False, "reason": str(exc)}

    # Manifeste lu de facon TOLERANTE : une valeur d'enum inconnue (prod_type/type legacy,
    # ex 'XR', 'ZZ_UNKNOWN') ne doit jamais faire lever - on ne valide rien, on resout des
    # chemins. Un manifeste illisible degrade proprement (dict vide).
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    entity_type = manifest.get("entity_type", "asset")
    steps = manifest.get("steps", [])
    if step is None:
        step = steps[0] if steps else None
    # step peut rester None (manifeste sans steps / corrompu) : les branches WIP et publish
    # sont step-dependantes et donc sautees, mais la scene par defaut (root d'assemblage,
    # step-agnostique) reste resoluble -> degradation propre, jamais d'exception.

    # 1. dernier WIP (Blender uniquement, step requis)
    if dcc == "blender" and step:
        wip, _wver = _latest_wip(entity_dir, step)
        if wip is not None:
            return {"path": str(wip), "kind": "wip", "step": step, "exists": True}

    # 2. scene par defaut = root d'assemblage de l'entite (step-agnostique)
    root_name = SHOT_ROOT_NAME if entity_type == "shot" else ASSET_ROOT_NAME
    default_path = entity_dir / root_name
    if default_path.is_file():
        return {"path": str(default_path), "kind": "scene_default", "step": step, "exists": True}

    # 3. dernier publish USD du step (chemin niche correct, jamais un scan de fichiers plats)
    if step:
        rel = _latest_step_publish_rel(manifest, step)
        if rel is not None:
            pub = entity_dir / rel
            if pub.is_file():
                return {"path": str(pub), "kind": "publish", "step": step, "exists": True}

    # 4. echec explicite (cas metier, pas une exception)
    return {"path": None, "kind": None, "step": step, "exists": False,
            "reason": (f"aucun WIP pour le step {step!r}, ni scene par defaut ({root_name}), "
                       f"ni publish USD pour '{entity_name}'")}


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
    # Validation de nommage a la creation - point unique (cf. validate_entity_name) : couvre
    # web UI, Blender, CLI, futur. _validate_segment protege le chemin, ceci protege la
    # convention metier TYPE_Nom_Variant.
    validate_entity_name(name, entity_type, asset_type)

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
    _atomic_write_json(manifest_path, manifest)

    # 3. stub d'assemblage USD (asset/set ; un shot compose differemment)
    asset_root_path = None
    if entity_type in ("asset", "set"):
        asset_root_path = entity_dir / ASSET_ROOT_NAME
        _atomic_write_text(asset_root_path, asset_root_usda(name))

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
    """DEPRECIE - publie source_file dans <asset>/<step>/publish/ avec versioning
    automatique, en ecriture directe (pas de staging, pas de thumbnail requis).

    Remplace par le contrat deux-phases allocate_publish_version()/finalize_publish_version()
    (kind=<step>), adopte par tous les bridges DCC (Houdini LOP, Blender USD/GLB) - garantit
    un thumbnail et un commit atomique via staging_dir. Conserve pour compatibilite
    (aucun appelant restant dans ce repo depuis la migration Blender), ne pas utiliser pour
    du nouveau code.

    - Scanne manifest.publishes[step] pour determiner la prochaine version (v001, v002...).
    - Copie source_file -> <step>/publish/<asset>_<step>_v<NNN><ext> (jamais d'ecrasement).
    - Met a jour manifest.json (publishes[step] et modified_utc).
    - Reconstruit asset_root.usda (subLayers) pour les entites asset/set.

    Retourne {name, step, version, publish_path, manifest, asset_root}.
    Non-destructif : leve FileExistsError si la version cible existe deja.
    """
    import warnings
    warnings.warn(
        "publish_asset() est deprecie - utiliser allocate_publish_version()/"
        "finalize_publish_version() (kind=<step>), le contrat deux-phases avec thumbnail "
        "requis adopte par tous les bridges DCC.",
        DeprecationWarning,
        stacklevel=2,
    )
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
        _atomic_write_json(manifest_path, manifest)

        # Reconstruire asset_root.usda (asset/set uniquement)
        asset_root_path = None
        entity_type = manifest.get("entity_type", "asset")
        if entity_type in ("asset", "set"):
            content = build_asset_root(manifest.get("name", asset_name),
                                       _latest_from_publishes(publishes))
            asset_root_path = entity_dir / ASSET_ROOT_NAME
            _atomic_write_text(asset_root_path, content)

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

def _suggested_entity_name(name, sub_type):
    """Propose un nom conforme a partir d'un nom brut invalide : capitalise, retire un
    eventuel prefixe existant (mal forme), variant 'Default' par defaut."""
    base = name.split("_")[-1] if "_" in name else name
    base = base[:1].upper() + base[1:] if base else base
    return f"{sub_type}_{base}_Default"


def validate_entity_name(name, entity_type, sub_type):
    """Valide 'name' contre la convention TYPE_Nom_Variant (TYPE = sub_type, restreint a la
    liste valide pour 'entity_type' - asset/set/shot, cf. _TYPES_BY_ENTITY). Match par
    prefixe exact (et non un split('_') naif) car certains types contiennent deja un
    underscore (FX_ELEMENT) : 'FX_ELEMENT_Drone_Default' a 4 segments '_', pas 3.

    Point unique de validation nommage, appele par create_asset() a la creation (couvre web
    UI, Blender, CLI, futur) - et par allocate_publish_version() au publish LOP (contrat
    historique inchange, cf. validate_publish_asset_name)."""
    valid_types = _TYPES_BY_ENTITY.get(entity_type)
    if valid_types is None:
        raise ValueError(f"entity_type invalide : {entity_type!r} (asset|set|shot)")
    if sub_type not in valid_types:
        raise ValueError(
            f"type invalide : {sub_type!r} (attendu un de {valid_types} pour entity_type={entity_type!r})"
        )
    prefix = f"{sub_type}_"
    valid = False
    if name.startswith(prefix):
        remainder = name[len(prefix):]
        parts = remainder.split("_")
        valid = len(parts) == 2 and all(parts)
    if not valid:
        suggestion = _suggested_entity_name(name, sub_type)
        raise ValueError(
            f"{name!r} invalide - suggestion : {suggestion!r}. "
            f"Convention : TYPE_Nom_Variant, familles valides : {', '.join(valid_types)}."
        )
    return True


def validate_publish_asset_name(asset_name, asset_type):
    """Alias historique de validate_entity_name(asset_name, 'asset', asset_type) - conserve
    pour compatibilite (Houdini HDA, tests, allocate_publish_version)."""
    return validate_entity_name(asset_name, "asset", asset_type)


def _find_asset_entity(project_root, asset_name):
    """Localise une entite deja creee (assets|sets|shots/<name>/manifest.json), quelle que
    soit sa famille (meme scan que publish_asset()). Un publish (LOP ou par step) n'est
    jamais createur d'entite : create_asset() doit avoir ete appele avant."""
    project_root = Path(project_root)
    for family in ENTITY_DIR.values():
        candidate = project_root / family / asset_name
        manifest_path = candidate / ASSET_MANIFEST_NAME
        if manifest_path.is_file():
            return candidate, manifest_path
    raise FileNotFoundError(
        f"Entite '{asset_name}' introuvable sous {project_root} (assets/, sets/, shots/) "
        f"(doit etre creee via create_asset() avant tout publish)."
    )


def resolve_entity(project_root, name):
    """Resout une entite deja creee par son nom, quelle que soit sa famille (assets/sets/
    shots) - wrapper PUBLIC de _find_asset_entity pour les consommateurs DCC (le State
    Manager Blender doit connaitre la famille d'une entite ciblee sans la stocker sur le
    state ; un bridge n8n en aura aussi besoin). Principe 5 : la resolution d'entite vit
    dans l'orchestrateur, pas dans le plugin. Ne leve JAMAIS pour un cas metier : retourne
    None si l'entite est introuvable OU son manifeste illisible. Retour :
    {"name","family","entity_type","dir","manifest"} - 'family' = cle ENTITY_DIR
    ('asset'|'set'|'shot', pour is_step_valid_for_context), 'entity_type' = sous-type
    (CHARACTER/PROP/...) du manifeste."""
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, name)
    except FileNotFoundError:
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    family = manifest.get("entity_type", "asset")
    if family not in ENTITY_DIR:
        # Manifeste incoherent -> le dossier disque fait foi (principe : source lisible).
        parent = entity_dir.parent.name
        family = next((k for k, v in ENTITY_DIR.items() if v == parent), "asset")
    return {
        "name": name,
        "family": family,
        "entity_type": manifest.get("type", ""),
        "dir": str(entity_dir),
        "manifest": manifest,
    }


def publish_version_from_dir(final_dir):
    """Extrait le numero de version d'un final_dir retourne par allocate_publish_version()
    (ex: 'CHARACTER_Lina_Default_lop_v003' -> 3). Distinct de _ver() : un final_dir est un
    nom de repertoire sans extension (le numero termine le nom), _ver() attend un nom de
    fichier versionne avec extension (cf. publish_asset)."""
    m = _DIR_VER_RE.search(Path(final_dir).name)
    if not m:
        raise ValueError(f"final_dir ne contient pas de suffixe de version : {final_dir!r}")
    return int(m.group(1))


def _publish_dirs(entity_dir, kind):
    """Sous-arbre de publish pour 'kind' : 'lop' (whole-asset LOP Houdini, historique) ou un
    nom de step (Blender/DCC par step, ex 'modeling') - reutilise entity_dir/<step> deja
    scaffolde par create_asset() (wip/, publish/). Retourne (publish_root, staging_root)."""
    base = entity_dir / (LOP_DIR_NAME if kind == "lop" else kind)
    return base / LOP_PUBLISH_DIR_NAME, base / LOP_STAGING_DIR_NAME


def _publish_entries(manifest, kind):
    """Liste des entrees de version pour 'kind' dans le manifeste (creee si absente).
    kind='lop' -> manifest[LOP_PUBLISHES_KEY] (liste plate, contrat historique inchange).
    Tout autre kind -> manifest[STEP_PUBLISHES_KEY][kind] (dict step -> liste, memes
    entrees) - cle distincte pour ne jamais collisionner avec 'publishes' (legacy)."""
    if kind == "lop":
        return manifest.setdefault(LOP_PUBLISHES_KEY, [])
    return manifest.setdefault(STEP_PUBLISHES_KEY, {}).setdefault(kind, [])


def allocate_publish_version(project_root, asset_name, asset_type=None, comment=None, kind="lop"):
    """Reserve atomiquement (fcntl.flock) le prochain numero de version de publish pour un
    asset existant, et cree un repertoire de staging vide. Ne touche a aucun artefact :
    l'appelant (callback HDA ou operateur Blender) les ecrit dans staging_dir, puis appelle
    finalize_publish_version() pour committer (os.replace atomique, meme filesystem que
    staging_dir car les deux vivent sous entity_dir/<kind>/) et finaliser le manifeste.

    'kind' (mot-cle, defaut 'lop' pour compatibilite Houdini) : 'lop' pour un publish LOP
    (instantane complet, hors taxonomie de steps - contrat historique inchange, 'asset_type'
    requis + valide via validate_publish_asset_name) ; ou un nom de step (ex 'modeling',
    'lookdev') pour un publish DCC par step (Blender USD/GLB...) - le nommage est deja
    garanti par create_asset() (cf. validate_entity_name), pas de revalidation ici et
    'asset_type' est ignore.

    Retourne (staging_dir, final_dir) en pathlib.Path. staging_dir existe deja (vide) ;
    final_dir n'existe pas encore (c'est la cible du futur replace).
    """
    if kind == "lop":
        validate_publish_asset_name(asset_name, asset_type)

    project_root = Path(project_root)
    entity_dir, manifest_path = _find_asset_entity(project_root, asset_name)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if kind == "lop":
            declared_type = manifest.get("type")
            if declared_type != asset_type:
                raise ValueError(
                    f"asset_type {asset_type!r} ne correspond pas au type declare de "
                    f"'{asset_name}' dans manifest.json ({declared_type!r})."
                )

        existing = _publish_entries(manifest, kind)
        next_ver = max((e.get("version", 0) for e in existing), default=0) + 1

        versioned_name = f"{asset_name}_{kind}_v{next_ver:03d}"
        publish_root, staging_root = _publish_dirs(entity_dir, kind)
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
        _atomic_write_json(manifest_path, manifest)

    return staging_dir, final_dir


def _missing_artifacts(staging_dir, expected_artifacts):
    """Verifie que chaque entree de expected_artifacts existe et est non-vide dans staging_dir.
    Une entree avec un '.' est un nom exact (ex: 'thumb.png') - branche PRIORITAIRE, jamais
    interpretee comme dossier. Une entree sans '.' est soit un stem d'artefact (layer USD, GLB
    ou cache .vdb/.bgeo.sc/.abc), matchee contre PUBLISH_ARTIFACT_EXTENSIONS (jamais d'extension
    supposee a l'avance - Apprentice ecrit '.usdnc', commerciale '.usd'/'.usdc'/'.usda', Blender
    '.glb'), soit un dossier de sequence (sim multi-frames) - accepte s'il existe et est non-vide.

    Retourne la liste des entrees manquantes/vides (liste vide = tout est present)."""
    missing = []
    for artifact in expected_artifacts:
        if "." in artifact:
            candidate = staging_dir / artifact
            if not candidate.is_file() or candidate.stat().st_size == 0:
                missing.append(artifact)
        else:
            matches = [
                staging_dir / f"{artifact}{ext}" for ext in PUBLISH_ARTIFACT_EXTENSIONS
                if (staging_dir / f"{artifact}{ext}").is_file()
                and (staging_dir / f"{artifact}{ext}").stat().st_size > 0
            ]
            seq_dir = staging_dir / artifact
            seq_ok = seq_dir.is_dir() and any(seq_dir.iterdir())
            if not matches and not seq_ok:
                missing.append(artifact)
    return missing


def finalize_publish_version(project_root, asset_name, staging_dir, final_dir, version,
                             expected_artifacts, comment=None):
    """Commit atomique d'un publish prealablement reserve par allocate_publish_version() :
    os.replace(staging_dir, final_dir) - point de commit unique pour TOUT ce que le staging
    contient (artefact + thumb.png) - puis mise a jour du manifeste sous flock (entree
    'pending' -> 'complete'). A appeler une fois que l'appelant (callback HDA, operateur
    Blender) a ecrit l'artefact et le thumbnail dans staging_dir.

    'kind' (lop ou nom de step) n'est PAS un parametre separe : il est retrouve depuis la
    structure de final_dir (entity_dir/<kind>/publish/<versioned_name>, cf.
    allocate_publish_version/_publish_dirs) - signature inchangee pour ne pas casser les
    appelants existants (build_publish_hda.py, test_publish_hda_e2e.py).

    expected_artifacts : liste de noms requis dans staging_dir avant le commit (ex:
    ['CHARACTER_Lina_Default_lop_v003', 'thumb.png'] - l'artefact par son stem, resolu contre
    les extensions connues (PUBLISH_ARTIFACT_EXTENSIONS) ; le thumb par son nom exact). Le
    thumbnail est REQUIS partout. Si un artefact manque ou est vide : leve ValueError, ne
    touche PAS staging_dir, n'appelle PAS os.replace, n'ecrit RIEN au manifeste (la
    reservation reste 'pending').

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

    kind_dirname = final_dir.parent.parent.name
    kind = "lop" if kind_dirname == LOP_DIR_NAME else kind_dirname

    os.replace(staging_dir, final_dir)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = _publish_entries(manifest, kind)
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
        # inexistant selon la licence/le DCC qui a publie.
        # Fichiers ET dossiers : un artefact de sequence (sim multi-frames, cf.
        # _missing_artifacts mode dossier) est un sous-dossier, jamais un fichier - l'ignorer
        # laisserait 'artifact' a None au manifeste. L'entree pointe alors le dossier.
        produced = sorted(p.name for p in final_dir.iterdir() if p.is_file() or p.is_dir())
        thumbs = [n for n in produced if n == LOP_THUMB_NAME]
        artifacts = [n for n in produced if n != LOP_THUMB_NAME]
        rel_dir = f"{kind_dirname}/{LOP_PUBLISH_DIR_NAME}/{final_dir.name}"
        # 'layer' conserve pour kind='lop' (contrat lu par tools/houdini/*.py) ; 'artifact'
        # pour tout le reste (generique - USD ou GLB selon le DCC appelant).
        artifact_key = "layer" if kind == "lop" else "artifact"
        entry[artifact_key] = f"{rel_dir}/{artifacts[0]}" if artifacts else None
        thumb_rel = f"{rel_dir}/{thumbs[0]}" if thumbs else None
        entry["thumb"] = thumb_rel
        # 'thumbnail' : meme chemin relatif entite que 'thumb', renseigne quand thumb.png
        # existe dans le dossier finalise (le champ etait absent -> lu None par les
        # consommateurs qui l'attendent). 'thumb' conserve pour compat (lecteurs existants).
        entry["thumbnail"] = thumb_rel
        entry["published_utc"] = _now()
        if comment:
            entry["comment"] = comment
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)

        # Recomposition du root d'assemblage (asset_root.usda / shot_root.usda) pour un
        # publish de STEP (kind != 'lop') : un step alimente la composition subLayers, un
        # LOP est un instantane complet hors taxonomie (jamais compose). Dans le meme flock,
        # depuis le manifeste deja mis a jour - _compose_entity_root ne re-verrouille pas.
        if kind != "lop":
            _compose_entity_root(entity_dir, manifest, asset_name)

    return {
        "name": asset_name,
        "version": version,
        "final_dir": str(final_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# Sweep des allocations orphelines - un staging_dir ne survit sur disque QUE si
# finalize_publish_version() n'a jamais ete appele (elle le consomme via os.replace) :
# un staging_dir present = allocation abandonnee (crash, kill -9...) OU publish en cours
# (process encore vivant). Distingue les deux via le PID encode dans le nom du dossier
# (cf. allocate_publish_version : '<versioned_name>.staging-<pid>').
# --------------------------------------------------------------------------------------

_STAGING_PID_RE = re.compile(r"\.staging-(\d+)$")


def _staging_pid(dirname):
    """Extrait le PID depuis un nom de staging_dir. None si le nom ne matche pas le motif
    (defensif - un staging_dir mal nomme n'est jamais touche par clean_stale_staging)."""
    m = _STAGING_PID_RE.search(dirname)
    return int(m.group(1)) if m else None


def _pid_alive(pid):
    """True si un process avec ce PID existe (kill(pid, 0), pas un vrai signal)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # le process existe, on n'a juste pas le droit de le signaler
    return True


def clean_stale_staging(project_root, dry_run=False):
    """Balaie tous les staging_dirs (entity_dir/<kind>/.staging/*, LOP ou step) du projet.

    Supprime (sauf dry_run=True) ceux dont le PID createur n'est plus vivant - jamais un
    staging_dir dont le process tourne encore (publish potentiellement en cours). Rapporte
    separement (jamais de suppression, meme sans dry_run) les entrees manifest.json restees
    'status': 'pending' sans staging_dir correspondant sur disque - une incoherence a
    investiguer manuellement (le manifeste n'est pas une donnee jetable comme staging_dir ;
    cf. CLAUDE.md sur project.json comme contrat).

    Retourne {"removed_staging": [str, ...], "pending_without_staging": [
        {"entity", "kind", "version", "manifest"}, ...]}.
    """
    project_root = Path(project_root)
    removed = []
    pending_without_staging = []

    for family in ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for entity_dir in sorted(family_dir.iterdir()):
            if not entity_dir.is_dir():
                continue
            manifest_path = entity_dir / ASSET_MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            # 1. Staging orphelins : scan de chaque sous-arbre <kind>/.staging/.
            for kind_dir in sorted(entity_dir.iterdir()):
                if not kind_dir.is_dir():
                    continue
                staging_root = kind_dir / LOP_STAGING_DIR_NAME
                if not staging_root.is_dir():
                    continue
                for staging_dir in sorted(staging_root.iterdir()):
                    if not staging_dir.is_dir():
                        continue
                    pid = _staging_pid(staging_dir.name)
                    if pid is not None and _pid_alive(pid):
                        continue  # publish potentiellement en cours - jamais touche
                    removed.append(str(staging_dir))
                    if not dry_run:
                        shutil.rmtree(staging_dir)

            # 2. Rapport (jamais de suppression) : entrees 'pending' sans staging au disque -
            #    calcule apres le sweep ci-dessus, donc reflete l'etat post-purge en un seul
            #    appel (une entree juste orpheline-purgee apparait ici immediatement).
            all_entries = [("lop", e) for e in manifest.get(LOP_PUBLISHES_KEY, [])]
            for step, entries in manifest.get(STEP_PUBLISHES_KEY, {}).items():
                all_entries += [(step, e) for e in entries]

            for kind, entry in all_entries:
                if entry.get("status") != "pending":
                    continue
                version = entry.get("version")
                versioned_name = f"{entity_dir.name}_{kind}_v{version:03d}"
                staging_root = entity_dir / (LOP_DIR_NAME if kind == "lop" else kind) / LOP_STAGING_DIR_NAME
                matches = list(staging_root.glob(f"{versioned_name}.staging-*")) if staging_root.is_dir() else []
                if not matches:
                    pending_without_staging.append({
                        "entity": entity_dir.name,
                        "kind": kind,
                        "version": version,
                        "manifest": str(manifest_path),
                    })

    return {"removed_staging": removed, "pending_without_staging": pending_without_staging}


# --------------------------------------------------------------------------------------
# Consommation web (sync vers un projet Three.js) - le projet web ne lit JAMAIS la
# structure du pipeline, uniquement public/assets/assets.json (cf. CLAUDE.md).
# --------------------------------------------------------------------------------------

WEB_ASSETS_DIRNAME = "assets"
_SYNCED_GLB_RE = re.compile(r"_v(\d+)\.glb$")


def _known_entity_names(project_root):
    """Noms de toutes les entites existantes du projet (assets/sets/shots), utilise par
    sync_web_assets() pour ne jamais toucher un fichier de public/assets/ qui ne correspond
    a aucune entite connue (cf. sa docstring)."""
    project_root = Path(project_root)
    names = set()
    for family in ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for d in family_dir.iterdir():
            if d.is_dir() and (d / ASSET_MANIFEST_NAME).is_file():
                names.add(d.name)
    return names


# --- API de pinning web (principe 5 : la logique vit dans l'orchestrateur, IMPORTABLE par un
# plugin DCC ou n8n - pas seulement par le serveur HTTP ylos_ui). Toutes NE LEVENT JAMAIS pour
# un cas metier (asset/version inconnu, pin d'un publish non-GLB...) : elles retournent un dict
# {"ok": bool, ...}. project.json['web'] a la forme {target_dir, pinned_assets: {<asset>:
# {step, version}}} - 'target_dir' memorise le web_project_dir cible (consomme par
# sync_web_assets ; INC-6 le nomme 'project_dir', on conserve 'target_dir' deja au contrat).

def _update_web(project_root, mutate):
    """Read-modify-write de project.json['web'] sous flock (le serveur HTTP multi-thread ET les
    plugins DCC ecrivent le meme manifeste - meme discipline que le reste du module). 'mutate'
    recoit le dict web (cree si absent, forme {target_dir, pinned_assets}) et le modifie en
    place ; ecriture atomique via write_manifest (_atomic_write_json en interne)."""
    project_root = Path(project_root)
    manifest_path = project_root / PIPELINE_DIR / MANIFEST_NAME
    with acquire_lock(manifest_path):
        manifest = read_manifest(project_root)
        web = manifest.setdefault("web", {"target_dir": None, "pinned_assets": {}})
        mutate(web)
        manifest["modified_utc"] = _now()
        write_manifest(project_root / PIPELINE_DIR, manifest)


def _pinnable_glb_versions(project_root, entity_name, step):
    """Versions (triees) des publishes 'complete' a artefact .glb pour ce step - les SEULES
    pinnables pour le web (sync_web_assets resout le GLB via (step, version)). Un publish USD
    n'apparait jamais. [] si entite/step introuvable (list_publishes ne leve jamais)."""
    return sorted(
        e["version"] for e in list_publishes(project_root, entity_name, step)
        if e.get("status") == "complete" and (e.get("artifact") or "").endswith(".glb")
    )


def pin_web_asset(project_root, asset, step, version):
    """Pinne un GLB publie pour la sync web : ecrit project.json['web']['pinned_assets'][asset]
    = {step, version}, apres avoir valide qu'un publish 'complete' a artefact .glb existe pour
    (asset, step, version). Le pin est un contrat consomme TEL QUEL par sync_web_assets (un pin
    casse n'y produirait qu'un warning tardif) : on le refuse ici, avec la liste de ce qui
    existe. NE LEVE JAMAIS pour un cas metier : retourne
    {"ok": True, "asset", "step", "version"} ou {"ok": False, "error": <str>}."""
    project_root = Path(project_root)
    asset = (asset or "").strip()
    step = (step or "").strip()
    # bool est un int en Python : version=True matcherait la version 1 - on l'exclut.
    if not asset or not step or not isinstance(version, int) or isinstance(version, bool):
        return {"ok": False, "error": "asset (str), step (str) et version (int) requis."}
    available = _pinnable_glb_versions(project_root, asset, step)
    if version not in available:
        return {"ok": False, "error": (
            f"Aucun publish GLB 'complete' pour {asset!r} en {step} v{version:03d}. "
            f"Disponibles : {available or 'aucun'}")}
    _update_web(project_root, lambda web: web.setdefault("pinned_assets", {}).__setitem__(
        asset, {"step": step, "version": version}))
    return {"ok": True, "asset": asset, "step": step, "version": version}


def unpin_web_asset(project_root, asset):
    """Retire le pin web d'un asset. Idempotent : de-pinner un asset non pinne est un succes.
    NE LEVE JAMAIS : {"ok": True, "asset", "was_pinned": bool} ou {"ok": False, "error"} si
    'asset' est vide."""
    asset = (asset or "").strip()
    if not asset:
        return {"ok": False, "error": "asset (str) requis."}
    removed = []
    _update_web(project_root, lambda web: removed.append(
        web.setdefault("pinned_assets", {}).pop(asset, None)))
    return {"ok": True, "asset": asset, "was_pinned": bool(removed and removed[0] is not None)}


def set_web_target(project_root, target_dir):
    """Memorise le web_project_dir cible dans project.json['web']['target_dir'] (consomme par
    sync_web_assets sans avoir a le repasser). '' -> None (efface la cible). NE LEVE JAMAIS :
    {"ok": True, "target_dir": <str|None>}."""
    target_dir = (target_dir or "").strip() or None
    _update_web(project_root, lambda web: web.__setitem__("target_dir", target_dir))
    return {"ok": True, "target_dir": target_dir}


def sync_web_assets(project_root, web_project_dir):
    """Synchronise les GLB PINNES (project.json['web']['pinned_assets'], jamais 'latest')
    vers {web_project_dir}/public/assets/. Le projet web est un consommateur passif : il ne
    lit jamais la structure du pipeline, uniquement assets.json genere ici.

    pinned_assets : {"<asset_name>": {"step": <step>, "version": <int>}} - le step est
    necessaire pour localiser le GLB sans ambiguite (un asset peut avoir des publishes GLB
    independants par step, cf. allocate_publish_version/kind).

    Comportement (miroir) :
      1. Copie chaque GLB pinne vers <ASSET_NAME>_v<VERSION:03d>.glb (cache-busting).
      2. Genere assets.json ({"assets": {...}, "generated": <ISO>}), sha256 par asset,
         ecrit atomiquement (_atomic_write_json).
      3. Supprime les <ASSET>_v*.glb d'assets CONNUS (cf. _known_entity_names) dont la
         version ne correspond plus au pin courant (ou dont l'asset n'est plus pinne du
         tout). Un fichier qui ne correspond a aucune entite connue n'est jamais touche.

    Retourne {"assets_dir", "synced", "warnings"} - 'warnings' liste les pins non
    resolus (asset/GLB introuvable) sans faire echouer le reste de la synchronisation.
    """
    project_root = Path(project_root)
    web_project_dir = Path(web_project_dir)
    manifest = read_manifest(project_root)
    pinned = manifest.get("web", {}).get("pinned_assets", {})

    assets_dir = web_project_dir / "public" / WEB_ASSETS_DIRNAME
    assets_dir.mkdir(parents=True, exist_ok=True)

    known_names = _known_entity_names(project_root)
    synced = {}
    warnings = []
    wanted_filenames = {}  # asset_name -> nom de fichier actuellement pinne

    for asset_name, pin in pinned.items():
        step = pin.get("step")
        version = pin.get("version")
        if not step or not isinstance(version, int):
            warnings.append(f"Pin invalide pour {asset_name!r} : {pin!r}")
            continue
        try:
            entity_dir, _ = _find_asset_entity(project_root, asset_name)
        except FileNotFoundError:
            warnings.append(f"Asset pinne introuvable : {asset_name!r}")
            continue

        stem = f"{asset_name}_{step}_v{version:03d}"
        src = entity_dir / step / LOP_PUBLISH_DIR_NAME / stem / f"{stem}.glb"
        if not src.is_file():
            warnings.append(f"GLB pinne introuvable pour {asset_name!r} : {src}")
            continue

        dest_filename = f"{asset_name}_v{version:03d}.glb"
        shutil.copy2(src, assets_dir / dest_filename)
        synced[asset_name] = {
            "file": dest_filename,
            "version": version,
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        }
        wanted_filenames[asset_name] = dest_filename

    # Miroir : purge les vieilles versions (ou les assets retires du pin) d'entites connues.
    for f in list(assets_dir.iterdir()):
        if not f.is_file() or f.suffix != ".glb":
            continue
        m = _SYNCED_GLB_RE.search(f.name)
        if not m:
            continue
        candidate_name = f.name[: m.start()]
        if candidate_name not in known_names:
            continue  # fichier etranger a une entite connue - jamais touche
        if wanted_filenames.get(candidate_name) != f.name:
            f.unlink()

    _atomic_write_json(assets_dir / "assets.json", {"assets": synced, "generated": _now()})

    return {"assets_dir": str(assets_dir), "synced": synced, "warnings": warnings}


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
                    help="Sous-type metier - requis pour respecter la convention TYPE_Nom_Variant "
                         "(asset: CHARACTER/PROP/VEHICLE/CREATURE/FX_ELEMENT, set: EXTERIOR/INTERIOR/"
                         "HERO_SET/MODULAR_KIT, shot: LAYOUT/ANIMATION/FX/LIGHTING/COMP ; defaut OTHER, "
                         "toujours invalide - create_asset() explique la convention si omis)")
    pa.add_argument("--steps", help="Steps separes par virgules (defaut : pipeline du projet)")
    pa.add_argument("--force", action="store_true", help="Passer outre si l'entite existe")

    pub = sub.add_parser("publish", help="Publie un fichier USD dans un step d'entite.")
    pub.add_argument("project", help="Chemin du projet existant")
    pub.add_argument("asset", help="Nom de l'entite")
    pub.add_argument("step", help="Step de publication (ex: modeling, lookdev)")
    pub.add_argument("file", help="Fichier source a publier (.usda ou .usdc)")

    pfr = sub.add_parser("set-frame-range",
                         help="Pose la plage d'images d'un shot (schema 2.1) et recompose "
                              "son shot_root.usda (timecodes).")
    pfr.add_argument("project", help="Chemin du projet existant")
    pfr.add_argument("shot", help="Nom du shot")
    pfr.add_argument("start", type=int, help="Premiere image (inclusive)")
    pfr.add_argument("end", type=int, help="Derniere image (inclusive), > start")
    pfr.add_argument("--fps", type=float, default=None,
                     help="Images par seconde (defaut : fps existant du shot ou defaut scene)")

    pcs = sub.add_parser("clean-staging",
                         help="Purge les staging_dirs orphelins (process mort) + rapporte "
                              "les entrees manifest 'pending' sans staging correspondant.")
    pcs.add_argument("project", help="Chemin du projet existant")
    pcs.add_argument("--apply", action="store_true",
                     help="Supprime reellement (defaut : dry-run, rapporte sans rien supprimer)")

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
        elif args.cmd == "publish":
            info = publish_asset(args.project, args.asset, args.step, args.file)
            print(f"[ok] publish {info['name']} / {info['step']} v{info['version']:03d}")
            print(f"  publish   : {info['publish_path']}")
            print(f"  manifeste : {info['manifest']}")
            if info["asset_root"]:
                print(f"  asset_root: {info['asset_root']}")
        elif args.cmd == "set-frame-range":
            fr = set_frame_range(args.project, args.shot, args.start, args.end, fps=args.fps)
            print(f"[ok] frame_range {args.shot} : {fr['start']}-{fr['end']} @ {fr['fps']} fps")
            print("  shot_root.usda recompose (timecodes)")
        else:  # clean-staging
            info = clean_stale_staging(args.project, dry_run=not args.apply)
            verb = "supprime(s)" if args.apply else "a supprimer (dry-run - passer --apply pour executer)"
            print(f"[ok] {len(info['removed_staging'])} staging_dir(s) {verb}")
            for path in info["removed_staging"]:
                print(f"  - {path}")
            if info["pending_without_staging"]:
                print(f"[rapport] {len(info['pending_without_staging'])} entree(s) manifest "
                      f"'pending' sans staging correspondant (non modifiees) :")
                for e in info["pending_without_staging"]:
                    print(f"  - {e['entity']} / {e['kind']} v{e['version']:03d}  ({e['manifest']})")
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        sys.stderr.write(f"[erreur] {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
