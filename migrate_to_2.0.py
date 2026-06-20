#!/usr/bin/env python3
"""
migrate_to_2.0.py - migre un projet Ylos Prod du schema legacy (sans schema_version) vers
le schema 2.0. Stdlib seule.

Garanties :
  - Non destructif : snapshot des fichiers texte modifies (project.json, manifests,
    asset_root) dans <projet>/_migration_backup/ + journal des renommages (rename_log.json).
  - Les publishes ne sont JAMAIS re-serialises : on corrige seulement l'extension selon le
    format reel (magic-byte : #usda -> .usda, PXR-USDC -> .usdc).
  - asset_root recompose en references-sous-/<Asset> (cible </root>), car les publishes
    Blender authorent /root. Repare les assets dont le defaultPrim pointait un prim vide.

NE traite PAS (hors perimetre, signale) :
  - L'axe Z->Y des publishes Blender (decision : script Blender import/export dedie).
  - La re-serialisation ASCII<->crate (usdcat existe si besoin, non requis ici).

Usage :
    python migrate_to_2.0.py /chemin/projet [--dry-run] [--no-backup]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import create_project as cp   # reutilise constantes + validate_manifest (logique unique)

# Sous-types metier pour les entites dont le 'type' valait 'asset' (a confirmer)
TYPE_OVERRIDES_DEFAULT = {"lecube": "PROP", "montains": "ENVIRONMENT"}
USD_EXTS = (".usd", ".usda", ".usdc")
# Ordre pipeline (amont -> aval) pour ranger manifest.steps
CANONICAL_STEP_ORDER = ["modeling", "uvs", "rigging", "lookdev", "fx",
                        "layout", "animation", "lighting", "render", "composite"]
_VER_RE = re.compile(r"_v(\d+)\.")
_FAMILIES = ("asset", "set", "shot")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _iso(value):
    """Normalise une date legacy ('YYYY-MM-DD' ou datetime) en ISO UTC."""
    if not value:
        return _now()
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return _now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def detect_ext(path):
    """Extension honnete selon le format reel du fichier USD (magic-byte)."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"#usda"):
        return ".usda"
    if head.startswith(b"PXR-USDC"):
        return ".usdc"
    return None


# --------------------------------------------------------------------------------------
# project.json legacy -> 2.0
# --------------------------------------------------------------------------------------

def migrate_project_manifest(legacy):
    p = legacy.get("project", {})
    pipeline = dict(legacy.get("pipeline", {}))
    pipeline.setdefault("asset_steps", list(cp.DEFAULT_ASSET_STEPS))
    pipeline.setdefault("shot_steps", list(cp.DEFAULT_SHOT_STEPS))
    pipeline.setdefault("set_steps", list(cp.DEFAULT_SET_STEPS))
    pipeline.setdefault("usd_root_prim", cp.USD_ROOT_PRIM)
    return {
        "schema_version": cp.SCHEMA_VERSION,
        "name": p.get("name") or legacy.get("name"),
        "display_name": p.get("display_name") or p.get("name"),
        "prod_type": p.get("prod_type", "FILM"),
        "topology": cp.TOPOLOGY,
        "created_utc": _iso(p.get("created")),
        "modified_utc": _now(),
        "env": {"root": f"${cp.ENV_ROOT}", "cache": f"${cp.ENV_CACHE}"},
        "structure": {"source": list(cp.SOURCE_TREE), "cache": list(cp.CACHE_TREE)},
        "cache_per_project": True,
        "pipeline": pipeline,
        "scene": dict(legacy.get("scene", cp.DEFAULT_SCENE)),
        "delivery": dict(legacy.get("delivery", cp.DEFAULT_DELIVERY)),
        "dcc": {"houdini": {}, "blender": {}},
        "status": "migrated",
        # tracabilite : ce qu'on a retire (path absolu, version projet)
        "_migrated_from": {"schema": "legacy (sans schema_version)", "dropped": ["project.path", "project.version"]},
    }


# --------------------------------------------------------------------------------------
# Publishes : normalisation d'extension + scan versions
# --------------------------------------------------------------------------------------

