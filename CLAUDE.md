# CLAUDE.md — Pipeline Ylos Prod

> Référence de travail : règles durables et carte du code, **pas un journal**.
> Historique détaillé (diagnostics, incréments datés) → `docs/pipeline-log.md` + `git log`.
> **Ne PAS appender d'incrément ici** : un nouveau travail va dans le commit + `pipeline-log.md` ;
> on ne touche ce fichier que si une **règle durable** change. Toute affirmation ci-dessous se
> vérifie contre le code avant qu'on s'y fie — un fichier peut mentir sur ce qui tourne réellement.

## Projet
Pipeline de production 3D/VFX freelance (**Ylos Prod**). Deux problèmes **distincts** à ne
jamais fusionner : la **gestion de production** (jobs, statut, deadlines) et le **pipeline
technique d'assets** (arborescence, ingestion, versioning). Construit depuis zéro en
anticipant des consommateurs futurs — agents d'automatisation (n8n), plugins DCC (Houdini,
Blender) — pas seulement le workflow humain immédiat.

## Environnement & contraintes
- **Machine** : MacBook Pro M2 Max, **macOS / Apple Silicon uniquement**. Toute dépendance
  native tourne sur arm64. Rien de Windows-only.
- **Blender 5.2.0 LTS** (Python 3.13), **Houdini** (édition Apprentice → rendus watermarkés,
  extensions `.usdnc`/`.hdanc`). Binaire Blender surchargeable par `$YLOS_BLENDER`.
- **stdlib seule** pour tout module qu'un DCC importe (`create_project.py` en tête). Les
  interpréteurs embarqués (hython, Python de Blender) ne doivent pas dépendre d'un `pip
  install`. Une dépendance tierce dans le chemin d'import = friction.
- **Python 3.9 → 3.13** : la CI teste **3.9 / 3.11 / 3.13** (3.9 = python système macOS qui fait
  tourner `ylos_ui.py` ; 3.13 = Blender). `create_project.py` et `ylos_ui.py` doivent tourner
  **dès 3.9** → pas de syntaxe 3.10+ (`match`, `X | Y` au runtime hors annotations…) : du code qui
  passe en local sous 3.13 peut casser la CI sous 3.9.
- **Stockage 3 tiers**, jamais mélangés :
  - NVMe interne → **cache régénérable** (`$PROJ_CACHE`, jetable, hors Git)
  - NVMe externe Thunderbolt → **source / projets actifs** (`$PROJ_ROOT`, permanent)
  - Disques mécaniques → **archive froide** (jamais de projet actif)

## Principes verrouillés
1. **Racine relocalisable** : tout passe par `$PROJ_ROOT` / `$PROJ_CACHE`. Jamais de chemin
   absolu en dur. Un projet se déplace interne↔externe sans casser les références.
2. **Séparation cache / source** : régénérable (interne) vs permanent (externe). Les deux
   tiers ne se croisent pas.
3. **Source de vérité lisible machine** : le manifeste (`project.json`, `manifest.json`) fait
   foi — **pas** les conventions de nommage de dossiers. On refuse d'écrire à côté du manifeste.
4. **Séparation prod / pipeline technique** (cf. Projet) : deux problèmes, deux archis.
5. **Logique unique, jamais dupliquée** : toute logique métier (création, résolution,
   composition, lecture de publishes, validation, pinning) vit dans **`create_project.py`**,
   importable par tous les consommateurs. Les plugins/serveur sont des **adaptateurs minces**.
   Écrire une règle dans le serveur HTTP la rendrait invisible aux DCC → ils la ré-implémenteraient.

## Carte du code — où vit quoi (= source de vérité)
```
create_project.py     ORCHESTRATEUR. Source de vérité UNIQUE, stdlib seule. Contient :
                      création projet/entité, vocabulaire (ASSET_TYPES/SET_TYPES/SHOT_TYPES,
                      DEFAULT_*_STEPS, PROD_TYPES, PROD_TYPE_TO_TARGET), validation nommage
                      (validate_entity_name), contrat deux-phases (allocate/finalize_publish_version,
                      kind), composition (refresh_entity_root/build_asset_root/build_shot_root),
                      résolution (resolve_entity, resolve_open_target, resolve_entity_thumbnail),
                      lecture publishes (list_publishes, latest_publish_artifact), caches
                      (entity_cache_dir, resolve_cache), pinning web (pin_web_asset, sync_web_assets),
                      I/O atomique (_atomic_write_*), verrou (acquire_lock), clean_stale_staging,
                      scenefiles multi-DCC (list_scenefiles), statut par step 2.2
                      (get/set_step_status), listing d'entités (list_entities, orphelins flaggés),
                      specs DCC-agnostiques (scene_starter_spec, playblast_spec, render_spec),
                      dépendances (build_dependency_index, entity_dependencies).
