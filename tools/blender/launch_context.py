# -*- coding: utf-8 -*-
"""
launch_context.py - Launcher versionne : ouvrir Blender dans un contexte pipeline Ylos.

Invoque par ylos_ui.py (bouton "Ouvrir dans Blender") et utilisable en CLI :

    blender --python tools/blender/launch_context.py -- \
        --project <root> [--entity <name>] [--step <step>] [--path <file>] \
        [--kind wip|publish|scene_default]

REGLE PIPELINE (cf. CLAUDE.md) : TOUT lancement DCC passe par ce launcher versionne.
Plus jamais de `--python-expr` inline. Diagnostic CC#1d : en GUI, une op lancee via
`--python-expr` s'execute pendant le boot (contexte pas pret) et echoue en silence ->
l'utilisateur obtient une instance vide. Ce launcher differe l'execution au premier
tick d'un timer (contexte pret) et journalise chaque etape.

- GUI (bpy.app.background == False) : les ops ne s'executent JAMAIS au parse time.
  bpy.app.timers.register(callback, first_interval=0.2) -> execution au 1er tick.
- Background (--background) : execution immediate + sys.exit(code) (pour la CI/tests).

Ordre d'ouverture impose :
  * .blend -> wm.open_mainfile D'ABORD (l'open remplace la scene), puis contexte.
  * USD    -> contexte D'ABORD (open_context + enums), puis wm.usd_import (merge).

Observabilite : chaque etape est journalisee dans ~/.ylos/launch.log (timestamp, argv,
succes/echec + traceback complet) ET imprimee. Aucune exception ne sort du timer.
Le chemin du log est surchargeable par $YLOS_LAUNCH_LOG (isolation des tests).
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import bpy

# --- Localisation du repo (pattern CLAUDE.md : realpath + remontee parents) -----------
_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))  # tools/blender/.. -> repo
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Log surchargeable ($YLOS_LAUNCH_LOG) : les tests isolent leur propre fichier.
LOG_PATH = os.environ.get("YLOS_LAUNCH_LOG") or str(Path.home() / ".ylos" / "launch.log")

# Extensions ouvertes par import USD (le reste = mainfile .blend). Miroir de ylos_ui.py.
USD_OPEN_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")


# --------------------------------------------------------------------------------------
# Observabilite
# --------------------------------------------------------------------------------------

def _log(msg, exc=False):
    """Append horodate dans LOG_PATH ET print. Ne leve jamais (echec d'ecriture avale)."""
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            if exc:
                fh.write(traceback.format_exc() + "\n")
    except OSError:
        pass
    if exc:
        traceback.print_exc()


def _set_enum_safe(obj, prop, value, items):
    """Affecte une valeur d'enum SANS jamais crasher : une valeur absente des items (valeur
    legacy/inconnue lue d'un manifeste ou prefixe de nom invalide) -> warning loggue +
    fallback (valeur courante conservee), jamais d'exception. Miroir du pattern
    op_open_context._set_enum_safe, cote launcher (pas d'operateur -> log au lieu de report)."""
    valid = {v for v, _label, _desc in items}
    if value in valid:
        try:
            setattr(obj, prop, value)
            _log(f"context: {prop} = {value!r}")
            return True
        except Exception:
            _log(f"context: setattr {prop}={value!r} a echoue", exc=True)
            return False
    _log(f"context: {prop} = {value!r} ignore (hors enum {sorted(valid)}) - valeur conservee")
    return False


# --------------------------------------------------------------------------------------
# Addon + resolution
# --------------------------------------------------------------------------------------

def _addon_registered():
    """True si l'op ylos.open_context est enregistree. `"op" in dir(bpy.ops.ylos)` est le
    seul check fiable dans les DEUX etats : `hasattr(bpy.types, "YLOS_OT_OpenContext")` renvoie
    toujours False (les operateurs ne sont pas exposes ainsi) et `hasattr(bpy.ops.ylos, "op")`
    toujours True (stub paresseux) - les deux menent a une mauvaise decision (re-register en
    GUI ou jamais de register en CI)."""
    return "open_context" in dir(bpy.ops.ylos)


def _ensure_addon():
    """L'op ylos.open_context et les proprietes de scene n'existent que si l'addon est
    enregistre. En GUI l'utilisateur l'a active (ne pas re-register : double register ->
    RuntimeError) ; en background (CI, --factory-startup) on l'enregistre nous-meme. Non
    fatal : un echec degrade le contexte, jamais l'ouverture du fichier."""
    if _addon_registered():
        return True
    try:
        import blender as addon  # package plugins/blender importe comme 'blender'
        addon.register()
        ok = _addon_registered()
        _log(f"addon: register() {'OK' if ok else 'sans ylos.open_context'}")
        return ok
    except Exception:
        _log("addon: register() a echoue - contexte pipeline indisponible", exc=True)
        return False


def _entity_type(project, entity):
    """Famille de l'entite (asset|set|shot) lue du manifeste, de facon tolerante. None si
    illisible -> le context_type reste inchange (set garde)."""
    try:
        import create_project as cp
        _edir, mpath = cp._find_asset_entity(Path(project), entity)
        manifest = json.loads(Path(mpath).read_text(encoding="utf-8"))
        return manifest.get("entity_type", "asset")
    except Exception:
        return None


def _resolve_path(args):
    """Fichier a ouvrir : --path explicite, sinon resolution via l'orchestrateur (logique
    unique, reutilisable Houdini). resolve_open_target NE LEVE JAMAIS -> dict exists=False."""
    if args.path:
        return args.path
    if not args.entity:
        _log("resolve: ni --path ni --entity - rien a resoudre")
        return None
    import create_project as cp
    target = cp.resolve_open_target(args.entity, "blender", args.step, project_root=args.project)
    if target.get("exists"):
        _log(f"resolve: {args.entity!r} (step={args.step!r}) -> [{target['kind']}] {target['path']}")
        return target["path"]
    _log(f"resolve: aucun target pour {args.entity!r}: {target.get('reason', '')}")
    return None


def _apply_context(project, entity, step):
    """Charge le projet (prod_type, preset de scene) via l'op de l'addon, puis pose le
    contexte d'entite sur la scene courante. Chaque affectation d'enum est gardee."""
    _ensure_addon()
    # 1. charger le projet - jamais fatal (l'ouverture du fichier prime).
    try:
        bpy.ops.ylos.open_context('EXEC_DEFAULT', directory=str(project))
        _log(f"context: open_context(directory={str(project)!r}) OK")
    except Exception:
        _log("context: open_context a echoue (non fatal)", exc=True)

    if not entity:
        return
    scene = bpy.context.scene
    from blender.core import vocab  # apres _ensure_addon : plugins sur sys.path

    # ylos_current_asset : StringProperty (jamais un enum).
    try:
        scene.ylos_current_asset = entity
        _log(f"context: ylos_current_asset = {entity!r}")
    except Exception:
        _log(f"context: set ylos_current_asset={entity!r} a echoue", exc=True)

    if step:
        _set_enum_safe(scene, "ylos_current_step", step, vocab.STEP_ITEMS_ALL)

    etype = _entity_type(project, entity)
    if etype:
        _set_enum_safe(scene, "ylos_context_type", etype.upper(), vocab.CONTEXT_TYPE_ITEMS)

    # Type d'asset = prefixe TYPE_ du nom (convention TYPE_Nom_Variant) si valide.
    prefix = entity.split("_", 1)[0]
    _set_enum_safe(scene, "ylos_asset_type", prefix, vocab.ASSET_TYPE_ITEMS)


# --------------------------------------------------------------------------------------
# Ouverture (callback)
# --------------------------------------------------------------------------------------

def _do_launch(args):
    """Ouvre le fichier resolu dans l'ordre impose, contextualise, journalise. Retourne un
    code de sortie (0 succes, 1 echec) pour le mode background. Ne leve jamais."""
    path = _resolve_path(args)
    if not path:
        _log("LAUNCH FAILURE: aucun fichier a ouvrir (ni --path, ni resolution)")
        return 1

    ext = os.path.splitext(path)[1].lower()
    is_usd = ext in USD_OPEN_EXTS
    try:
        if is_usd:
            # USD : contexte D'ABORD (open_context + enums), puis import (merge dans la scene).
            _apply_context(args.project, args.entity, args.step)
            bpy.ops.wm.usd_import(filepath=path)
            _log(f"open: usd_import({path!r}) OK")
        else:
            # .blend : open_mainfile D'ABORD (remplace la scene), puis contexte.
            bpy.ops.wm.open_mainfile(filepath=path)
            _log(f"open: open_mainfile({path!r}) OK")
            _apply_context(args.project, args.entity, args.step)
    except Exception:
        _log(f"LAUNCH FAILURE: ouverture de {path!r} a echoue", exc=True)
        return 1

    n_objects = len(bpy.data.objects)
    mode = "usd_import" if is_usd else "mainfile"
    _log(f"LAUNCH SUCCESS: [{mode}] {path} objects={n_objects}")
    return 0


# --------------------------------------------------------------------------------------
# Entree
# --------------------------------------------------------------------------------------

def _parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="launch_context.py",
                                description="Lancer Blender dans un contexte pipeline Ylos.")
    p.add_argument("--project", required=True, help="Racine du projet Ylos.")
    p.add_argument("--entity", help="Nom de l'entite (asset/set/shot).")
    p.add_argument("--step", help="Step vise (defaut : 1er step declare au manifeste).")
    p.add_argument("--path", help="Fichier a ouvrir ; absent -> resolve_open_target.")
    p.add_argument("--kind", choices=("wip", "publish", "scene_default"),
                   help="Indice de nature du fichier (metadonnee, journalisee).")
    return p.parse_args(argv)


def main():
    try:
        args = _parse_args()
    except SystemExit:
        _log("LAUNCH FAILURE: arguments invalides (voir argv ci-dessus)")
        raise
    _log(f"launch argv={sys.argv!r} kind={args.kind!r}")

    if bpy.app.background:
        # Background : le contexte est pret, execution immediate + code de sortie.
        sys.exit(_do_launch(args))
    else:
        # GUI : NE JAMAIS executer au parse time (contexte pas pret pendant le boot).
        # Differer au 1er tick d'un timer ; aucune exception ne doit en sortir.
        def _tick():
            try:
                _do_launch(args)
            except Exception:
                _log("LAUNCH FAILURE: exception non capturee dans le timer", exc=True)
            return None  # None -> ne pas re-armer le timer
        bpy.app.timers.register(_tick, first_interval=0.2)
        _log("GUI: lancement differe via bpy.app.timers (first_interval=0.2)")


if __name__ == "__main__":
    main()