def normalize_publish_exts(entity_dir, steps, rename_log, dry):
    for step in steps:
        pub = entity_dir / step / "publish"
        if not pub.is_dir():
            continue
        for f in list(pub.iterdir()):
            if f.is_file() and f.suffix in USD_EXTS:
                want = detect_ext(f)
                if want and f.suffix != want:
                    target = f.with_suffix(want)
                    rename_log.append({"from": str(f), "to": str(target)})
                    if not dry:
                        if target.exists():
                            target.unlink()
                        f.rename(target)


def effective_steps(entity_dir, declared):
    """Steps reels = declares dans le manifeste UNION presents sur le disque (un dossier de
    step a un wip/ ou un publish/). Corrige les manifests sous-declares (ex lecube :
    steps=[modeling] alors que lookdev/ existe et a un publish)."""
    found = set(declared or [])
    for d in entity_dir.iterdir():
        if d.is_dir() and ((d / "publish").is_dir() or (d / "wip").is_dir()):
            found.add(d.name)
    ordered = [s for s in CANONICAL_STEP_ORDER if s in found]
    ordered += [s for s in sorted(found) if s not in CANONICAL_STEP_ORDER]
    return ordered


def _version(name):
    m = _VER_RE.search(name)
    return int(m.group(1)) if m else 0


def latest_publishes(entity_dir, steps):
    """step -> chemin relatif du publish le plus recent (apres normalisation)."""
    result = {}
    for step in steps:
        pub = entity_dir / step / "publish"
        if not pub.is_dir():
            continue
        best, best_v = None, -1
        for f in pub.iterdir():
            if f.is_file() and f.suffix in USD_EXTS and _version(f.name) > best_v:
                best, best_v = f, _version(f.name)
        if best is not None:
            result[step] = f"{step}/publish/{best.name}"
    return result


def all_publishes(entity_dir, steps):
    """step -> liste triee des publishes (pour manifest.publishes)."""
    out = {}
    for step in steps:
        pub = entity_dir / step / "publish"
        files = []
        if pub.is_dir():
            for f in sorted(pub.iterdir()):
                if f.is_file() and f.suffix in USD_EXTS:
                    files.append(f"{step}/publish/{f.name}")
        out[step] = files
    return out


# --------------------------------------------------------------------------------------
# asset_root : recompose depuis les publishes existants
# --------------------------------------------------------------------------------------

def recompose_asset_root(entity_dir, name, steps, rename_log, dry):
    content = cp.build_asset_root(name, latest_publishes(entity_dir, steps))
    legacy = entity_dir / "asset_root.usd"
    target = entity_dir / cp.ASSET_ROOT_NAME   # asset_root.usda
    if legacy.exists():
        rename_log.append({"from": str(legacy), "to": str(target), "recomposed": True})
    if not dry:
        if legacy.exists():
            legacy.unlink()
        target.write_text(content, encoding="utf-8")
    return content


# --------------------------------------------------------------------------------------
# Entite (asset/set/shot)
# --------------------------------------------------------------------------------------

