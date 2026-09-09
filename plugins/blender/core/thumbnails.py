# -*- coding: utf-8 -*-
# Viewport thumbnail generation on WIP save + preview loading for version picker.
# + headless publish thumbnail (render_publish_thumbnail, cf. bas de fichier).

import math
import os
import bpy
from bpy.utils import previews
from mathutils import Euler, Vector
from pathlib import Path

_pcoll = None


def get_thumb_path(blend_path: str) -> str:
    """Return the expected thumbnail PNG path for a given .blend path."""
    p = Path(blend_path)
    return str(p.parent / (p.stem + "_thumb.png"))


def thumb_exists(blend_path: str) -> bool:
    return os.path.isfile(get_thumb_path(blend_path))


def generate_thumbnail(blend_path: str, context) -> str:
    """
    Render a 512×512 viewport thumbnail and save it as PNG next to the .blend.
    Returns the thumbnail path on success, empty string on failure.
    """
    thumb_path = get_thumb_path(blend_path)
    scene = context.scene

    orig = {
        "filepath":    scene.render.filepath,
        "res_x":       scene.render.resolution_x,
        "res_y":       scene.render.resolution_y,
        "res_pct":     scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
    }

    try:
        scene.render.filepath                   = thumb_path
        scene.render.resolution_x               = 512
        scene.render.resolution_y               = 512
        scene.render.resolution_percentage      = 100
        scene.render.image_settings.file_format = "PNG"

        bpy.ops.render.opengl(write_still=True, view_context=True)

    except Exception as e:
        print(f"[Ylos] Thumbnail generation failed: {e}")
        return ""

    finally:
        scene.render.filepath                   = orig["filepath"]
        scene.render.resolution_x               = orig["res_x"]
        scene.render.resolution_y               = orig["res_y"]
        scene.render.resolution_percentage      = orig["res_pct"]
        scene.render.image_settings.file_format = orig["file_format"]

    return thumb_path if os.path.isfile(thumb_path) else ""


def init_previews():
    global _pcoll
    if _pcoll is None:
        _pcoll = previews.new()


def clear_previews():
    global _pcoll
    if _pcoll is not None:
        previews.remove(_pcoll)
        _pcoll = None


def load_thumb_icon(blend_path: str) -> int:
    global _pcoll
    if _pcoll is None:
        init_previews()

    thumb_path = get_thumb_path(blend_path)
    if not os.path.isfile(thumb_path):
        return 0

    key = thumb_path
    if key not in _pcoll:
        try:
            _pcoll.load(key, thumb_path, "IMAGE")
        except Exception as e:
            print(f"[Ylos] Preview load failed for {thumb_path}: {e}")
            return 0

    return _pcoll[key].icon_id


def load_icon(abs_path: str) -> int:
    """Preview icon generique pour un chemin absolu arbitraire (ex un publish thumb.png,
    qui ne suit pas la convention '<stem>_thumb.png' de get_thumb_path/load_thumb_icon).
    Reutilise la meme collection _pcoll. Retourne 0 si le fichier n'existe pas (jamais
    d'exception - meme convention que load_thumb_icon)."""
    global _pcoll
    if _pcoll is None:
        init_previews()

    if not abs_path or not os.path.isfile(abs_path):
        return 0

    key = abs_path
    if key not in _pcoll:
        try:
            _pcoll.load(key, abs_path, "IMAGE")
        except Exception as e:
            print(f"[Ylos] Preview load failed for {abs_path}: {e}")
            return 0

    preview = _pcoll[key]
    # Blender charge les previews PARESSEUSEMENT : tant que rien ne lit leurs pixels, un
    # template_icon(icon_value=...) affiche l'indicateur de chargement au lieu de l'image
    # (constate en direct dans le N-panel). Toucher image_size force le decodage tout de
    # suite. On paie ici, hors draw() (ce module est appele depuis les caches), jamais dans
    # la boucle de redraw.
    try:
        _ = preview.image_size[:]   # image plein format (template_icon)
        _ = preview.icon_size[:]    # icone reduite (icon_value d'un bouton d'operateur)
    except Exception:
        pass
    return preview.icon_id


