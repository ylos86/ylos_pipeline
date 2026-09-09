# -*- coding: utf-8 -*-
"""Re-rend les thumbnails de publish existants, depuis l'ARTEFACT deja publie.

Pourquoi cet outil existe
-------------------------
Trois bugs cumulatifs de render_publish_thumbnail() ont produit, pendant toute leur duree de
vie, des thumbnails PLATS (silhouette noire ou fond uni) : eclairage insuffisant, clipping
camera au defaut (clip_end=1000) et objets hide_render inclus dans le cadrage. Les corriger
ne repare pas le passe : les thumb.png deja ecrits restent tels quels sur disque. Cet outil
les regenere sans toucher a la geometrie publiee ni aux numeros de version — il relit
l'artefact (GLB/USD) du publish, le cadre et le rend a nouveau.

ATTENTION - ecriture dans une zone de publish
---------------------------------------------
Un dossier de publish est cense etre IMMUABLE. Cet outil y reecrit thumb.png : c'est le seul
cas ou c'est defendable (le thumbnail est une representation derivee, pas la donnee publiee —
l'artefact, le manifeste et la version ne bougent pas), mais ca reste une mutation. D'ou :
  - DRY-RUN PAR DEFAUT (meme convention que create_project.clean_stale_staging) ;
  - --apply obligatoire pour ecrire ;
  - par defaut, seuls les thumbnails DETECTES PLATS sont regeneres (--all pour tout refaire).

Usage
-----
  BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
  "$BLENDER" --background --factory-startup \\
      --python tools/blender/backfill_thumbnails.py -- --project /chemin/projet
  # puis, une fois le rapport relu :
  "$BLENDER" --background --factory-startup \\
      --python tools/blender/backfill_thumbnails.py -- --project /chemin/projet --apply

Exit code : 0 si le scan s'est deroule sans erreur (meme en dry-run), 1 sinon.
"""

