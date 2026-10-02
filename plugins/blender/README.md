# Addon Blender — Ylos Pipeline

Addon de production (`bl_info` **v0.4.0**, Blender 4.2 LTS → 5.x, validé sur **5.2.0 LTS**).

Couche **adaptateur uniquement**. Aucune logique métier ici : l'addon importe
`create_project.py` (racine du repo, source de vérité unique, stdlib seule) et appelle ses
fonctions. Principe « logique unique, jamais dupliquée » — cf. `CLAUDE.md`.

> Ce README décrivait encore `ylos_pipeline.py` / `ylos_pipeline_ui.py` (deux fichiers plats
> dans `startup/` et `addons/`) : cette structure n'existe plus depuis longtemps. L'addon est
> un **package** installé par un lien symbolique unique.

## Installation (lien symbolique = versionné ET installé, sans copie)

Remplacer `5.2` par ta version de Blender. Sur macOS :

```bash
REPO="$HOME/Developer/YlosPipeline"   # jamais sous Desktop / Documents / iCloud (cf. CLAUDE.md, TCC)
BL="$HOME/Library/Application Support/Blender/5.2/scripts"

ln -sfn "$REPO/plugins/blender" "$BL/addons/ylos_pipeline"
```

Puis dans Blender : `Preferences > Add-ons` → activer **Ylos Pipeline**.
Panneau : vue 3D > touche `N` > onglet **Ylos**. Menu **Ylos** dans la barre du haut.

> C'est un **lien symbolique** : éditer un fichier du repo met Blender à jour directement.
> Jamais de copie (`_REPO_ROOT` se résout via `os.path.realpath`). Désinstaller = supprimer
> le lien.

## Carte du code

