# CLAUDE.md — Pipeline Ylos Prod

## Contexte
Pipeline de production 3D/VFX freelance (marque **Ylos Prod**). Objectif : un système
de **gestion de production + pipeline d'assets**, scalable et améliorable, destiné à être
étendu dans le temps par des agents d'automatisation (n8n) et des plugins DCC (Houdini,
Blender). On construit depuis zéro **en anticipant ces consommateurs futurs**, pas
seulement le workflow humain immédiat.

## Branches
`main` est désormais la branche de travail unique (ex-`ui-pipeline`, promue le 2026-07-01 :
`ui-pipeline` et l'ancien `main` avaient des historiques Git sans ancêtre commun, donc la
promotion a été un `reset --hard` + force-push, pas un fast-forward). L'ancien addon Blender
standalone (v0.1.1 → v0.2.7, historique pré-monorepo) est archivé sous
`legacy/standalone-addon-v0.2.7`, conservé pour référence mais plus actif.

`origin/v0.4-monorepo` (rewrite monorepo orphelin, `ylos_core/`, tip `d6964a3` — cf. verdict
C1-C7 plus bas) a été renommée `legacy/v0.4-monorepo` le 2026-07-02 (push du nouveau nom +
suppression de l'ancien sur le remote, même tip) : ses correctifs utiles (C1, C3) sont
absorbés sur `main`, le reste est hors scope/non pertinent. Conservée pour référence, plus
active.

## Environnement
- **Machine** : MacBook Pro M2 Max, 64 Go, **macOS uniquement** (Apple Silicon).
  Toute dépendance native doit tourner sur arm64. Pas de solution Windows-only.
- **Stockage, 3 tiers** :
  - NVMe interne → **cache régénérable** (rapide, jetable)
  - NVMe externe Thunderbolt 4 → **source / projets actifs** (permanent)
  - Disques mécaniques → **archive froide uniquement** (I/O aléatoire inadapté à la 3D,
    jamais de projet actif)
- **Python** : **stdlib privilégiée** pour tout module qu'un DCC doit importer. Raison :
  les interpréteurs embarqués (hython, le Python de Blender) ne doivent pas dépendre d'un
  `pip install`. Une dépendance tierce dans le chemin d'import = friction.

## Principes d'architecture (verrouillés)
1. **Racine relocalisable** : variables d'environnement `$PROJ_ROOT`, `$PROJ_CACHE`.
   Jamais de chemin absolu en dur. Les scènes référencent via env → un projet se déplace
   entre interne et externe sans casser les références.
2. **Séparation cache / source** : interne rapide pour le régénérable, externe pour le
   permanent. Les deux tiers ne se mélangent pas.
3. **Source de vérité lisible par machine** : le manifeste `project.json` fait foi, pas
   les conventions de nommage de dossiers.
4. **Séparation des responsabilités** : la **gestion de production** (jobs, statut,
   deadlines) et le **pipeline technique d'assets** (arborescence, ingestion, versioning)
   sont deux problèmes distincts. Ne pas les fusionner dans l'archi.
5. **Logique unique** : `create_project.py` est importable par les plugins DCC. La logique
   de création vit à **un seul endroit**, jamais dupliquée entre outils.

## État actuel
`create_project.py` (stdlib, importable par les DCC) émet la structure **schéma 2.1.0**
(2.0.0 + `frame_range` optionnel des shots, additif — cf. « Schéma 2.1 » plus bas) via
deux sous-commandes / fonctions :
- `project` / `create()` → coquille asset-centric (`_pipeline/project.json`, `assets/`,
  `sets/`+`shots/` vides, `references/`, `resources/`, `delivery/`, `edit/`) + cache séparé
  sous `$PROJ_CACHE/<projet>`.
- `asset` / `create_asset()` → scaffolde une entité (asset/set/shot) : steps → dossiers
  (+`publish/`), `manifest.json`, stub `asset_root.usda` (convention USD).
- `.metadata_never_index` (anti-Spotlight) + `.gitignore` (cache, rendus, `*.usdc` hors Git).

Contrats figés : `project.schema.json`, `asset.schema.json`, `docs/usd-convention.md`,
`docs/migration-1.0-to-2.0.md`, `docs/migration-2.0-to-2.1.md`. Validé end-to-end (arbre +
JSON conformes aux schémas).

**Reste :** vérifier l'up-axis Blender↔USD à l'usage ; TODO
`validate_texture_paths_relative` (anti chemin absolu dans les textures USD) le jour où
un chemin de publish lookdev/texture existera. (Incrément 4 — nettoyage — fait le
2026-07-06 : `~/Desktop/create_project.py` mort et `files003.zip` supprimés.)
L'ex-Incrément 3 (migrer les projets existants) est **abandonné** — décision 2026-07-06 :
`YLOS__TEST` et `Pachamama` ne sont que des projets de test, pas de données à préserver ;
`migrate_to_2.0.py` reste disponible si un vrai projet legacy apparaît un jour.

**Workflow shot/Solaris Houdini — terminé (2026-07-08).** Plan `docs/plan-houdini-shots.md`,
incréments 0-7 tous implémentés et commités (numérotation propre au plan, sans rapport avec
les « Incréments » historiques ci-dessus) : bridge `ylos_houdini.py` + shelf (0), composeur
unifié `refresh_entity_root` (1), schéma 2.1 `frame_range` (2), HDA `ylos::publish::0.2` mode
step (3), outils shelf shot (4), convention cache + caches consommables (5), rendu Karma →
cache + delivery (6), validation e2e + sweep docs (7). Les sections dédiées ci-dessus font foi
pour chaque brique. Validation end-to-end : CI (`python3 -m unittest`, sans Houdini) verte ;
scénario complet manuel (hython) `tools/houdini/test_shot_workflow_e2e.py` (create shot → WIP
anim → publish anim/lighting via HDA → `shot_root.usda` recomposé, lighting plus fort → load
shot → rendu Karma cache → deliver), complémentaire de `test_publish_hda_e2e.py` (fidélité du
noeud HDA du TAB menu). **Hors scope resté hors scope** (cf. plan) : launcher per-session env,
cycle de vie des caches (TTL/sweep/quotas), Python Panel / asset browser, portage des variantes
Blender dans le composeur unique, tooling comp 2D, pages shot dans l'UI web, multi-séquences.

### Validation de nommage — point unique (2026-07-02)
`create_asset()` valide désormais le nom **à la création**, pour les trois familles
(asset/set/shot), via `validate_entity_name(name, entity_type, sub_type)` — même fonction
que l'alias historique `validate_publish_asset_name` (asset uniquement, contrat Houdini
inchangé). Convention `TYPE_Nom_Variant` : `ASSET_TYPES`/`SET_TYPES`/`SHOT_TYPES` dans
`create_project.py` — **seule source** : depuis 2026-07-06, `app.html` les récupère via
`GET /api/config` (`FAMILY_CONFIG` n'y est plus qu'un fallback hors-ligne, cf. section
UI plus bas). Message d'erreur toujours avec suggestion (nom capitalisé + `_Default`) et
liste des types valides — même message partout (web UI, Blender, CLI) car même fonction.
Couvre tous les entrants : web UI (`ylos_ui.py::_post_create_asset`), Blender
(`op_new_asset.py`, qui composait auparavant un nom PascalCase via un validateur local
dupliqué dans `core/asset.py` — supprimé, il passe maintenant par
`create_project.validate_entity_name`), CLI, futur.

### Migration 2.0 : renommage à la convention (2026-07-06)
La validation de nommage n'existant qu'à la création, une entité legacy la contourne…
jusqu'au premier publish LOP qui échoue (`validate_publish_asset_name`) — mur silencieux.
`migrate_to_2.0.py` renomme donc les entités à la convention `TYPE_Nom_Variant` : dossier +
stems des fichiers de publish + `manifest.name` (`asset_root.usda` et `manifest.publishes`
sont recomposés depuis le disque APRÈS renommage, donc auto-cohérents). Les `wip/` ne sont
jamais touchés : la détection de version Blender est agnostique au nom
(`core/asset.py::VERSION_PATTERN`), la continuité de versions est conservée telle quelle.
Type invalide pour la famille (ex `ENVIRONMENT`, absent d'`ASSET_TYPES`) → renommage
impossible : entité migrée telle quelle + warning actionnable (`--type-override NOM=TYPE`,
répétable, prioritaire sur le type legacy). Tests : `tests/test_migrate_to_2_0.py`
(chargé via `importlib`, le nom de fichier contient un point). Mode d'emploi (si un vrai
projet legacy doit migrer un jour — cf. « Reste » plus haut, les projets existants sont
des projets de test) : `--dry-run` d'abord, trancher les overrides signalés, appliquer.