def reload_icon(abs_path: str) -> int:
    """Force le rechargement d'un preview pour un chemin ABSOLU arbitraire.

    Necessaire parce que _pcoll memoise par chemin : un thumb.png reecrit AU MEME CHEMIN (ce
    que fait chaque republish d'un step) resterait affiche avec l'ancienne image tant que la
    session Blender vit. Miroir de reload_thumb_icon pour la convention '<stem>_thumb.png'."""
    global _pcoll
    if _pcoll is not None and abs_path in _pcoll:
        del _pcoll[abs_path]
    return load_icon(abs_path)


def reload_thumb_icon(blend_path: str) -> int:
    global _pcoll
    if _pcoll is None:
        return 0

    thumb_path = get_thumb_path(blend_path)
    key = thumb_path

    if key in _pcoll:
        del _pcoll[key]

    return load_thumb_icon(blend_path)


# ---------------------------------------------------------------------------
# Headless publish thumbnail (allocate/finalize contract - cf. create_project.py).
# Separe de generate_thumbnail() ci-dessus (celle-ci reste le rendu viewport pour la
# preview WIP - usage different, contexte fenetre disponible). Ici : jamais
# bpy.ops.render.opengl (exige un contexte fenetre, casse le headless/hython-like usage) -
# rendu EEVEE reel sur scene/camera temporaires.
# ---------------------------------------------------------------------------

_BBOX_TYPES = {"MESH", "ARMATURE", "CURVE", "SURFACE", "META", "FONT", "VOLUME"}

# Derniere cause d'echec de render_publish_thumbnail() (texte de l'exception sous-jacente),
# posee module-level pour que l'appelant (op_publish) remonte la CAUSE a l'utilisateur sans
# changer la convention de retour "" ni la signature publique. "" = pas d'echec enregistre.
LAST_ERROR = ""

# Candidats de moteur de rendu, du plus recent au plus universel. BLENDER_EEVEE_NEXT n'existe
# qu'en Blender 4.2-4.4 (retire en 5.x) ; BLENDER_EEVEE couvre 5.x (et l'EEVEE historique) ;
# BLENDER_WORKBENCH est le filet toujours dispo. Le moteur est un enum DYNAMIQUE : on le probe
# par affectation (try/except TypeError), jamais par une liste codee en dur ni l'introspection
# RNA (cf. CLAUDE.md, bugs empiriques Blender #1).
_ENGINE_CANDIDATES = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")


def _pick_render_engine(scene) -> str:
    """Retourne le premier moteur de _ENGINE_CANDIDATES affectable sur CE Blender, en le
    posant sur scene.render.engine (l'affectation est le probe fiable : l'enum des moteurs est
    dynamique, un identifiant absent leve TypeError). Si aucun candidat ne passe (tres
    improbable), laisse scene.render.engine inchange et retourne sa valeur courante."""
    for engine in _ENGINE_CANDIDATES:
        try:
            scene.render.engine = engine
            return engine
        except TypeError:
            continue
    return scene.render.engine


def renderable_objects(objects):
    """Sous-ensemble de 'objects' qui contribuera REELLEMENT au rendu du thumbnail.

    Bug reel corrige : un set de layout publie ses meshes sources de scattering avec
    hide_render=True (elles n'existent que comme geometrie d'instanciation). Elles entraient
    quand meme dans _world_bbox() -> bbox gonflee, camera reculee sur du vide, zero pixel de
    sujet. Le cadrage doit porter sur ce que la camera VERRA, pas sur ce que l'appelant a
    collecte.

    Repli sur la liste complete si le filtre vide tout (mieux vaut un cadrage imparfait
    qu'aucun thumbnail : le contrat deux-phases REJETTE le publish sans thumb.png)."""
    visible = [o for o in objects if not getattr(o, "hide_render", False)]
    return visible or list(objects)