ylos_ui.py            Serveur HTTP local + API REST (/api/*). Adaptateur mince → create_project.
app.html              Web UI (Project Browser). Config via GET /api/config (jamais codée en dur).
migrate_to_2.0.py     Migration legacy → convention TYPE_Nom_Variant (dispo si vrai projet legacy).
README.md             Arborescence d'un projet CRÉÉ sur disque (source + cache) — ne pas la redupliquer.

plugins/blender/      Addon (symlink dans scripts/addons/ylos_pipeline, JAMAIS une copie).
  __init__.py           register/unregister ; purge sys.modules de create_project au (un)register.
  core/                 vocab.py (SEUL home des EnumProperty items), asset.py, project.py,
                        thumbnails.py, states.py, entity_thumbs.py, usd_composer.py, scene_checker.py.
  operators/            op_publish (cœur publish_entity_step), op_state_manager, op_save_wip,
                        op_new_asset/project, op_open_context, op_import_product, op_io, op_scene_check…
  ui/                   panel.py (N-panel unifié), menu.py (top bar), state_manager.py, io_panel.py.
plugins/houdini/      ylos_publish.hdanc (HDA, régénéré JAMAIS édité en GUI), python/ylos_houdini.py
                      (bridge : caches, render Karma, deliver), toolbar/ shelf, ylos.json.

tools/blender/        launch_context.py (launcher versionné — TOUT lancement Blender passe par lui),
                      backfill_thumbnails.py, test_*_headless.py (Blender réel, HORS CI stdlib).
tools/houdini/        build_publish_hda.py (source scriptée du HDA), test_*_e2e.py (hython, hors CI).
tests/                Suite stdlib CI (python3 -m unittest, sans DCC). ~150+ tests.
docs/                 usd-convention.md, migration-*.md, plan-houdini-shots.md, ui-workstream.md,
                      pipeline-log.md (journal détaillé archivé).
```

## Commandes
```bash
# Tests logique orchestrateur (stdlib, sans DCC) — exactement ce que vérifie la CI (3.9/3.11/3.13) :
python3 -m unittest discover -s tests -v
# Batterie headless Blender (HORS CI, exige Blender 5.2) — launch_context = python3, le reste via Blender :
BIN="${YLOS_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
for t in tools/blender/test_*.py; do
  if [ "$(basename "$t")" = test_launch_context.py ]; then YLOS_BLENDER="$BIN" python3 "$t";
  else "$BIN" --background --python "$t"; fi
done
# e2e Houdini (hython, hors CI) :
hython tools/houdini/test_shot_workflow_e2e.py
# Régénérer le HDA après TOUT changement de son build (jamais d'édition GUI) :
hython tools/houdini/build_publish_hda.py
# Lancer l'UI web (Project Browser) :
python3 ylos_ui.py --port 8765          # ou ./launch_ui.command (double-clic Finder)
```

## Contrats & conventions
- **Schéma 2.2.0** (partagé `project.json` + `manifest.json`). Additif sur 2.0 : `frame_range`
  optionnel du shot (2.1) ; `step_status` explicite par step + `dependencies` sur les entrées
  de `step_publishes` (2.2). `additionalProperties: true` → aucun manifeste antérieur invalidé.
  Tout changement de schéma = **migration documentée**, jamais une édition silencieuse.
  Contrats : `project.schema.json`, `asset.schema.json`, `docs/migration-*.md`.
- **Statut par step** : seuls `review`/`approved` sont **persistés** (`set_step_status`, point
  unique) ; `empty`/`wip`/`published` sont **dérivés** du disque à la lecture
  (`get_step_status`), jamais écrits. Un step non déclaré est refusé (pas de création implicite).
- **Dépendances** : `finalize_publish_version(..., dependencies=[{entity, step, version}])`
  enregistre ce que le DCC avait importé ; `build_dependency_index()` fusionne cette source
  (manifeste) avec le scan des layers USD **ASCII** (`@…@`, `$PROJ_ROOT` expansé). Jamais de
  fichier annexe : manifestes + layers sur disque font foi. `.usdc`/`.usdnc` ignorés → côté
  Houdini, la source manifeste est la seule fiable.
- **Scenefiles multi-DCC** : `list_scenefiles()` / `_latest_wip(…, dcc)` scannent
  `SCENEFILE_EXTENSIONS` (`.blend` Blender, `.hip/.hiplc/.hipnc` Houdini) — un consommateur ne
  re-scanne jamais `wip/` lui-même. `resolve_open_target(dcc="houdini")` résout les WIP `.hip*`.
- **Convention de nommage `TYPE_Nom_Variant`** validée **à la création** par
  `validate_entity_name(name, entity_type, sub_type)` — point unique, même message d'erreur
  partout (web/Blender/CLI). Une entité sans manifeste ou hors convention est un **fantôme** :
  `op_save_wip` la refuse, la web UI la remonte en carte `broken`. On ne fabrique jamais
  d'arborescence à la volée depuis un nom libre.
- **Publish = contrat deux-phases** `allocate_publish_version()` → export → `finalize_publish_version()`.
  Paramètre `kind` : `"lop"` (Houdini, instantané complet, `lop_publishes`) ou un nom de step
  (`step_publishes[step]`). **Thumbnail requis partout** : `finalize` refuse le commit si un
  `expected_artifacts` manque/est vide, et préserve le `.staging` pour audit/retry. Écriture
  directe (`publish_asset`) **dépréciée**. `finalize` (kind ≠ lop) recompose auto le root de l'entité.
- **Le thumbnail est le test de fumée du publish**, pas une décoration. Rendu EEVEE headless réel
  (`render_publish_thumbnail`), color management épinglé (`Standard`, exp 0, gamma 1). Aucune
  géométrie visible → échec explicite → publish refusé. Cadrage/clipping **dérivés de la bbox
  visible** (exclut `hide_render`), jamais de `clip_end` en dur. **Jamais `render.opengl`** (casse le headless).
- **Composition USD** : `refresh_entity_root()` recompose le root depuis le manifeste (latest
  publish `complete` par step). Asset/set → `asset_root.usda` (`defaultPrim=<Nom>`, `DOWNSTREAM_ORDER`) ;
  shot → `shot_root.usda` (root `/ROOT`, `SHOT_DOWNSTREAM_ORDER` — le lighting override l'anim —
  + timecodes du `frame_range`). Un publish **LOP** ou un **cache** (VDB/GLB) n'entre JAMAIS dans
  la compo (`_latest_by_step` filtre par `_is_usd_layer`). Convention figée : `docs/usd-convention.md`.
- **Format d'artifact = décision d'orchestrateur** (`PROD_TYPE_TO_TARGET`) : cible `web`
  (XR/AR/VR/GAME) → `.glb` ; `offline` (FILM/SERIES) → `.usd`. `op_publish` est format-aware
  via `get_pipeline_target`. Un publish émet **un** artefact selon la cible.
- **Caches** : scratch jetables sous `$PROJ_CACHE/.../<step>/<label>/` (versioning natif du
  filecache SOP, aucun manifeste) ; caches *consommables* (FX publié) = contrat deux-phases
  `kind=<step>` dans la source. Rendus Karma → tier cache, `deliver_render()` est le **seul**
  chemin qui écrit dans `delivery/`. Expressions littérales (`$PROJ_CACHE`, `$F4`) jamais résolues en dur.
- **Vocabulaire pipeline** : valeurs dans `create_project.py`, libellés/items d'enum dans
  `plugins/blender/core/vocab.py` uniquement. **Plus jamais de `items=[…]` en dur pour du
  vocabulaire pipeline** hors `vocab.py` ; seuls les enums d'**UI pure** en gardent (garde :
  `grep -rn "items=\[" plugins/blender` → uniquement `ylos_preview_size` dans `core/project.py`
  et `direction` UP/DOWN dans `op_state_manager.py`). Un `items=` callback ne retourne JAMAIS un
  tuple construit à la volée (GC/bpy) → tous les `*_ITEMS` sont module-level.
- **I/O & concurrence** : écritures atomiques (`_atomic_write_text/json`, tmp + `os.replace`)
  pour tout manifeste/root. `acquire_lock()` (fcntl.flock) = **seul** point de verrou — advisory,
  non réentrant (jamais imbriqué), POSIX-only, non fiable sur NFS/SMB (OK car stockage local).
- **Langue** : le **français** est réservé à la **communication avec Sébastien** et à la
  **doc** (`docs/`, ce fichier). **Tout le reste est en anglais** : le code (identifiants,
  noms de fichiers, commentaires, docstrings) ET **toutes les chaînes vues par l'utilisateur**
  — messages d'erreur, sorties CLI/`print`, help `argparse`, labels/tooltips des panels
  Blender, textes d'`app.html`. (Décision Sébastien 2026-09-09 : le produit devient anglophone ;
  les tests qui assèrent des chaînes FR sont traduits avec le code.) `pathlib` partout, jamais
  de concaténation manuelle de chemins.

## Gotchas DCC (durables — vérifiés en live)
**Houdini — LOP HDA** (cf. `tools/houdini/build_publish_hda.py`) :
1. HDA verrouillé refuse l'écriture directe sur ses nœuds internes → **promouvoir** les
   paramètres au niveau HDA, relier via `chs("../param")` posé au build.
2. `hda_def.save()` sans `template_node=hda_node` **perd** les `setExpression()` au reload.
3. ROP USD : `savestyle='separate'` échoue sur layer anonyme → `'flattenimplicitlayers'` +
   `errorsavingimplicitpaths=0` ; rediriger le `lopoutput` de stitching vers un tmp jetable.
4. Extensions Apprentice (`.usdnc`/`.hdanc`) → **découvrir sur disque** après écriture, jamais
   supposer `.usd`/`.hda`.
5. `node.render()` sur `usdrender_rop` **ne bloque pas** en GUI (husk async) → poser
   **`soho_foreground=1`**. (Filet indépendant : `finalize` exige `expected_artifacts`.)
6. `item_generator_script` d'un menu dynamique = mode **`eval`** (une expression qui *retourne*
   la liste plate, ni `return` ni `menu=`) → déléguer à `hdaModule().kind_menu_items(...)`.

**Blender** (miroir, cf. `plugins/blender/core/`) :
1. **5.x a retiré `BLENDER_EEVEE_NEXT`** → tout moteur de rendu se **probe par affectation
   `try/except TypeError`**, jamais codé en dur (chaîne `NEXT → EEVEE → WORKBENCH`). Sites :
   `thumbnails._pick_render_engine`, `core/project._resolve_render_engine`.
2. **`addon_disable`+`addon_enable` NE RECHARGE PAS le code** (modules restent en `sys.modules`,
   `.pyc` périmé exécuté sans le moindre message) → `ui/menu._purge_addon_modules` purge
   `ylos_pipeline` + sous-modules. **Vérif avant tout diagnostic** : `inspect.getsource()` lit le
   *fichier* et peut mentir ; `f.__code__.co_consts/co_names` montrent le *bytecode chargé* — comparer les deux.
3. Caméra neuve = `clip_end=1000` en dur → dériver les plans de clipping de la distance de cadrage.
- **Rechargement addon propre** : `__init__.py` purge `create_project` de `sys.modules` au
  (un)register ; un launcher/addon passe par un **symlink**, jamais une copie (résolution `_REPO_ROOT`).

## Décisions tranchées & branches
- **Design hybride, asset-centric** : l'asset est la colonne vertébrale (step-folders +
  `manifest.json` + `asset_root.usda`) ; `sets/` et `shots/` = entités à part entière (steps +
  publishes), scaffoldées. **Pas de bibliothèque transverse** (`$ASSET_LIB`) — chaque projet
  autonome, réemploi par copie. **Cache root interne séparé par projet** (`$PROJ_CACHE/<projet>`).
- Dossier config = **`_pipeline/`**. Project Browser = **web** (choix hybride assumé).
- **`main`** = branche de travail unique (ex-`ui-pipeline`). Archivées, inactives :
  `legacy/standalone-addon-v0.2.7` (ancien addon standalone) et `legacy/v0.4-monorepo`
  (rewrite orphelin ; correctifs utiles C1/C3 déjà absorbés, reste hors scope).

## Chantiers ouverts (hors scope actuel — cf. `pipeline-log.md` pour le détail)
- **Update Import = remplacement pur (v1)** : aucun remap des overrides (matériaux, contraintes,
  anim ajoutés à la main) — à traiter avant un usage intensif.
- **Variantes dans le composeur unique** : `refresh_entity_root` ne les gère pas encore ;
  `plugins/blender/core/usd_composer.py` (logique de variantes dupliquée) reste à résorber.
- **Env par-session** (cf. Tensions) : launcher posant `$PROJ_ROOT`/`$PROJ_CACHE` par process.
- **Cycle de vie des caches** : TTL / sweep / quotas du tier régénérable (aujourd'hui jamais purgé).
- **Multi-séquences** de shots ; **tooling comp 2D** ; **up-axis Blender↔USD** à vérifier à l'usage.

## Tensions connues
- **Collision d'env vars** : `$PROJ_ROOT`/`$PROJ_CACHE` sont globaux au shell. Houdini +
  Blender sur deux projets simultanés → conflit. Piste : env posée **par-session** par le
  launcher. À résoudre avant que le multi-projet simultané devienne réel.

## Mode de collaboration attendu
- **Lis avant d'écrire** : mappe l'état réel du repo avant toute mutation.
- **Plan avant action** pour toute opération destructive ou tout changement de schéma.
- **Ne casse pas l'importabilité** : pas d'import lourd au niveau module dans ce que les DCC chargent.
- **Challenge l'archi** : si une décision a une implication downstream non vue, nomme-la avant
  d'exécuter. Réframe une intention mal posée plutôt que de la suivre aveuglément.
- **Un fix n'est pas fini sans son test** : stdlib (CI) pour la logique orchestrateur ; headless
  Blender / e2e hython (hors CI) pour ce qui exige un DCC.