import argparse
import json
import os
import sys
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for _p in (REPO_ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Un thumbnail dont l'ecart-type des pixels est sous ce seuil ne porte aucune information
# visuelle exploitable (mesure : les thumbs casses du projet de test tombaient a 0.001-0.01,
# un rendu correct depasse 0.05). Seuil sur les valeurs lineaires 0-1 de bpy.data.images.
FLAT_STD_THRESHOLD = 0.02

IMPORTERS = {
    ".glb":  "gltf",
    ".gltf": "gltf",
    ".usd":  "usd",
    ".usda": "usd",
    ".usdc": "usd",
    ".usdz": "usd",
    ".usdnc": "usd",
    ".abc":  "abc",
}


def _image_std(path):
    """Ecart-type des canaux RGB d'une image, ou None si illisible."""
    import bpy
    try:
        img = bpy.data.images.load(path)
    except Exception:
        return None
    try:
        px = list(img.pixels)
        rgb = px[0::4] + px[1::4] + px[2::4]
        if not rgb:
            return None
        mean = sum(rgb) / len(rgb)
        var = sum((v - mean) ** 2 for v in rgb) / len(rgb)
        return var ** 0.5
    finally:
        bpy.data.images.remove(img)


def _import_artifact(path):
    """Importe l'artefact dans la scene courante. Retourne les objets AJOUTES, ou []."""
    import bpy
    kind = IMPORTERS.get(os.path.splitext(path)[1].lower())
    if kind is None:
        return []
    before = set(bpy.context.scene.objects)
    try:
        if kind == "gltf":
            bpy.ops.import_scene.gltf(filepath=path)
        elif kind == "usd":
            bpy.ops.wm.usd_import(filepath=path)
        elif kind == "abc":
            bpy.ops.wm.alembic_import(filepath=path)
    except Exception:
        traceback.print_exc()
        return []
    return [o for o in bpy.context.scene.objects if o not in before]


def _iter_publishes(project_root):
    """(entity_name, entity_dir, step, version, artifact_abs, thumb_abs) pour chaque publish
    'complete' du contrat deux-phases dont l'artefact existe encore sur disque."""
    from pathlib import Path
    for family in ("assets", "sets", "shots"):
        fam = Path(project_root) / family
        if not fam.is_dir():
            continue
        for ent in sorted(fam.iterdir()):
            mf = ent / "manifest.json"
            if not ent.is_dir() or not mf.is_file():
                continue
            try:
                manifest = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for step, entries in (manifest.get("step_publishes") or {}).items():
                for e in entries:
                    if e.get("status") != "complete":
                        continue
                    art = e.get("artifact")
                    thumb = e.get("thumbnail") or e.get("thumb")
                    if not art or not thumb:
                        continue
                    art_abs = ent / art
                    if not art_abs.exists():
                        continue
                    yield (ent.name, ent, step, e.get("version", 0),
                           str(art_abs), str(ent / thumb))


def main(argv):
    ap = argparse.ArgumentParser(prog="backfill_thumbnails")
    ap.add_argument("--project", required=True, help="Racine du projet Ylos")
    ap.add_argument("--apply", action="store_true",
                    help="Ecrire reellement (sans ce flag : dry-run, rien n'est modifie)")
    ap.add_argument("--all", action="store_true",
                    help="Regenerer TOUS les thumbnails, pas seulement ceux detectes plats")
    ap.add_argument("--size", type=int, default=512)
    args = ap.parse_args(argv)

    import bpy
    from blender.core import thumbnails  # plugins/blender importe comme package 'blender'

    print(f"[backfill] projet   : {args.project}")
    print(f"[backfill] mode     : {'APPLY (ecriture)' if args.apply else 'DRY-RUN (aucune ecriture)'}")
    print(f"[backfill] selection: {'tous les publishes' if args.all else 'thumbnails plats uniquement'}")
    print()

    stats = {"scanned": 0, "flat": 0, "rendered": 0, "written": 0, "failed": 0, "skipped_ok": 0}

    for name, ent_dir, step, version, artifact, thumb in _iter_publishes(args.project):
        stats["scanned"] += 1
        label = f"{name}/{step}/v{version:03d}"

        std = _image_std(thumb) if os.path.isfile(thumb) else None
        is_flat = (std is None) or (std < FLAT_STD_THRESHOLD)
        if is_flat:
            stats["flat"] += 1
        if not is_flat and not args.all:
            stats["skipped_ok"] += 1
            print(f"  OK    {label}  (std={std:.4f})")
            continue

        # Scene neuve par publish : aucun residu d'un import precedent dans le cadrage.
        bpy.ops.wm.read_homefile(use_empty=True)
        objs = _import_artifact(artifact)
        if not objs:
            stats["failed"] += 1
            print(f"  FAIL  {label}  import impossible : {os.path.basename(artifact)}")
            continue

        target_dir = os.path.dirname(thumb) if args.apply else \
            os.path.join(os.path.expanduser("~"), ".ylos", "backfill-preview",
                         name, step, f"v{version:03d}")
        os.makedirs(target_dir, exist_ok=True)
        produced = thumbnails.render_publish_thumbnail(objs, target_dir, size=args.size)
        if not produced:
            stats["failed"] += 1
            print(f"  FAIL  {label}  rendu : {thumbnails.LAST_ERROR}")
            continue

        stats["rendered"] += 1
        new_std = _image_std(produced)
        if args.apply:
            # render_publish_thumbnail ecrit deja 'thumb.png' dans target_dir : quand
            # --apply, target_dir EST le dossier de publish, donc le fichier est en place.
            stats["written"] += 1
            print(f"  WRITE {label}  std {std if std is None else round(std,4)} -> {round(new_std,4)}")
        else:
            print(f"  DRY   {label}  std {std if std is None else round(std,4)} -> {round(new_std,4)}"
                  f"   apercu: {produced}")

    print()
    print("[backfill] " + json.dumps(stats))
    if not args.apply and stats["rendered"]:
        print("[backfill] Dry-run : les apercus sont dans ~/.ylos/backfill-preview/. "
              "Relance avec --apply pour ecrire dans les dossiers de publish.")
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    try:
        sys.exit(main(argv))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