def migrate_entity(entity_dir, type_overrides, rename_log, dry):
    mpath = entity_dir / "manifest.json"
    if not mpath.is_file():
        return None
    m = json.loads(mpath.read_text(encoding="utf-8"))
    name = m.get("name", entity_dir.name)
    steps = effective_steps(entity_dir, m.get("steps", []))

    entity_type = m.get("entity_type")
    if entity_type not in _FAMILIES:
        entity_type = "asset"
    asset_type = m.get("type", "OTHER")
    if asset_type in _FAMILIES:          # 'type' valait une famille -> sous-type errone
        asset_type = type_overrides.get(name, "OTHER")

    normalize_publish_exts(entity_dir, steps, rename_log, dry)

    asset_root = None
    if entity_type in ("asset", "set"):
        asset_root = recompose_asset_root(entity_dir, name, steps, rename_log, dry)

    new_manifest = {
        "schema_version": cp.SCHEMA_VERSION,
        "name": name,
        "entity_type": entity_type,
        "type": asset_type,
        "steps": steps,
        "publishes": all_publishes(entity_dir, steps),
        "created_utc": _now(),
        "modified_utc": _now(),
    }
    if not dry:
        mpath.write_text(
            json.dumps(new_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return {"name": name, "entity_type": entity_type, "type": asset_type,
            "steps": steps, "asset_root": asset_root}


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def _snapshot(project_dir, backup_dir):
    """Copie les fichiers texte qui vont changer (reversibilite)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    targets = [project_dir / cp.PIPELINE_DIR / cp.MANIFEST_NAME]
    targets += list(project_dir.glob("assets/*/manifest.json"))
    targets += list(project_dir.glob("sets/*/manifest.json"))
    targets += list(project_dir.glob("shots/*/manifest.json"))
    targets += list(project_dir.glob("assets/*/asset_root.usd"))
    targets += list(project_dir.glob("sets/*/asset_root.usd"))
    for t in targets:
        if t.exists():
            rel = t.relative_to(project_dir)
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(t, dest)


def migrate(project_dir, dry=False, backup=True, type_overrides=None):
    project_dir = Path(project_dir).expanduser().resolve()
    type_overrides = type_overrides or TYPE_OVERRIDES_DEFAULT
    report = {"project": str(project_dir), "dry_run": dry, "entities": [],
              "renames": [], "warnings": []}

    legacy_path = project_dir / cp.PIPELINE_DIR / cp.MANIFEST_NAME
    if not legacy_path.is_file():
        raise FileNotFoundError(f"Pas de manifeste : {legacy_path}")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))

    if backup and not dry:
        _snapshot(project_dir, project_dir / "_migration_backup")

    # 1. project.json -> 2.0
    new_proj = migrate_project_manifest(legacy)
    cp.validate_manifest(new_proj)
    if not dry:
        legacy_path.write_text(
            json.dumps(new_proj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    # 2. dossiers 2.0 manquants (non destructif)
    if not dry:
        for rel in cp.SOURCE_TREE:
            (project_dir / rel).mkdir(parents=True, exist_ok=True)

    # 3. entites
    rename_log = []
    for family in ("assets", "sets", "shots"):
        base = project_dir / family
        if not base.is_dir():
            continue
        for entity_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            info = migrate_entity(entity_dir, type_overrides, rename_log, dry)
            if info:
                report["entities"].append(info)
    report["renames"] = rename_log

    # 4. cache co-localise -> signale (relocalisation = $PROJ_CACHE, hors copie pilote)
    coloc = project_dir / "cache"
    if coloc.is_dir():
        report["warnings"].append(
            f"cache/ co-localise present ({coloc}) : a deplacer sous $PROJ_CACHE/<projet> "
            f"(regenerable). Non touche par la migration."
        )

    # 5. axe Z->Y (publishes Blender Z-up vs asset_root Y-up)
    report["warnings"].append(
        "Publishes Blender en Z-up alors que asset_root declare Y-up : conversion d'axe a "
        "gerer par le script Blender import/export, hors de cette migration."
    )

    if not dry:
        log_path = project_dir / "_migration_backup" / "rename_log.json"
        if backup:
            log_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
    return report


def _cli(argv=None):
    p = argparse.ArgumentParser(description="Migration projet Ylos Prod legacy -> 2.0.")
    p.add_argument("project", help="Chemin du projet a migrer")
    p.add_argument("--dry-run", action="store_true", help="Rapport sans rien modifier")
    p.add_argument("--no-backup", action="store_true", help="Ne pas snapshotter (deconseille)")
    args = p.parse_args(argv)
    try:
        report = migrate(args.project, dry=args.dry_run, backup=not args.no_backup)
    except (FileNotFoundError, ValueError) as e:
        sys.stderr.write(f"[erreur] {e}\n")
        return 1
    mode = "DRY-RUN" if args.dry_run else "applique"
    print(f"[ok] migration {mode} : {report['project']}")
    print(f"  entites : {len(report['entities'])}  |  renommages : {len(report['renames'])}")
    for w in report["warnings"]:
        print(f"  [warn] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
