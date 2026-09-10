# -*- coding: utf-8 -*-
"""Re-renders existing publish thumbnails, from the ALREADY-published ARTIFACT.

Why this tool exists
--------------------
Three cumulative bugs in render_publish_thumbnail() produced, throughout their
lifetime, FLAT thumbnails (black silhouette or plain background): insufficient lighting, default
camera clipping (clip_end=1000) and hide_render objects included in the framing. Fixing them
does not repair the past: the thumb.png already written stay as-is on disk. This tool
regenerates them without touching the published geometry or the version numbers — it re-reads
the publish's artifact (GLB/USD), frames it and renders it again.

WARNING - writing into a publish area
-------------------------------------
A publish folder is meant to be IMMUTABLE. This tool rewrites thumb.png in it: it's the only
case where that is defensible (the thumbnail is a derived representation, not the published data —
the artifact, the manifest and the version don't move), but it remains a mutation. Hence:
  - DRY-RUN BY DEFAULT (same convention as create_project.clean_stale_staging);
  - --apply mandatory to write;
  - by default, only the thumbnails DETECTED AS FLAT are regenerated (--all to redo everything).

Usage
-----
  BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
  "$BLENDER" --background --factory-startup \\
      --python tools/blender/backfill_thumbnails.py -- --project /path/project
  # then, once the report is reviewed:
  "$BLENDER" --background --factory-startup \\
      --python tools/blender/backfill_thumbnails.py -- --project /path/project --apply

Exit code: 0 if the scan ran without error (even in dry-run), 1 otherwise.
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

# A thumbnail whose pixel standard deviation is below this threshold carries no usable
# visual information (measured: the test project's broken thumbs fell to 0.001-0.01,
# a correct render exceeds 0.05). Threshold on the linear 0-1 values of bpy.data.images.
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
    """Standard deviation of an image's RGB channels, or None if unreadable."""
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
    """Imports the artifact into the current scene. Returns the ADDED objects, or []."""
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
    """(entity_name, entity_dir, step, version, artifact_abs, thumb_abs) for each 'complete'
    publish of the two-phase contract whose artifact still exists on disk."""
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
    ap.add_argument("--project", required=True, help="Ylos project root")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write (without this flag: dry-run, nothing is modified)")
    ap.add_argument("--all", action="store_true",
                    help="Regenerate ALL thumbnails, not only those detected as flat")
    ap.add_argument("--size", type=int, default=512)
    args = ap.parse_args(argv)

    import bpy
    from blender.core import thumbnails  # plugins/blender importe comme package 'blender'

    print(f"[backfill] project  : {args.project}")
    print(f"[backfill] mode     : {'APPLY (writing)' if args.apply else 'DRY-RUN (no writing)'}")
    print(f"[backfill] selection: {'all publishes' if args.all else 'flat thumbnails only'}")
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

        # Fresh scene per publish: no residue from a previous import in the framing.
        bpy.ops.wm.read_homefile(use_empty=True)
        objs = _import_artifact(artifact)
        if not objs:
            stats["failed"] += 1
            print(f"  FAIL  {label}  import impossible: {os.path.basename(artifact)}")
            continue

        target_dir = os.path.dirname(thumb) if args.apply else \
            os.path.join(os.path.expanduser("~"), ".ylos", "backfill-preview",
                         name, step, f"v{version:03d}")
        os.makedirs(target_dir, exist_ok=True)
        produced = thumbnails.render_publish_thumbnail(objs, target_dir, size=args.size)
        if not produced:
            stats["failed"] += 1
            print(f"  FAIL  {label}  render: {thumbnails.LAST_ERROR}")
            continue

        stats["rendered"] += 1
        new_std = _image_std(produced)
        if args.apply:
            # render_publish_thumbnail already writes 'thumb.png' in target_dir: when
            # --apply, target_dir IS the publish folder, so the file is in place.
            stats["written"] += 1
            print(f"  WRITE {label}  std {std if std is None else round(std,4)} -> {round(new_std,4)}")
        else:
            print(f"  DRY   {label}  std {std if std is None else round(std,4)} -> {round(new_std,4)}"
                  f"   preview: {produced}")

    print()
    print("[backfill] " + json.dumps(stats))
    if not args.apply and stats["rendered"]:
        print("[backfill] Dry-run: the previews are in ~/.ylos/backfill-preview/. "
              "Re-run with --apply to write into the publish folders.")
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
