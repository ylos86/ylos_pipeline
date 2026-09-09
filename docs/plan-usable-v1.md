# Plan — Amener le pipeline à un état utilisable (v1)

> Document d'architecture destiné à l'implémentation. Rédigé le 2026-09-09 après audit du
> repo (CLAUDE.md, ylos_ui.py, app.html, create_project.py::resolve_open_target, git log).
> **Lire `CLAUDE.md` en entier avant de commencer** — principes verrouillés, gotchas DCC,
> conventions. Rien ici ne les contredit ; quand un incrément étend un contrat, il le dit.
>
> Objet : combler l'écart entre « fonctionnellement riche » et « utilisable au quotidien ».
> Le manque ressenti par Sébastien (vérifié dans le code, pas supposé) : **Project Browser
> cockpit**, **Scene Creator**, **polish visuel**. Le reste du browser existe déjà — on
> construit dessus, on ne réécrit pas.

## Définition de « utilisable » (definition of done)

Depuis **un cockpit unique** : choisir/créer un projet → entrer dans un asset/shot → voir
ses **départements/steps** avec **statut + vignette** → **créer en un clic une scène
d'auteur** pour un task dans le bon DCC (starter par département) → itérer des versions →
publier → voir **ce qui dépend de quoi** — **sans toucher aux variables d'env ni aux
scripts de lancement à la main**. Prouvé **de bout en bout sur un vrai mini-projet**.

## Décisions figées (avec Sébastien, 2026-09-09)

| Sujet | Décision |
|---|---|
| Scene Creator — templates | **Starter par département.** Chaque step ouvre une scène pré-contextualisée (pas une scène vide générique). |
| Statut par step | **Nouveau champ de schéma** (2.1.0 → 2.2.0, additif, migration documentée). Pas de dérivation seule. |
| Media / review | **Galerie de versions** (comparer les vignettes v001/v002…). **Playblasts = hors scope v1**, à activer plus tard. |
| UI Houdini | **Python Panel (cockpit) ET shelf.** On construit le panel ; le shelf reste et sera étendu par la suite (les deux, pas l'un à la place de l'autre). |

## Contrainte transversale (rappel CLAUDE.md — ne pas violer)

- Toute **logique métier** vit dans `create_project.py` (source unique, **stdlib seule**).
  Web/Blender/Houdini = **adaptateurs minces**.
- **`create_project.py` ne peut pas faire de `bpy`/`hou`.** Conséquence directe pour le
  Scene Creator (Phase 1) : l'orchestrateur décide **quoi** contient un starter (spec pure
  et sérialisable) ; le DCC **réalise** la scène à partir de la spec. Voir Phase 1.
- Tout changement de schéma = **migration documentée**, jamais d'édition silencieuse.
- Un fix n'est pas fini sans son test : stdlib (CI 3.9/3.11/3.13) pour l'orchestrateur ;
  headless Blender / e2e hython (hors CI) pour ce qui exige un DCC.

## Séquencement recommandé

**0 → 1 → 2 → 5a (1er shakedown) → 3 → 4 → 5b (backlog).**

Rationale : Phase 0 débloque ; **Phase 1 (Scene Creator) = le manque #1**, elle rend le
browser réellement opérant ; Phase 2 en fait le cockpit. Un **premier shakedown dès la fin
de la Phase 2** livre un pipeline *utilisable* au plus tôt et fait remonter les vraies
frictions **avant** d'investir dans dependency tracking (3) et panels DCC (4).

---

## Phase 0 — Assainissement & socle *(prérequis courts)*

Objectif : base propre + supprimer la friction qui saboterait un vrai shakedown.

- **0.1 Nettoyer `Ylos__Test`** (questions ouvertes de `ui-workstream.md`) :
  - Publish vide `CHARACTER_Sissa02_Default/modeling/v001` (GLB = un EMPTY, zéro mesh, marqué
    `complete`) → republier proprement **ou** marquer `broken` dans le manifeste.
  - Orphelin `sets/lecube` (dossier sans manifeste, hors convention) → supprimer **ou**
    recréer via `+ New` et déplacer le WIP.
  - *Done :* `/api/assets` ne remonte plus aucune carte `broken`, aucun publish `complete`
    sans géométrie.
- **0.2 Env par-session** — tension #1 des docs. `$PROJ_ROOT`/`$PROJ_CACHE` sont globaux au
  shell → Houdini + Blender sur 2 projets = conflit. Le launcher `tools/blender/launch_context.py`
  (déjà le point de passage **unique** de tout lancement DCC) pose l'env **par process**
  enfant, jamais un export global.
  - *Fichiers :* `tools/blender/launch_context.py`, `ylos_ui.py` (`_build_launch_argv` /
    `subprocess.Popen`).
  - *Done :* deux Blender sur deux projets différents, lancés depuis la même UI, ne se
    marchent pas dessus (test : `os.environ['PROJ_ROOT']` distinct par process, vérifié au 1er tick).