| Dossier | Rôle |
|---------|------|
| `__init__.py` | `register()`/`unregister()`, purge de `create_project` dans `sys.modules`, **libération des identités jumelles** (voir plus bas) |
| `core/` | `vocab.py` (**seul** home des items d'enum du vocabulaire pipeline), `usd_convention.py` (**seule** traduction de la convention USD en kwargs Blender), `asset.py`, `project.py`, `thumbnails.py`, `states.py`, `entity_thumbs.py`, `usd_composer.py`, `scene_checker.py` |
| `operators/` | `op_publish` (cœur `publish_entity_step`), `op_create_scene` (Scene Builder), `op_step_status`, `op_import_product`, `op_update_imports`, `op_state_manager`, `op_save_wip`, `op_io`… |
| `ui/` | `browser.py` (Project Browser), `scenefile.py`, `scene_check.py`, `state_manager.py`, `io_panel.py`, `menu.py`, `common.py` |

## N-panel « Ylos » (vue 3D > `N`)

| Section | Contenu |
|---------|---------|
| **Context** | Projet, type de contexte (asset/set/shot), entité active, steps |
| **Assets** | Vignettes des entités + **marqueur de statut par step** (schéma 2.2) ; compteur d'entités dans l'en-tête |
| **Scenefile** | **Statut du step actif** (Review / Approved / Auto) · versions WIP · Scene Builder · Save Version · Playblast/Render · ligne compacte **« Updates available »** + bouton Check |
| **State Manager** | Export states empilables + Publish · Import states (versions + updates) · **« Outdated after \<entité\> »** après un publish ; compteurs dans l'en-tête |
| **Scene Check** | Naming / readiness ; compteurs erreurs/warnings dans l'en-tête (section repliée par défaut) |

Les en-têtes affichent des compteurs lus **en mémoire ou en cache TTL** : aucun accès disque
supplémentaire par redraw.

## Statut par step (schéma 2.2)

Opérateur **`ylos.set_step_status`** (`entity`, `step`, `status`). `entity`/`step` vides =
l'entité et le step actifs de la scène. Seuls `review` et `approved` sont **persistés** dans
le manifeste ; `auto` efface la valeur explicite et rend le step à sa dérivation (`empty` /
`wip` / `published`, calculée depuis le disque). Point unique d'écriture :
`create_project.set_step_status` — même message d'erreur qu'en web et en CLI (un step non
déclaré est refusé, rien n'est écrit).

## Dépendances enregistrées au publish

Un publish enregistre ce que la scène avait **importé** (collections taguées
`ylos_import_entity/step/version`) dans l'entrée de manifeste, via
`finalize_publish_version(..., dependencies=[…])`. C'est la **seule** source exploitable côté
Blender : l'artefact publié est un `.usdc` ou un `.glb`, opaque au scan de layers USD ASCII
de `build_dependency_index()`.

Juste après le commit, l'addon lit `create_project.entity_dependencies()` et signale les
entités **en aval désormais périmées** (elles pointent une version antérieure), dans le
rapport de l'opérateur et dans la section State Manager.

## Convention USD (`docs/usd-convention.md`)

Tout export USD de l'addon (publish **et** export brut) passe par `core/usd_convention.py` :

* `upAxis = "Y"`, `metersPerUnit = 1` (valeurs venues de `create_project`, jamais réécrites ici) ;
* prim racine `/<Entité>` pour un asset/set, `/ROOT` pour un shot, avec `defaultPrim` posé ;
* cible `offline` → artefact **`.usdc`** (le `.usd` nu est banni par la convention) ;
* import symétrique : la scène revient à son orientation et à son échelle d'origine
  (aller-retour vérifié par `tools/blender/test_usd_convention_headless.py`).

Les kwargs sont **filtrés contre la RNA** du Blender qui tourne : un argument inconnu d'un
build plus ancien est ignoré avec une note console, jamais un `TypeError` en plein publish.

## Scenefiles multi-DCC

Le listing des WIP est un **adaptateur mince** sur `create_project.list_scenefiles` /
`_latest_wip(..., "blender")`. Un WIP Houdini (`.hip`/`.hiplc`/`.hipnc`) du même step est
donc **visible** dans le panneau (avec son DCC) mais **jamais proposé à l'ouverture** depuis
Blender — et il ne décale pas la numérotation des versions Blender.

## Rechargement de l'addon

`Ylos > Reload Pipeline` désactive / purge / réactive (`ui/menu._purge_addon_modules`) : un
`addon_disable` + `addon_enable` seul **ne recharge pas** le code (les sous-modules restent
dans `sys.modules`). Le rechargement est **refusé** tant qu'un export state existe (RNA
invalidée sous un UIList vivant = crash observé).

## Identités jumelles (register/unregister)

Le même fichier est atteignable sous **deux identités d'import** : `ylos_pipeline` (le lien
dans `scripts/addons`, ce que les préférences activent) et `blender` / `plugins.blender` (le
chemin du repo, ce qu'importent les scripts headless). Chaque identité construit ses
**propres** objets de classe partageant les mêmes `bl_idname` ; enregistrer la seconde
désenregistrait la première **implicitement**, et au quit de Blender le `unregister()` de
l'identité dépossédée levait :

```
RuntimeError: unregister_class(...): missing bl_rna attribute from '_RNAMeta' instance
Exception in module unregister(): .../scripts/addons/ylos_pipeline/__init__.py
```

`register()` **libère** désormais proprement toute identité jumelle vivante avant de
s'enregistrer, et `unregister()` ne défait que ce que **cette** identité a posé (chaque
`unregister_class` gardé par `cls.is_registered`). Les deux sont **idempotents** et ne lèvent
jamais. Couvert par `tools/blender/test_addon_registration_headless.py`.

## Tests (hors CI — exigent Blender 5.2)

```bash
BIN="${YLOS_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
for t in tools/blender/test_*_headless.py tools/blender/test_vocab_sync.py; do
  "$BIN" --background --python "$t" || echo "FAILED: $t"
done
```

`tools/blender/test_launch_context.py` fait exception : il tourne sous `python3` (il lance
Blender lui-même).