def has_visible_geometry(objects) -> bool:
    """True si au moins un objet porte de la geometrie REELLE (et pas seulement un transform).

    Sert de controle d'integrite au publish, pas de detail de cadrage. Trou reel trouve en
    conditions de production : un publish GLB de CHARACTER_Sissa02_Default ne contenait qu'un
    EMPTY — zero mesh — et etait pourtant marque 'complete'. Le garde-fou du contrat
    deux-phases (_missing_artifacts) ne verifie que "le fichier existe et n'est pas vide" :
    un .glb ne contenant qu'un empty pese quelques ko, donc il passe. Le seul signal etait un
    thumbnail plat... que l'ancien code produisait de toute facon, donc inexploitable.

    En rendant l'absence de geometrie FATALE pour le thumbnail, on la rend fatale pour le
    publish (thumbnail requis partout -> finalize_publish_version refuse le commit et preserve
    le staging). Le thumbnail cesse d'etre decoratif : il devient le test de fumee du publish.

    Un EMPTY, une CAMERA, une LIGHT ou un mesh a 0 vertex ne comptent pas. Un ARMATURE compte
    (un publish d'anim/rig est legitime) des lors qu'il a des os."""
    for obj in objects:
        t = obj.type
        data = getattr(obj, "data", None)
        if t == "MESH":
            if data is not None and len(data.vertices) > 0:
                return True
        elif t == "ARMATURE":
            if data is not None and len(getattr(data, "bones", ())) > 0:
                return True
        elif t in _BBOX_TYPES:
            return True
    return False


def _world_bbox(objects):
    """Bbox monde (min, max) unifiee des objets avec geometrie reelle. Retombe sur tous les
    objets si aucun ne qualifie (ex: que des EMPTY). Retourne (None, None) si aucun coin
    exploitable - l'appelant tranche, jamais d'inf/nan propage au cadrage."""
    candidates = [o for o in objects if o.type in _BBOX_TYPES] or list(objects)
    mins = Vector((float("inf"),) * 3)
    maxs = Vector((float("-inf"),) * 3)
    seen = False
    for obj in candidates:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            if any(math.isnan(c) or math.isinf(c) for c in world_co):
                continue
            mins = Vector(min(a, b) for a, b in zip(mins, world_co))
            maxs = Vector(max(a, b) for a, b in zip(maxs, world_co))
            seen = True
    if not seen:
        return None, None
    return mins, maxs


def _frame_camera(cam_obj, cam_data, mins, maxs,
                  azimuth_deg=45.0, elevation_deg=30.0, padding=1.4):
    """Cadrage trois-quarts : place cam_obj pour englober (mins, maxs) avec une marge, ET
    accorde les plans de clipping a la distance calculee.

    Bug reel corrige (2e cause des thumbnails plats, independante de l'eclairage) : une camera
    neuve (bpy.data.cameras.new) a clip_end = 1000 EN DUR. La distance de cadrage vaut
    radius * padding / sin(fov/2) - pour une entite de ~690 unites (un set de layout avec son
    terrain) ca donne ~1420 : le sujet ENTIER passe derriere le far plane et l'image ne
    contient que le fond du world, sans la moindre erreur remontee. Le clipping doit donc etre
    derive du cadrage, jamais laisse au defaut. Retourne la distance camera-centre."""
    center = (mins + maxs) / 2.0
    diagonal = (maxs - mins).length
    radius = max(diagonal / 2.0, 0.5)  # plancher pour eviter un cadrage degenere (bbox nulle)
    fov = cam_data.angle if cam_data.angle else math.radians(50)
    distance = (radius * padding) / math.sin(fov / 2.0)

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    offset = Vector((
        distance * math.cos(el) * math.sin(az),
        -distance * math.cos(el) * math.cos(az),
        distance * math.sin(el),
    ))
    cam_obj.location = center + offset
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Clipping derive du cadrage : le sujet occupe [distance - radius, distance + radius].
    # Marges larges pour absorber un bbox sous-estime (modifiers, instances) sans jamais
    # tomber sous les limites RNA (clip_start > 0).
    cam_data.clip_start = max(distance * 0.001, 1e-4)
    cam_data.clip_end = max((distance + radius * 4.0) * 1.5, 10.0)
    return distance