- **0.3 Démarrage cockpit sans friction** — fiabiliser `launch_ui.command` (ou une commande
  unique) : le serveur web démarre en un geste (double-clic Finder), log propre, port par défaut.
- **0.4 Trancher le scope Update Import v1** — remplacement pur, **pas** de remap des
  overrides (matériaux/contraintes/anim ajoutés main). L'acter par écrit ici, ou le lever si
  le shakedown en dépend. *Décision par défaut : accepté pour v1.*

---

## Phase 1 — Scene Creator *(cœur du manque ressenti)*

Objectif : créer une scène d'auteur **neuve, pré-contextualisée par département**, pour une
entité+step dans un DCC, **en un clic depuis le browser**.

État actuel confirmé (`create_project.py::resolve_open_target`, docstring) : *« Pas de
template .blend par step au scaffold »*. Ouvrir un step neuf résout vers (1) dernier WIP,
(2) root USD composé, (3) publish — **il n'existe aucun flux de création de scène d'auteur
depuis le browser.** Une scène ne naît qu'en faisant *Save Version* depuis Blender.

**Découpage archi (respecte stdlib-only) :**

- **1.1 Orchestrateur — spec pure.** `scene_starter_spec(entity, step, dcc, project_root)`
  dans `create_project.py`. Retourne un **dict sérialisable** décrivant le starter, jamais
  du `bpy` : chemin WIP alloué (réutilise le contrat d'allocation), preset de scène à
  appliquer, `frame_range`/fps si shot, liste des **références/imports** à poser (ex. le root
  d'assemblage de l'entité), flag caméra. Point unique, réutilisé par les 3 UI. Tests stdlib.
- **1.2 Contenu des starters par département** (décision figée). Table de règles dans
  l'orchestrateur (pas d'`items=` en dur hors `vocab.py` côté Blender) :
  - **shot / layout|anim** → référence `shot_root.usda`, pose `frame_range`+fps du manifeste,
    crée une caméra si absente.
  - **asset / modeling** → scène vide + preset + repère d'échelle.
  - **asset|set / lookdev|surfacing** → référence `asset_root.usda` en lecture, setup
    d'éclairage/turntable minimal.
  - (extensible ; toute valeur de step vient du vocabulaire orchestrateur, cf. `vocab.py`.)
- **1.3 Réalisation DCC.** Blender : `op_create_scene` consomme la spec (applique le preset
  via `core/project.apply_scene_preset` existant, pose refs, sauve le `.blend` initial,
  pose le contexte pipeline). Houdini : idem via `ylos_houdini.py`.
- **1.4 Web.** Bouton **« Nouvelle scène »** dans le drill-down entité+step. `POST
  /api/open-blender` apprend un 3e verbe `create` (il en gère déjà deux : ouvrir / importer).
- *Fichiers :* `create_project.py` (spec + table starters), `plugins/blender/operators/op_create_scene.py`
  (nouveau), `plugins/houdini/python/ylos_houdini.py`, `ylos_ui.py`, `app.html`.
