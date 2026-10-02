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
                      Verbes open-blender : ouvrir (WIP-first) / scenefile / import (version exacte) /
                      create (Scene Creator) ; set-step-status ; reveal ; env par-session (_launch_env).
app.html              Web UI (Project Browser cockpit : grille → vue entité en drill-down, steps +
                      statut, galerie de versions A/B, scenefiles multi-DCC, products, dépendances).
                      Config via GET /api/config (jamais codée en dur).
launch_ui.command     MOTEUR de lancement de l'UI (bash 3.2) : sans option = foreground (Terminal) ;
                      --detach (arrière-plan, log ~/.ylos/ui-server.log) | --stop | --status
                      (stopped / running / stale / blocked). « Est-ce Ylos ? » = process (listener du
                      port dont la ligne de commande lance ylos_ui.py), jamais « quelque chose répond ».
Ylos.app/             Coquille double-clic SANS Terminal : retrouve le repo, délègue à launch_ui.command,
                      dialogue natif (Ouvrir / Redémarrer / Arrêter ; erreurs + fin du log). AUCUNE logique
                      de lancement propre ; chaque double-clic laisse une trace dans ~/.ylos/launcher.log
                      (sans Terminal, un échec silencieux est indiagnosticable). Icône :
                      tools/macos/make_app_icon.py (Pillow, outil dev ; l'.icns est committé ;
                      --style fullbleed macOS 26 / classic macOS ≤ 15).
migrate_to_2.0.py     Migration legacy → convention TYPE_Nom_Variant (dispo si vrai projet legacy).
README.md             Arborescence d'un projet CRÉÉ sur disque (source + cache) — ne pas la redupliquer.