def _add_key_and_fill(tmp_scene, cam_obj, center):
    """Eclairage deterministe : une key alignee camera + une fill laterale plus faible.

    Bug reel corrige (1re cause des thumbnails plats) : un monde plat (use_nodes=False)
    eclaire un materiau diffus de facon UNIFORME quelle que soit la normale - sans lumiere
    directe, sujet et fond rendent a une luminance quasi identique. La key (alignee sur l'axe
    camera, facon flash monte camera) garantit que les faces visibles sont les faces
    eclairees ; la fill (azimut +75 deg, ~2.5x plus faible) casse la silhouette plate et donne
    du volume - sans elle un cube lu de face reste une tache uniforme.

    Retourne la liste des (objet, data) crees, a purger par l'appelant."""
    created = []
    to_center = center - cam_obj.location
    key_dir = to_center.normalized() if to_center.length else Vector((0.0, 0.0, -1.0))

    key_data = bpy.data.lights.new("YLOS_thumb_key", type="SUN")
    key_data.energy = 4.0
    key_data.angle = math.radians(10.0)
    key_obj = bpy.data.objects.new("YLOS_thumb_key", key_data)
    tmp_scene.collection.objects.link(key_obj)
    key_obj.rotation_euler = key_dir.to_track_quat("-Z", "Y").to_euler()
    created.append((key_obj, key_data))

    # Fill : meme direction pivotee de +75 deg autour de Z, elevation remontee -> eclaire le
    # cote laisse dans l'ombre par la key, sans jamais partir en contre-jour total.
    fill_dir = key_dir.copy()
    fill_dir.rotate(Euler((0.0, 0.0, math.radians(75.0)), "XYZ"))
    fill_dir.z = min(fill_dir.z + 0.25, -0.05)
    fill_data = bpy.data.lights.new("YLOS_thumb_fill", type="SUN")
    fill_data.energy = 1.6
    fill_data.angle = math.radians(25.0)
    fill_obj = bpy.data.objects.new("YLOS_thumb_fill", fill_data)
    tmp_scene.collection.objects.link(fill_obj)
    fill_obj.rotation_euler = fill_dir.normalized().to_track_quat("-Z", "Y").to_euler()
    created.append((fill_obj, fill_data))
    return created