- *Tests :* stdlib (spec : chemin alloué, refs, frame_range) + headless Blender (la scène
  s'ouvre, contexte posé, refs présentes).
- *Done :* depuis le browser, « Nouvelle scène » sur un step neuf ouvre Blender sur un `.blend`
  versionné, pré-contextualisé, prêt à travailler — sans passer par *Save Version* d'abord.

---

## Phase 2 — Project Browser en cockpit *(drill-down + statut + galerie + « hue »)*

Objectif : transformer la grille plate d'assets en la navigation attendue, versions/médias
first-class. **Les données existent déjà** : `GET /api/asset/<name>` renvoie versions par
step + scenefiles.

- **2.1 Statut par step — schéma 2.2.0.** Champ additif au `manifest.json` :
  `step_status: {"<step>": "review"|"approved"}`. Absent → dérivé (`empty` si aucun
  scenefile, `wip` si WIP présent). `additionalProperties: true` → aucun manifeste 2.0/2.1
  invalidé.
  - *Fichiers :* `create_project.py` (lecture/écriture statut, point unique),
    `project.schema.json` / `asset.schema.json`, **`docs/migration-2.1-to-2.2.md`** (nouveau,
    miroir des migrations existantes), `ylos_ui.py` (endpoint set-status), `app.html`.
  - *Done :* changer le statut d'un step depuis le web le persiste au manifeste ; migration
    documentée ; tests stdlib (statut explicite override la dérivation ; défaut correct).
- **2.2 Vue entité en drill-down.** Départements/Steps → Tasks → Scenefiles (versions WIP) →
  Products (publishes), **pas** un modal enterré (`js-modal-scenefiles`). Chaque step affiche
  son statut (2.1) + sa vignette (`resolve_entity_thumbnail`, déjà orchestrateur).
- **2.3 Galerie de versions** (décision figée, MVP). Comparer les vignettes des versions d'un
  step (v001/v002…), ouvrir/importer une version précise (endpoint `open-blender` verbe
  « importer » déjà présent). **Playblasts = hors scope v1.**
- **2.4 Polish visuel (« hue »).** Finaliser la direction Prism déjà amorcée (commit
  *« palette framboise + wine »*) : densités, états vides, découvrabilité pin/sync/versions/statut.
  Design tokens (`--bg`, `--accent`…) déjà en place → **affinage, pas refonte**.
- *Done :* une session de travail (choisir projet → entité → step → statut → versions →
  ouvrir/importer) se fait sans ouvrir un DCC ni un modal enfoui, et « ressemble à un vrai outil ».

---

## Phase 3 — Dependency tracking *(brique Prism choisie)*

Objectif : « cet asset est utilisé dans quels shots », + propagation d'updates.

- **3.1 Index inverse — orchestrateur.** `build_dependency_index(project_root)` dans
  `create_project.py` : lit les roots USD composés (`shot_root.usda`/`asset_root.usda`
  référencent déjà les publishes en subLayers) → pour chaque asset, la liste des shots/sets
  qui le référencent, **avec la version référencée**. Source de vérité = les roots/manifestes,
  **jamais** un fichier annexe à maintenir. Tests stdlib.
- **3.2 Web.** Section « Utilisé dans » sur la fiche entité ; badge **« update dispo »** quand
  un shot pointe une version < latest `complete`.
- **3.3 Blender.** `op_update_imports` détecte déjà les updates → exposer « quels shots sont
  impactés par ce republish » (lecture de l'index, pas de logique dupliquée).
- *Done :* republier un asset fait apparaître un badge « update dispo » sur chaque shot qui
  le référence en version antérieure.

---

## Phase 4 — Panels DCC unifiés *(brique Prism choisie)*

Objectif : le même cockpit *dans* les DCC.

- **4.1 Houdini — Python Panel (cockpit) + shelf** (décision figée : les deux). Python Panel =
  mini Project Browser (entités, versions, ouvrir/importer/publier). Vignettes « gratuites »
  via `resolve_entity_thumbnail`. Le **shelf reste** (Setup File Cache, Render Shot, Deliver
  Render) et sera étendu par la suite. Fonctions pures séparées des dialogues (pattern
  `ylos_houdini.py` existant, cf. `plan-houdini-shots.md`).
- **4.2 Blender — densité N-panel.** Resserrer (4 sections + State Manager en popup) : fusion/
  onglets, State Manager moins caché. **À trancher après le shakedown** (les docs disent :
  décider la densité après usage réel, pas avant).
- *Done :* ouvrir/importer/publier une entité sans quitter Houdini ; N-panel Blender jugé
  confortable après usage réel.

---

## Phase 5 — Shakedown réel + backlog

- **5a (après Phase 2) — 1er shakedown.** Un vrai mini-projet (1 asset, 1 set, 1 shot) traversé
  de bout en bout, web + 2 DCC : create project → create asset → **create scene** → itérer →
  publish → composer shot → render Karma → deliver → review. **Chaque friction est notée** dans
  `pipeline-log.md` et réinjectée dans les phases 3/4 et le backlog.
- **5b — backlog** (activé selon ce que le shakedown révèle) :
  - Variantes dans le composeur unifié (`refresh_entity_root` ne les gère pas ; logique
    dupliquée dans `plugins/blender/core/usd_composer.py` à résorber).
  - Cycle de vie des caches : TTL / sweep / quotas du tier régénérable (jamais purgé
    aujourd'hui).
  - Update Import : remap des overrides (si Phase 0.4 l'a repoussé).
  - Multi-séquences de shots ; tooling comp 2D ; up-axis Blender↔USD à vérifier à l'usage.

---

## Carte des fichiers touchés (récap)

| Zone | Fichiers |
|---|---|
| Orchestrateur | `create_project.py` (scene_starter_spec, table starters, step_status, build_dependency_index) |
| Schéma | `project.schema.json`, `asset.schema.json`, `docs/migration-2.1-to-2.2.md` (nouveau) |
| Web | `ylos_ui.py` (verbe `create`, set-status, dependency), `app.html` (drill-down, galerie, statut, « Nouvelle scène », polish) |
| Blender | `op_create_scene.py` (nouveau), `op_update_imports.py`, `core/project.apply_scene_preset`, `core/vocab.py`, `ui/panel.py` |
| Houdini | `plugins/houdini/python/ylos_houdini.py` (panel + spec), `toolbar/ylos_pipeline.shelf` |
| Launcher | `tools/blender/launch_context.py` (env par-session) |
| Tests | `tests/` (stdlib : spec, statut, index) + `tools/blender/test_*_headless.py` |
