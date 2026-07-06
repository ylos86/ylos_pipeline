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
`create_project.py` (stdlib, importable par les DCC) émet la structure **schéma 2.0.0** via
deux sous-commandes / fonctions :
- `project` / `create()` → coquille asset-centric (`_pipeline/project.json`, `assets/`,
  `sets/`+`shots/` vides, `references/`, `resources/`, `delivery/`, `edit/`) + cache séparé
  sous `$PROJ_CACHE/<projet>`.
- `asset` / `create_asset()` → scaffolde une entité (asset/set/shot) : steps → dossiers
  (+`publish/`), `manifest.json`, stub `asset_root.usda` (convention USD).
- `.metadata_never_index` (anti-Spotlight) + `.gitignore` (cache, rendus, `*.usdc` hors Git).

Contrats figés : `project.schema.json`, `asset.schema.json`, `docs/usd-convention.md`,
`docs/migration-1.0-to-2.0.md`. Validé end-to-end (arbre + JSON conformes aux schémas).

**Reste :** vérifier l'up-axis Blender↔USD à l'usage ; TODO
`validate_texture_paths_relative` (anti chemin absolu dans les textures USD) le jour où
un chemin de publish lookdev/texture existera. (Incrément 4 — nettoyage — fait le
2026-07-06 : `~/Desktop/create_project.py` mort et `files003.zip` supprimés.)
L'ex-Incrément 3 (migrer les projets existants) est **abandonné** — décision 2026-07-06 :
`YLOS__TEST` et `Pachamama` ne sont que des projets de test, pas de données à préserver ;
`migrate_to_2.0.py` reste disponible si un vrai projet legacy apparaît un jour.

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

### Sync web (`sync_web_assets`)
`create_project.py::sync_web_assets(project_root, web_project_dir)` copie les GLB
**pinnés** (`project.json["web"]["pinned_assets"]`, jamais "latest") vers
`{web_project_dir}/public/assets/`, génère `assets.json` (sha256, écriture atomique), et
fait le ménage en miroir (vieilles versions d'assets connus retirées, tout fichier étranger
laissé intact). **Le projet web ne lit jamais la structure du pipeline, uniquement
`assets.json`.** Schéma `project.json["web"]` : `{target_dir, pinned_assets: {<asset>:
{step, version}}}` — le `step` est requis car un asset peut avoir des publishes GLB
indépendants par step. Exposé côté `ylos_ui.py` : `POST /api/set-web-target`,
`POST /api/sync-web` ; bouton "Sync Web" dans `app.html`.

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

Filet de sécurité indépendant de ce fix : `finalize_publish_version()` exige un paramètre
`expected_artifacts` et refuse tout `os.replace()` si un artefact déclaré manque ou est vide
dans `staging_dir` (thumbnail **requis**, LOP et tout autre `kind` — cf. section "Contrat
deux-phases généralisé" plus bas) — protège même si un futur cas async imprévu réapparaît.

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