### Contrat deux-phases généralisé (allocate/finalize, `kind`)
`allocate_publish_version()`/`finalize_publish_version()` ne sont plus spécifiques au
publish LOP Houdini : un paramètre `kind` sélectionne le sous-arbre
(`entity_dir/<kind>/{publish,.staging}/`) et la clé manifeste — `kind="lop"` (défaut, pour
compat Houdini) écrit dans `lop_publishes` (liste plate, clé `"layer"` inchangée) ; tout
autre `kind` (un nom de step, ex `"modeling"`) écrit dans `step_publishes[step]` (clé
`"artifact"`). `finalize_publish_version()` retrouve `kind` depuis la structure de
`final_dir` — signature inchangée, aucun appelant Houdini existant à toucher. Adopté par
tous les bridges Blender (`op_publish.py` USD, `op_export_glb.py` GLB) : plus aucun
publish en écriture directe, **thumbnail requis partout** (`_missing_artifacts` refuse le
commit si `thumb.png` manque, `staging_dir` reste intact pour audit/retry). L'ancien
`publish_asset()` (écriture directe, pas de thumbnail garanti) est déprécié
(`DeprecationWarning`), conservé, plus aucun appelant dans ce repo.

### Composition unifiée : `refresh_entity_root()` (2026-07-08, Incrément 1 shots)
Composeur **unique** du fichier root d'assemblage d'une entité (principe 5), dans
`create_project.py` — corrige une dérive : depuis la généralisation du contrat deux-phases,
`build_asset_root()` n'avait plus aucun appelant vivant (seul `publish_asset()` déprécié),
donc rien ne recomposait le root après un publish de step. `refresh_entity_root(project_root,
entity_name)` lit le manifeste (sous `acquire_lock`), collecte le latest publish `complete`
par step en **fusionnant** `step_publishes` (clé `artifact`, statut `complete`, prioritaire à
step égal) et `publishes` legacy (`_latest_by_step`), puis recompose : asset/set →
`ASSET_ROOT_NAME` (`defaultPrim=<Nom>`, ordre `DOWNSTREAM_ORDER`, via `build_asset_root`) ;
shot → `SHOT_ROOT_NAME` (root prim `/ROOT`, `defaultPrim="ROOT"`, ordre `SHOT_DOWNSTREAM_ORDER`
— **pas** `DOWNSTREAM_ORDER` : sur un shot le lighting override l'anim — + timecodes depuis
`frame_range` si présent, via `build_shot_root`). Les publishes **LOP** n'entrent jamais dans
la composition (`_latest_by_step` ne lit pas `lop_publishes` : un LOP est un instantané complet
hors taxonomie de steps). Écriture atomique, subLayers relatifs à l'entité (le root vit à sa
racine). Appelé automatiquement en fin de `finalize_publish_version()` **pour `kind != "lop"`
uniquement**, dans le même flock, depuis le manifeste déjà mis à jour — via le helper interne
`_compose_entity_root()` qui **ne re-verrouille pas** (`acquire_lock` ouvre un nouveau fd
bloquant à chaque appel : nesting = interblocage). `refresh_entity_root()` public prend le
flock ; les deux passent par `_compose_entity_root()`.