plugins/blender/      Addon (symlink dans scripts/addons/ylos_pipeline, JAMAIS une copie).
  __init__.py           register/unregister ; purge sys.modules de create_project au (un)register.
  core/                 vocab.py (SEUL home des EnumProperty items, dont STEP_STATUS_ITEMS), asset.py
                        (wrappers minces sur list_scenefiles/list_entities/resolve_entity — plus aucun
                        scan wip/ local), project.py, thumbnails.py, states.py, entity_thumbs.py,
                        usd_convention.py (SEULE traduction de docs/usd-convention.md en kwargs RNA,
                        filtrés contre le Blender qui tourne), usd_composer.py, scene_checker.py.
  operators/            op_publish (cœur publish_entity_step, dependencies= depuis les collections
                        d'import taguées, impact downstream), op_state_manager, op_save_wip,
                        op_create_scene / op_playblast / op_render (specs orchestrateur),
                        op_step_status (ylos.set_step_status), op_new_asset/project, op_open_context,
                        op_import_product (famille via resolve_entity), op_update_imports, op_io…
  ui/                   Modèle Prism : PAS de N-panel, menu top bar + fenêtres (popups). menu.py (top bar),
                        browser.py (Project Browser : grille de vignettes, seul endroit qui liste les autres
                        entités), scenefile.py (statut step, WIP, Save Version), scene_check.py, core/publish_check.py (check pré-publish + vérif re-import, règle dans create_project.validate_publish_roundtrip),
                        state_manager.py, io_panel.py, common.py (carte de contexte partagée).
                        Montage des fenêtres : operators/op_asset_list.py (ylos.asset_browser),
                        op_windows.py (open_scenefile / open_scene_check), op_state_manager, op_io.
plugins/houdini/      ylos_publish.hdanc (HDA, régénéré JAMAIS édité en GUI ; embarque son
                      PythonModule depuis tools/houdini/build_publish_hda.py), python/ylos_houdini.py
                      (bridge : WIP + sidecar, create_scene = réalisation de scene_starter_spec via
                      starter_plan pur, caches, render Karma, deliver, dependencies_from_paths),
                      python/ylos_browser_model.py (PUR : ni hou ni Qt, nourri par l'orchestrateur),
                      python/ylos_browser_panel.py (vue Qt via hutil.Qt, hou paresseux),
                      python_panels/ylos_browser.pypanel (cockpit), toolbar/ shelf (11 outils),
                      ylos.json (HOUDINI_PATH / OTLSCAN / PYTHONPATH / TOOLBAR).

tools/blender/        launch_context.py (launcher versionné — TOUT lancement Blender passe par lui ;
                      --kind wip|publish|scene_default|create, --version ; import via
                      ylos.import_product, env par-session), backfill_thumbnails.py,
                      test_*_headless.py (Blender réel, HORS CI stdlib — 20 scripts).
tools/houdini/        build_publish_hda.py (source scriptée du HDA), test_*_e2e.py (hython, hors CI).
tests/                Suite stdlib CI (python3 -m unittest, sans DCC). ~340 tests (dont
                      test_step_status, test_dependency_index, test_houdini_browser_*).
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
# Garde de compilation Houdini SANS licence (le Python embarqué est sous Frameworks/Python.framework,
# PAS sous Resources/bin) :
/Applications/Houdini/Houdini21.0.631/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
  -m py_compile plugins/houdini/python/*.py tools/houdini/*.py
# Lancer l'UI web (Project Browser). Usage courant : double-clic sur Ylos.app (racine du repo, sans
# Terminal ; re-double-clic = Ouvrir / Redémarrer / Arrêter). Même moteur en ligne de commande :
./launch_ui.command --detach            # serveur en arrière-plan + navigateur (log ~/.ylos/ui-server.log)
./launch_ui.command --status            # stopped | running | stale (code plus récent que le serveur) | blocked
./launch_ui.command --stop
./launch_ui.command                     # foreground : le Terminal EST le serveur (ou double-clic Finder)
python3 ylos_ui.py --port 8765          # foreground brut, sans navigateur
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
  (XR/AR/VR/GAME) → `.glb` ; `offline` (FILM/SERIES) → **`.usdc`** (le `.usd` nu est banni par
  la convention). `op_publish` est format-aware via `get_pipeline_target`. Un publish émet
  **un** artefact selon la cible.
- **USD côté Blender = `plugins/blender/core/usd_convention.py`**, point unique : tout export/import
  USD (`op_publish`, `op_io`, `op_import_product`) passe par ses kwargs (Y-up, `metersPerUnit`
  de l'orchestrateur, root prim `/<Entité>` pour asset/set et `/ROOT` pour shot, `defaultPrim`),
  filtrés contre la RNA du Blender qui tourne. Aller-retour Y-up vérifié sans perte (5.2).
- **Env par-session (tension levée)** : tout lancement DCC reçoit `$PROJ_ROOT` (= **parent** du
  dossier projet, contrat `$PROJ_ROOT/<projet> == project_dir`) et `$PROJ_CACHE` dans le process
  **enfant** uniquement (`ylos_ui._launch_env`, ré-affirmé par `launch_context._apply_session_env`,
  tracé dans `~/.ylos/launch.log`). **Jamais un export global.**
- **Import depuis le cockpit = `ylos.import_product`** (collection taguée
  `ylos_import_entity/step/version`, version exacte) — jamais `wm.usd_import` /
  `import_scene.gltf` en direct : un import non tagué est invisible de Check Updates et absent
  des `dependencies` du prochain publish.
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
- **Cycle de vie du serveur UI** : détaché (`Ylos.app` / `--detach`) il survit à toute fenêtre et
  **ne recharge jamais son code** (`create_project` est importé au démarrage). Après toute modif de
  `ylos_ui.py` / `create_project.py`, `launch_ui.command --status` dit `stale` et *Redémarrer* est la
  seule façon d'exécuter le nouveau code — premier réflexe quand un fix « ne prend pas » côté web.
  `--detach` met le serveur dans **sa propre session** (`setsid`) : lancé par `Ylos.app`, le lanceur est
  le process principal d'un job launchd, qui peut tuer ce qui reste dans son groupe de processus à sa
  sortie (comportement supposé, non vérifié sur un Mac ; `tests/test_launch_ui.py` joue launchd). Le
  double-clic qui « ne fait rien » se diagnostique dans `~/.ylos/launcher.log` (+ `ui-server.log`).
- **Repo hors des dossiers protégés de macOS (TCC)** : `Ylos.app` est un script ; macOS lui refuse
  (`Operation not permitted`, **sans invite**) tout fichier sous `~/Desktop`, `~/Documents`,
  `~/Downloads`, iCloud Drive et les volumes externes / réseau — constaté sur macOS 27, repo sous
  `~/Desktop`. Le serveur relit `app.html` à chaque requête : il lui faut l'accès pendant toute sa vie.
  Repo et projets que le serveur lit : hors de ces zones (ex. `~/Developer`) ; sinon lancer via
  `launch_ui.command` dans le Terminal (qui détient la permission). Diagnostic : `~/.ylos/launcher.log`.
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
7. **UI Houdini testable sans licence** = découpage en trois : modèle **pur** (ni `hou` ni Qt,
   nourri par l'orchestrateur, testé en stdlib) / vue Qt (`hutil.Qt`, PySide6 sous H21) / pont
   `hou` paresseux. Pattern à reproduire pour tout futur panel DCC. `hou.ui.selectFile` n'est pas
   dans `hou.py` (ajouté à l'exécution par `houpythonportion/ui.py`).

**Blender** (miroir, cf. `plugins/blender/core/`) :
1. **5.x a retiré `BLENDER_EEVEE_NEXT`** → tout moteur de rendu se **probe par affectation
   `try/except TypeError`**, jamais codé en dur (chaîne `NEXT → EEVEE → WORKBENCH`). Sites :
   `thumbnails._pick_render_engine`, `core/project._resolve_render_engine`.
2. **`addon_disable`+`addon_enable` NE RECHARGE PAS le code** (modules restent en `sys.modules`,
   `.pyc` périmé exécuté sans le moindre message) → `ui/menu._purge_addon_modules` purge
   `ylos_pipeline` + sous-modules. **Vérif avant tout diagnostic** : `inspect.getsource()` lit le
   *fichier* et peut mentir ; `f.__code__.co_consts/co_names` montrent le *bytecode chargé* — comparer les deux.
3. Caméra neuve = `clip_end=1000` en dur → dériver les plans de clipping de la distance de cadrage.
4. **`bpy.utils.unregister_class()` sur une classe non enregistrée LÈVE** (pas de no-op) ;
   `<UIType>.remove(<func absente>)` ne lève pas. L'addon est joignable sous **deux identités
   d'import** (`ylos_pipeline` via le symlink addons, `blender`/`plugins.blender` via le chemin
   repo — ce que font les tests headless) : enregistrer la seconde dé-enregistre implicitement la
   première (les lignes Info « has been registered before » sont le symptôme) et la fermeture
   levait `missing bl_rna`. **`register()`/`unregister()` sont pilotés par l'état**
   (`_registered_classes`, gardes `is_registered`, libération de l'identité jumelle), jamais par
   le tuple statique `_classes`. Test : `test_addon_registration_headless.py`.
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
- **Cycle de vie des caches** : TTL / sweep / quotas du tier régénérable (aujourd'hui jamais purgé).
- **Multi-séquences** de shots ; **tooling comp 2D**.
- **HDA `ylos_publish.hdanc` à régénérer** (`hython tools/houdini/build_publish_hda.py`) dès qu'une
  licence Houdini est disponible : la source (`dependencies=` au publish) a changé, le `.hdanc`
  embarque encore l'ancien PythonModule. Puis validation manuelle : panel, New Scene (shot + asset),
  Save Version + commentaire, publish → clé `dependencies` dans le manifeste.
- **Shakedown réel (plan 5a)** : `~/Ylos__Test` n'a encore aucune arête de dépendances (aucun
  publish n'en déclare) ; nettoyage Phase 0.1 (orphelin `sets/lecube`, publish vide
  `CHARACTER_Sissa02_Default/modeling/v001`) = décision utilisateur, données de test.

## Tensions connues
- **Collision d'env vars** — **levée** côté lancements web/launcher (env par-session, cf.
  Contrats). Reste vrai pour un Houdini lancé **à la main** depuis un shell : `ylos.json` ne pose
  pas `$PROJ_ROOT` ; le panel/bridge lisent `~/.ylos/active_project`.
- **Licence Houdini** : `hython` sans licence sur la machine (serveur `localhost:1715` muet) →
  aucun e2e Houdini ni régénération de HDA possible tant qu'elle n'est pas renouvelée.

## Mode de collaboration attendu
- **Lis avant d'écrire** : mappe l'état réel du repo avant toute mutation.
- **Plan avant action** pour toute opération destructive ou tout changement de schéma.
- **Ne casse pas l'importabilité** : pas d'import lourd au niveau module dans ce que les DCC chargent.
- **Challenge l'archi** : si une décision a une implication downstream non vue, nomme-la avant
  d'exécuter. Réframe une intention mal posée plutôt que de la suivre aveuglément.
- **Un fix n'est pas fini sans son test** : stdlib (CI) pour la logique orchestrateur ; headless
  Blender / e2e hython (hors CI) pour ce qui exige un DCC.
