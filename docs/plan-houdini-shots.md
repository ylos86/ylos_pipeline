# Plan — Workflow shot / Solaris (Houdini)

> Document d'architecture destiné à l'implémentation (Opus). Rédigé le 2026-07-07 après
> audit du repo. Chaque incrément est autonome : specs, fichiers touchés, tests, critère
> de done. **Lire CLAUDE.md en entier avant de commencer** — principes verrouillés,
> gotchas LOP HDA, conventions. Rien dans ce plan ne les contredit ; quand un incrément
> étend un contrat, il le dit explicitement.

## Décisions figées (avec Sébastien, 2026-07-07)

| Sujet | Décision |
|---|---|
| Périmètre | Workflow shot/Solaris. Parité complète des outils asset, gestion complète des caches et launcher per-session : hors scope. |
| Contrat géo | Deux contrats séparés : USD/LOP pour les steps (assets ET shots), SOP `kind=step` pour les caches FX consommables (bgeo.sc/vdb/abc). |
| UI Houdini | Shelf + `hou.ui` uniquement. Fonctions pures séparées des dialogues (pattern `ylos_houdini.py` existant). Pas de Python Panel. |
| Licence | Apprentice pour l'instant. Découverte d'extensions partout (jamais `.usd`/`.hip` supposés), limite `.usdnc` documentée (chiffré, illisible hors Houdini — l'interop Blender/web des publishes Houdini est cassée tant qu'on est en Apprentice ; assumé). |
| Compo shot | `shot_root.usda` recomposé automatiquement à chaque publish de step — miroir de la convention asset, machine-readable (n8n/web sans ouvrir Houdini). |
| Rendus EXR | `$PROJ_CACHE/<projet>/render/` (tier régénérable). `delivery/` ne reçoit que des finals validés, copiés explicitement. |
| Frame range | Source de vérité dans `manifest.json` du shot. Schéma 2.0.0 → 2.1.0 (additif). |

## État des lieux (audit 2026-07-07)

**Existe, commité :** HDA `ylos::publish::0.1` (publish LOP deux-phases + thumbnail,
build scripté `tools/houdini/build_publish_hda.py`, e2e `test_publish_hda_e2e.py`),
package `ylos.json`.

**Existe, NON commité (prérequis — Incrément 0) :** `plugins/houdini/python/ylos_houdini.py`
(bridge : save WIP versionné, new asset, load asset_root, `latest_lop_publish()`,
`env_relative()`), shelf `ylos_pipeline.shelf` (3 outils), extension de `ylos.json`
(PYTHONPATH + TOOLBAR), `create_project.read_active_project()`. Le docstring référence
`tests/test_ylos_houdini.py` **qui n'existe pas**.