**Duplication Blender à résorber** (nommée, non traitée dans cet incrément — hors scope) :
`plugins/blender/core/usd_composer.py::compose_asset_root()` écrit un `asset_root.usd` (nom ≠
`ASSET_ROOT_NAME`, extension `.usd` bannie par la convention) avec sa propre logique de
variantes. La cible est ce composeur unique ; les **variantes** en seront une extension future
(le portage n'a pas été fait ici pour ne rien casser côté Blender). Convention shot figée dans
`docs/usd-convention.md` (section 6).

### Schéma 2.1 : `frame_range` du shot (2026-07-08, Incrément 2 shots)
`SCHEMA_VERSION = "2.1.0"` (partagée par les deux manifestes — `project.json` ET manifeste
d'entité ; le bump s'applique aux deux, changement additif nul côté projet). Additif :
`asset.schema.json` gagne une propriété **optionnelle** `frame_range`
(`{"start": int, "end": int, "fps": number}`, les trois requis si l'objet est présent).
`build_asset_manifest()` pose un défaut `{start: 1001, end: 1100, fps: DEFAULT_SCENE["fps"]}`
pour `entity_type="shot"` uniquement (assets/sets n'en ont pas). `set_frame_range(project_root,
shot_name, start, end, fps=None)` valide (`start < end`, entité = shot), écrit sous
`acquire_lock` + `_atomic_write_json`, puis recompose le `shot_root.usda` (timecodes) via
`refresh_entity_root` **hors flock** (`acquire_lock` non réentrant). `additionalProperties: true`
+ propriété optionnelle → aucun manifeste 2.0 invalidé, aucune migration de fichiers : un shot
créé en 2.0 sans `frame_range` reste valide, les consommateurs traitent l'absence (fallback
défaut, jamais de crash — cf. `refresh_entity_root` timecodes conditionnels, `render_shot`
fallback range du hip avec warning). Doc : `docs/migration-2.0-to-2.1.md`. Édition UI web hors
scope (CLI/`set_frame_range` pour l'instant).

### HDA `ylos::publish::0.2` — mode step (2026-07-08, Incrément 3 shots)
Le HDA (`tools/houdini/build_publish_hda.py`, source de vérité scriptée — le `.hdanc` est
régénéré, **jamais** édité en GUI) ne publie plus seulement en `kind="lop"`. Un paramètre
`publish_kind` (menu **dynamique** : `lop` + les steps de l'entité saisie, lus de son
`manifest.json` par `kind_menu_items()` via `create_project` — source unique, jamais de liste
codée en dur ; fallback statique `DEFAULT_SHOT_STEPS + DEFAULT_ASSET_STEPS` si le manifeste est
illisible) sélectionne le sous-arbre. `lop` = contrat historique inchangé (instantané complet,
`asset_type` requis + validé). Un nom de step → `allocate_publish_version(..., kind=<step>)`,
`asset_type` **ignoré** (nommage déjà garanti à la création), stem `f"{asset}_{step}_v{NNN}"`
(= `versioned_name`, identique lop/step), `expected_artifacts` inchangé (artefact + thumbnail
requis). Le publish de step alimente `step_publishes[step]` et déclenche la recomposition
`shot_root.usda`/`asset_root.usda` (finalize → `refresh_entity_root`, `kind != "lop"`). Le menu
délègue au module embarqué via `hdaModule()` (jamais de logique dupliquée dans la définition).
**Pas de cohabitation 0.1** : `build()` supprime le `.hdanc` existant avant de reconstruire (un
`save()` sur une librairie existante y *ajouterait* la définition). Les 5 gotchas LOP HDA
(`template_node=`, paramètres promus, `savestyle`, `soho_foreground`) restent intégralement
appliqués. e2e `tools/houdini/test_publish_hda_e2e.py::test_step_publish_shot` (hython, manuel,
**hors CI**). Régénération obligatoire après tout changement du build :
`hython tools/houdini/build_publish_hda.py`.

### Convention cache + caches consommables (2026-07-08, Incrément 5 shots)
Deux contrats géo distincts, jamais confondus (décision figée du plan) :
- **Caches scratch (jetables)** : `$PROJ_CACHE/<projet>/houdini/<entité>/<step>/<label>/`
  (tier régénérable, NVMe interne). Résolution unique dans
  `create_project.entity_cache_dir(project_root, entity_name, step, label)` (stdlib, `mkdir`
  parents, `label` validé par `_validate_segment`). Le bridge Houdini
  (`ylos_houdini.tool_setup_filecache()` — shelf « Setup File Cache ») pose sur le `basedir`
  du filecache SOP sélectionné l'**expression littérale** `$PROJ_CACHE/<projet>/houdini/
  <entité>/<step>/` (via `cache_dir_expression()`, pure, miroir cache d'`env_relative` :
  relocalisable, variable jamais résolue en dur). Versioning v1/v2 = celui **natif du
  filecache**, aucun manifeste (une donnée jetable n'a pas de trace versionnée). Contexte
  déduit du hip courant (`parse_wip_context`).
- **Caches consommables (FX publié pour le lighting / un autre DCC)** = contrat deux-phases
  `kind=<step>` dans la **source** (permanent), jamais dans le cache. `PUBLISH_ARTIFACT_EXTENSIONS`
  gagne `.vdb`, `.bgeo.sc` (suffixe **double**, résolu par concaténation `f"{stem}{ext}"` dans
  `_missing_artifacts`, jamais par split), `.abc`. Une **séquence** (sim multi-frames) peut être
  un **sous-dossier** de `staging_dir` : `_missing_artifacts` accepte un dossier non vide (la
  branche « nom exact = contient un point », ex `thumb.png`, reste **prioritaire** — jamais
  interprétée comme dossier), et `finalize_publish_version()` liste fichiers **et** dossiers
  (`p.is_file() or p.is_dir()`) sinon `artifact` resterait `None` ; l'entrée manifeste `artifact`
  pointe alors le dossier.
- **Un cache publié n'entre JAMAIS dans la composition** `shot_root`/`asset_root` (ce n'est pas
  un layer USD). `_latest_by_step()` filtre explicitement par `_is_usd_layer()` (extension dans
  `USD_LAYER_EXTENSIONS`) **avant** le `max` par version : un step avec un VDB plus récent mais
  un USD plus ancien compose quand même son latest USD. Idem pour un `.glb` (bridge Blender) :
  filtré, jamais empilé en subLayer.

### Rendu Karma → cache, delivery explicite (2026-07-08, Incrément 6 shots)
Les rendus de shot vivent dans le **tier cache régénérable**, jamais dans la source ; seul un
geste humain les promeut en livraison. Tout dans `ylos_houdini.py` (le rendu est un flux
Houdini, pas de la logique métier de `create_project`) :
- **Convention de sortie** : `$PROJ_CACHE/<projet>/render/<shot>/<step>/v<NNN>/<shot>_<step>_
  v<NNN>.$F4.exr`. Résolution disque via `render_dir()` (utilise `create_project.resolve_cache()`
  — logique unique de `$PROJ_CACHE`, comme `entity_cache_dir`) ; expression **littérale** posée
  sur `outputimage` du ROP via `render_output_expression()` (miroir de `cache_dir_expression` :
  `$PROJ_CACHE` non résolu + `$F4` = frame Houdini 4 chiffres, relocalisable).
- **Versioning par scan disque, aucun manifeste** : `list_render_versions()` /
  `next_render_version()` (max des `v<NNN>` + 1). Un rendu est régénérable **et** son suivi
  relève de la gestion de prod (principe 4) — hors pipeline technique, donc pas de trace
  versionnée (même raison que les caches scratch de l'Incrément 5).
- `render_shot()` (action hou) : `usdrender_rop` dans `/stage`, entrée = display node courant,
  `trange=1` + f1/f2 depuis `frame_range` du manifeste (fallback range du hip **avec warning**),
  caméra = premier prim `Camera` sous `/ROOT/cameras/` (convention shot, `docs/usd-convention.md`)
  sauf argument explicite. Le parm caméra du `usdrender_rop` est **`override_camera`** (vérifié
  par énumération hython — `node.parm("camera")` est `None` ; même parm que le build du HDA sur
  ce type de node) : warning explicite si introuvable, jamais de garde silencieuse. **`soho_
  foreground=1`** posé (gotcha 5 : `node.render()` en GUI rend la main à la soumission de husk,
  pas à la fin). Shelf « Render Shot » (`tool_render_shot`) configure puis propose de lancer.
- `deliver_render(project_root, shot, step, version)` : **seul** chemin qui écrit dans
  `delivery/` — copie explicite (`shutil.copytree`) de `v<NNN>` (cache) vers
  `delivery/render/<shot>/<step>/v<NNN>/`. Le `<step>` est dans le chemin : deux steps livrés à
  la même version ne fusionnent pas (`dirs_exist_ok=True` les écraserait sinon). Refuse si la
  source est absente **ou vide** (rien à livrer).
  Pas de manifeste. Shelf « Deliver Render » (`tool_deliver_render`). Note Apprentice : rendus
  watermarkés — plomberie validable, pas livrable en l'état.
- Tests CI (sans hou) : `tests/test_ylos_houdini.py::RenderCacheTestCase` /
  `RenderOutputExpressionTestCase` (scan version, copie/refus delivery, expression littérale).
  L'e2e rendu réel reste manuel (hython, Incrément 7).

### Thumbnail Blender headless
`plugins/blender/core/thumbnails.py::render_publish_thumbnail()` — scène/caméra/world
temporaires, rendu EEVEE réel (256×256, cadrage trois-quarts auto sur la bbox), purgés en
`try/finally` strict. **Jamais `bpy.ops.render.opengl`** (exige un contexte fenêtré, casse
le headless). Distinct de `generate_thumbnail()` (preview WIP viewport, usage différent,
conservée telle quelle).

### Écritures atomiques + rechargement addon propre
`create_project.py::_atomic_write_text()`/`_atomic_write_json()` (motif `tmp` +
`os.replace()`, à côté de `acquire_lock`) : tous les écrivains de `project.json`,
`manifest.json`, `asset_root.usda` sont passés dessus — protège contre un fichier
tronqué si le process crashe mi-écriture (`acquire_lock` protège la concurrence
inter-process, pas un crash ; les deux sont complémentaires). Côté addon,
`plugins/blender/__init__.py` purge `create_project` de `sys.modules` au `register()` et à
l'`unregister()`, pour qu'un disable → edit → enable dans la même session Blender recharge
le vrai fichier plutôt qu'une version en cache.

### Vocabulaire pipeline centralisé (Blender) — `core/vocab.py` (2026-07-13)
Le vocabulaire pipeline (asset/set/shot types, steps, prod types, context types) était
**dupliqué en dur** dans les `EnumProperty` de l'addon (steps ×4 : `core/project.py`
`ylos_current_step`, `op_switch_context` `new_step`, `op_publish` `step`, `op_save_wip`
`step` ; types ×2 ; prod_type ×2 dont un enum FILM/AR/VR qui **crashait** en lisant un
`project.json` réel à `prod_type` hors liste — ex `Pachamama` = `XR`). Résorbé :
- **Source des VALEURS = `create_project.py`** (l'orchestrateur possède identité ET
  vocabulaire, principe 5) : `ASSET_TYPES`/`SET_TYPES`/`SHOT_TYPES`, `DEFAULT_*_STEPS`,
  **`PROD_TYPES`** (nouveau — union rétro-compatible FILM/SERIES/GAME/XR/AR/VR, jamais rien
  retirer : un `project.json` existant doit rester lisible). Les **context types** sont
  **dérivés d'`ENTITY_DIR`** (asset/set/shot upper) — pas de constante redondante.
- **`plugins/blender/core/vocab.py`** : SEUL home des `EnumProperty` items de l'addon.
  `PRESENTATION` = `{domaine: {valeur: (label, description)}}`, SEUL endroit où vivent les
  libellés humains (fallback `(valeur.replace("_"," ").title(), "")`). Items construits
  **une fois à l'import** en tuples module-level : `ASSET_TYPE_ITEMS`, `SET_TYPE_ITEMS`,
  `SHOT_TYPE_ITEMS`, `PROD_TYPE_ITEMS`, `CONTEXT_TYPE_ITEMS`, `STEP_ITEMS`
  (`{"ASSET"/"SET"/"SHOT": (...)}`), `STEP_ITEMS_ALL` (union ordonnée sans doublons).
  Auto-amorce `sys.path` (pattern `os.path.realpath`, 4 remontées) car les corps de classe
  d'opérateurs sont évalués à l'**import** de l'addon, avant `register()`.
- **Piège GC/bpy** (déjà documenté) : un callback `items=` ne doit JAMAIS retourner un
  tuple construit à la volée. Ici tous les `*_ITEMS` sont module-level ; **tous** les
  call-sites utilisent le mode statique `items=vocab.X_ITEMS`. Le mode callback (b)
  (`return STEP_ITEMS[ctx]`) reste autorisé mais **n'est utilisé nulle part** : chaque enum
  step round-trip avec la propriété Scene context-agnostique `ylos_current_step`
  (`STEP_ITEMS_ALL`) — la recopie croisée en `invoke`/`execute` casserait avec une liste
  filtrée par contexte. Le filtrage sémantique par famille reste assuré à l'exécution par
  `is_step_valid_for_context`.
- **Règle** : **plus jamais de `items=[…]` en dur dans `plugins/blender`** hors `vocab.py`
  et les enums d'**UI pure** (`ylos_popup_tab` dans `__init__.py` — onglets du popup, pas du
  vocabulaire pipeline). Garde : `grep -rn "items=\[" plugins/blender` → seulement
  `__init__.py` (`ylos_popup_tab`).
- **Résolu (2026-07-15, Incrément 2 de « Refonte UI Blender + web » plus bas)** : le résiduel
  ci-dessous a été traité — `ASSET_STEPS`/`SHOT_STEPS`/`SET_STEPS` sont maintenant dérivées de
  `vocab.STEP_ITEMS`, et les `BoolVectorProperty` à taille figée d'`op_new_asset` ont été
  remplacées par une `CollectionProperty` dynamique. Description originale du résiduel, conservée
  pour l'historique : `core/project.py` portait des LISTES de steps `SHOT_STEPS`/`SET_STEPS`
  **dérivées à la main** (et driftées vs `DEFAULT_*_STEPS`) consommées par les
  `BoolVectorProperty` (taille codée en dur) d'`op_new_asset` et par
  `is_step_valid_for_context`/`get_asset_step_status`.
- Test headless : `tools/blender/test_vocab_sync.py` (`"$BLENDER" --background --python …`)
  asserte `*_ITEMS == constantes create_project` (valeurs ET ordre), active l'addon sans
  exception, et vérifie que `scene.ylos_prod_type = "XR"` ne lève plus.

### `resolve_open_target()` — quel fichier ouvrir pour une entité+step
`create_project.py::resolve_open_target(entity_name, dcc="blender", step=None,
project_root=None) -> dict`. Logique dans l'orchestrateur (réutilisable Houdini), l'addon
consomme. **Ne lève JAMAIS** pour un cas métier : renvoie
`{"path": str|None, "kind": "wip"|"scene_default"|"publish"|None, "step": str|None,
"exists": bool, "reason": str (si exists=False)}`. `project_root=None` → projet actif
(`read_active_project()`). `step=None` → premier step déclaré du manifeste (peut rester
`None` sur manifeste corrompu → branches WIP/publish sautées, `scene_default` reste
résoluble). Ordre `blender` : (1) dernier WIP `<step>/wip/<name>_<step>_vNNN.blend` →
`wip` ; (2) root d'assemblage de l'entité (`shot_root.usda`/`asset_root.usda`, step-agnostique,
référence déjà les latest publishes en subLayers) → `scene_default` ; (3) dernier publish USD
`complete` du step **par chemin niché correct** (`step_publishes[step]` cle `artifact`,
`entity_dir/<step>/publish/<versioned_name>/<file>`) → `publish` ; (4) échec `exists=False`.
**Diagnostic du bug corrigé** : l'addon (`core/asset.py::list_publish_versions`,
`op_load_publish`) scannait `.../<step>/publish/` pour des fichiers `.usd*` **à plat**, alors
que le contrat deux-phases écrit un **dossier par version** (`publish/<name>_<step>_vNNN/…`) —
`f.suffix` d'un dossier vaut `""`, donc un publish deux-phases était **invisible** (aucun /
mauvais fichier). `resolve_open_target` lit le manifeste (chemin niché), jamais un scan plat.
`op_open_context` : (a) `prod_type` lu du manifeste passe par `_set_enum_safe` (valeur hors
enum → `WARNING` + fallback, **plus jamais d'exception**) ; (b) option `dry_run` (BoolProperty)
qui `print`/report le chemin résolu sans ouvrir. Tests : `tests/test_resolve_open_target.py`
(stdlib, CI) — manifeste synthétique avec `prod_type="ZZ_UNKNOWN"`, WIP/scene_default/publish,
entité/projet absents, manifeste corrompu : tous résolvent proprement sans lever.

### Lecture des publishes — API publique de l'orchestrateur (2026-07-14, CC#1c)
`create_project.py` porte la **logique UNIQUE de lecture** des publishes ; les consommateurs
(addon Blender, `ylos_ui`) sont des adaptateurs minces. Ne lèvent JAMAIS pour un cas métier.
- `list_publishes(project_root, entity_name, step, entity_type="asset") -> list[dict]` :
  **manifest-first** (`step_publishes[step]`, chaque entrée copiée + enrichie `abs_path`/
  `exists`, `legacy=False`) **fusionné** avec un **fallback fichiers plats legacy** (scan
  disque de `<step>/publish/` pour les `_vNNN.<ext>` USD — un dossier deux-phases a
  `is_file()` False, jamais capté ; entrées `legacy=True`). Dédup par numéro de version, le
  deux-phases prime. Trié par version croissante.
- `latest_publish_artifact(project_root, entity_name, step, entity_type="asset") -> dict|None` :
  entrée `complete` de version max (deux-phases + legacy), enrichie `abs_path`/`exists`.
  Généralisation disque-aware de `_latest_step_publish_rel()` (qui, lui, opère sur un
  manifeste déjà en mémoire et filtre aux seuls layers USD, pour la composition/ouverture).
- **Adaptateurs** `plugins/blender/core/asset.py` (signatures conservées) : `list_publish_versions`
  (USD only, forme `{version, variant, filename, path}`), `get_latest_publish_path` (dernier USD),
  `get_latest_publish_version` (via `latest_publish_artifact`, version `complete` max — tout type
  d'artefact). Le **scan à plat local a disparu** (c'était la cause du « No published USD found »
  du Load Latest et de l'estimation de version faussée du dialog publish : un publish deux-phases
  en DOSSIER était invisible).
- `ylos_ui.py::_last_versions(project_root, entity_name, manifest)` passe par
  `latest_publish_artifact` (plus de résolution dupliquée — le « logique unique, jamais
  dupliquée » en tête de module devient vrai). Contrat de sortie vers `app.html` inchangé.
- `_post_open_blender` : **délègue au launcher versionné** (cf. « Lancement DCC » ci-dessous) —
  plus de `--python-expr`. Construit `[BLENDER_APP, "--python", <launcher>, "--", "--project",
  …, ("--entity"), ("--step"), ("--path")]` pour **tous** les cas (.blend inclus, uniformité du
  contexte). `BLENDER_APP` surchargeable par **`$YLOS_BLENDER`** ; binaire (ou launcher) introuvable
  → **réponse HTTP d'erreur explicite**, jamais de no-op silencieux (leçon CC#1b). Body : `path`
  **ou** `entity` requis (au moins un fichier à résoudre), `project` → projet actif à défaut, `step`
  optionnel. Réponse JSON : `argv` lancé. Sortie du Popen → `~/.ylos/launch-server.log` (append,
  fini `DEVNULL`).
  > **Caduc (2026-07-15)** : accepter `path` depuis le client était exactement la cause du bug
  > corrigé à l'Incrément 3 (cf. « Refonte UI Blender + web » plus bas) — le contrat actuel
  > n'accepte plus que `{entity, step?}` ou `{entity, step, version}`, jamais de chemin.
- `finalize_publish_version` renseigne désormais `entry["thumbnail"]` (même chemin relatif entité
  que `entry["thumb"]`, conservé pour compat) quand `thumb.png` existe dans le dossier finalisé.
- Tests stdlib (CI) : `tests/test_create_project.py::TestListPublishes` (fusion deux-phases +
  legacy, dédup, `complete` max, `pending` exclu, entité absente) et `::TestFinalizeThumbnailField`.

### Lancement DCC contextualisé — launcher versionné (2026-07-14, CC#1d)
**Règle : TOUT lancement DCC passe par un launcher versionné. Plus jamais de `--python-expr`
inline.** Diagnostic : `_post_open_blender` lançait `--python-expr "usd_import(...)"` (stdout/stderr
→ `DEVNULL`). La commande est valide **headless** (import ~8 ms) mais en **GUI** l'op s'exécute
pendant le boot (contexte pas prêt) et échoue **en silence** → instance vide. Et par design aucune
instance ne recevait le contexte pipeline (projet/entité/step).
- **`tools/blender/launch_context.py`** (source unique de l'ouverture Blender). CLI, args **après
  `--`** : `--project <root>` (**requis**) `[--entity <name>] [--step <step>] [--path <file>]
  [--kind wip|publish|scene_default]`. Repo localisé par `os.path.realpath(__file__)` + remontée
  parents (pattern module), `sys.path` amorcé (repo + `plugins`), `import create_project`.
- **`--path` absent → `create_project.resolve_open_target(entity, "blender", step,
  project_root=project)`** (logique unique, réutilisable Houdini ; ne lève jamais).
- **GUI (`bpy.app.background` False)** : les ops ne s'exécutent **JAMAIS** au parse time —
  `bpy.app.timers.register(cb, first_interval=0.2)` (contexte prêt au 1er tick, callback retourne
  `None`, aucune exception n'en sort). **Background (`--background`)** : exécution immédiate +
  `sys.exit(code)` (CI/tests).
- **Ordre d'ouverture imposé** : `.blend` → `wm.open_mainfile` **d'abord** (l'open remplace la
  scène) puis contexte ; **USD** → contexte **d'abord** puis `wm.usd_import` (merge dans la scène
  contextualisée). Contexte = `bpy.ops.ylos.open_context('EXEC_DEFAULT', directory=project)` +
  set **gardé** de `ylos_current_asset` / `ylos_current_step` / `ylos_context_type` (famille du
  manifeste) / `ylos_asset_type` (préfixe `TYPE_` du nom si valide) — même pattern `_set_enum_safe`
  que `op_open_context` (valeur hors enum → warning loggé, **jamais d'exception**). L'addon est
  auto-enregistré s'il ne l'est pas (cas CI `--factory-startup`).
- **Logs** : `~/.ylos/launch.log` (launcher : timestamp, argv, chaque étape succès/échec +
  traceback complet ; surchargeable par **`$YLOS_LAUNCH_LOG`** pour l'isolation des tests) ;
  `~/.ylos/launch-server.log` (stdout/stderr du Popen côté `ylos_ui`). Marqueur de succès :
  `LAUNCH SUCCESS: [<mode>] <path> objects=<N>` (l'`objects=N` est le self-check de peuplement de
  la scène avant sortie).
- Test e2e : `tools/blender/test_launch_context.py` (python3 ordinaire — monte la fixture via
  `create_project`, puis **subprocess Blender réel** `--background --python launcher -- …`) :
  invocation A (`--path` publish niché contenant un cube `.usda` écrit à la main) et B (sans
  `--path`, résolution `scene_default`) → exit 0, `LAUNCH SUCCESS`, `objects>0`, trace de
  résolution et de contexte dans le log. **Hors CI stdlib** (exige Blender), comme les autres
  `tools/blender/test_*`.

### Publish propre : noms préservés + format d'artifact par cible (2026-07-14, CC#2)
**Deux volets, même zone (`op_publish` + orchestrateur).**

**A. Hygiène des noms au publish (round-trip sans perte).** Un objet renommé mais dont le
datablock garde `Cube.001` sortait en prim USD `Cube_001` / node glTF erroné → un Load Latest
ramenait un objet ne portant plus le nom de l'asset. `op_publish::_normalize_datablock_names`
(appelé **au gather, AVANT export**) : pour chaque objet exporté, si `obj.data` mono-user
(`data.users == 1`) et `data.name != obj.name` → `data.name = obj.name` (rename permanent,
hygiène standard). Datablock **multi-user → jamais renommé** (affecterait les autres users) →
collecté et **warné** (console + compteur). Full-scene → normalise toute la scène exportée. Le
résultat du publish rapporte toujours `N datablocks renommés, M partagés non touchés (voir
console)` — jamais silencieux. Les publishes déjà bakés (vN sales) restent tels quels ; c'est le
**prochain** publish qui sort propre.

**B. Format d'artifact par cible — décision d'ORCHESTRATEUR, jamais du DCC (principe 5).**
- `create_project.PROD_TYPE_TO_TARGET` (**source unique**) : `{XR, AR, VR, GAME → "web"; FILM,
  SERIES → "offline"}`. `create()` écrit `pipeline_target` dans `project.json` (dérivé du
  prod_type, `build_manifest`). `get_pipeline_target(project_root) -> "web"|"offline"` : lecture
  **tolérante** (jamais d'exception métier) — champ `pipeline_target` s'il est valide, sinon
  dérivé du prod_type, défaut `"offline"` (projet 2.0 sans le champ / manifeste illisible dégrade
  proprement ; un champ explicite prime la dérivation → override possible).
- `op_publish` : le format **découle de la cible** (`get_pipeline_target`) — `"web"` → `.glb` via
  `bpy.ops.export_scene.gltf(export_format='GLB', use_selection=<objets gather>, export_apply=True)`
  (+Y up par défaut de l'exporter = correct pour Three.js ; `_glb_export`, miroir de `_usd_export`) ;
  `"offline"` → `.usd` comme avant. Le **dialog** affiche le format cible : `…_vNNN.<ext> (<target>)`.
  **Staging / finalize / manifest / thumbnail inchangés** — le contrat deux-phases est agnostique à
  l'extension (`expected_artifacts=[stem, "thumb.png"]`, stem sans ext ; `.glb ∈
  PUBLISH_ARTIFACT_EXTENSIONS`). `load_after` route `import_scene.gltf` pour un `.glb`.
- **Pas de double artifact** (usd+glb) : un publish émet UN artefact selon la cible (le double
  reste réservé à une éventuelle passerelle Houdini). `op_export_glb` (bouton dédié du panel pour
  target web) et le routage du panel restent **inchangés** (hors scope UI) ; `op_publish` est
  désormais format-aware quel que soit le point d'entrée.
- **Tests** : `tools/blender/test_publish_glb_headless.py` (Blender headless — projet `XR`, publish
  d'un cube renommé → `.glb` non vide + entrée manifest `complete` + thumbnail + datablock réaligné ;
  **hors CI**) ; `tests/test_create_project.py::TestPipelineTarget` (stdlib CI — mapping complet,
  écriture par `create()`, dérivation legacy, override explicite, défauts tolérants). Le cas
  offline/`.usd` reste couvert par l'existant.

> **Caduc** : la phrase « `op_export_glb` (bouton dédié du panel pour target web) et le routage
> du panel restent inchangés » ci-dessus ne tient plus — `op_export_glb.py` est supprimé
> (Incrément 2 ci-dessous, code mort qui crashait à l'usage). `op_publish` reste l'unique point
> d'entrée format-aware, comme décrit.

### Refonte UI Blender + web : panel unifié, résolution serveur, import states — terminé (2026-07-15)
Cinq incréments (numérotation propre à ce chantier, sans rapport avec les « Incréments »
historiques ni les Incréments shots) : menu top bar (1), refonte N-panel + purge de dette (2),
fix résolution serveur web + verbes Ouvrir/Importer (3), Save Version + commentaires sidecar (4),
import states (5). Validation : suite stdlib CI (139 tests), 9 scripts headless Blender
(`tools/blender/test_*.py`), launcher e2e à 3 invocations — tous verts ; Incréments 3 et 4
vérifiés en direct dans le navigateur/Blender sur le projet réel `Ylos__Test`.

**Incrément 1 — Menu top bar « Ylos ».** `plugins/blender/ui/menu.py` : pull-down accroché à
`TOPBAR_MT_editor_menus` (pattern Prism), zéro logique dans `draw()` — réutilise `ylos.save_wip`,
`wm.save_as_mainfile` (natif Blender, pas d'équivalent pipeline), `ylos.open_context`,
`ylos.publish`. Trois petits opérateurs sans équivalent existant : `ylos.open_project_browser`
(`webbrowser.open` vers le serveur web local), `ylos.reload_pipeline` (disable/enable — la purge
`sys.modules` de `create_project` est déjà gérée par `register()`/`unregister()`, cf. section
dédiée plus haut), `ylos.about` (version addon + `git rev-parse --short HEAD`, tolérant). Test :
`tools/blender/test_menu_headless.py`.

**Incrément 2 — N-panel unifié + purge de dette.** `plugins/blender/ui/panel.py` remplace
`panel_pipeline.py` (4 panels empilés Project/Asset Context/Scene Settings/Tools) par 4 sections
Context/Scenefile/Publish/Imports — Scene Settings (redondant avec les Properties natives) et
Tools/migration (ex-Incrément 3 abandonné) ne sont pas reconduits (`ylos.convert_legacy` reste
enregistré, accessible via F3, sans bouton dédié). Purges :
- `core/project.py` : `ASSET_STEPS`/`SHOT_STEPS`/`SET_STEPS` dérivées de `vocab.STEP_ITEMS` (donc
  `create_project.DEFAULT_*_STEPS`) au lieu de listes recopiées à la main — **résout le résiduel
  connu documenté dans la section « Vocabulaire pipeline centralisé » ci-dessus** (`SHOT_STEPS`
  avait `layout`/`render`/`composite`, absents du vocabulaire réel ; `SET_STEPS` avait `modeling`
  au lieu de `layout`).
- `op_new_asset.py` : les 3 `BoolVectorProperty` à taille figée (Shot=6, drifté vs les 4 steps
  réels — aurait cassé l'import de l'addon au prochain changement de vocabulaire) remplacées par
  une `CollectionProperty` (`YLOS_PG_StepToggle`) reconstruite depuis `vocab.STEP_ITEMS[ctx]` à
  chaque changement de `context_type` — ne peut plus driver, par construction.
- `op_export_glb.py` **supprimé** (`export_selected=` inexistant sur l'exporter gltf actuel,
  crash garanti à l'usage) — `op_publish.py` couvre déjà le besoin, format-aware via
  `get_pipeline_target` (cf. CC#2 ci-dessus).
Résiduel connu — **résolu le 2026-07-16** (cf. « State Manager Blender » plus bas) : le popup
header (`op_popup.py`, `bpy.ops.ylos.open_popup`) portait ses propres onglets PIPELINE/ASSETS/SCENE,
dupliquant le N-panel ET un `_STEP_MAP` codé en dur périmé (steps shot bidon). Il a été **retiré**
(fichier supprimé), son contenu subsumé par le State Manager + une section Scene Check.

**Incrément 3 — Web UI : résolution 100% serveur + verbes Ouvrir/Importer.** Bug réel reproduit
sur le projet `Ylos__Test`/`PROP_Tente_Default` (log `launch.log` observé avant fix) :
`app.html` construisait `fullPath = projPath + '/' + last_versions[step]`, mais le chemin de
publish est relatif à l'**entité**, pas au projet — la concaténation naïve sautait le segment
`assets/<entité>` (`.../Ylos__Test/modeling/publish/...` au lieu de
`.../Ylos__Test/assets/PROP_Tente_Default/modeling/publish/...`).
- Le client n'envoie plus jamais de chemin. `POST /api/open-blender` accepte `{entity, step?}`
  (« Ouvrir la scène », résolu via `create_project.resolve_open_target`, WIP-first) ou
  `{entity, step, version}` (« Importer » une version **exacte**, résolu via `list_publishes`) —
  **remplace le contrat décrit dans la section CC#1c plus haut** (qui acceptait encore
  `path`/`entity`, cause du bug). `_build_launch_argv()` (fonction pure, testée stdlib) construit
  l'argv à partir d'un chemin déjà résolu, jamais de reconstruction manuelle ni côté client ni
  côté serveur.
- `launch_context.py` : routage par extension étendu aux `.glb`/`.gltf` (`import_scene.gltf`,
  même ordre contexte-puis-import que l'USD) — sans ce fix, Importer un GLB tentait un
  `open_mainfile` et échouait.
- `app.html` : `last_versions` expose désormais `{version, ext, thumb}` (jamais un chemin) ;
  boutons renommés « Ouvrir la scène » / « Importer (nouvelle session) », vignette
  (`manifest.thumbnail`) + badge d'extension par ligne, badge cible (web/offline) dans le pill
  projet (`get_pipeline_target` exposé sur `/api/project`).
- Tests : stdlib sur `_build_launch_argv` + résolution serveur (`subprocess.Popen` mocké, jamais
  de Blender lancé en test) ; `tools/blender/test_launch_context.py` étendu d'une invocation C
  (publish GLB réel + import via le launcher). Vérifié en direct sur `Ylos__Test` : chemins
  canoniques (segment `assets/<entité>` présent) confirmés dans `launch.log` pour les deux
  verbes.

**Incrément 4 — Save Version + commentaires (sidecar JSON).** `op_save_wip.py` : `StringProperty
comment` (pré-rempli depuis `scene.ylos_wip_comment`, posé par le panel Scenefile), version-up
automatique inchangé (`get_latest_wip_version()+1`). Sidecar `<wip>.blend.json` `{comment, user,
date, blender_version}` écrit atomiquement (`create_project._atomic_write_json`) juste après le
save réussi — best-effort (un échec d'écriture du sidecar ne fait jamais perdre le `.blend` déjà
sauvé). La note est consommée (vidée) après chaque save, pas sticky pour la version suivante
(pattern Prism). `core/asset.py::list_wip_versions()` fusionne le sidecar quand présent, tolérant
si absent (WIP legacy pré-Incrément 4). `ylos_ui.py`/`app.html` : nouveau champ `scenefiles` sur
`/api/asset/<name>` (scan disque des WIP + sidecar par step, jamais de manifeste — un WIP n'est
pas une donnée versionnée du pipeline) + modal « Scenefiles » (bouton historique par carte)
listant version/commentaire/user/date. Test : `tools/blender/test_save_wip_comment_headless.py`
(deux Save Version successifs → v001 puis v002, sidecars distincts et non écrasés, forme exacte
vérifiée sur disque).

**Incrément 5 — Import states (State-Manager-lite).** `op_import_product.py` (nouveau) absorbe
`op_load_publish.py` (**supprimé** — USD-only, import à plat par chemin, aucun suivi) :
`ylos.import_product(entity, step, version)` résout via `core.asset.resolve_publish_entry`
(`version=0` = dernière, sinon **exactement** celle demandée), route par extension (même logique
que `launch_context.py`), crée une collection **taguée** (custom props
`ylos_import_entity`/`step`/`version`/`path`) sous le même parent que la création d'entité
(`core.project.resolve_parent_collection`). Identité stable `'<entity>_<step>'` : un second
import du même couple est refusé (→ Update Import), pour que les références externes (caméras,
animation) restent valides d'une version à l'autre. `op_update_imports.py` (nouveau) :
`ylos.check_updates` compare chaque collection taguée à `latest_publish_artifact` (cache
module-level, pattern `op_scene_check.py` — jamais de scan à chaque redraw du panel) ;
`ylos.update_import` v1 = remplacement pur (retire tout, réimporte à neuf dans la même
collection), **aucun** remap d'overrides (matériaux/contraintes/animation ajoutés manuellement),
signalé explicitement dans le rapport (N objets avant/après). `core/project.py` récupère les
helpers de hiérarchie de collections (déplacés depuis `op_new_asset.py`, partagés avec l'import ;
import paresseux de `core.asset` pour éviter un cycle) :
`get_or_create_collection`/`link_collection`/`resolve_parent_collection`/
`collection_target_label`/`set_active_collection`. Test :
`tools/blender/test_import_states_headless.py` (scénario exact : publier v1 → importer (props
vérifiées sur disque) → re-import refusé → publier v2 → `check_updates` détecte (current=1,
latest=2) → `update_import` remplace (1→2 objets) → update répété = no-op propre).

### State Manager Blender (façon Prism) — terminé (2026-07-16)
Suite du rapprochement UI/Prism (l'utilisateur est passé sous Blender 5.2 LTS, cf. section portage
plus haut). Un **vrai State Manager** remplace le publish mono-step + la dette du popup : des
**export states empilables** (persistés dans le `.blend`) et **un unique bouton Publish** qui les
exécute tous, comme le State Manager de Prism.
- **Cœur de publish unique** : `op_publish.py::publish_entity_step(context, project_path, entity,
  step, *, allow_full_scene, comment, load_after) -> dict` extrait de l'ancien `execute()` (contrat
  deux-phases inchangé : allocate → export USD/GLB selon `get_pipeline_target` → thumbnail requis →
  finalize). Ne lève JAMAIS pour un cas métier. Le bouton simple `ylos.publish` (dialog, step
  courant) ET le batch `ylos.publish_states` passent par lui — principe 5, plus aucune duplication.
  La **famille** de l'entité (asset/set/shot, pour `is_step_valid_for_context`) est résolue par le
  nouveau `create_project.resolve_entity(project_root, name)` — wrapper PUBLIC de `_find_asset_entity`
  (scan des familles, déjà utilisé par allocate/finalize) : un state ne stocke pas sa famille, et un
  bridge n8n/Houdini pourra réutiliser ce résolveur (principe 5, résolution d'entité dans
  l'orchestrateur). `resolve_entity` retourne `None` si absente/illisible, jamais d'exception.
- **Modèle de données** : `plugins/blender/core/states.py::YLOS_PG_ExportState`
  (`enabled/entity/step/allow_full_scene/comment` + `last_result`/`last_version` affichage seul).
  `Scene.ylos_export_states` (`CollectionProperty`) + `..._index` enregistrés dans
  `states.register_properties()` appelé **après** le loop `register_class` (`CollectionProperty(type=
  YLOS_PG_ExportState)` exige le PG déjà enregistré ; PG placé avant tout référent dans `_classes`,
  comme `YLOS_PG_StepToggle`). `step` = `vocab.STEP_ITEMS_ALL` (domaine complet, validé à
  l'exécution — même pattern qu'`op_publish`).
- **Opérateurs** `operators/op_state_manager.py` : `YLOS_UL_ExportStates` (UIList),
  `ylos.state_add_export` (pré-rempli asset/step courant), `state_remove_export`,
  `state_move_export` (UP/DOWN), **`ylos.publish_states`** (itère les states `enabled`, délègue
  chacun à `publish_entity_step`, un échec n'interrompt pas les suivants — staging préservé, détail
  console ; `CANCELLED` si aucun `ok`), `ylos.open_state_manager` (`invoke_popup` width 500 →
  fenêtre façon Prism).
- **Draw unique** `ui/state_manager.py::draw_state_manager(layout, context)` — export states
  (template_list + add/remove/move + réglages du state actif + bouton Publish) puis import states
  (collections taguées + `check_updates`/`update_import`, repris de l'ancien `YLOS_PT_Imports`).
  Monté en DEUX points sans copie : section N-panel `YLOS_PT_StateManager` (`ui/panel.py`) ET popup
  `YLOS_OT_OpenStateManager`. **Import paresseux** de `draw_state_manager` dans le `draw()` du popup
  (sinon cycle : `ui.state_manager` charge le package `operators`, qui charge `op_state_manager`,
  avant que `draw_state_manager` n'existe).
- **`ui/panel.py`** : sections `Context`(0) · `Assets`(1, `panel_asset_list`) · `Scenefile`(2) ·
  **`State Manager`**(3, subsume Publish+Imports) · **`Scene Check`**(4, `DEFAULT_CLOSED`, recueille
  le scene-checker de l'ex-onglet Scene du popup). Menu top bar : « State Manager… », « Quick Publish
  (current step)… », « Check Scene ». Bouton header repurposé sur `ylos.open_state_manager`. La prop
  `ylos_popup_tab` retirée. `bl_info` bumpé `(0,3,1)→(0,3,2)`.
- **`op_popup.py` supprimé** (dernier `_STEP_MAP` codé en dur + icône `ENVIRONMENT` périmée résorbés).
- **Tests** : `tests/test_create_project.py::TestResolveEntity` (stdlib CI — familles asset/set/shot,
  absente → None) ; `tools/blender/test_state_manager_headless.py` (add×2 → reorder → remove → **1
  Publish → 2 versions `complete` sur disque** → state désactivé sauté → aucun enabled = CANCELLED) ;
  `test_panel_draw_headless.py` étendu aux nouvelles sections. Validé sous 5.2 : CI stdlib (151) + 10
  headless + launcher e2e, tous verts.
- **Hors scope (choix utilisateur)** : Project Browser reste **web** (app.html = orchestrateur, choix
  « hybride ») ; states Playblast/Render non repris (le rendu Karma est côté Houdini) ; scope objet
  par state limité à convention+full-scene (raffinement futur) ; remap d'overrides sur Update Import
  inchangé (v1).

### Panel Import / Export à la demande (`ylos.open_io`) — terminé (2026-07-16)
Suite immédiate du State Manager. **Régression corrigée** : en consolidant l'ancien `YLOS_PT_Imports`
dans le State Manager, seule la liste « imports déjà en scène » avait été conservée — la liste
« Available publishes → import » (déclencher un import d'un nouveau product) était **perdue**, plus
aucun point d'entrée UI pour importer. Restauré + étendu au raw file I/O, dans un **popup ouvert à la
demande** (choix utilisateur « les deux » : products pipeline ET fichiers bruts).
- `operators/op_io.py` : `ylos.open_io` (popup `invoke_popup` width 460, draw délégué à
  `ui/io_panel.draw_io` en **import paresseux** anti-cycle) ; `ylos.refresh_products` +
  cache module-level `_product_cache` (`compute_products(project_path, ctx_type)` = latest publish
  `complete` par (entité, step) de la **famille de contexte courante** — cohérent avec
  `ylos.import_product` qui résout via ce même `ctx_type` ; calculé sur invoke/refresh, **jamais en
  draw**, pattern `op_scene_check`/`op_update_imports`) ; `ylos.raw_import`/`ylos.raw_export`
  (délèguent aux opérateurs natifs OBJ/USD/glTF/FBX : `filepath` vide → `INVOKE_DEFAULT` = file
  browser natif ; `filepath` fourni → `EXEC_DEFAULT` = usage programmatique/tests ; le résultat natif
  est propagé, un format indisponible → `CANCELLED` propre). Export = **sélection courante**
  (`export_selected_objects`/`selected_objects_only`/`use_selection`), hors versioning pipeline.
- `ui/io_panel.py::draw_io(layout, context)` : 3 blocs — **Import Product** (browser filtré par
  `Scene.ylos_io_search`, vignette `thumb.png` via `load_icon`, bouton Import → `ylos.import_product`),
  **Import File** et **Export Selection** (boutons OBJ/USD/glTF/FBX). `create_project.resolve_entity`
  fournit les steps de chaque entité au browser.
- Points d'entrée : menu top bar « Import / Export… », bouton **« Import… »** dans la section Import
  States du State Manager (restaure le point d'entrée perdu). `Scene.ylos_io_search` enregistré dans
  `core/project.register_properties`.
- Test : `tools/blender/test_io_headless.py` (browser vide → publish → browser peuplé + `draw_io` sans
  exception ; roundtrip **raw OBJ** export sélection → fichier non vide → import → objets en plus ;
  OBJ = core, aucun addon requis). Validé sous 5.2 : CI stdlib (151) + 11 headless + launcher, verts.

### Sync web (`sync_web_assets`)
`create_project.py::sync_web_assets(project_root, web_project_dir)` copie les GLB
**pinnés** (`project.json["web"]["pinned_assets"]`, jamais "latest") vers
`{web_project_dir}/public/assets/`, génère `assets.json` (sha256, écriture atomique), et
fait le ménage en miroir (vieilles versions d'assets connus retirées, tout fichier étranger
laissé intact). **Le projet web ne lit jamais la structure du pipeline, uniquement
`assets.json`.** Schéma `project.json["web"]` : `{target_dir, pinned_assets: {<asset>:
{step, version}}}` — le `step` est requis car un asset peut avoir des publishes GLB
indépendants par step.

**API de pinning dans l'orchestrateur (2026-07-15, INC-6).** La logique de pinning
(validation + écriture de `project.json["web"]`) vit désormais dans `create_project.py`
(principe 5 — **importable par un plugin DCC / n8n**, plus seulement par le serveur HTTP) :
`pin_web_asset(project_root, asset, step, version)` (valide via `list_publishes` qu'un publish
`complete` à artefact `.glb` existe pour (asset, step, version), refuse sinon avec la liste des
versions dispo), `unpin_web_asset(project_root, asset)` (idempotent, `was_pinned` dans le
retour), `set_web_target(project_root, target_dir)` — toutes **ne lèvent JAMAIS** pour un cas
métier (retour `{"ok": bool, ...}`). Unique writer `_update_web()` (read-modify-write sous
`acquire_lock`, écriture atomique via `write_manifest`) : plus aucune mutation de
`project.json["web"]` ailleurs. Le champ `target_dir` mémorise le `web_project_dir` cible
(consommé par `sync_web_assets` sans le repasser) — INC-6 le nommait `project_dir`, on conserve
`target_dir` déjà au contrat (renommer = migration inutile, lu par n8n + 2 plugins). Tests :
`tests/test_create_project.py::TestPinWebAsset` (pin valide/version inexistante/step USD/mauvais
type, unpin idempotent, set-target, cycle complet pin → sync).

Exposé côté `ylos_ui.py` (adaptateurs **minces** qui délèguent à l'orchestrateur, contrat HTTP
inchangé) : `POST /api/set-web-target`, `POST /api/sync-web`, `GET /api/web-pins` (pins courants
+ publishes GLB disponibles par entité, seuls les deux-phases `complete` à artefact `.glb` sont
pinnables), `POST /api/pin-asset` (un pin refusé → 400 avec la liste de ce qui existe : le pin
est un contrat consommé tel quel par `sync_web_assets`, un pin cassé n'y produirait qu'un
warning tardif), `POST /api/unpin-asset` (idempotent). L'ancien writer dupliqué
`ylos_ui._update_project_web()` a été **retiré** (la logique est passée dans `_update_web`).
UI : le modal "Sync Web" d'`app.html` liste les entités à publishes GLB avec un select
step/version par entité — pin/unpin immédiat au changement, plus d'édition manuelle de
`pinned_assets`. Un pin pointant vers un publish disparu s'affiche "(introuvable)"
plutôt que "non pinné".

### UI web : source unique de config + durcissements (2026-07-06)
- `GET /api/config` (`ylos_ui.py::_get_config`) : types (`ASSET_TYPES`/`SET_TYPES`/
  `SHOT_TYPES`, jamais surchargés — contrat de validation) + steps par famille (pipeline
  du projet actif si lisible, sinon défauts du module — même résolution que
  `create_project._project_steps`, donc le modal « nouvel asset » propose exactement ce
  que `create_asset()` fera). `app.html::loadConfig()` recharge au boot, au changement et
  à la création de projet ; son `FAMILY_CONFIG` n'est plus qu'un fallback hors-ligne.
- `app.html::BASE` n'est plus codé en dur sur `:8765` : `location.origin` quand la page
  est servie en http (suit `--port`), fallback 8765 sinon.
- `/thumb/` : `..` interdit sur **tout** le chemin, `asset_name` compris (avant :
  seulement le sous-chemin — `/thumb/../_pipeline/project.json` restait dans le projet
  grâce au containment mais servait des fichiers hors contrat thumb).
- `_post_set_web_target` : read-modify-write de `project.json` sous `acquire_lock`
  (serveur multi-thread + plugins DCC écrivent le même manifeste).
- `_load_recent`/`_push_recent` : pathlib + écriture atomique + excepts ciblés (alignés
  sur les conventions du module).
- Tests : `tests/test_ylos_ui.py` (garde d'origine, `/api/config`, traversal `/thumb/`).

### Sweep des allocations orphelines (`clean_stale_staging`)
`create_project.py::clean_stale_staging(project_root, dry_run=False)` — un `staging_dir`
(`entity_dir/<kind>/.staging/*`) ne survit sur disque QUE si `finalize_publish_version()`
n'a jamais été appelée (elle le consomme via `os.replace`). Distingue allocation abandonnée
(process mort) de publish en cours via le PID encodé dans le nom du dossier
(`<versioned_name>.staging-<pid>`, `os.kill(pid, 0)`) — ne touche jamais un staging dont le
process est vivant. Rapporte séparément (jamais de suppression, même hors dry-run) les
entrées manifeste `"status": "pending"` sans staging correspondant — investigation
manuelle, le manifeste n'est pas une donnée jetable comme `staging_dir`. CLI : `clean-staging
<projet> [--apply]` (dry-run par défaut, cohérent avec le reste du module — rien de
destructif sans confirmation explicite).

### Branche orpheline `v0.4-monorepo` (verdict archivé, 2026-07-02)
Rewrite monorepo complet (`ylos_core/`, historique disjoint de `main`) contenant un commit
"correctifs v0.3.1 (C1-C7)". Verdict : C1 (écritures atomiques) et C3 (purge
`sys.modules`) étaient de vrais trous côté `main` → absorbés ci-dessus. C2 (immutabilité
publish) déjà équivalent sur `main` (échec explicite plutôt qu'auto-retry). C4
(`step_owners`, multi-DCC), C6 (harness de build/vendoring) et C7
(`validate_texture_paths_relative`) sont hors scope actuel (fonctionnalités jamais
adoptées par `main`, pas des régressions). C5 (ASCII-only) non pertinent (convention
propre à l'autre branche, `main` autorise l'UTF-8/français dans les libellés UI).

## LOP HDA gotchas
Vérifié empiriquement pendant le build de `ylos::publish` (cf. `tools/houdini/
build_publish_hda.py`) :

1. Une instance de HDA verrouillée refuse l'écriture directe sur les paramètres de ses
   noeuds internes (`hou.PermissionError`). Contournement : promouvoir les paramètres au
   niveau du noeud HDA, les relier aux noeuds internes via `chs("../_param_name")` posé au
   build, jamais écrire directement sur un noeud interne depuis le callback.

2. `hou.HDADefinition.save()` sans l'argument `template_node=` ne remonte PAS l'état live du
   noeud source dans la définition sauvegardée — les expressions posées via `setExpression()`
   sont silencieusement perdues au rechargement. Toujours appeler
   `hda_def.save(otl_path, template_node=hda_node)`.

3. Sur un ROP USD, `savestyle='separate'` échoue (`hou.OperationFailed`) dès qu'un noeud
   amont produit un layer anonyme sans savepath explicite. La combinaison qui marche :
   `savestyle='flattenimplicitlayers'` + `errorsavingimplicitpaths=0`. Effet de bord : ce
   mode écrit aussi un petit layer racine de stitching sur le paramètre `lopoutput` — le
   rediriger vers un dossier temporaire jetable (`tempfile.mkdtemp`, nettoyé après le render)
   pour ne jamais le laisser polluer `staging_dir` ou le repo.

4. Extensions de fichier Apprentice (`.usdnc`/`.hdanc`) — toujours découvrir sur disque après
   écriture, jamais supposer `.usd`/`.hda` en dur (cf. `finalize_publish_version()`).

5. `node.render()` (Python, `hou.RopNode`) sur un `usdrender_rop` **ne bloque pas** en session
   GUI : husk est soumis en arrière-plan (intégré à la boucle d'évènements Qt), `render()` rend
   la main avant la fin réelle du rendu — non reproductible en `hython` headless (bloque
   naturellement, pas de boucle Qt). Bug réel observé : `thumb.png` écrit ~4s après que
   `finalize_publish_version()` ait déjà fait son `os.replace()`, atterrissant dans un
   `staging_dir` orphelin recréé par husk. Trouvé par énumération réelle de `node.parms()`
   (jamais par la doc) : le toggle **`soho_foreground`** ("Wait for Render to Complete", hérité
   de l'héritage Mantra du node, défaut `False`) force le blocage. Posé à `1` sur `thumb_rop`
   dans `build_publish_hda.py`. `publish_rop` (type `usd_rop`, pas `usdrender_rop`) n'a pas ce
   problème — sérialisation de layer in-process, pas de `husk` séparé.

6. `item_generator_script` d'un `ParmTemplate` de menu (menu dynamique) est évalué en mode
   **`eval`** : **UNE expression** qui doit *retourner* la liste plate `[valeur, label, ...]`,
   pas un bloc d'instructions — ni `return X` ni `menu = X` ne sont valides (**SyntaxError sur
   les deux**, vérifié empiriquement). D'où la délégation en une expression unique au module
   embarqué : `item_generator_script="kwargs['node'].hdaModule().kind_menu_items(kwargs['node'])"`
   (jamais de logique de menu dupliquée dans la définition — la source de vérité reste
   `kind_menu_items`, cf. `build_publish_hda.py`). Fixer aussi
   `item_generator_script_language=hou.scriptLanguage.Python`.

Filet de sécurité indépendant de ce fix : `finalize_publish_version()` exige un paramètre
`expected_artifacts` et refuse tout `os.replace()` si un artefact déclaré manque ou est vide
dans `staging_dir` (thumbnail **requis**, LOP et tout autre `kind` — cf. section "Contrat
deux-phases généralisé" plus bas) — protège même si un futur cas async imprévu réapparaît.

## Bugs empiriques Blender
Miroir des gotchas Houdini ci-dessus, vérifiés en live (à compléter à mesure) :

1. **Blender 5.x a retiré `BLENDER_EEVEE_NEXT`** — tout moteur de rendu doit être **probé par
   affectation, jamais codé en dur**. L'identifiant `BLENDER_EEVEE_NEXT` (valide en 4.2–4.4)
   n'existe plus en 5.x (enum : `BLENDER_EEVEE`, `BLENDER_WORKBENCH`, `CYCLES`) ; l'affecter
   lève `TypeError`. Symptôme : `render_publish_thumbnail()` avalait l'exception, retournait
   `""`, et `finalize_publish_version()` **rejetait le publish** (garde-fou correct mais cause
   opaque). Fix : `plugins/blender/core/thumbnails.py::_pick_render_engine(scene)` essaie
   `("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")` par affectation
   `try/except TypeError` (l'enum des moteurs est **dynamique** : la tentative d'affectation
   est le probe fiable, pas l'introspection RNA) et retourne l'identifiant retenu. La cause
   d'échec remonte à l'utilisateur via `thumbnails.LAST_ERROR` (module-level, posé dans le
   `except`, sans changer la convention de retour `""` ni la signature publique) — repris dans
   le `report({'WARNING'})` d'`op_publish`. Régression : `tools/blender/test_thumbnail_headless.py`
   (`--background`, exit code) : `_pick_render_engine` retourne un moteur affectable + cube →
   `thumb.png` non vide. `generate_thumbnail()` (preview WIP, `render.opengl` viewport) est un
   chemin distinct, non concerné.
   - **2e site du même bug — `core/project.py::apply_scene_preset` (portage 5.2, 2026-07-15).**
     Les presets AR/VR de `SCENE_PRESETS` portaient `"renderer": "BLENDER_EEVEE_NEXT"` posé **en
     dur** (`scene.render.engine = preset["renderer"]`) — non protégé, contrairement à
     `thumbnails.py`. Créer **ou** ouvrir un projet AR/VR (`op_new_project` / `op_open_context`,
     les deux appellent `apply_scene_preset`) faisait donc planter l'opérateur sous 5.2
     (`TypeError`). Fix : `_resolve_render_engine(scene, desired)` (même probe par affectation
     `try/except TypeError`, chaîne de repli `_EEVEE_FALLBACKS = (NEXT, EEVEE, WORKBENCH)`) —
     un moteur non-EEVEE (CYCLES pour FILM) est affecté tel quel, `prod_type` inconnu reste
     no-op. Régression : `tools/blender/test_scene_preset_headless.py` (AR/VR → engine
     affectable sans TypeError, FILM → CYCLES préservé, inconnu → no-op).

2. **Portage Blender 5.2.0 LTS validé end-to-end (2026-07-15).** Machine passée de ≤5.1 à
   **5.2.0 LTS** (Python 3.13). Seule cassure de code trouvée = le 2e site `BLENDER_EEVEE_NEXT`
   ci-dessus ; le reste de l'addon tourne inchangé (USD/glTF import-export, contrat deux-phases,
   composition, launcher). Validation : CI stdlib (147 tests) + **toute** la batterie headless
   `tools/blender/test_*.py` (10 scripts, dont le nouveau scene_preset) + launcher e2e 3
   invocations, tous verts sous 5.2. **Install** (même pattern que sous 5.1, réplicable pour toute
   version future) : symlink `~/Library/Application Support/Blender/5.2/scripts/addons/ylos_pipeline
   -> <repo>/plugins/blender` (l'addon calcule `_REPO_ROOT` via `os.path.realpath(__file__)`, donc
   un **symlink** — jamais une copie — résout vers le repo et garde `create_project` importable) +
   startup `scripts/startup/ylos_enable.py` (active l'addon au 1er lancement, `save_userpref`, garde
   `not in prefs`). `bl_info["version"]` bumpé `(0,3,0)→(0,3,1)`. Le launcher web (`ylos_ui.BLENDER_APP`)
   pointe `/Applications/Blender.app` par défaut = 5.2, surchargeable par `$YLOS_BLENDER`.

## Verrouillage (fcntl.flock)
`acquire_lock(path)` (`create_project.py`) est le **seul** point du module qui touche
`fcntl.flock` — verrou exclusif sur un fichier `.lock` sibling de `path`, utilisé par
`publish_asset()`, `allocate_publish_version()`, `finalize_publish_version()`. Contrainte
connue : `flock` est **advisory** (n'empêche rien si un process ignore le verrou), **non
fiable sur NFS/SMB** (sémantique de lock réseau inconsistante selon l'implémentation serveur),
**POSIX-only** (pas de portage Windows direct, `msvcrt.locking` a une API différente). Tout le
stockage actuel est local (cf. section Stockage 3 tiers) donc pas un problème aujourd'hui — mais
si un tier réseau ou un portage Windows devient réel, `acquire_lock()` est le seul endroit à
faire évoluer.

## Décisions tranchées (2026-06-14)
1. **Design cible : hybride.** Garder les principes verrouillés, absorber le modèle métier
   des projets réels (USD, `scene`, steps, manifeste par asset). Schéma `1.0.0` → `2.0.0`.
2. **Topologie : asset-centric.** L'asset est la colonne vertébrale (step-folders +
   `manifest.json` + `asset_root.usd`). `shots/` et `sets/` = scaffolding optionnel (créés
   vides), non first-class.
3. **Pas de bibliothèque transverse.** Chaque projet est autonome, réemploi par copie.
   Pas de `$ASSET_LIB`. (Doublon `Casa` entre projets assumé.)
4. **Cache : root interne séparé, par projet** (`$PROJ_CACHE/<projet>`). Le `cache/`
   co-localisé des projets réels est abandonné (corrige la violation du principe 2).

### Conventions tranchées
- Nom du dossier config : **`_pipeline/`** (≠ `_config/` du code committé).
- **Convention USD figée** (`subLayers` intra-asset, `.usda` compo / `.usdc` géo,
  `defaultPrim = /<NomAsset>`) : cf. `docs/usd-convention.md`. Reste à vérifier à l'Inc. 2 :
  l'**up-axis** (fichiers réels en Y-up alors que renderer = Cycles/Blender Z-up).

## Conventions
- Communication : **français**. Code, identifiants, noms de fichiers : **anglais**.
  (À inverser si le code existant est déjà en français.)
- `pathlib` partout, pas de concaténation manuelle de chemins.
- `project.json` est un **contrat**, pas juste un fichier : lu par le créateur, n8n et
  2 plugins. Tout changement de schéma casse des lecteurs → champ `schema_version`
  obligatoire + schéma documenté. Le faire évoluer = **migration**, pas édition silencieuse.

## Tensions connues (à garder en tête)
- **Collision d'env vars** : `$PROJ_ROOT` est global au shell. Si Houdini et Blender
  pointent sur deux projets différents en simultané, la variable globale entre en conflit.
  Piste : env posée **par-session** par le plugin/launcher, plutôt qu'un export global.
  À résoudre avant que le multi-projet simultané devienne réel.

## Mode de collaboration attendu
- **Lis avant d'écrire.** Avant toute mutation de fichier, mappe l'état réel du repo.
- **Plan avant action** pour toute opération destructive ou tout changement de schéma.
- Ne casse pas l'**importabilité** : pas d'import lourd au niveau module dans ce que les
  DCC chargent.
- **Challenge l'archi.** Si une décision a une implication downstream que je n'ai pas vue,
  nomme-la avant d'exécuter. Réframe une intention mal posée plutôt que de la suivre
  aveuglément.