def render_publish_thumbnail(objects: list, staging_dir: str, size: int = 512) -> str:
    """
    Rend un thumbnail headless (512x512 EEVEE par defaut, camera trois-quarts auto-cadree sur
    la bbox de 'objects', fond neutre, key+fill) dans staging_dir/thumb.png.

    try/finally strict : scene temporaire, camera (objet + data), lumieres et world temporaire
    sont purges quoi qu'il arrive - jamais de datablock residuel dans le .blend utilisateur.

    TROIS bugs reels corriges, cumulatifs (chacun suffisait a produire un thumbnail plat) :

    1. Ciblage de tmp_scene au rendu : bpy.ops.render.render() lit la scene a rendre via
       window.scene au niveau C, PAS via bpy.context.scene - un bpy.context.temp_override(
       scene=tmp_scene) est SILENCIEUSEMENT ignore par cet operateur des qu'une fenetre
       existe. Pattern officiel : bascule reelle de window.scene, restauree juste apres. En
       pur --background (bpy.context.window is None) on retombe sur temp_override, seul
       mecanisme disponible et suffisant dans ce mode.
    2. Eclairage : cf. _add_key_and_fill.
    3. Clipping camera : cf. _frame_camera (clip_end=1000 en dur sur une camera neuve).

    Objets caches au rendu (hide_render) exclus du cadrage ET du lien - cf.
    renderable_objects(). Aucune geometrie visible du tout -> ECHEC explicite (cf.
    has_visible_geometry) : le thumbnail est le test de fumee du publish, pas une decoration.

    Color management EPINGLE (Standard, exposure 0, gamma 1) : une scene neuve herite des
    defauts du fichier de demarrage - un utilisateur avec un view transform exotique ou une
    exposure decalee obtiendrait des thumbnails incoherents d'une machine a l'autre. Un
    thumbnail est une donnee de pipeline, pas un rendu artistique : il doit etre
    reproductible.

    Retourne le chemin du thumb en succes, "" en echec (meme convention que
    generate_thumbnail ci-dessus) - le garde-fou de completude vit deja dans
    finalize_publish_version() (_missing_artifacts) : pas duplique ici.
    """
    global LAST_ERROR
    LAST_ERROR = ""

    if not objects:
        LAST_ERROR = "no objects to frame"
        print("[Ylos] Publish thumbnail: no objects to frame")
        return ""

    framed = renderable_objects(objects)
    thumb_path = str(Path(staging_dir) / "thumb.png")

    tmp_scene = None
    tmp_world = None
    cam_obj = None
    cam_data = None
    lights = []
    engine = "?"

    try:
        tmp_scene = bpy.data.scenes.new("YLOS_thumb_tmp")
        # Moteur probe par affectation (BLENDER_EEVEE_NEXT retire en Blender 5.x - cf. CLAUDE.md).
        engine = _pick_render_engine(tmp_scene)
        tmp_scene.render.resolution_x = size
        tmp_scene.render.resolution_y = size
        tmp_scene.render.resolution_percentage = 100
        tmp_scene.render.image_settings.file_format = "PNG"
        tmp_scene.render.filepath = thumb_path
        tmp_scene.render.film_transparent = False
        # Color management epingle - cf. docstring. try/except : les enums de view transform
        # dependent de l'OCIO config, 'Standard' existe partout mais on ne parie pas dessus.
        try:
            tmp_scene.view_settings.view_transform = "Standard"
            tmp_scene.view_settings.look = "None"
            tmp_scene.view_settings.exposure = 0.0
            tmp_scene.view_settings.gamma = 1.0
        except TypeError:
            pass
        # Echantillonnage bas : un thumbnail 256px n'a pas besoin de 64 samples (EEVEE only,
        # l'attribut n'existe pas sur tous les moteurs).
        try:
            tmp_scene.eevee.taa_render_samples = 16
        except AttributeError:
            pass

        tmp_world = bpy.data.worlds.new("YLOS_thumb_world")
        tmp_world.use_nodes = False
        tmp_world.color = (0.18, 0.18, 0.18)
        tmp_scene.world = tmp_world

        cam_data = bpy.data.cameras.new("YLOS_thumb_cam")
        cam_obj = bpy.data.objects.new("YLOS_thumb_cam", cam_data)
        tmp_scene.collection.objects.link(cam_obj)
        tmp_scene.camera = cam_obj

        for obj in framed:
            if obj.name not in tmp_scene.collection.objects:
                tmp_scene.collection.objects.link(obj)

        if not has_visible_geometry(framed):
            LAST_ERROR = ("no renderable geometry among the %d object(s) to frame "
                          "(a publish with no visible content would be committed silently)"
                          % len(framed))
            print("[Ylos] Publish thumbnail: " + LAST_ERROR)
            return ""

        mins, maxs = _world_bbox(framed)
        if mins is None:
            LAST_ERROR = "degenerate bounding box (no usable geometry)"
            print("[Ylos] Publish thumbnail: degenerate bbox")
            return ""
        _frame_camera(cam_obj, cam_data, mins, maxs)
        lights = _add_key_and_fill(tmp_scene, cam_obj, (mins + maxs) / 2.0)

        window = bpy.context.window
        if window is not None:
            # Session interactive : window.scene est ce que render.render() lit reellement -
            # cf. docstring. Restaure inconditionnellement, meme si le rendu leve.
            orig_window_scene = window.scene
            try:
                window.scene = tmp_scene
                bpy.ops.render.render(write_still=True)
            finally:
                window.scene = orig_window_scene
        else:
            # --background pur (pas de fenetre) : pas de window.scene a bousculer, l'override
            # de contexte suffit (cf. test_thumbnail_headless.py, --background).
            with bpy.context.temp_override(scene=tmp_scene):
                bpy.ops.render.render(write_still=True)

    except Exception as e:
        LAST_ERROR = str(e)
        print(f"[Ylos] Publish thumbnail render failed (engine={engine}): {e}")
        return ""

    finally:
        if tmp_scene is not None:
            for obj in framed:
                if obj.name in tmp_scene.collection.objects:
                    tmp_scene.collection.objects.unlink(obj)
            if cam_obj is not None and cam_obj.name in tmp_scene.collection.objects:
                tmp_scene.collection.objects.unlink(cam_obj)
            for lobj, _ldata in lights:
                if lobj.name in tmp_scene.collection.objects:
                    tmp_scene.collection.objects.unlink(lobj)
        if cam_obj is not None:
            bpy.data.objects.remove(cam_obj, do_unlink=True)
        if cam_data is not None:
            bpy.data.cameras.remove(cam_data)
        for lobj, ldata in lights:
            bpy.data.objects.remove(lobj, do_unlink=True)
            bpy.data.lights.remove(ldata)
        if tmp_world is not None:
            bpy.data.worlds.remove(tmp_world)
        if tmp_scene is not None:
            bpy.data.scenes.remove(tmp_scene)

    if os.path.isfile(thumb_path):
        print(f"[Ylos] Publish thumbnail OK (engine={engine}): {thumb_path}")
        return thumb_path
    LAST_ERROR = LAST_ERROR or f"render produced no file at {thumb_path} (engine={engine})"
    print(f"[Ylos] Publish thumbnail: no file produced (engine={engine})")
    return ""