**Dérive architecturale constatée (fondation — Incrément 1) :** depuis la généralisation
du contrat deux-phases, `create_project.build_asset_root()` n'a plus aucun appelant vivant
(seul `publish_asset()` déprécié l'appelle) → **rien ne recompose `asset_root.usda` après
un publish de step**. En parallèle, `plugins/blender/core/usd_composer.py::compose_asset_root()`
écrit `asset_root.usd` (nom ≠ `ASSET_ROOT_NAME`) avec sa propre logique (variantes) —
duplication qui viole le principe 5 (logique unique). Conséquence concrète :
`ylos_houdini.reference_asset()` référence un stub jamais rafraîchi.

## Architecture cible — flux shot

```
asset publishes (kind=<step>)          shot step publishes (kind=<step>, HDA 0.2)
        │                                        │
        ▼                                        ▼
asset_root.usda (recomposé auto)       shot_root.usda (recomposé auto)
        │                                        │  subLayers ordonnés SHOT_DOWNSTREAM_ORDER
        │  référencé par les steps               │  + startTimeCode/endTimeCode (frame_range)
        └────────► hip de step de shot ◄─────────┘
                   (animation / fx / lighting)
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
   caches sims (jetables)      publish du step (USD via HDA,
   $PROJ_CACHE/<projet>/       ou cache consommable kind=step)
   houdini/<entité>/<step>/           │
                                      ▼
                          rendu Karma → $PROJ_CACHE/<projet>/render/
                                      │  (validation humaine)
                                      ▼
                            delivery/render/<shot>/ (copie explicite)
```

Principes appliqués : le shot est une entité comme une autre (manifeste, steps, publishes
deux-phases) ; la composition vit dans `create_project.py`, jamais dans un DCC ; tout
chemin écrit dans une scène passe par `$PROJ_ROOT`/`$PROJ_CACHE` (`env_relative`).

---

## Incrément 0 — Committer et tester le socle bridge (prérequis)

Le workflow shot s'appuie sur `ylos_houdini.py`. On ne construit pas sur du code hors Git.

- Écrire `tests/test_ylos_houdini.py` — fonctions pures uniquement, **sans hou ni licence
  Houdini** (contrainte CI, cf. docstring du module) : `hip_extension` (via
  `license_category` injecté), `parse_wip_context` (fixture tempfile avec
  `_pipeline/project.json` réel — chemin conforme accepté, chemin hors projet refusé),
  `list_wip_versions`/`next_wip_path` (extensions `.hip`/`.hiplc`/`.hipnc` mélangées,
  validation de step contre le manifeste), `env_relative` (avec/sans `$PROJ_ROOT` posé),
  `latest_lop_publish` (entrées `pending` ignorées), `list_entities`. Import du module par
  chemin (`plugins/houdini/python` n'est pas un package : `sys.path.insert` ou `importlib`,
  pattern de `tests/test_migrate_to_2_0.py`).
- Vérifier qu'importer `ylos_houdini` sans hou ne lève rien (aucun `import hou` top-level —
  c'est déjà le cas, le test le verrouille).
- Commit : bridge + shelf + `ylos.json` + `read_active_project()` + tests. CI verte.

**Done :** `python3 -m unittest` passe sans Houdini installé ; `git status` propre.

## Incrément 1 — Composition unifiée : `refresh_entity_root()`

Corrige la dérive constatée ET pose la fondation du shot_root. Un seul composeur, dans
`create_project.py`.

- Nouvelles constantes : `SHOT_ROOT_NAME = "shot_root.usda"` ;
  `SHOT_DOWNSTREAM_ORDER = ["comp", "lighting", "fx", "animation", "layout"]` (plus fort
  en premier — **ne pas réutiliser `DOWNSTREAM_ORDER`** : il place `animation` plus fort
  que `lighting`, correct pour un asset, faux pour un shot où le lighting override l'anim).
  `comp` déclaré pour l'ordre mais ne produira pas de layer USD (2D) — simplement jamais
  présent dans les publishes.
- `refresh_entity_root(project_root, entity_name)` : lit le manifeste (sous
  `acquire_lock`), collecte le latest publish `complete` par step depuis
  `step_publishes` (clé `artifact`) **et** `publishes` legacy (chemins relatifs — même
  fusion que `_latest_from_publishes`), recompose :
  - asset/set → `ASSET_ROOT_NAME`, `defaultPrim = <Nom>`, ordre `DOWNSTREAM_ORDER`
    (réutilise `build_asset_root`) ;
  - shot → `SHOT_ROOT_NAME`, root prim `/ROOT` (`USD_ROOT_PRIM`), `defaultPrim = "ROOT"`,
    ordre `SHOT_DOWNSTREAM_ORDER`, et si `frame_range` présent au manifeste (Incrément 2) :
    `startTimeCode`/`endTimeCode`/`timeCodesPerSecond` dans le header de stage.
  - Écriture via `_atomic_write_text`. Chemins de subLayers relatifs à l'entité (comme
    aujourd'hui — le fichier vit à la racine de l'entité, les publishes dessous).
- Appel automatique à la fin de `finalize_publish_version()` **pour `kind != "lop"`
  uniquement** (les publishes LOP restent hors composition — contrat existant, cf.
  commentaire `LOP_PUBLISHES_KEY`). Après la mise à jour du manifeste, dans le même flock.
- Dérive Blender : ne PAS porter les variantes maintenant. Documenter dans CLAUDE.md que
  `usd_composer.compose_asset_root()` (fichier `asset_root.usd`, variantes) est une
  duplication à résorber — la cible est ce composeur unique, les variantes en seront une
  extension future. Ne rien casser côté Blender dans cet incrément.
- Tests : recomposition asset (ordre, latest par step), recomposition shot (root prim,
  timecodes absents/présents), publish deux-phases fixture → root rafraîchi, `kind="lop"`
  → root intact.

**Done :** un publish `kind=step` sur une fixture recompose le bon fichier root ;
`docs/usd-convention.md` gagne la section shot (voir Conventions plus bas).

## Incrément 2 — Schéma 2.1 : `frame_range` du shot

- `asset.schema.json` : propriété optionnelle `frame_range` :
  `{"start": int, "end": int, "fps": number}` (objet, les trois requis si présent).
  `SCHEMA_VERSION = "2.1.0"` (les DEUX manifestes partagent la constante — le bump
  s'applique aussi à `project.schema.json`, changement additif nul dedans, le documenter).
- `build_asset_manifest()` : pour `entity_type="shot"`, poser un défaut
  `{"start": 1001, "end": 1100, "fps": DEFAULT_SCENE["fps"]}`.
- `set_frame_range(project_root, shot_name, start, end, fps=None)` dans
  `create_project.py` : validation (`start < end`, entité = shot), écriture sous
  `acquire_lock` + `_atomic_write_json`, puis `refresh_entity_root()` (timecodes).
- `docs/migration-2.0-to-2.1.md` : changement additif, `additionalProperties: true` →
  aucun manifeste existant invalidé, aucune migration de fichiers requise ; les shots
  créés en 2.0 n'ont pas de `frame_range` → les consommateurs traitent l'absence
  (fallback défaut, jamais de crash).
- Exposition UI web : hors scope (lecture seule possible plus tard via les endpoints
  existants). L'édition passe par la CLI/`set_frame_range` pour l'instant.
- Tests : défaut posé à la création d'un shot, `set_frame_range` (validation + timecodes
  dans `shot_root.usda`), manifeste 2.0 sans `frame_range` toujours accepté.

**Done :** `create_asset(..., entity_type="shot")` produit un manifeste 2.1.0 avec
`frame_range` ; schémas et doc de migration à jour.

## Incrément 3 — HDA `ylos::publish::0.2` : mode step

Aujourd'hui le HDA ne publie qu'en `kind="lop"` (instantané hors taxonomie). Les steps de
shot (et d'asset côté Houdini) publient en `kind=<step>` pour alimenter la composition.

- `tools/houdini/build_publish_hda.py` : bump `TYPE_NAME = "ylos::publish::0.2"` (le
  script reste la source de vérité, le `.hdanc` est régénéré — pas de cohabitation 0.1).
- Nouveaux paramètres : `publish_kind` (menu : `lop` + steps) — menu script Python lisant
  `manifest.json` de l'entité saisie (via le module embarqué, même mécanique d'import que
  `_cp(node)`), fallback statique `DEFAULT_SHOT_STEPS + DEFAULT_ASSET_STEPS` si manifeste
  illisible. `asset_type` ne s'applique qu'en mode `lop` (le callback l'ignore sinon,
  comme `allocate_publish_version`).
- Callback : `allocate_publish_version(..., kind=<step>)` quand `publish_kind != "lop"` ;
  stem `f"{asset_name}_{step}_v{version:03d}"` ; `expected_artifacts` inchangé (artefact +
  `LOP_THUMB_NAME` — le thumbnail reste requis, `_missing_artifacts` s'en charge déjà).
  Le reste du contrat (scratch dir, `soho_foreground`, paramètres promus) ne bouge pas.
- **Relire les 5 gotchas LOP HDA de CLAUDE.md avant de toucher au build** — notamment
  `template_node=` obligatoire au save et paramètres promus (jamais d'écriture directe
  sur un noeud interne).
- e2e `test_publish_hda_e2e.py` étendu : mode step sur une fixture shot (hython, manuel,
  hors CI).

**Done :** un layer USD publié depuis le HDA en `kind="lighting"` atterrit dans
`step_publishes["lighting"]` et déclenche la recomposition de `shot_root.usda`.

## Incrément 4 — Outils shelf shot

Dans `ylos_houdini.py` + `ylos_pipeline.shelf`, même pattern que l'existant (fonction
pure testable / action hou / dialogue `tool_*`).

- `latest_step_publish(project_root, entity_name, step)` (pure) : miroir de
  `latest_lop_publish` sur `step_publishes[step]` (clé `artifact`, statut `complete`).
- `shot_root_path(project_root, shot_name)` (pure) : chemin du `shot_root.usda`,
  `FileNotFoundError` explicite si absent (aucun step publié encore).
- `tool_load_shot()` : choix d'un shot (entités `entity_type == "shot"`), crée un LOP
  `sublayer` sur `shot_root.usda` — **sublayer, pas reference** : le shot EST le stage
  (root prim `/ROOT`), on ne le greffe pas sous un prim. Chemin via `env_relative`.
- `tool_load_step_publish()` : choix entité + step, sublayer du latest publish du step —
  pour composer manuellement quand on ne veut pas tout le shot_root (ex. lighting qui ne
  veut que l'anim). Toujours `env_relative`.
- `tool_save_wip` existant : fonctionne déjà pour les shots (steps lus du manifeste) —
  vérifier, ne rien dupliquer.
- Tests : les deux fonctions pures sur fixtures (avec entrées `pending` à ignorer).

**Done :** depuis un hip vierge, shelf → Load Shot → le stage compose les steps publiés
du shot, chemins en `$PROJ_ROOT`.

## Incrément 5 — Convention cache + publish de caches consommables

Le tier `$PROJ_CACHE` existe (`CACHE_TREE`) mais rien côté Houdini ne le consomme. On fige
la convention minimale maintenant pour ne pas migrer dans la douleur plus tard. La gestion
complète (TTL, sweep, quotas) reste hors scope.

- `create_project.entity_cache_dir(project_root, entity_name, step, label)` (logique
  unique, stdlib) → `$PROJ_CACHE/<projet>/houdini/<entité>/<step>/<label>/` (résolution
  via `resolve_cache()`, `mkdir` parents, retourne le Path). `label` validé par
  `_validate_segment`.
- `ylos_houdini.tool_setup_filecache()` : configure le noeud `filecache` SOP sélectionné —
  `basedir` reçoit l'**expression** `$PROJ_CACHE/<projet>/houdini/<entité>/<step>/`
  (littérale avec la variable, jamais le chemin résolu : relocalisable). Le versioning
  des caches jetables = celui natif du filecache (v1, v2…), pas de manifeste : un cache
  scratch est une donnée jetable, le manifeste n'en garde aucune trace.
- Caches **consommables** (un FX publié pour le lighting/un autre DCC) = contrat
  deux-phases `kind=<step>`, dans la source (permanent), pas dans le cache. Deux
  extensions à `create_project.py` :
  - `PUBLISH_ARTIFACT_EXTENSIONS` += `.vdb`, `.bgeo.sc`, `.abc`. Le suffixe double
    `.bgeo.sc` passe tel quel : `_missing_artifacts` résout par concaténation
    `f"{stem}{ext}"` (vérifié), pas par split d'extension.
  - Séquences (sim multi-frames) : autoriser dans `expected_artifacts` un nom de
    **sous-dossier** de `staging_dir` — nouvelle branche dans `_missing_artifacts`
    (dossier existant ET non vide ; attention : la branche actuelle « contient un point =
    nom de fichier exact » doit rester prioritaire pour `thumb.png`). Adapter AUSSI la
    découverte dans `finalize_publish_version()` : `produced` ne liste que les fichiers
    (`p.is_file()`) — un dossier de séquence n'y apparaîtrait pas et `artifact` resterait
    `None` au manifeste. L'entrée `artifact` pointe alors le dossier. Tests dédiés
    (staging avec séquence, manifeste correct après finalize).
- Un cache publié n'entre PAS dans la composition `shot_root` (ce n'est pas un layer
  USD) : `refresh_entity_root()` ne considère que les artefacts d'extension USD —
  filtrer explicitement, test dédié.
- Tests : `entity_cache_dir`, `_missing_artifacts` mode dossier, extensions nouvelles,
  filtre non-USD dans la recomposition.

**Done :** convention de chemin cache documentée dans CLAUDE.md ; un publish VDB fixture
passe le deux-phases sans polluer `shot_root.usda`.

## Incrément 6 — Rendu Karma → cache, delivery explicite

- Convention de sortie :
  `$PROJ_CACHE/<projet>/render/<shot>/<step>/v<NNN>/<shot>_<step>_v<NNN>.<F4>.exr`.
- `next_render_version(project_root, shot_name, step)` (pure, dans `ylos_houdini.py`) :
  scan disque des `v<NNN>` existants, max+1 — pas de manifeste (tier cache, régénérable ;
  et le suivi des rendus est de la gestion de prod, principe 4 : hors pipeline technique).
- `tool_render_shot()` : crée/configure un `usdrender_rop` dans /stage — `trange=1` avec
  f1/f2 depuis `frame_range` du manifeste (fallback range du hip si absent, avec warning),
  caméra : paramètre du dialogue, pré-rempli par scan du stage sous `/ROOT/cameras/`
  (convention ci-dessous), `outputimage` vers la convention (expression `$PROJ_CACHE`
  littérale + `$F4`). Poser `soho_foreground=1` (gotcha connu : `node.render()` en GUI
  rend la main à la soumission de husk, pas à la fin).
- `deliver_render(project_root, shot_name, step, version)` : copie explicite du dossier
  `v<NNN>` validé vers `delivery/render/<shot>/<step>/v<NNN>/` (le `<step>` est dans le
  chemin — décision Incrément 6 : sans lui, deux steps livrés à la même version fusionnent
  silencieusement via `copytree(dirs_exist_ok=True)`). Refuse si la source est vide.
  Pas d'écriture manifeste (même raison que ci-dessus). Outil shelf `tool_deliver_render()`.
- Note Apprentice : rendus watermarkés/résolution limitée — suffisant pour valider la
  plomberie, pas pour livrer.
- Tests : `next_render_version` (fixtures disque), `deliver_render` (copie, refus si vide).

**Done :** un rendu fixture atterrit dans le cache à la bonne version ; `deliver_render`
copie vers `delivery/` ; rien d'autre n'écrit dans `delivery/`.

## Incrément 7 — Validation end-to-end + sweep docs

- Scénario complet sur projet fixture (hython, manuel) : create shot → save WIP anim →
  publish anim (HDA mode step) → `shot_root.usda` recomposé → load shot en lighting →
  publish lighting → root recomposé avec lighting plus fort → render vers cache →
  deliver. Vérifier chaque fichier/manifeste à chaque étape.
- Sweep documentation : CLAUDE.md (nouvelles sections : composition unifiée, workflow
  shot, convention cache, schéma 2.1, HDA 0.2), `docs/usd-convention.md` (section shot),
  README si besoin.

---

## Conventions à figer (dans `docs/usd-convention.md`, Incrément 1)

- **Shot root** : `shots/<SHOT_Nom_Variant>/shot_root.usda`, root prim `/ROOT`
  (`USD_ROOT_PRIM`), `defaultPrim = "ROOT"`, subLayers = latest `complete` par step,
  ordre `SHOT_DOWNSTREAM_ORDER` (plus fort en premier), `startTimeCode`/`endTimeCode`/
  `timeCodesPerSecond` depuis `frame_range`.
- **Caméra de shot** : publiée par layout/animation sous `/ROOT/cameras/` (ex.
  `/ROOT/cameras/cam_main`). Distincte de `/cameras/ylos_thumb_cam` (thumbnail HDA,
  jamais dans un layer publié).
- **Assets dans un shot** : `references` (ou `payload`) de `asset_root.usda` sous
  `/ROOT/<zone>/<NomAsset>` — convention existante (asset → set/shot), inchangée.
- **Caches** : scratch → `$PROJ_CACHE/<projet>/houdini/<entité>/<step>/<label>/` ;
  consommables → publish deux-phases `kind=<step>` dans la source ; jamais l'inverse.
- **Rendus** : takes → `$PROJ_CACHE/<projet>/render/...` ; finals → copie explicite
  `delivery/render/...`.

## Hors scope explicite (ne pas implémenter, ne pas « améliorer en passant »)

Launcher per-session env (tension `$PROJ_ROOT` connue, CLAUDE.md) ; gestion du cycle de
vie des caches (TTL/sweep/quotas) ; Python Panel / asset browser Houdini ; portage des
variantes Blender dans le composeur unique (nommé Incrément 1, résorption ultérieure) ;
tooling comp (2D) ; pages shot dans l'UI web ; multi-séquences (les shots restent plats
sous `shots/`).

## Points de vigilance pour l'implémentation

1. **Lis avant d'écrire** (CLAUDE.md, mode de collaboration) : mappe l'état réel avant
   toute mutation ; plan avant action pour tout changement de schéma.
2. **Importabilité** : `create_project.py` et `ylos_houdini.py` restent stdlib pure, sans
   import lourd top-level, sans `import hou` top-level.
3. **Extensions jamais supposées** : `.usdnc`/`.hdanc`/`.hipnc` en Apprentice — toujours
   découvrir sur disque (pattern `finalize_publish_version`).
4. **Gotchas LOP HDA** : les 5 points de CLAUDE.md s'appliquent intégralement à
   l'Incrément 3 (`template_node=`, paramètres promus, `savestyle`, `soho_foreground`).
5. **Écritures** : `_atomic_write_*` pour tout JSON/usda ; mutations de manifeste sous
   `acquire_lock` ; `flock` advisory et local-only (connu, pas un problème aujourd'hui).
6. **Un incrément = tests + commit.** Pas de commit multi-incréments ; la CI (unittest
   sans Houdini) doit passer à chaque étape ; les tests hython restent manuels et le
   disent dans leur docstring.
7. **Communication français, code/identifiants anglais** (convention repo).
